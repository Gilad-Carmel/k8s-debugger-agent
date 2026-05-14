#!/usr/bin/env bash
# scripts/demo/trigger-oom.sh
# S3 — OOMKill: stress podinfo memory past the 32Mi limit, wait for OOMKilled, fire webhook.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAMESPACE="${DEMO_NAMESPACE:-demo}"
MAX_WAIT=60
POLL=3

echo "==> [S3] OOMKill demo"

POD=$(kubectl get pod -n "${NAMESPACE}" -l app=podinfo \
  -o jsonpath='{.items[0].metadata.name}')
echo "    Pod: ${POD}"

# Start port-forward
kubectl port-forward "pod/${POD}" 9898:9898 -n "${NAMESPACE}" &>/dev/null &
PF_PID=$!
trap 'kill ${PF_PID} 2>/dev/null || true' EXIT
sleep 2

# Request 50MB allocation — limit is 32Mi, so this will OOMKill the container
echo "==> Sending GET /stress?mem=50&duration=30 (limit is 32Mi)..."
curl -s "http://localhost:9898/stress?mem=50&duration=30" &
STRESS_PID=$!

# Wait for OOMKilled
echo "==> Waiting for OOMKilled (max ${MAX_WAIT}s)..."
ELAPSED=0
while [[ $ELAPSED -lt $MAX_WAIT ]]; do
  STATE=$(kubectl get pod -n "${NAMESPACE}" -l app=podinfo \
    -o jsonpath='{.items[0].status.containerStatuses[0]}' 2>/dev/null || echo "{}")
  REASON=$(echo "${STATE}" | grep -o '"reason":"[^"]*"' | cut -d'"' -f4 || echo "")
  EXIT_CODE=$(echo "${STATE}" | grep -o '"exitCode":[0-9]*' | grep -o '[0-9]*' || echo "")

  if [[ "${REASON}" == "OOMKilled" ]] || [[ "${EXIT_CODE}" == "137" ]]; then
    echo "    ✓ OOMKilled (reason=${REASON}, exitCode=${EXIT_CODE})"
    break
  fi
  echo "    ... reason=${REASON:-running} (${ELAPSED}s elapsed)"
  sleep $POLL
  ELAPSED=$((ELAPSED + POLL))
done

kill "${STRESS_PID}" 2>/dev/null || true
kill "${PF_PID}" 2>/dev/null || true
trap - EXIT

bash "${SCRIPT_DIR}/fire-webhook.sh" \
  --scenario KubePodOOMKilled \
  --namespace "${NAMESPACE}" \
  --pod "${POD}"
