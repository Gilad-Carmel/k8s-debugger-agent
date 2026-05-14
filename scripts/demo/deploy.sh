#!/usr/bin/env bash
# scripts/demo/deploy.sh
# Idempotently deploy podinfo v1 into the demo namespace.
# Usage: bash scripts/demo/deploy.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
MANIFEST_DIR="${REPO_ROOT}/deploy/demo"

# Guard: require kubectl
if ! command -v kubectl &>/dev/null; then
  echo "ERROR: kubectl not found on PATH. Install kubectl and ensure your kubeconfig is set." >&2
  exit 1
fi

# Guard: require cluster connectivity
if ! kubectl cluster-info &>/dev/null; then
  echo "ERROR: Cannot reach the Kubernetes cluster. Check your kubeconfig / cluster is running." >&2
  exit 1
fi

echo "==> Applying demo manifests..."
kubectl apply -f "${MANIFEST_DIR}/00-namespace.yaml"
kubectl apply -f "${MANIFEST_DIR}/01-podinfo-v1.yaml"
kubectl apply -f "${MANIFEST_DIR}/03-service.yaml"
kubectl apply -f "${MANIFEST_DIR}/04-pdb.yaml"

echo "==> Waiting for podinfo to become Ready (timeout 60s)..."
kubectl rollout status deployment/podinfo -n demo --timeout=60s

POD=$(kubectl get pod -n demo -l app=podinfo -o jsonpath='{.items[0].metadata.name}')
echo ""
echo "✓ podinfo is Ready (pod: ${POD})"
echo "  To access: kubectl port-forward svc/podinfo 9898:9898 -n demo"
echo "  Then open: http://localhost:9898"
