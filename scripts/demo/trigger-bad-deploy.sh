#!/usr/bin/env bash
# scripts/demo/trigger-bad-deploy.sh
# S2 — Bad Deploy: apply the v2 deployment with RUNTIME_ERROR=true.
# The agent detects the errors from logs/events automatically.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
NAMESPACE="${DEMO_NAMESPACE:-demo}"

echo "==> [S2] Applying bad deployment (RUNTIME_ERROR=true)..."
kubectl apply -f "${REPO_ROOT}/deploy/demo/02-podinfo-v2-bad.yaml"
echo "✓ Bad deployment applied — pod will start returning HTTP 500"
