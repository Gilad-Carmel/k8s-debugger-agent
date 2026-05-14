#!/usr/bin/env bash
# scripts/demo/trigger-scale.sh
# S4 — Scale Pressure: flood podinfo with requests from inside the cluster.
# The agent detects the errors from logs/events automatically.
set -euo pipefail

NAMESPACE="${DEMO_NAMESPACE:-demo}"
CONCURRENCY=50
DURATION=60s

echo "==> [S4] Starting load test (${CONCURRENCY} concurrent, ${DURATION})..."

kubectl delete pod bombardier -n "${NAMESPACE}" --ignore-not-found=true &>/dev/null

kubectl run bombardier \
  --image=alpine/bombardier \
  --restart=Never \
  -n "${NAMESPACE}" \
  -- -c "${CONCURRENCY}" -d "${DURATION}" \
     "http://podinfo.${NAMESPACE}.svc.cluster.local:9898"

echo "✓ Load test running — watch for errors in pod logs"
