#!/usr/bin/env python3
"""
plot_figure5.py - Reproduce NSDI'25 Figure 5 (Search Hotel Time-Series)

Reads ghz JSON output (warmup + overload) for each OC method and generates:
  - Row 1: Latency over time (avg + P95) with log-scale Y-axis
  - Row 2: Throughput breakdown (goodput, SLO violation, dropped/rejected)
  - 5 columns: Rajomon, Breakwater, Breakwaterd, Dagor, TopFull

Usage:
    python3 plot_figure5.py [--results-dir figure5] [--slo 60]
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime

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
METHOD_LABELS = {
    'rajomon':     'Rajomon',
    'breakwater':  'Breakwater',
    'breakwaterd': 'Breakwaterd',
    'dagor':       'Dagor',
    'topdown':     'TopFull',
}


# ======================== Data Parsing ========================

def parse_timestamp(ts):
    """Parse a timestamp string to epoch seconds."""
    if isinstance(ts, (int, float)):
        return float(ts)
    if isinstance(ts, str):
        ts_clean = re.sub(r'[+-]\d{2}:\d{2}$', '', ts)
        ts_clean = re.sub(r'Z$', '', ts_clean)
        try:
            dt = datetime.fromisoformat(ts_clean)
        except ValueError:
            dt = datetime.strptime(ts_clean, '%Y-%m-%dT%H:%M:%S.%f')
        return dt.timestamp()
    return 0.0


def load_phase_data(filepath):
    """Load per-request details from a ghz JSON file.

    ghz reports latency in **nanoseconds**.
    Returns list of dicts: [{timestamp_s, latency_ms, status, error}, ...]
    """
    try:
        with open(filepath, 'r') as f:
            data = json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return []

    details = data.get('details', [])
    records = []

    for d in details:
        ts = parse_timestamp(d.get('timestamp', 0))
        latency_ns = d.get('latency', 0)        # nanoseconds
        latency_ms = latency_ns / 1e6
        status = d.get('status', 'OK')
        error = d.get('error', '')

        records.append({
            'timestamp_s': ts,
            'latency_ms': latency_ms,
            'status': status,
            'error': error,
        })

    return records


def merge_phases(warmup_records, overload_records):
    """Merge warmup and overload records, normalize timestamps to start at 0."""
    all_records = warmup_records + overload_records
    if not all_records:
        return []

    t_start = min(r['timestamp_s'] for r in all_records)
    for r in all_records:
        r['rel_time_s'] = r['timestamp_s'] - t_start

    all_records.sort(key=lambda r: r['rel_time_s'])
    return all_records


def compute_time_series(records, slo_ms, bin_size_s):
    """Compute binned time-series statistics.

    Auto-detects total duration from the data.
    Returns dict with arrays (one value per time bin).
    """
    if not records:
        return None

    total_duration_s = records[-1]['rel_time_s'] + 0.1
    bins = np.arange(0, total_duration_s + bin_size_s, bin_size_s)
    n_bins = len(bins) - 1

    time_centers = (bins[:-1] + bins[1:]) / 2

    avg_lat = np.full(n_bins, np.nan)
    p95_lat = np.full(n_bins, np.nan)
    goodput = np.zeros(n_bins)
    slo_viol = np.zeros(n_bins)
    dropped = np.zeros(n_bins)

    for i in range(n_bins):
        t_lo, t_hi = bins[i], bins[i + 1]
        bin_records = [r for r in records if t_lo <= r['rel_time_s'] < t_hi]

        if not bin_records:
            continue

        ok_latencies = []
        n_dropped = 0

        for r in bin_records:
            error = r.get('error', '')
            status = r.get('status', 'OK')

            if error or status not in ('OK', ''):
                n_dropped += 1
            else:
                ok_latencies.append(r['latency_ms'])

        if ok_latencies:
            avg_lat[i] = np.mean(ok_latencies)
            p95_lat[i] = np.percentile(ok_latencies, 95)

            n_goodput = sum(1 for l in ok_latencies if l <= slo_ms)
            n_slo_viol = len(ok_latencies) - n_goodput

            goodput[i] = n_goodput / bin_size_s
            slo_viol[i] = n_slo_viol / bin_size_s

        dropped[i] = n_dropped / bin_size_s

    return {
        'time': time_centers,
        'avg_latency_ms': avg_lat,
        'p95_latency_ms': p95_lat,
        'goodput_rps': goodput,
        'slo_violation_rps': slo_viol,
        'dropped_rps': dropped,
    }


# ======================== Plotting ========================

def plot_figure5(all_series, slo_ms, output_path):
    """Generate Figure 5: 2 rows x N columns."""
    methods = [m for m in METHOD_ORDER if m in all_series]
    n_methods = len(methods)

    if n_methods == 0:
        print("No data to plot!", file=sys.stderr)
        return

    fig, axes = plt.subplots(2, n_methods, figsize=(4 * n_methods, 6),
                             sharex='col', squeeze=False)

    for col, method in enumerate(methods):
        ts = all_series[method]
        t = ts['time']

        # --- Row 1: Latency ---
        ax_lat = axes[0][col]

        mask_avg = np.isfinite(ts['avg_latency_ms']) & (ts['avg_latency_ms'] > 0)
        mask_p95 = np.isfinite(ts['p95_latency_ms']) & (ts['p95_latency_ms'] > 0)

        has_latency = np.any(mask_avg) or np.any(mask_p95)

        if np.any(mask_avg):
            ax_lat.plot(t[mask_avg], ts['avg_latency_ms'][mask_avg],
                        color='#457B9D', linewidth=1.5, label='Avg E2E')
        if np.any(mask_p95):
            ax_lat.plot(t[mask_p95], ts['p95_latency_ms'][mask_p95],
                        color='#E76F51', linewidth=1.5, label='P95 E2E')

        ax_lat.axhline(y=slo_ms, color='red', linestyle='--', alpha=0.6, linewidth=1)
        ax_lat.axvline(x=5.0, color='gray', linestyle=':', alpha=0.5)  # Overload starts

        if has_latency:
            ax_lat.set_yscale('log')
            ax_lat.set_ylim(1, 1000)
        else:
            ax_lat.text(0.5, 0.5, 'All requests\nrejected/errored',
                        transform=ax_lat.transAxes, ha='center', va='center',
                        fontsize=9, color='red', alpha=0.7)

        ax_lat.set_title(METHOD_LABELS.get(method, method), fontsize=12, fontweight='bold')
        ax_lat.grid(True, alpha=0.3, which='both')

        if col == 0:
            ax_lat.set_ylabel('Latency (ms)', fontsize=10)
            if has_latency:
                ax_lat.legend(fontsize=7, loc='upper left')

        # --- Row 2: Throughput breakdown ---
        ax_tp = axes[1][col]

        goodput_k = ts['goodput_rps'] / 1000.0
        slo_viol_k = ts['slo_violation_rps'] / 1000.0
        dropped_k = ts['dropped_rps'] / 1000.0

        ax_tp.fill_between(t, 0, goodput_k, alpha=0.7, color='#2A9D8F', label='Goodput')
        ax_tp.fill_between(t, goodput_k, goodput_k + slo_viol_k,
                           alpha=0.7, color='#F4A261', label='SLO Violation')
        ax_tp.fill_between(t, goodput_k + slo_viol_k,
                           goodput_k + slo_viol_k + dropped_k,
                           alpha=0.7, color='#E63946', label='Dropped/Rejected')

        ax_tp.axvline(x=5.0, color='gray', linestyle=':', alpha=0.5)
        max_tp = max(np.max(goodput_k + slo_viol_k + dropped_k), 1)
        ax_tp.set_ylim(0, max_tp * 1.15)
        ax_tp.set_xlabel('Time (s)', fontsize=10)
        ax_tp.grid(True, alpha=0.3)

        if col == 0:
            ax_tp.set_ylabel('Throughput (kRPS)', fontsize=10)
            ax_tp.legend(fontsize=6, loc='upper right')

    plt.tight_layout()
    plt.savefig(output_path, dpi=300, bbox_inches='tight')
    print(f"Figure saved to: {output_path}")
    plt.close()


# ======================== Main ========================

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser(description='Plot NSDI25 Figure 5')
    parser.add_argument('--results-dir', default=os.path.join(script_dir, 'figure5'))
    parser.add_argument('--slo', type=float, default=60.0)
    parser.add_argument('--bin-size', type=float, default=0.5, help='Time bin size in seconds')
    parser.add_argument('--output', default=None)

    args = parser.parse_args()

    if args.output is None:
        args.output = os.path.join(args.results_dir, 'figure5_search_hotel.pdf')

    all_series = {}

    for method in METHOD_ORDER:
        warmup_file = os.path.join(args.results_dir, f'{method}_warmup.json')
        overload_file = os.path.join(args.results_dir, f'{method}_overload.json')

        if not os.path.exists(overload_file):
            print(f"  Skipping {method}: no overload data found")
            continue

        warmup_records = load_phase_data(warmup_file) if os.path.exists(warmup_file) else []
        overload_records = load_phase_data(overload_file)

        merged = merge_phases(warmup_records, overload_records)

        if not merged:
            print(f"  Skipping {method}: no records parsed")
            continue

        # Count OK vs errors
        n_ok = sum(1 for r in merged if not r['error'] and r['status'] in ('OK', ''))
        n_err = len(merged) - n_ok
        print(f"  {method}: {len(warmup_records)} warmup + {len(overload_records)} overload = "
              f"{len(merged)} total ({n_ok} OK, {n_err} errors)")

        ts = compute_time_series(merged, args.slo, args.bin_size)
        if ts is not None:
            all_series[method] = ts

    if not all_series:
        print("Error: No data loaded!", file=sys.stderr)
        sys.exit(1)

    plot_figure5(all_series, args.slo, args.output)


if __name__ == '__main__':
    main()
