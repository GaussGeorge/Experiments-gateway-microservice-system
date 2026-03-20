#!/usr/bin/env python3
"""
plot_figure7.py - Generate Figure 7 from NSDI'25 Rajomon paper
Plots 95th percentile tail latency and goodput for concurrent
Search Hotel and Reserve Hotel requests.

Usage:
    python plot_figure7.py [results_dir]
    python plot_figure7.py results_figure7
"""

import os
import sys
import glob
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib
matplotlib.rcParams['font.size'] = 12
matplotlib.rcParams['font.family'] = 'serif'

# Configuration
SLO_MS = 200.0  # SLO in milliseconds
LOADS_KRPS = [4, 6, 8, 10, 12, 14, 16]  # Load levels in kRPS

# Visual style for each OC method (matching the paper)
STYLES = {
    'dagor':      {'color': '#1f77b4', 'marker': 's', 'label': 'Dagor',      'linestyle': '-'},
    'breakwater': {'color': '#ff7f0e', 'marker': '^', 'label': 'Breakwater', 'linestyle': '-'},
    'topfull':    {'color': '#2ca02c', 'marker': 'D', 'label': 'TopFull',    'linestyle': '-'},
    'rajomon':    {'color': '#d62728', 'marker': 'o', 'label': 'Rajomon',    'linestyle': '-'},
    'none':       {'color': '#7f7f7f', 'marker': 'x', 'label': 'No OC',     'linestyle': '--'},
}


def load_results(results_dir):
    """Load all CSV result files from the results directory."""
    data = {}  # {oc_type: {load_rps: [DataFrame, ...]}}

    pattern = os.path.join(results_dir, "*.csv")
    files = sorted(glob.glob(pattern))

    if not files:
        print(f"No CSV files found in {results_dir}")
        sys.exit(1)

    for f in files:
        basename = os.path.basename(f).replace('.csv', '')
        parts = basename.split('_')
        # Format: {oc_type}_{load}rps_rep{n}
        if len(parts) < 3:
            continue

        oc_type = parts[0]
        load_str = parts[1].replace('rps', '')
        try:
            load_rps = int(load_str)
        except ValueError:
            continue

        df = pd.read_csv(f)
        if oc_type not in data:
            data[oc_type] = {}
        if load_rps not in data[oc_type]:
            data[oc_type][load_rps] = []
        data[oc_type][load_rps].append(df)

    return data


def compute_metrics(data, request_type=None):
    """Compute p95 latency and goodput for each (oc_type, load) combination."""
    metrics = {}  # {oc_type: {'loads': [], 'p95_lat': [], 'goodput': [], 'p95_err': [], 'goodput_err': []}}

    for oc_type, loads in data.items():
        loads_list = sorted(loads.keys())
        p95_means, p95_stds = [], []
        gp_means, gp_stds = [], []

        for load in loads_list:
            reps_p95 = []
            reps_gp = []

            for df in loads[load]:
                if request_type:
                    df = df[df['request_type'] == request_type]

                if len(df) == 0:
                    continue

                latencies = df['latency_ms'].values
                p95 = np.percentile(latencies, 95)
                reps_p95.append(p95)

                # Goodput: requests completed within SLO
                good = ((df['success'] == True) & (df['latency_ms'] <= SLO_MS)).sum()
                # Calculate duration
                start_times = df['start_time_ns'].values
                if len(start_times) > 1:
                    dur_s = (start_times.max() - start_times.min()) / 1e9
                else:
                    dur_s = 1.0
                if dur_s <= 0:
                    dur_s = 1.0
                goodput_rps = good / dur_s / 1000.0  # Convert to kRPS
                reps_gp.append(goodput_rps)

            if reps_p95:
                p95_means.append(np.mean(reps_p95))
                p95_stds.append(np.std(reps_p95))
            else:
                p95_means.append(0)
                p95_stds.append(0)

            if reps_gp:
                gp_means.append(np.mean(reps_gp))
                gp_stds.append(np.std(reps_gp))
            else:
                gp_means.append(0)
                gp_stds.append(0)

        metrics[oc_type] = {
            'loads': [l / 1000.0 for l in loads_list],  # Convert to kRPS
            'p95_lat': p95_means,
            'p95_err': p95_stds,
            'goodput': gp_means,
            'goodput_err': gp_stds,
        }

    return metrics


