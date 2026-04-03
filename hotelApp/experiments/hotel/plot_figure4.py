#!/usr/bin/env python3
"""
plot_figure4.py - Reproduce NSDI'25 Figure 4 (Search Hotel)

Reads ghz JSON output files and generates:
  - Left panel: 95th percentile tail latency vs offered load
  - Right panel: Goodput vs offered load

Usage:
    python3 plot_figure4.py [--results-dir figure4] [--slo 60]
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
except ImportError:
    print("Error: matplotlib required. Install with: pip3 install matplotlib", file=sys.stderr)
    sys.exit(1)


# ======================== Configuration ========================

METHOD_ORDER = ['rajomon', 'breakwater', 'breakwaterd', 'dagor', 'topdown']

METHOD_COLORS = {
    'rajomon':     '#E63946',
    'breakwater':  '#457B9D',
    'breakwaterd': '#1D3557',
    'dagor':       '#F4A261',
    'topdown':     '#2A9D8F',
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

    ghz reports latency in **nanoseconds** in the details array.

    Returns dict with: p95_ms, goodput_rps, total_rps, avg_ms, error_rate, offered_rps
    """
    try:
        with open(filepath, 'r') as f:
            data = json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return None

    total_count = data.get('count', 0)
    if total_count == 0:
        return None

    # Duration in seconds from the summary (total is in nanoseconds)
    total_ns = data.get('total', 0)
    duration_s = total_ns / 1e9 if total_ns > 0 else 10.0
    duration_s = max(duration_s, 0.001)

    # Offered load from summary
    offered_rps = data.get('rps', total_count / duration_s)

    details = data.get('details', [])

    # ---- Path A: we have per-request details ----
    if details:
        ok_latencies_ms = []
        goodput_count = 0
        error_count = 0

        for d in details:
            latency_ns = d.get('latency', 0)        # nanoseconds
            latency_ms = latency_ns / 1e6
            error = d.get('error', '')
            status = d.get('status', 'OK')

            if error or status not in ('OK', ''):
                error_count += 1
                continue

            ok_latencies_ms.append(latency_ms)
            if latency_ms <= slo_ms:
                goodput_count += 1

        if not ok_latencies_ms:
            # All requests failed
            return {
                'p95_ms': float('nan'),
                'goodput_rps': 0.0,
                'total_rps': offered_rps,
                'avg_ms': float('nan'),
                'error_rate': 1.0,
                'offered_rps': offered_rps,
            }

        return {
            'p95_ms': float(np.percentile(ok_latencies_ms, 95)),
            'goodput_rps': goodput_count / duration_s,
            'total_rps': total_count / duration_s,
            'avg_ms': float(np.mean(ok_latencies_ms)),
            'error_rate': error_count / total_count,
            'offered_rps': offered_rps,
        }

    # ---- Path B: aggregate stats only (no details) ----
    lat_dist = data.get('latencyDistribution', None) or []
    p95_ns = 0
    for ld in lat_dist:
        if ld.get('percentage') == 95:
            p95_ns = ld.get('latency', 0)
            break
    avg_ns = data.get('average', 0)

    return {
        'p95_ms': p95_ns / 1e6,
        'goodput_rps': 0.0,
        'total_rps': offered_rps,
        'avg_ms': avg_ns / 1e6,
        'error_rate': 0.0,
        'offered_rps': offered_rps,
    }


def load_results(results_dir, slo_ms):
    """Load and aggregate all experiment results.

    Returns: {method: {load_rps: {'p95_ms': [...], 'goodput_rps': [...]}}}
    """
    results = {}

    pattern = os.path.join(results_dir, '*_*rps_rep*.json')
    files = glob.glob(pattern)

    if not files:
        print(f"No result files found matching {pattern}", file=sys.stderr)
        sys.exit(1)

    for filepath in sorted(files):
        filename = os.path.basename(filepath)
        match = re.match(r'(\w+)_(\d+)rps_rep(\d+)\.json', filename)
        if not match:
            continue

        method = match.group(1)
        load_rps = int(match.group(2))

        parsed = parse_ghz_json(filepath, slo_ms)
        if parsed is None:
            print(f"  Warning: skip {filename} (empty)", file=sys.stderr)
            continue

        results.setdefault(method, {})
        results[method].setdefault(load_rps, {'p95_ms': [], 'goodput_rps': []})

        results[method][load_rps]['p95_ms'].append(parsed['p95_ms'])
        results[method][load_rps]['goodput_rps'].append(parsed['goodput_rps'])

    return results


