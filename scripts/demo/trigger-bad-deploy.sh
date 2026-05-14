#!/usr/bin/env bash
# scripts/demo/trigger-bad-deploy.sh
# S2 — Bad Deploy: apply v2 (RUNTIME_ERROR=true), wait for NotReady, fire webhook.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
NAMESPACE="${DEMO_NAMESPACE:-demo}"
MAX_WAIT=60
POLL=3

echo "==> [S2] Bad-deploy demo"

# Apply the bad deployment
echo "==> Applying v2-bad deployment..."
kubectl apply -f "${REPO_ROOT}/deploy/demo/02-podinfo-v2-bad.yaml"

# Wait for the deployment to roll out the bad version
sleep 3
kubectl rollout status deployment/podinfo -n "${NAMESPACE}" --timeout=30s || true

# Wait until readiness probe fails (pod NotReady or 500 responses)
echo "==> Waiting for NotReady or 500 errors (max ${MAX_WAIT}s)..."
ELAPSED=0
NOT_READY=false

while [[ $ELAPSED -lt $MAX_WAIT ]]; do
  READY=$(kubectl get pod -n "${NAMESPACE}" -l app=podinfo \
    -o jsonpath='{.items[0].status.containerStatuses[0].ready}' 2>/dev/null || echo "true")

  if [[ "${READY}" == "false" ]]; then
    echo "    ✓ Pod is NotReady"
    NOT_READY=true
    break
  fi

  # Also try a quick HTTP check via port-forward
  POD=$(kubectl get pod -n "${NAMESPACE}" -l app=podinfo \
    -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")
  if [[ -n "$POD" ]]; then
    kubectl port-forward "pod/${POD}" 9899:9898 -n "${NAMESPACE}" &>/dev/null &
    PF_PID=$!
    sleep 2
    STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:9899/ || echo "000")
    kill "${PF_PID}" 2>/dev/null || true
    if [[ "${STATUS}" == "500" ]]; then
      echo "    ✓ Pod returning HTTP 500"
      NOT_READY=true
      break
    fi
  fi

  echo "    ... ready=${READY} (${ELAPSED}s elapsed)"
  sleep $POLL
  ELAPSED=$((ELAPSED + POLL))
done

POD=$(kubectl get pod -n "${NAMESPACE}" -l app=podinfo \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "podinfo-unknown")

# Fire the webhook
bash "${SCRIPT_DIR}/fire-webhook.sh" \
  --scenario KubePodNotReady \
  --namespace "${NAMESPACE}" \
  --pod "${POD}"

echo ""
echo "NOTE: After approving the rollback in the agent, run:"
echo "  kubectl apply -f deploy/demo/01-podinfo-v1.yaml"
echo "  (or: make demo-reset)"
