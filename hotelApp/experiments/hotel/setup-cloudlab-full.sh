#!/bin/bash
###############################################################################
# setup-cloudlab-full.sh - Complete CloudLab setup for NSDI'25 experiments
#
# This script sets up a 7-node CloudLab m510 cluster for reproducing
# Figure 4 and Figure 5 from the Rajomon paper.
#
# Prerequisites:
#   - CloudLab experiment with 7 m510 nodes (node-0 to node-6)
#   - Ubuntu 18.04+
#   - SSH access between nodes
#
# Usage:
#   # Run on the control node (node-0):
#   ./setup-cloudlab-full.sh
#
# Node assignment:
#   node-0: Control plane + client (ghz load generator)
#   node-1 ~ node-6: Kubernetes worker nodes (hotel microservices)
###############################################################################

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOTELAPP_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
USERNAME=${CLOUDLAB_USER:-$(echo "$SCRIPT_DIR" | grep -oP '(?<=/users/)[^/]+' || whoami)}
REPO_ROOT="/users/$USERNAME/Experiments-gateway-microservice-system"

log() {
    echo "[$(date '+%H:%M:%S')] $*"
}

# ======================== Step 1: Install Dependencies ========================
install_deps() {
    log "Step 1: Installing dependencies..."
    
    sudo apt-get update -qq
    sudo apt-get install -y -qq python3 python3-pip wget curl git bc jq
    
    # Python packages for experiment scripts
    pip3 install --user matplotlib numpy pyyaml scikit-optimize
    
    # Install Go (if not present)
    if ! command -v go &> /dev/null; then
        log "Installing Go 1.22..."
        wget -q https://go.dev/dl/go1.22.5.linux-amd64.tar.gz
        sudo tar -C /usr/local -xzf go1.22.5.linux-amd64.tar.gz
        echo 'export PATH=$PATH:/usr/local/go/bin' >> ~/.bashrc
        export PATH=$PATH:/usr/local/go/bin
        rm go1.22.5.linux-amd64.tar.gz
    fi
    
    # Install ghz
    if ! command -v ghz &> /dev/null; then
        log "Installing ghz..."
        GHZ_VERSION="v0.120.0"
        wget -q "https://github.com/bojand/ghz/releases/download/${GHZ_VERSION}/ghz-linux-x86_64.tar.gz"
        tar -xzf ghz-linux-x86_64.tar.gz
        sudo mv ghz /usr/local/bin/
        rm -f ghz-linux-x86_64.tar.gz
    fi
    
    log "Dependencies installed."
}

# ======================== Step 2: Clone Repositories ========================
clone_repos() {
    log "Step 2: Cloning repositories..."
    
    cd /users/$USERNAME
    
    # Add GitHub to known hosts
    ssh-keyscan -t rsa github.com >> ~/.ssh/known_hosts 2>/dev/null
    
    # Clone your experiment repo (contains hotelApp + all 4 gateways)
    local REPO_NAME="Experiments-gateway-microservice-system"
    if [ -d "$REPO_NAME" ]; then
        log "  $REPO_NAME already exists, pulling latest..."
        cd "$REPO_NAME" && git pull && cd ..
    else
        log "  Cloning $REPO_NAME..."
        git clone https://github.com/GaussGeorge/Experiments-gateway-microservice-system.git
    fi
    
    # Create convenience symlink so paths like /users/$USERNAME/hotelApp work
    if [ ! -e "hotelApp" ]; then
        ln -s "$REPO_NAME/hotelApp" hotelApp
    fi
    
    # Clone hotelproto separately (needed by ghz for .proto files)
    if [ -d "hotelproto" ]; then
        log "  hotelproto already exists, pulling latest..."
        cd hotelproto && git pull && cd ..
    else
        log "  Cloning hotelproto..."
        git clone https://github.com/Jiali-Xing/hotelproto.git
    fi
    
    log "Repositories ready."
}

# ======================== Step 3: Build Binaries ========================
build_binaries() {
    log "Step 3: Building hotel app binaries..."
    
    cd /users/$USERNAME/hotelApp
    
    # Build populate tool
    if [ -d "populate" ]; then
        log "  Building populate tool..."
        cd populate
        CGO_ENABLED=0 GOOS=linux go build -o populate .
        cd ..
    fi
    
    # Build service binaries
    bash build-binaries.sh
    
    log "Binaries built."
}

