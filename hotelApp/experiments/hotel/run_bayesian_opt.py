#!/usr/bin/env python3
"""
run_bayesian_opt.py - Bayesian optimization for OC parameters (NSDI'25 §5.1)

Tunes OC-specific parameters using Bayesian optimization with the objective:
    goodput - 10 * max(0, p95_latency - SLO)

Each iteration:
1. Select parameter values from search space
2. Update msgraph.yaml with selected parameters
3. Redeploy services (update configmap + restart pods)
4. Run warmup (5s at 80% max throughput) + overload (10s at 200% max throughput)
5. Measure goodput and P95 tail latency
6. Feed back to optimizer

Usage:
    python3 run_bayesian_opt.py --method rajomon [--n-calls 50] [--slo 60]
    python3 run_bayesian_opt.py --method breakwater [--n-calls 50]
    python3 run_bayesian_opt.py --method breakwaterd [--n-calls 50]
    python3 run_bayesian_opt.py --method dagor [--n-calls 50]

Requirements:
    pip3 install scikit-optimize numpy pyyaml
"""

import argparse
import json
import os
import subprocess
import sys
import time

import shutil
import numpy as np
import yaml

# Ensure common binary locations are in PATH (CloudLab root sessions may miss these)
_extra_paths = ['/usr/local/bin', '/usr/bin', '/usr/local/sbin', '/usr/sbin', '/snap/bin',
                os.path.expanduser('~/bin'), os.path.expanduser('~/.local/bin')]
os.environ['PATH'] = os.pathsep.join(_extra_paths) + os.pathsep + os.environ.get('PATH', '')

try:
    from skopt import gp_minimize
    from skopt.space import Real, Integer, Categorical
except ImportError:
    print("Error: scikit-optimize required. Install with: pip3 install scikit-optimize", file=sys.stderr)
    sys.exit(1)

# Pre-flight check: verify kubectl and ghz are reachable
for _tool in ('kubectl', 'ghz'):
    if not shutil.which(_tool):
        print(f"Error: '{_tool}' not found in PATH. PATH={os.environ.get('PATH')}", file=sys.stderr)
        sys.exit(1)


# ======================== Configuration ========================

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
HOTELAPP_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, '..', '..'))
YAML_PATH = os.path.join(HOTELAPP_DIR, 'msgraph.yaml')
# Detect CloudLab username from script path (works even when running as root)
import re as _re
_match = _re.search(r'/users/([^/]+)', SCRIPT_DIR)
_cloudlab_user = _match.group(1) if _match else os.environ.get("USER", "Gauss")
PROTO_DIR = os.environ.get('PROTO_DIR', f'/users/{_cloudlab_user}/hotelproto')

SLO_MS = 60          # Search Hotel SLO
WARMUP_RPS = 4000    # 80% of max sustainable
OVERLOAD_RPS = 10000  # 200% of max sustainable (roughly)

GHZ_CALL = "hotelproto.FrontendService/SearchHotels"

# ======================== Parameter Search Spaces ========================

# Paper §5.1: four categories of parameters
# 1. Queuing threshold
# 2. Update frequency
# 3. Step width
# 4. Client-side parameters

