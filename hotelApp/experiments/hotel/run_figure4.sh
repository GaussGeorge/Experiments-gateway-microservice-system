#!/bin/bash
###############################################################################
# run_figure4.sh - Reproduce NSDI'25 Figure 4 (Search Hotel)
#
# Figure 4: Performance comparison under varying overload levels
#   - X-axis: Offered load (kRPS)
#   - Y-axis left: 95th percentile tail latency (ms)
#   - Y-axis right: Goodput (kRPS) = requests completed within SLO
#   - Methods: rajomon, breakwater, breakwaterd, dagor, topdown
#
# Paper parameters:
#   - CloudLab m510, 7 nodes, 8 cores each
#   - SLO = 60ms (Search Hotel)
#   - Warmup: 4k RPS for 5s
#   - Overload: 6k-18k RPS for 10s
#   - Workers/connections: 1000
#   - Poisson arrival process
#   - 5 repeats per data point
###############################################################################

set -euo pipefail

# ======================== Configuration ========================
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOTELAPP_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
RESULTS_DIR="$SCRIPT_DIR/results/figure4"
YAML_PATH="$HOTELAPP_DIR/msgraph.yaml"
# Detect CloudLab username from script path (works even when running as root)
CLOUDLAB_USER=$(echo "$SCRIPT_DIR" | grep -oP '(?<=/users/)[^/]+' || whoami)
PROTO_DIR="${PROTO_DIR:-/users/$CLOUDLAB_USER/hotelproto}"

# Experiment parameters (matching paper §5)
SLO_MS=60                          # Search Hotel SLO (Table 2)
WARMUP_RPS=4000                    # 80% of max sustainable throughput
WARMUP_DURATION="5s"               # Warmup phase duration
OVERLOAD_DURATION="10s"            # Overload phase duration
NUM_WORKERS=1000                   # Concurrent workers
NUM_CONNECTIONS=1000               # gRPC connections
NUM_REPEATS=5                      # Repeats per data point
TIMEOUT="20s"                      # Request timeout

# Load levels to test (kRPS * 1000)
LOAD_LEVELS=(6000 8000 10000 12000 14000 16000 18000)

# OC methods to compare
METHODS=(rajomon breakwater breakwaterd dagor topdown)

# gRPC call target
GHZ_CALL="hotelproto.FrontendService/SearchHotels"
GHZ_HOST=""  # Will be set dynamically

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
    sleep 10  # Extra buffer for service initialization
}

update_configmap() {
    log "Updating configmap with new msgraph.yaml..."
    kubectl delete configmap msgraph-config --ignore-not-found
    kubectl create configmap msgraph-config --from-file="$YAML_PATH"
}

redeploy() {
    local method=$1
    log "Redeploying with INTERCEPT=$method..."
    
    # Update INTERCEPT in msgraph.yaml
    python3 "$SCRIPT_DIR/update_intercept.py" "$method" --yaml-path "$YAML_PATH"
    
    # Update configmap
    update_configmap
    
    # Redeploy services (keep Redis)
    cd "$HOTELAPP_DIR"
    METHOD=hotel ./setup-k8s-redeploy.sh hotel
    
    wait_for_pods
    
    # Give services extra time to initialize OC state
    sleep 5
}

run_ghz() {
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

# ======================== Main Experiment Loop ========================

main() {
    mkdir -p "$RESULTS_DIR"
    
    log "========================================="
    log "Starting Figure 4 Experiments"
    log "  SLO: ${SLO_MS}ms"
    log "  Warmup: ${WARMUP_RPS} RPS x ${WARMUP_DURATION}"
    log "  Load levels: ${LOAD_LEVELS[*]}"
    log "  Methods: ${METHODS[*]}"
    log "  Repeats: $NUM_REPEATS"
    log "========================================="
    
    for method in "${METHODS[@]}"; do
        log "--- Method: $method ---"
        
        # Deploy with this OC method
        redeploy "$method"
        
        # Get frontend address
        local frontend_ip
        frontend_ip=$(get_frontend_ip)
        GHZ_HOST="${frontend_ip}:50051"
        log "Frontend address: $GHZ_HOST"
        
        for load_rps in "${LOAD_LEVELS[@]}"; do
            local load_k=$(echo "scale=0; $load_rps / 1000" | bc)
            log "  Load: ${load_k}k RPS"
            
            for repeat in $(seq 1 $NUM_REPEATS); do
                local result_file="$RESULTS_DIR/${method}_${load_rps}rps_rep${repeat}.json"
                
                if [ -f "$result_file" ] && [ -s "$result_file" ]; then
                    log "    Repeat $repeat: already exists, skipping"
                    continue
                fi
                
                log "    Repeat $repeat/$NUM_REPEATS: warmup..."
                
                # Phase 1: Warmup (discard results)
                run_ghz "$WARMUP_RPS" "$WARMUP_DURATION" "/dev/null" || true
                
                log "    Repeat $repeat/$NUM_REPEATS: overload at ${load_k}k RPS..."
                
                # Phase 2: Overload (collect results)
                run_ghz "$load_rps" "$OVERLOAD_DURATION" "$result_file" || {
                    log "    WARNING: ghz failed for $method @ ${load_k}k RPS rep $repeat"
                    continue
                }
                
                log "    Repeat $repeat/$NUM_REPEATS: done"
                
                # Brief cooldown between repeats
                sleep 3
            done
        done
        
        log "--- Method $method complete ---"
    done
    
    log "========================================="
    log "Figure 4 experiments complete!"
    log "Results in: $RESULTS_DIR"
    log "Run: python3 $SCRIPT_DIR/plot_figure4.py"
    log "========================================="
}

main "$@"