def plot_figure7(data, output_file='figure7.pdf'):
    """Generate Figure 7: 2x2 subplot (Search Hotel latency/goodput, Reserve Hotel latency/goodput)."""
    fig, axes = plt.subplots(2, 2, figsize=(12, 8))

    # Compute metrics for each request type
    search_metrics = compute_metrics(data, 'SearchHotel')
    reserve_metrics = compute_metrics(data, 'ReserveHotel')

    # --- Plot Search Hotel ---
    ax_lat_search = axes[0, 0]
    ax_gp_search = axes[1, 0]

    for oc_type, m in search_metrics.items():
        style = STYLES.get(oc_type, STYLES['none'])
        ax_lat_search.errorbar(m['loads'], m['p95_lat'], yerr=m['p95_err'],
                               color=style['color'], marker=style['marker'],
                               label=style['label'], linestyle=style['linestyle'],
                               capsize=3, markersize=6)
        ax_gp_search.errorbar(m['loads'], m['goodput'], yerr=m['goodput_err'],
                              color=style['color'], marker=style['marker'],
                              label=style['label'], linestyle=style['linestyle'],
                              capsize=3, markersize=6)

    # SLO line
    ax_lat_search.axhline(y=SLO_MS, color='gray', linestyle=':', label='SLO', alpha=0.7)

    ax_lat_search.set_ylabel('95th Tail Latency (ms)')
    ax_lat_search.set_title('Search Hotel')
    ax_lat_search.set_ylim(bottom=0)
    ax_lat_search.legend(fontsize=9, loc='upper left')
    ax_lat_search.grid(True, alpha=0.3)

    ax_gp_search.set_xlabel('Load (kRPS)')
    ax_gp_search.set_ylabel('Goodput (kRPS)')
    ax_gp_search.set_ylim(bottom=0)
    ax_gp_search.grid(True, alpha=0.3)

    # --- Plot Reserve Hotel ---
    ax_lat_reserve = axes[0, 1]
    ax_gp_reserve = axes[1, 1]

    for oc_type, m in reserve_metrics.items():
        style = STYLES.get(oc_type, STYLES['none'])
        ax_lat_reserve.errorbar(m['loads'], m['p95_lat'], yerr=m['p95_err'],
                                color=style['color'], marker=style['marker'],
                                label=style['label'], linestyle=style['linestyle'],
                                capsize=3, markersize=6)
        ax_gp_reserve.errorbar(m['loads'], m['goodput'], yerr=m['goodput_err'],
                               color=style['color'], marker=style['marker'],
                               label=style['label'], linestyle=style['linestyle'],
                               capsize=3, markersize=6)

    ax_lat_reserve.axhline(y=SLO_MS, color='gray', linestyle=':', label='SLO', alpha=0.7)

    ax_lat_reserve.set_ylabel('95th Tail Latency (ms)')
    ax_lat_reserve.set_title('Reserve Hotel')
    ax_lat_reserve.set_ylim(bottom=0)
    ax_lat_reserve.legend(fontsize=9, loc='upper left')
    ax_lat_reserve.grid(True, alpha=0.3)

    ax_gp_reserve.set_xlabel('Load (kRPS)')
    ax_gp_reserve.set_ylabel('Goodput (kRPS)')
    ax_gp_reserve.set_ylim(bottom=0)
    ax_gp_reserve.grid(True, alpha=0.3)

    fig.suptitle('Figure 7: Performance of OC on concurrent Search Hotel and Reserve Hotel requests',
                 fontsize=14, fontweight='bold')
    plt.tight_layout()
    plt.savefig(output_file, dpi=300, bbox_inches='tight')
    plt.savefig(output_file.replace('.pdf', '.png'), dpi=300, bbox_inches='tight')
    print(f"Figure saved to {output_file} and {output_file.replace('.pdf', '.png')}")
    plt.show()


def main():
    results_dir = sys.argv[1] if len(sys.argv) > 1 else 'results_figure7'

    if not os.path.isdir(results_dir):
        print(f"Results directory not found: {results_dir}")
        print("Run the experiments first using run_figure7.sh")
        sys.exit(1)

    print(f"Loading results from {results_dir}...")
    data = load_results(results_dir)

    print(f"Found OC types: {list(data.keys())}")
    for oc, loads in data.items():
        print(f"  {oc}: {sorted(loads.keys())} RPS, {sum(len(v) for v in loads.values())} total runs")

    plot_figure7(data)


if __name__ == '__main__':
    main()