# ======================== Plotting ========================

def plot_figure4(results, slo_ms, output_path):
    """Generate Figure 4: (a) P95 tail latency, (b) Goodput."""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4.5))

    # Plot methods in a fixed order
    for method in METHOD_ORDER:
        if method not in results:
            continue
        loads = sorted(results[method].keys())
        load_k = [l / 1000.0 for l in loads]

        # Aggregate across repeats (skip NaN for p95 when all errors)
        p95_vals = [results[method][l]['p95_ms'] for l in loads]
        p95_mean = []
        p95_std = []
        for vals in p95_vals:
            finite = [v for v in vals if np.isfinite(v)]
            if finite:
                p95_mean.append(np.mean(finite))
                p95_std.append(np.std(finite))
            else:
                p95_mean.append(float('nan'))
                p95_std.append(0)

        goodput_mean = [np.mean(results[method][l]['goodput_rps']) / 1000.0 for l in loads]
        goodput_std = [np.std(results[method][l]['goodput_rps']) / 1000.0 for l in loads]

        color = METHOD_COLORS.get(method, '#333333')
        marker = METHOD_MARKERS.get(method, 'o')
        label = METHOD_LABELS.get(method, method)

        # Left panel: P95 tail latency (only plot finite values)
        finite_mask = [np.isfinite(v) for v in p95_mean]
        lk_f = [lk for lk, m in zip(load_k, finite_mask) if m]
        pm_f = [pm for pm, m in zip(p95_mean, finite_mask) if m]
        ps_f = [ps for ps, m in zip(p95_std, finite_mask) if m]
        if lk_f:
            ax1.errorbar(lk_f, pm_f, yerr=ps_f,
                         color=color, marker=marker, label=label,
                         linewidth=2, markersize=6, capsize=3)
        else:
            # All-error method: show as a note in legend (plot invisible point)
            ax1.plot([], [], color=color, marker=marker, label=f'{label} (all errors)')

        # Right panel: Goodput (0 for all-error methods)
        ax2.errorbar(load_k, goodput_mean, yerr=goodput_std,
                     color=color, marker=marker, label=label,
                     linewidth=2, markersize=6, capsize=3)

    # Left panel formatting
    ax1.set_xlabel('Offered Load (kRPS)', fontsize=12)
    ax1.set_ylabel('95th Tail Latency (ms)', fontsize=12)
    ax1.axhline(y=slo_ms, color='red', linestyle='--', alpha=0.5, label=f'SLO ({slo_ms}ms)')
    ax1.legend(fontsize=8, loc='upper left')
    ax1.grid(True, alpha=0.3)
    ax1.set_title('(a) Search Hotel - Tail Latency', fontsize=12)

    # Right panel formatting
    ax2.set_xlabel('Offered Load (kRPS)', fontsize=12)
    ax2.set_ylabel('Goodput (kRPS)', fontsize=12)
    ax2.set_ylim(bottom=0)
    ax2.legend(fontsize=8, loc='upper left')
    ax2.grid(True, alpha=0.3)
    ax2.set_title('(b) Search Hotel - Goodput', fontsize=12)

    plt.tight_layout()
    plt.savefig(output_path, dpi=300, bbox_inches='tight')
    print(f"Figure saved to: {output_path}")
    plt.close()


# ======================== Main ========================

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser(description='Plot NSDI25 Figure 4')
    parser.add_argument('--results-dir', default=os.path.join(script_dir, 'figure4'))
    parser.add_argument('--slo', type=float, default=60.0, help='SLO in milliseconds')
    parser.add_argument('--output', default=None, help='Output file path')

    args = parser.parse_args()

    if args.output is None:
        args.output = os.path.join(args.results_dir, 'figure4_search_hotel.pdf')

    results = load_results(args.results_dir, args.slo)

    print(f"Loaded results for methods: {list(results.keys())}")
    for method in sorted(results.keys()):
        loads = sorted(results[method].keys())
        for l in loads:
            p95s = results[method][l]['p95_ms']
            gps = results[method][l]['goodput_rps']
            finite_p95 = [v for v in p95s if np.isfinite(v)]
            avg_p95 = np.mean(finite_p95) if finite_p95 else float('nan')
            avg_gp = np.mean(gps)
            print(f"  {method}@{l/1000:.0f}k: p95={avg_p95:.1f}ms, goodput={avg_gp:.0f}rps ({len(p95s)} reps)")

    plot_figure4(results, args.slo, args.output)


if __name__ == '__main__':
    main()
