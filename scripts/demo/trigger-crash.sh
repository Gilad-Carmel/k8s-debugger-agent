#!/usr/bin/env bash
# scripts/demo/trigger-crash.sh
# S1 — CrashLoop: send /panic to podinfo, wait for CrashLoopBackOff, fire webhook.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAMESPACE="${DEMO_NAMESPACE:-demo}"
MAX_WAIT=60
POLL=3

echo "==> [S1] CrashLoop demo"

# Resolve pod name
POD=$(kubectl get pod -n "${NAMESPACE}" -l app=podinfo \
  -o jsonpath='{.items[0].metadata.name}')
echo "    Pod: ${POD}"

# Start port-forward in background
kubectl port-forward "pod/${POD}" 9898:9898 -n "${NAMESPACE}" &>/dev/null &
PF_PID=$!
trap 'kill ${PF_PID} 2>/dev/null || true' EXIT
sleep 2

# Trigger the crash
echo "==> Sending POST /panic..."
curl -s -X POST http://localhost:9898/panic || true

# Wait for restart count >= 2 or CrashLoopBackOff
echo "==> Waiting for crash loop (max ${MAX_WAIT}s)..."
ELAPSED=0
while [[ $ELAPSED -lt $MAX_WAIT ]]; do
  STATE=$(kubectl get pod -n "${NAMESPACE}" -l app=podinfo \
    -o jsonpath='{.items[0].status.containerStatuses[0]}' 2>/dev/null || echo "{}")
  RESTARTS=$(echo "${STATE}" | grep -o '"restartCount":[0-9]*' | grep -o '[0-9]*' || echo "0")
  REASON=$(echo "${STATE}" | grep -o '"reason":"[^"]*"' | cut -d'"' -f4 || echo "")

  if [[ "${RESTARTS}" -ge 2 ]] || [[ "${REASON}" == "CrashLoopBackOff" ]]; then
    echo "    ✓ Crash observed (restarts=${RESTARTS}, reason=${REASON})"
    break
  fi
  echo "    ... restarts=${RESTARTS} (${ELAPSED}s elapsed)"
  sleep $POLL
  ELAPSED=$((ELAPSED + POLL))
done

# Kill port-forward before firing webhook
kill "${PF_PID}" 2>/dev/null || true
trap - EXIT

# Fire the webhook
bash "${SCRIPT_DIR}/fire-webhook.sh" \
  --scenario KubePodCrashLooping \
  --namespace "${NAMESPACE}" \
  --pod "${POD}"
