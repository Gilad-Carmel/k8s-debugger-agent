#!/usr/bin/env bash
# scripts/demo/trigger-scale.sh
# S4 — Scale Pressure: run bombardier in-cluster, wait for errors, fire webhook.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAMESPACE="${DEMO_NAMESPACE:-demo}"
LOAD_DURATION=30
CONCURRENCY=50

echo "==> [S4] Scale-pressure demo"

# Clean up any previous bombardier job
kubectl delete pod bombardier -n "${NAMESPACE}" --ignore-not-found=true &>/dev/null

POD=$(kubectl get pod -n "${NAMESPACE}" -l app=podinfo \
  -o jsonpath='{.items[0].metadata.name}')
echo "    Target pod: ${POD}"

# Launch bombardier inside the cluster (avoids kind networking complexity)
echo "==> Launching bombardier (${CONCURRENCY} concurrent, ${LOAD_DURATION}s)..."
kubectl run bombardier \
  --image=alpine/bombardier \
  --restart=Never \
  -n "${NAMESPACE}" \
  -- -c "${CONCURRENCY}" -d "${LOAD_DURATION}s" \
     "http://podinfo.${NAMESPACE}.svc.cluster.local:9898"

# Give it time to accumulate errors in podinfo logs
echo "==> Waiting 10s for errors to accumulate..."
sleep 10

# Fire the webhook while the load is still running
bash "${SCRIPT_DIR}/fire-webhook.sh" \
  --scenario KubeContainerWaiting \
  --namespace "${NAMESPACE}" \
  --pod "${POD}"

# Wait for bombardier to finish
echo "==> Waiting for bombardier job to finish..."
kubectl wait pod/bombardier -n "${NAMESPACE}" \
  --for=condition=Succeeded --timeout=60s 2>/dev/null || true

echo "==> Bombardier results:"
kubectl logs bombardier -n "${NAMESPACE}" 2>/dev/null || true

# Clean up
kubectl delete pod bombardier -n "${NAMESPACE}" --ignore-not-found=true
