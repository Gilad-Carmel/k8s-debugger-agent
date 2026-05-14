#!/usr/bin/env bash
# scripts/demo/teardown.sh
# Remove all demo resources by deleting the demo namespace.
set -euo pipefail

echo "==> Deleting demo namespace..."
kubectl delete namespace demo --ignore-not-found=true
echo "✓ demo namespace removed"
