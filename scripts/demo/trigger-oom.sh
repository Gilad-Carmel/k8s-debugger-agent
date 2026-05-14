#!/usr/bin/env bash
# scripts/demo/trigger-oom.sh
# S3 — OOMKill: stress podinfo memory past the 32Mi limit.
# The agent detects the OOMKill from logs/events automatically.
set -euo pipefail

NAMESPACE="${DEMO_NAMESPACE:-demo}"

POD=$(kubectl get pod -n "${NAMESPACE}" -l app=podinfo \
  -o jsonpath='{.items[0].metadata.name}')
echo "==> [S3] Stressing memory on pod ${POD} (limit: 32Mi, requesting 50Mi)..."

kubectl port-forward "pod/${POD}" 9898:9898 -n "${NAMESPACE}" &>/dev/null &
PF_PID=$!
trap 'kill ${PF_PID} 2>/dev/null || true' EXIT
sleep 2

curl -s "http://localhost:9898/stress?mem=50&duration=30" || true
echo "✓ Memory stress triggered — pod will OOMKill shortly"