SEARCH_SPACES = {
    'rajomon': {
        'params': [
            ('LATENCY_THRESHOLD', Real(10, 10000, name='LATENCY_THRESHOLD')),   # us
            ('PRICE_UPDATE_RATE', Real(1000, 100000, name='PRICE_UPDATE_RATE')),  # us
            ('TOKEN_UPDATE_RATE', Real(1000, 100000, name='TOKEN_UPDATE_RATE')),  # us
            ('PRICE_STEP', Real(0, 100, name='PRICE_STEP')),
        ],
        'format': {
            'LATENCY_THRESHOLD': lambda v: f'{int(v)}us',
            'PRICE_UPDATE_RATE': lambda v: f'{int(v)}us',
            'TOKEN_UPDATE_RATE': lambda v: f'{int(v)}us',
            'PRICE_STEP': lambda v: str(int(v)),
        },
    },
    'breakwater': {
        'params': [
            ('BREAKWATER_SLO', Real(5000, 60000, name='BREAKWATER_SLO')),         # us
            ('BREAKWATER_A', Real(0.0001, 5.0, name='BREAKWATER_A', prior='log-uniform')),
            ('BREAKWATER_B', Real(0.001, 10.0, name='BREAKWATER_B', prior='log-uniform')),
            ('BREAKWATER_CLIENT_EXPIRATION', Real(0, 50000, name='BREAKWATER_CLIENT_EXPIRATION')),  # us
            ('BREAKWATER_INITIAL_CREDIT', Integer(10, 5000, name='BREAKWATER_INITIAL_CREDIT')),
            ('BREAKWATER_RTT', Real(100, 5000, name='BREAKWATER_RTT')),  # us
        ],
        'format': {
            'BREAKWATER_SLO': lambda v: f'{int(v)}us',
            'BREAKWATER_A': lambda v: str(round(v, 6)),
            'BREAKWATER_B': lambda v: str(round(v, 6)),
            'BREAKWATER_CLIENT_EXPIRATION': lambda v: f'{int(v)}us',
            'BREAKWATER_INITIAL_CREDIT': lambda v: str(int(v)),
            'BREAKWATER_RTT': lambda v: f'{int(v)}us',
        },
    },
    'breakwaterd': {
        # Breakwaterd has BOTH frontend and backend params (2x parameter count)
        'params': [
            # Frontend params
            ('BREAKWATER_SLO', Real(5000, 60000, name='BREAKWATER_SLO')),
            ('BREAKWATER_A', Real(0.0001, 5.0, name='BREAKWATER_A', prior='log-uniform')),
            ('BREAKWATER_B', Real(0.001, 10.0, name='BREAKWATER_B', prior='log-uniform')),
            ('BREAKWATER_CLIENT_EXPIRATION', Real(0, 50000, name='BREAKWATER_CLIENT_EXPIRATION')),
            ('BREAKWATER_INITIAL_CREDIT', Integer(10, 5000, name='BREAKWATER_INITIAL_CREDIT')),
            ('BREAKWATER_RTT', Real(100, 5000, name='BREAKWATER_RTT')),
            # Backend params (BREAKWATERD_*)
            ('BREAKWATERD_SLO', Real(5000, 60000, name='BREAKWATERD_SLO')),
            ('BREAKWATERD_A', Real(0.0001, 5.0, name='BREAKWATERD_A', prior='log-uniform')),
            ('BREAKWATERD_B', Real(0.001, 10.0, name='BREAKWATERD_B', prior='log-uniform')),
            ('BREAKWATERD_CLIENT_EXPIRATION', Real(0, 50000, name='BREAKWATERD_CLIENT_EXPIRATION')),
            ('BREAKWATERD_INITIAL_CREDIT', Integer(10, 5000, name='BREAKWATERD_INITIAL_CREDIT')),
            ('BREAKWATERD_RTT', Real(100, 5000, name='BREAKWATERD_RTT')),
        ],
        'format': {
            'BREAKWATER_SLO': lambda v: f'{int(v)}us',
            'BREAKWATER_A': lambda v: str(round(v, 6)),
            'BREAKWATER_B': lambda v: str(round(v, 6)),
            'BREAKWATER_CLIENT_EXPIRATION': lambda v: f'{int(v)}us',
            'BREAKWATER_INITIAL_CREDIT': lambda v: str(int(v)),
            'BREAKWATER_RTT': lambda v: f'{int(v)}us',
            'BREAKWATERD_SLO': lambda v: f'{int(v)}us',
            'BREAKWATERD_A': lambda v: str(round(v, 6)),
            'BREAKWATERD_B': lambda v: str(round(v, 6)),
            'BREAKWATERD_CLIENT_EXPIRATION': lambda v: f'{int(v)}us',
            'BREAKWATERD_INITIAL_CREDIT': lambda v: str(int(v)),
            'BREAKWATERD_RTT': lambda v: f'{int(v)}us',
        },
    },
    'dagor': {
        'params': [
            ('DAGOR_QUEUING_THRESHOLD', Real(500, 100000, name='DAGOR_QUEUING_THRESHOLD')),  # us
            ('DAGOR_ALPHA', Real(0.001, 1.0, name='DAGOR_ALPHA', prior='log-uniform')),
            ('DAGOR_BETA', Real(0.001, 1.0, name='DAGOR_BETA', prior='log-uniform')),
            ('DAGOR_ADMISSION_LEVEL_UPDATE_INTERVAL', Real(1000, 100000, name='DAGOR_ADMISSION_LEVEL_UPDATE_INTERVAL')),  # us
            ('DAGOR_UMAX', Real(1, 100, name='DAGOR_UMAX')),
        ],
        'format': {
            'DAGOR_QUEUING_THRESHOLD': lambda v: f'{int(v)}us',
            'DAGOR_ALPHA': lambda v: str(round(v, 4)),
            'DAGOR_BETA': lambda v: str(round(v, 4)),
            'DAGOR_ADMISSION_LEVEL_UPDATE_INTERVAL': lambda v: f'{int(v)}us',
            'DAGOR_UMAX': lambda v: str(int(v)),
        },
    },
}


