#!/bin/bash
###############################################################################
# run_all_experiments.sh - Master script to reproduce NSDI'25 Figures 4 & 5
#
# This script runs the complete experiment pipeline on CloudLab:
#   1. (Optional) Bayesian optimization for each OC method
#   2. Figure 4: Performance under varying overload levels
#   3. Figure 5: Time-series dynamics during traffic surge
#   4. Generate plots
#
# Usage:
#   ./run_all_experiments.sh [--skip-bo] [--skip-fig4] [--skip-fig5]
#
# Prerequisites:
#   - CloudLab cluster with 7 m510 nodes, K8s configured
#   - hotelApp deployed with: ./setup-k8s-initial.sh hotel
#   - ghz installed
#   - Python3 with: pip3 install matplotlib numpy pyyaml scikit-optimize
###############################################################################

set -euo pipefail

# Ensure common binary locations are in PATH (CloudLab root sessions may miss these)
export PATH="/usr/local/bin:/usr/bin:/usr/local/sbin:/usr/sbin:/snap/bin:$HOME/bin:$HOME/.local/bin:$PATH"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

SKIP_BO=false
SKIP_FIG4=false
SKIP_FIG5=false

for arg in "$@"; do
    case $arg in
        --skip-bo)    SKIP_BO=true ;;
        --skip-fig4)  SKIP_FIG4=true ;;
        --skip-fig5)  SKIP_FIG5=true ;;
        --help|-h)
            echo "Usage: $0 [--skip-bo] [--skip-fig4] [--skip-fig5]"
            exit 0
            ;;
    esac
done

log() {
    echo ""
    echo "================================================================"
    echo " $*"
    echo "================================================================"
    echo ""
}

# ======================== Pre-flight checks ========================
preflight_ok=true
for cmd in kubectl ghz python3; do
    if ! command -v "$cmd" &>/dev/null; then
        echo "ERROR: '$cmd' not found in PATH ($PATH)"
        preflight_ok=false
    fi
done
if [ "$preflight_ok" = false ]; then
    echo "Please install missing tools or add them to PATH before running."
    exit 1
fi
echo "Pre-flight: kubectl=$(command -v kubectl), ghz=$(command -v ghz), python3=$(command -v python3)"

# ======================== Step 1: Bayesian Optimization ========================
if [ "$SKIP_BO" = false ]; then
    log "Step 1: Bayesian Optimization (parameter tuning)"
    
    for method in rajomon breakwater breakwaterd dagor; do
        echo "--- Optimizing $method ---"
        python3 "$SCRIPT_DIR/run_bayesian_opt.py" --method "$method" --n-calls 50 --n-initial 10
    done
    
    log "Bayesian optimization complete"
else
    log "Step 1: Bayesian Optimization SKIPPED"
fi

# ======================== Step 2: Figure 4 ========================
if [ "$SKIP_FIG4" = false ]; then
    log "Step 2: Running Figure 4 experiments"
    bash "$SCRIPT_DIR/run_figure4.sh"
    
    log "Step 2b: Generating Figure 4 plot"
    python3 "$SCRIPT_DIR/plot_figure4.py"
else
    log "Step 2: Figure 4 SKIPPED"
fi

# ======================== Step 3: Figure 5 ========================
if [ "$SKIP_FIG5" = false ]; then
    log "Step 3: Running Figure 5 experiments"
    bash "$SCRIPT_DIR/run_figure5.sh"
    
    log "Step 3b: Generating Figure 5 plot"
    python3 "$SCRIPT_DIR/plot_figure5.py"
else
    log "Step 3: Figure 5 SKIPPED"
fi

# ======================== Done ========================
log "All experiments complete!"
echo "Results:"
echo "  Figure 4: $SCRIPT_DIR/results/figure4/figure4_search_hotel.pdf"
echo "  Figure 5: $SCRIPT_DIR/results/figure5/figure5_search_hotel.pdf"
echo "  BO results: $SCRIPT_DIR/results/bayesian_opt/"
