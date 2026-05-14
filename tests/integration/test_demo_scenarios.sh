#!/usr/bin/env bash
# tests/integration/test_demo_scenarios.sh
#
# Smoke test for the podinfo demo setup.
# Verifies: deploy succeeds, webhook payload is valid JSON, teardown cleans up.
#
# Usage: bash tests/integration/test_demo_scenarios.sh
# Exit 0 = pass, non-zero = fail.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PASS=0
FAIL=0

pass() { echo "  ✓ $1"; PASS=$((PASS + 1)); }
fail() { echo "  ✗ $1"; FAIL=$((FAIL + 1)); }

echo "=== Demo smoke tests ==="

# ---- T1: deploy -----------------------------------------------------------
echo ""
echo "-- T1: make demo-deploy"
if make -C "${REPO_ROOT}" demo-deploy &>/dev/null; then
  pass "demo-deploy succeeded"
else
  fail "demo-deploy failed"
fi

# ---- T2: pod is Ready -----------------------------------------------------
echo ""
echo "-- T2: podinfo pod is Ready"
READY=$(kubectl get pod -n demo -l app=podinfo \
  -o jsonpath='{.items[0].status.containerStatuses[0].ready}' 2>/dev/null || echo "false")
if [[ "${READY}" == "true" ]]; then
  pass "pod is Ready"
else
  fail "pod is not Ready (ready=${READY})"
fi

# ---- T3: fire-webhook dry-run produces valid JSON -------------------------
echo ""
echo "-- T3: fire-webhook --dry-run produces valid JSON"
POD=$(kubectl get pod -n demo -l app=podinfo \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "podinfo-test")

DRY_OUTPUT=$(bash "${REPO_ROOT}/scripts/demo/fire-webhook.sh" \
  --scenario KubePodCrashLooping \
  --namespace demo \
  --pod "${POD}" \
  --hmac-secret test-secret \
  --dry-run 2>&1 || true)

if echo "${DRY_OUTPUT}" | python3 -c "import sys,json; json.loads(sys.stdin.read())" &>/dev/null 2>&1; then
  pass "dry-run payload is valid JSON"
else
  # Extract just the JSON part (script prints a header line before the JSON)
  JSON_PART=$(echo "${DRY_OUTPUT}" | grep -A999 '^{' | head -n -3 || true)
  if echo "${JSON_PART}" | python3 -c "import sys,json; json.loads(sys.stdin.read())" &>/dev/null 2>&1; then
    pass "dry-run payload is valid JSON"
  else
    fail "dry-run payload is not valid JSON"
    echo "    Output: ${DRY_OUTPUT}" | head -5
  fi
fi

# ---- T4: teardown cleans up -----------------------------------------------
echo ""
echo "-- T4: make demo-teardown"
if make -C "${REPO_ROOT}" demo-teardown &>/dev/null; then
  pass "demo-teardown succeeded"
else
  fail "demo-teardown failed"
fi

NS_EXISTS=$(kubectl get namespace demo --ignore-not-found -o name 2>/dev/null || echo "")
if [[ -z "${NS_EXISTS}" ]]; then
  pass "demo namespace is gone"
else
  fail "demo namespace still exists after teardown"
fi

# ---- Summary ---------------------------------------------------------------
echo ""
echo "=== Results: ${PASS} passed, ${FAIL} failed ==="
[[ $FAIL -eq 0 ]]
