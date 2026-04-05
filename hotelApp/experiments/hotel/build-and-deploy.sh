#!/bin/bash
###############################################################################
# build-and-deploy.sh - Build custom Docker image and deploy to K8s
#
# This script builds a new Docker image from your modified source code
# (including gateway modules) and deploys it to the CloudLab K8s cluster.
#
# The default xjiali/social-hotel:latest image contains the ORIGINAL author's
# code. Any changes you make to overloadcontrol.go, interceptors.go, main.go,
# or gateway repos will NOT take effect until you rebuild the image.
#
# Usage (run from hotelApp/experiments/hotel/ or hotelApp/):
#   bash experiments/hotel/build-and-deploy.sh [--push]
#
# Options:
#   --push    Push to DockerHub (requires login). Without this flag,
#             the image is loaded into local k3s/containerd only.
###############################################################################

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOTELAPP_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
WORKSPACE_ROOT="$(cd "$HOTELAPP_DIR/.." && pwd)"
CLOUDLAB_USER=$(echo "$SCRIPT_DIR" | grep -oP '(?<=/users/)[^/]+' || whoami)

# Docker image name - change this to your own DockerHub repo
IMAGE_NAME="${DOCKER_IMAGE:-gaussgeorge/social-hotel}"
IMAGE_TAG="${DOCKER_TAG:-latest}"
FULL_IMAGE="${IMAGE_NAME}:${IMAGE_TAG}"

PUSH_TO_HUB=false
if [[ "${1:-}" == "--push" ]]; then
    PUSH_TO_HUB=true
fi

log() {
    echo "[$(date '+%H:%M:%S')] $*"
}

# ======================== Step 1: Build Docker Image ========================
build_image() {
    log "Building Docker image: ${FULL_IMAGE}"
    log "  Build context: ${WORKSPACE_ROOT}"
    log "  Dockerfile: ${HOTELAPP_DIR}/Dockerfile.custom"

    cd "${WORKSPACE_ROOT}"

    # Check that all gateway repos exist
    for repo in breakwater-grpc-main dagor-grpc-main rajomon-main topdown-grpc-main; do
        if [ ! -d "$repo" ]; then
            log "ERROR: Missing ${repo}/ in workspace root"
            exit 1
        fi
    done

    # Build (context is workspace root, Dockerfile is in hotelApp/)
    docker build \
        -t "${FULL_IMAGE}" \
        -f hotelApp/Dockerfile.custom \
        . 2>&1 | tail -20

    log "Docker image built: ${FULL_IMAGE}"
}

# ======================== Step 2: Load into K8s ========================
load_into_k8s() {
    if $PUSH_TO_HUB; then
        log "Pushing to DockerHub: ${FULL_IMAGE}"
        docker push "${FULL_IMAGE}"
        log "Pushed. K8s will pull on next deploy."
    else
        log "Loading image into local container runtime..."

        # Detect container runtime
        if command -v k3s &>/dev/null; then
            log "  Detected k3s — importing via ctr"
            docker save "${FULL_IMAGE}" | sudo k3s ctr images import -
        elif command -v crictl &>/dev/null; then
            log "  Detected crictl — importing via ctr"
            docker save "${FULL_IMAGE}" -o /tmp/hotel-image.tar
            sudo ctr -n k8s.io images import /tmp/hotel-image.tar
            rm -f /tmp/hotel-image.tar
        else
            log "  No k3s/ctr detected — saving to /tmp/hotel-image.tar"
            log "  You'll need to load this on each worker node:"
            log "    docker load < /tmp/hotel-image.tar"
            docker save "${FULL_IMAGE}" -o /tmp/hotel-image.tar
        fi

        log "Image loaded into local runtime."
    fi
}

# ======================== Step 3: Update K8s Deployments ========================
update_deployments() {
    log "Updating K8s deployment image references..."

    # Update all deployment YAML files to use the new image
    cd "${HOTELAPP_DIR}"
    for yaml_file in k8s/*-deployment.yaml; do
        if [ -f "$yaml_file" ]; then
            sed -i "s|image: xjiali/social-hotel:latest|image: ${FULL_IMAGE}|g" "$yaml_file"
        fi
    done

    # Also update the template if it exists
    if [ -f "scripts/deploy_template.yaml" ]; then
        sed -i "s|image: xjiali/social-hotel:latest|image: ${FULL_IMAGE}|g" scripts/deploy_template.yaml
    fi

    # If not pushing to DockerHub, set imagePullPolicy to IfNotPresent
    if ! $PUSH_TO_HUB; then
        for yaml_file in k8s/*-deployment.yaml; do
            if [ -f "$yaml_file" ]; then
                sed -i "s|imagePullPolicy: Always|imagePullPolicy: IfNotPresent|g" "$yaml_file"
            fi
        done
        if [ -f "scripts/deploy_template.yaml" ]; then
            sed -i "s|imagePullPolicy: Always|imagePullPolicy: IfNotPresent|g" scripts/deploy_template.yaml
        fi
    fi

    log "Deployment YAMLs updated to use ${FULL_IMAGE}"
}

# ======================== Step 4: Redeploy ========================
redeploy() {
    log "Redeploying hotel services..."

    cd "${HOTELAPP_DIR}"

    # Update configmap
    kubectl delete configmap msgraph-config --ignore-not-found
    kubectl create configmap msgraph-config --from-file=msgraph.yaml

    # Redeploy
    METHOD=hotel bash setup-k8s-redeploy.sh hotel

    # Wait for pods
    log "Waiting for pods to be ready..."
    kubectl wait --for=condition=ready pod --all --timeout=120s
    sleep 10

    log "Redeployment complete."
    kubectl get pods -o wide
}

# ======================== Main ========================
main() {
    log "========================================="
    log "Build & Deploy Custom Docker Image"
    log "  Image: ${FULL_IMAGE}"
    log "  Push to Hub: ${PUSH_TO_HUB}"
    log "========================================="

    build_image
    load_into_k8s
    update_deployments
    redeploy

    log "========================================="
    log "Done! Your modified code is now deployed."
    log "  You can now run experiments:"
    log "    bash experiments/hotel/run_figure4.sh"
    log "========================================="
}

main "$@"
