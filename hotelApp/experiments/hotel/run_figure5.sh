#!/bin/bash
###############################################################################
# run_figure5.sh - Reproduce NSDI'25 Figure 5 (Search Hotel Time-Series)
#
# Figure 5: Control dynamics during a single traffic surge
#   - Row 1: Latency over time (avg + P95, log scale)
#   - Row 2: Throughput breakdown (goodput, SLO violation, dropped, rate-limited)
#   - 5 columns: rajomon, breakwater, breakwaterd, dagor, topdown
#
# Paper parameters:
#   - Search Hotel application
#   - Warmup: 4k RPS for 5s
#   - Overload: 10k RPS for 10s
#   - SLO = 60ms
#   - 1000 workers, Poisson arrivals
###############################################################################

set -euo pipefail

# ======================== Configuration ========================
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOTELAPP_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
RESULTS_DIR="$SCRIPT_DIR/results/figure5"
YAML_PATH="$HOTELAPP_DIR/msgraph.yaml"
PROTO_DIR="${PROTO_DIR:-/users/$(whoami)/hotelproto}"

# Experiment parameters
SLO_MS=60
WARMUP_RPS=4000
OVERLOAD_RPS=10000
NUM_WORKERS=1000
NUM_CONNECTIONS=1000
TIMEOUT="20s"

# For Figure 5 we need per-request detail: use step schedule to combine warmup+overload
# Step schedule: warmup for 5s, then overload for 10s
# ghz step: start=4000, end=10000, step=6000, step-duration=5s → creates 2 steps
# But step duration is per-step... so we use two separate runs and merge

# OC methods
METHODS=(rajomon breakwater breakwaterd dagor topdown)

GHZ_CALL="hotelproto.FrontendService/SearchHotels"
GHZ_HOST=""

# ======================== Helper Functions ========================

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

get_frontend_ip() {
    kubectl get service frontend -o=jsonpath='{.spec.clusterIP}'
}

wait_for_pods() {
    log "Waiting for all pods to be ready..."
    kubectl wait --for=condition=ready pod --all --timeout=120s
    sleep 10
}

update_configmap() {
    kubectl delete configmap msgraph-config --ignore-not-found
    kubectl create configmap msgraph-config --from-file="$YAML_PATH"
}

redeploy() {
    local method=$1
    log "Redeploying with INTERCEPT=$method..."
    python3 "$SCRIPT_DIR/update_intercept.py" "$method" --yaml-path "$YAML_PATH"
    update_configmap
    cd "$HOTELAPP_DIR"
    METHOD=hotel ./setup-k8s-redeploy.sh hotel
    wait_for_pods
    sleep 5
}

run_ghz_detailed() {
    # Run ghz and capture per-request details
    # $1 = RPS, $2 = duration, $3 = output_file
    local rps=$1
    local duration=$2
    local output_file=$3

    ghz --insecure \
        --proto "$PROTO_DIR/frontend.proto" \
        --import-paths "$PROTO_DIR" \
        --call "$GHZ_CALL" \
        --rps "$rps" \
        --concurrency "$NUM_WORKERS" \
        --connections "$NUM_CONNECTIONS" \
        --duration "$duration" \
        --timeout "$TIMEOUT" \
        --format json \
        --metadata '{"timestamp":"{{.TimestampUnix}}"}' \
        "$GHZ_HOST" > "$output_file" 2>/dev/null
}

# ======================== Main ========================

main() {
    mkdir -p "$RESULTS_DIR"
    
    log "========================================="
    log "Starting Figure 5 Experiments"
    log "  Warmup: ${WARMUP_RPS} RPS for 5s"
    log "  Overload: ${OVERLOAD_RPS} RPS for 10s"
    log "  SLO: ${SLO_MS}ms"
    log "  Methods: ${METHODS[*]}"
    log "========================================="
    
    for method in "${METHODS[@]}"; do
        log "--- Method: $method ---"
        
        redeploy "$method"
        
        local frontend_ip
        frontend_ip=$(get_frontend_ip)
        GHZ_HOST="${frontend_ip}:50051"
        log "Frontend address: $GHZ_HOST"
        
        local warmup_file="$RESULTS_DIR/${method}_warmup.json"
        local overload_file="$RESULTS_DIR/${method}_overload.json"
        
        log "  Phase 1: Warmup at ${WARMUP_RPS} RPS for 5s..."
        run_ghz_detailed "$WARMUP_RPS" "5s" "$warmup_file" || {
            log "  WARNING: warmup ghz failed for $method"
        }
        
        log "  Phase 2: Overload at ${OVERLOAD_RPS} RPS for 10s..."
        run_ghz_detailed "$OVERLOAD_RPS" "10s" "$overload_file" || {
            log "  WARNING: overload ghz failed for $method"
        }
        
        log "--- Method $method complete ---"
        
        # Cooldown between methods
        sleep 5
    done
    
    log "========================================="
    log "Figure 5 experiments complete!"
    log "Results in: $RESULTS_DIR"
    log "Run: python3 $SCRIPT_DIR/plot_figure5.py"
    log "========================================="
}

main "$@"
