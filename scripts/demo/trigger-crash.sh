#!/usr/bin/env bash
# scripts/demo/trigger-crash.sh
# S1 — CrashLoop: call /panic on podinfo to crash the pod.
# The agent detects the crash from logs/events automatically.
set -euo pipefail

NAMESPACE="${DEMO_NAMESPACE:-demo}"

POD=$(kubectl get pod -n "${NAMESPACE}" -l app=podinfo \
  -o jsonpath='{.items[0].metadata.name}')
echo "==> [S1] Crashing pod ${POD}..."

kubectl port-forward "pod/${POD}" 9898:9898 -n "${NAMESPACE}" &>/dev/null &
PF_PID=$!
trap 'kill ${PF_PID} 2>/dev/null || true' EXIT
sleep 2

curl -s http://localhost:9898/panic || true
echo "✓ Panic sent — pod is crashing"
