#!/usr/bin/env python3
"""
plot_figure4.py - Reproduce NSDI'25 Figure 4 (Search Hotel)

Reads ghz JSON output files from results/figure4/ and generates:
  - Left panel: 95th percentile tail latency vs offered load
  - Right panel: Goodput vs offered load

Usage:
    python3 plot_figure4.py [--results-dir results/figure4] [--slo 60]
"""

import argparse
import json
import os
import glob
import re
import sys

import numpy as np

try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter
except ImportError:
    print("Error: matplotlib required. Install with: pip3 install matplotlib", file=sys.stderr)
    sys.exit(1)


# ======================== Configuration ========================

METHOD_COLORS = {
    'rajomon':     '#E63946',  # Red
    'breakwater':  '#457B9D',  # Blue
    'breakwaterd': '#1D3557',  # Dark blue
    'dagor':       '#F4A261',  # Orange
    'topdown':     '#2A9D8F',  # Teal
}

METHOD_MARKERS = {
    'rajomon':     'o',
    'breakwater':  's',
    'breakwaterd': 'D',
    'dagor':       '^',
    'topdown':     'v',
}

METHOD_LABELS = {
    'rajomon':     'Rajomon',
    'breakwater':  'Breakwater',
    'breakwaterd': 'Breakwaterd',
    'dagor':       'Dagor',
    'topdown':     'TopFull',
}


# ======================== Data Parsing ========================

def parse_ghz_json(filepath, slo_ms):
    """Parse a single ghz JSON result file.
    
    Returns:
        dict with keys: p95_ms, goodput_rps, total_rps, avg_ms, error_rate
        or None if parsing fails
    """
    try:
        with open(filepath, 'r') as f:
            data = json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return None
    
    details = data.get('details', [])
    if not details:
        # Try to use aggregate stats
        options = data.get('options', {})
        total_count = data.get('count', 0)
        total_time_ns = data.get('total', 0)
        
        if total_count == 0:
            return None
        
        # Use latency distribution if available
        lat_dist = data.get('latencyDistribution', [])
        p95_ns = 0
        for ld in (lat_dist or []):
            if ld.get('percentage') == 95:
                p95_ns = ld.get('latency', 0)
                break
        
        return {
            'p95_ms': p95_ns / 1e6,
            'goodput_rps': 0,  # Can't compute without per-request detail
            'total_rps': data.get('rps', 0),
            'avg_ms': data.get('average', 0) / 1e6,
            'error_rate': 0,
        }
    
    # Per-request analysis
    latencies_ms = []
    goodput_count = 0
    slo_violation_count = 0
    error_count = 0
    
    slo_ns = slo_ms * 1e6  # Convert to nanoseconds (ghz uses ns for latency)
    
    for detail in details:
        latency_us = detail.get('latency', 0)  # ghz reports in microseconds
        latency_ms = latency_us / 1000.0
        error = detail.get('error', '')
        status = detail.get('status', 'OK')
        
        if error or status not in ('OK', ''):
            error_count += 1
            continue
        
        latencies_ms.append(latency_ms)
        
        if latency_ms <= slo_ms:
            goodput_count += 1
        else:
            slo_violation_count += 1
    
    total_count = len(details)
    if not latencies_ms:
        return {
            'p95_ms': 0,
            'goodput_rps': 0,
            'total_rps': 0,
            'avg_ms': 0,
            'error_rate': error_count / max(total_count, 1),
        }
    
    # Compute time span from timestamps
    timestamps = [d['timestamp'] for d in details if 'timestamp' in d]
    if len(timestamps) >= 2:
        # Parse ISO timestamps
        from datetime import datetime
        try:
            ts_list = []
            for ts in timestamps:
                # Handle various timestamp formats
                if isinstance(ts, str):
                    # Remove timezone info for parsing
                    ts_clean = re.sub(r'[+-]\d{2}:\d{2}$', '', ts)
                    ts_clean = re.sub(r'Z$', '', ts_clean)
                    try:
                        dt = datetime.fromisoformat(ts_clean)
                    except ValueError:
                        dt = datetime.strptime(ts_clean, '%Y-%m-%dT%H:%M:%S.%f')
                    ts_list.append(dt.timestamp())
                elif isinstance(ts, (int, float)):
                    ts_list.append(ts)
            
            if ts_list:
                duration_s = max(ts_list) - min(ts_list)
                if duration_s <= 0:
                    duration_s = 10.0  # Default overload duration
            else:
                duration_s = 10.0
        except Exception:
            duration_s = 10.0
    else:
        # Fallback: use total time from summary
        total_ns = data.get('total', 0)
        duration_s = total_ns / 1e9 if total_ns > 0 else 10.0
    
    duration_s = max(duration_s, 0.001)  # Avoid division by zero
    
    p95_ms = float(np.percentile(latencies_ms, 95))
    goodput_rps = goodput_count / duration_s
    total_rps_actual = total_count / duration_s
    
    return {
        'p95_ms': p95_ms,
        'goodput_rps': goodput_rps,
        'total_rps': total_rps_actual,
        'avg_ms': float(np.mean(latencies_ms)),
        'error_rate': error_count / max(total_count, 1),
    }


