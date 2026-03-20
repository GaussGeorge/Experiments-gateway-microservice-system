#!/bin/bash
# run_figure7.sh - Run experiments to reproduce Figure 7 from NSDI'25 Rajomon paper
# Usage: ./run_figure7.sh [frontend_addr]
#
# This script runs concurrent Search Hotel and Reserve Hotel requests
# at varying loads for each overload control mechanism.

set -e

FRONTEND_ADDR="${1:-localhost:5000}"
RESULTS_DIR="results_figure7"
DURATION="10s"
WARMUP="5s"
WORKERS=1000
SEARCH_RATIO=0.5
SLO=200  # ms
REPEAT=5  # Number of repetitions per experiment

# Load levels (kRPS) matching Figure 7 x-axis
LOADS="4000 6000 8000 10000 12000 14000 16000"

# Overload control types
OC_TYPES="none rajomon dagor breakwater topfull"

mkdir -p "$RESULTS_DIR"

echo "========================================="
echo "Figure 7 Reproduction Experiment"
echo "========================================="
echo "Frontend:     $FRONTEND_ADDR"
echo "Duration:     $DURATION"
echo "Warmup:       $WARMUP"
echo "Workers:      $WORKERS"
echo "Search Ratio: $SEARCH_RATIO"
echo "SLO:          ${SLO}ms"
echo "Repeats:      $REPEAT"
echo "Loads:        $LOADS"
echo "OC Types:     $OC_TYPES"
echo "========================================="
echo ""

for oc in $OC_TYPES; do
    echo ">>> Setting OC_TYPE=$oc"
    echo ">>> IMPORTANT: Restart all services with OC_TYPE=$oc before continuing"
    echo ">>> Press Enter when services are ready..."
    read -r

    for load in $LOADS; do
        warmup_rps=$((load * 80 / 100))
        for rep in $(seq 1 $REPEAT); do
            outfile="${RESULTS_DIR}/${oc}_${load}rps_rep${rep}.csv"
            echo "  Running: OC=$oc, Load=${load}RPS, Rep=${rep}..."

            go run ./benchmark/loadgen.go \
                -frontend "$FRONTEND_ADDR" \
                -rps "$load" \
                -warmup-rps "$warmup_rps" \
                -duration "$DURATION" \
                -warmup "$WARMUP" \
                -workers "$WORKERS" \
                -search-ratio "$SEARCH_RATIO" \
                -slo "$SLO" \
                -output "$outfile" \
                -mode http

            echo "  -> Saved to $outfile"
            sleep 2  # Brief cooldown between experiments
        done
    done
    echo ""
done

echo "All experiments complete. Results in $RESULTS_DIR/"
echo "Run: python plot_figure7.py $RESULTS_DIR to generate the plot"