# ======================== Helper Functions ========================

def log(msg):
    print(f"[BO] {msg}", flush=True)


def update_yaml_params(yaml_path, method, param_dict):
    """Update msgraph.yaml: set INTERCEPT + OC params for all nodes."""
    with open(yaml_path, 'r') as f:
        data = yaml.safe_load(f)
    
    for node in data.get('nodes', []):
        if 'rajomon' not in node:
            continue
        for cfg in node['rajomon']:
            if cfg['name'] == 'INTERCEPT':
                cfg['value'] = method
            elif cfg['name'] in param_dict:
                cfg['value'] = param_dict[cfg['name']]
    
    with open(yaml_path, 'w') as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)


def update_configmap(yaml_path):
    """Update K8s ConfigMap."""
    subprocess.run(['kubectl', 'delete', 'configmap', 'msgraph-config', '--ignore-not-found'],
                    capture_output=True)
    subprocess.run(['kubectl', 'create', 'configmap', 'msgraph-config', f'--from-file={yaml_path}'],
                    capture_output=True)


def redeploy_services():
    """Redeploy non-Redis services and wait for ready."""
    result = subprocess.run(
        ['bash', '-c', f'cd {HOTELAPP_DIR} && METHOD=hotel ./setup-k8s-redeploy.sh hotel'],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        log(f"Redeploy warning: {result.stderr[:200]}")
    
    # Wait for pods
    subprocess.run(['kubectl', 'wait', '--for=condition=ready', 'pod', '--all', '--timeout=120s'],
                    capture_output=True)
    time.sleep(10)


def get_frontend_ip():
    """Get frontend service ClusterIP."""
    result = subprocess.run(
        ['kubectl', 'get', 'service', 'frontend', '-o=jsonpath={.spec.clusterIP}'],
        capture_output=True, text=True
    )
    return result.stdout.strip()


def run_ghz_and_measure(host, rps, duration_s, slo_ms, tmp_file='/tmp/bo_ghz_result.json'):
    """Run ghz and return (goodput_rps, p95_ms)."""
    cmd = [
        'ghz', '--insecure',
        '--proto', f'{PROTO_DIR}/frontend.proto',
        '--import-paths', PROTO_DIR,
        '--call', GHZ_CALL,
        '--rps', str(rps),
        '--concurrency', '1000',
        '--connections', '1000',
        '--duration', f'{duration_s}s',
        '--timeout', '20s',
        '--format', 'json',
        '--metadata', '{"timestamp":"{{.TimestampUnix}}"}',
        host,
    ]
    
    with open(tmp_file, 'w') as outf:
        result = subprocess.run(cmd, stdout=outf, stderr=subprocess.DEVNULL, timeout=duration_s + 30)
    
    if result.returncode != 0:
        return 0.0, 9999.0
    
    try:
        with open(tmp_file, 'r') as f:
            data = json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return 0.0, 9999.0
    
    details = data.get('details', [])
    if not details:
        return 0.0, 9999.0
    
    ok_latencies_ms = []
    goodput_count = 0
    
    for d in details:
        error = d.get('error', '')
        status = d.get('status', 'OK')
        if error or status not in ('OK', ''):
            continue
        latency_ms = d.get('latency', 0) / 1000.0
        ok_latencies_ms.append(latency_ms)
        if latency_ms <= slo_ms:
            goodput_count += 1
    
    if not ok_latencies_ms:
        return 0.0, 9999.0
    
    p95_ms = float(np.percentile(ok_latencies_ms, 95))
    goodput_rps = goodput_count / max(duration_s, 0.001)
    
    return goodput_rps, p95_ms


# ======================== Objective Function ========================

def create_objective(method, space_config, slo_ms):
    """Create the BO objective function for a given method."""
    param_names = [name for name, _ in space_config['params']]
    formatters = space_config['format']
    iteration = [0]
    
    def objective(x):
        iteration[0] += 1
        
        # Map optimizer values to param dict
        param_dict = {}
        for i, (name, _) in enumerate(space_config['params']):
            if name in formatters:
                param_dict[name] = formatters[name](x[i])
            else:
                param_dict[name] = str(x[i])
        
        log(f"Iteration {iteration[0]}: {param_dict}")
        
        try:
            # Update config and redeploy
            update_yaml_params(YAML_PATH, method, param_dict)
            update_configmap(YAML_PATH)
            redeploy_services()
            
            frontend_ip = get_frontend_ip()
            host = f"{frontend_ip}:50051"
            
            # Phase 1: Warmup
            run_ghz_and_measure(host, WARMUP_RPS, 5, slo_ms)
            
            # Phase 2: Overload measurement
            goodput_rps, p95_ms = run_ghz_and_measure(host, OVERLOAD_RPS, 10, slo_ms)
            
            # Objective: goodput - 10 * max(0, p95 - SLO)
            # Negate because gp_minimize minimizes
            penalty = 10.0 * max(0.0, p95_ms - slo_ms)
            score = goodput_rps - penalty
            
            log(f"  -> goodput={goodput_rps:.1f} RPS, p95={p95_ms:.1f}ms, "
                f"penalty={penalty:.1f}, score={score:.1f}")
            
            return -score  # Negate for minimization
            
        except Exception as e:
            log(f"  -> ERROR: {e}")
            return 0.0  # Neutral score on failure
    
    return objective, [dim for _, dim in space_config['params']]


# ======================== Main ========================

def main():
    parser = argparse.ArgumentParser(description='Bayesian optimization for OC parameters')
    parser.add_argument('--method', required=True,
                        choices=['rajomon', 'breakwater', 'breakwaterd', 'dagor'])
    parser.add_argument('--n-calls', type=int, default=50, help='Number of BO iterations')
    parser.add_argument('--n-initial', type=int, default=10, help='Initial random evaluations')
    parser.add_argument('--slo', type=float, default=60.0, help='SLO in ms')
    parser.add_argument('--output-dir', default=os.path.join(SCRIPT_DIR, 'results', 'bayesian_opt'))
    
    args = parser.parse_args()
    
    if args.method not in SEARCH_SPACES:
        print(f"Error: No search space defined for {args.method}", file=sys.stderr)
        sys.exit(1)
    
    os.makedirs(args.output_dir, exist_ok=True)
    
    space_config = SEARCH_SPACES[args.method]
    objective_fn, dimensions = create_objective(args.method, space_config, args.slo)
    
    log(f"Starting Bayesian optimization for {args.method}")
    log(f"  Parameters: {[n for n, _ in space_config['params']]}")
    log(f"  Total calls: {args.n_calls} ({args.n_initial} initial)")
    log(f"  SLO: {args.slo}ms")
    
    result = gp_minimize(
        objective_fn,
        dimensions,
        n_calls=args.n_calls,
        n_initial_points=args.n_initial,
        random_state=42,
        verbose=True,
    )
    
    # Save results
    best_params = {}
    formatters = space_config['format']
    for i, (name, _) in enumerate(space_config['params']):
        raw_val = result.x[i]
        formatted_val = formatters[name](raw_val) if name in formatters else str(raw_val)
        best_params[name] = formatted_val
    
    result_summary = {
        'method': args.method,
        'slo_ms': args.slo,
        'best_score': -result.fun,
        'best_params': best_params,
        'n_calls': args.n_calls,
        'all_scores': [-f for f in result.func_vals],
    }
    
    output_file = os.path.join(args.output_dir, f'bo_result_{args.method}.json')
    with open(output_file, 'w') as f:
        json.dump(result_summary, f, indent=2)
    
    log(f"\nOptimization complete!")
    log(f"Best score: {-result.fun:.1f}")
    log(f"Best params: {best_params}")
    log(f"Results saved to: {output_file}")
    
    # Also update msgraph.yaml with best params
    update_yaml_params(YAML_PATH, args.method, best_params)
    log(f"Updated {YAML_PATH} with best params")


if __name__ == '__main__':
    main()