# ======================== Step 4: Setup Kubernetes ========================
setup_k8s() {
    log "Step 4: Setting up Kubernetes cluster..."
    
    # Check if kubectl is available
    if ! command -v kubectl &> /dev/null; then
        log "ERROR: kubectl not found. Please set up Kubernetes first."
        log "  On CloudLab, use the Kubernetes profile or install manually:"
        log "    - kubeadm init (on control node)"
        log "    - kubeadm join (on worker nodes)"
        exit 1
    fi
    
    # Verify cluster
    log "  Verifying cluster..."
    kubectl get nodes
    
    local node_count
    node_count=$(kubectl get nodes --no-headers | wc -l)
    log "  Found $node_count nodes in cluster."
    
    if [ "$node_count" -lt 7 ]; then
        log "WARNING: Expected 7 nodes but found $node_count."
        log "  Paper uses 7 m510 nodes (1 control + 6 workers)."
    fi
    
    log "Kubernetes ready."
}

# ======================== Step 5: Initial Deployment ========================
initial_deploy() {
    log "Step 5: Deploying hotel application..."
    
    cd /users/$USERNAME/hotelApp
    
    # Deploy with plain (no OC) first for baseline
    python3 experiments/hotel/update_intercept.py plain --yaml-path msgraph.yaml
    
    # Run initial setup (creates configmap, deploys services, populates data)
    bash setup-k8s-initial.sh hotel
    
    log "Hotel application deployed and populated."
}

# ======================== Step 6: Verify Deployment ========================
verify_deployment() {
    log "Step 6: Verifying deployment..."
    
    # Check all pods running
    kubectl get pods -o wide
    
    # Get frontend IP
    local frontend_ip
    frontend_ip=$(kubectl get service frontend -o=jsonpath='{.spec.clusterIP}')
    log "  Frontend service IP: $frontend_ip"
    
    # Quick smoke test
    log "  Running smoke test (100 requests at 100 RPS)..."
    ghz --insecure \
        --proto /users/$USERNAME/hotelproto/frontend.proto \
        --import-paths /users/$USERNAME/hotelproto \
        --call hotelproto.FrontendService/SearchHotels \
        --rps 100 \
        --concurrency 10 \
        --connections 10 \
        --duration 3s \
        --timeout 10s \
        "${frontend_ip}:50051" | tail -20
    
    log "Deployment verified!"
}

# ======================== Step 7: Find Max Throughput ========================
find_max_throughput() {
    log "Step 7: Finding max sustainable throughput (for warmup RPS)..."
    log "  This step is optional but recommended."
    log "  Paper uses warmup at 80% of max sustainable throughput."
    log ""
    log "  To find max throughput manually, run increasing loads:"
    log "    for rps in 1000 2000 3000 4000 5000 6000; do"
    log "      echo '--- Testing \$rps RPS ---'"
    log "      ghz --insecure --proto /users/$USERNAME/hotelproto/frontend.proto \\"
    log "          --import-paths /users/$USERNAME/hotelproto \\"
    log "          --call hotelproto.FrontendService/SearchHotels \\"
    log "          --rps \$rps --concurrency 200 --connections 200 \\"
    log "          --duration 10s --timeout 20s \${FRONTEND_IP}:50051 | grep -E 'Summary|Latency'"
    log "    done"
    log ""
    log "  Max sustainable throughput = highest RPS where P99 < SLO (60ms)"
    log "  Warmup RPS = 80% of max sustainable throughput"
    log "  Paper reports: max ~5k RPS → warmup = 4k RPS for Search Hotel"
}

# ======================== Main ========================

main() {
    log "========================================="
    log "CloudLab Setup for NSDI'25 Experiments"
    log "========================================="
    
    install_deps
    clone_repos
    build_binaries
    setup_k8s
    initial_deploy
    verify_deployment
    find_max_throughput
    
    log ""
    log "========================================="
    log "Setup Complete!"
    log "========================================="
    log ""
    log "Next steps:"
    log "  1. (Optional) Run Bayesian optimization:"
    log "     cd /users/$USERNAME/hotelApp/experiments/hotel"
    log "     python3 run_bayesian_opt.py --method rajomon --n-calls 50"
    log ""
    log "  2. Run Figure 4 experiments:"
    log "     bash run_figure4.sh"
    log ""
    log "  3. Run Figure 5 experiments:"
    log "     bash run_figure5.sh"  
    log ""
    log "  4. Or run everything:"
    log "     bash run_all_experiments.sh"
    log ""
    log "  5. Plots will be in experiments/hotel/results/"
}

main "$@"
