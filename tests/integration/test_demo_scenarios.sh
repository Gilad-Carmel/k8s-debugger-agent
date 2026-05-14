#!/usr/bin/env bash
# tests/integration/test_demo_scenarios.sh
#
# Smoke test for the podinfo demo setup.
# Verifies: deploy succeeds, pod is Ready, teardown cleans up.
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
echo "-- T1: bash scripts/demo/deploy.sh"
if bash "${REPO_ROOT}/scripts/demo/deploy.sh" &>/dev/null; then
  pass "deploy succeeded"
else
  fail "deploy failed"
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

# ---- T3: trigger scripts exist and are executable -------------------------
echo ""
echo "-- T3: trigger scripts are present and executable"
ALL_OK=true
for script in trigger-crash trigger-bad-deploy trigger-oom trigger-scale; do
  if [[ -x "${REPO_ROOT}/scripts/demo/${script}.sh" ]]; then
    pass "${script}.sh is executable"
  else
    fail "${script}.sh missing or not executable"
    ALL_OK=false
  fi
done

# ---- T4: teardown cleans up -----------------------------------------------
echo ""
echo "-- T4: bash scripts/demo/teardown.sh"
if bash "${REPO_ROOT}/scripts/demo/teardown.sh" &>/dev/null; then
  pass "teardown succeeded"
else
  fail "teardown failed"
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