def load_results(results_dir, slo_ms):
    """Load and aggregate all experiment results.
    
    Returns:
        dict: {method: {load_rps: {'p95_ms': [vals], 'goodput_rps': [vals]}}}
    """
    results = {}
    
    pattern = os.path.join(results_dir, '*_*rps_rep*.json')
    files = glob.glob(pattern)
    
    if not files:
        print(f"No result files found in {results_dir}", file=sys.stderr)
        print(f"Expected pattern: {pattern}", file=sys.stderr)
        sys.exit(1)
    
    for filepath in sorted(files):
        filename = os.path.basename(filepath)
        # Parse: method_RPSrps_repN.json
        match = re.match(r'(\w+)_(\d+)rps_rep(\d+)\.json', filename)
        if not match:
            continue
        
        method = match.group(1)
        load_rps = int(match.group(2))
        
        parsed = parse_ghz_json(filepath, slo_ms)
        if parsed is None:
            print(f"  Warning: Failed to parse {filename}", file=sys.stderr)
            continue
        
        if method not in results:
            results[method] = {}
        if load_rps not in results[method]:
            results[method][load_rps] = {'p95_ms': [], 'goodput_rps': []}
        
        results[method][load_rps]['p95_ms'].append(parsed['p95_ms'])
        results[method][load_rps]['goodput_rps'].append(parsed['goodput_rps'])
    
    return results


# ======================== Plotting ========================

def plot_figure4(results, slo_ms, output_path):
    """Generate Figure 4 with two panels: tail latency and goodput."""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4.5))
    
    for method in sorted(results.keys()):
        loads = sorted(results[method].keys())
        load_k = [l / 1000.0 for l in loads]
        
        # Aggregate across repeats
        p95_mean = [np.mean(results[method][l]['p95_ms']) for l in loads]
        p95_std = [np.std(results[method][l]['p95_ms']) for l in loads]
        
        goodput_mean = [np.mean(results[method][l]['goodput_rps']) / 1000.0 for l in loads]
        goodput_std = [np.std(results[method][l]['goodput_rps']) / 1000.0 for l in loads]
        
        color = METHOD_COLORS.get(method, '#333333')
        marker = METHOD_MARKERS.get(method, 'o')
        label = METHOD_LABELS.get(method, method)
        
        # Left panel: P95 tail latency
        ax1.errorbar(load_k, p95_mean, yerr=p95_std,
                      color=color, marker=marker, label=label,
                      linewidth=2, markersize=6, capsize=3)
        
        # Right panel: Goodput
        ax2.errorbar(load_k, goodput_mean, yerr=goodput_std,
                      color=color, marker=marker, label=label,
                      linewidth=2, markersize=6, capsize=3)
    
    # Left panel formatting
    ax1.set_xlabel('Offered Load (kRPS)', fontsize=12)
    ax1.set_ylabel('95th Tail Latency (ms)', fontsize=12)
    ax1.axhline(y=slo_ms, color='red', linestyle='--', alpha=0.5, label=f'SLO ({slo_ms}ms)')
    ax1.set_ylim(bottom=0, top=1000)
    ax1.legend(fontsize=9, loc='upper left')
    ax1.grid(True, alpha=0.3)
    ax1.set_title('(a) Search Hotel - Tail Latency', fontsize=12)
    
    # Right panel formatting
    ax2.set_xlabel('Offered Load (kRPS)', fontsize=12)
    ax2.set_ylabel('Goodput (kRPS)', fontsize=12)
    ax2.set_ylim(bottom=0)
    ax2.legend(fontsize=9, loc='upper right')
    ax2.grid(True, alpha=0.3)
    ax2.set_title('(a) Search Hotel - Goodput', fontsize=12)
    
    plt.tight_layout()
    plt.savefig(output_path, dpi=300, bbox_inches='tight')
    print(f"Figure saved to: {output_path}")
    plt.close()


# ======================== Main ========================

def main():
    parser = argparse.ArgumentParser(description='Plot NSDI25 Figure 4')
    parser.add_argument('--results-dir', default=os.path.join(os.path.dirname(__file__), 'results', 'figure4'))
    parser.add_argument('--slo', type=float, default=60.0, help='SLO in milliseconds')
    parser.add_argument('--output', default=None, help='Output file path')
    
    args = parser.parse_args()
    
    if args.output is None:
        args.output = os.path.join(args.results_dir, 'figure4_search_hotel.pdf')
    
    results = load_results(args.results_dir, args.slo)
    
    print(f"Loaded results for methods: {list(results.keys())}")
    for method in sorted(results.keys()):
        loads = sorted(results[method].keys())
        print(f"  {method}: {len(loads)} load levels, "
              f"{[len(results[method][l]['p95_ms']) for l in loads]} repeats")
    
    plot_figure4(results, args.slo, args.output)


if __name__ == '__main__':
    main()
