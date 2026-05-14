#!/usr/bin/env bash
# scripts/demo/fire-webhook.sh
#
# Construct a valid Alertmanager v4 webhook payload, HMAC-sign it, and POST
# it to the agent's webhook endpoint.
#
# Usage:
#   bash scripts/demo/fire-webhook.sh \
#     --scenario KubePodCrashLooping \
#     --namespace demo \
#     --pod podinfo-abc123 \
#     [--agent-url http://localhost:8000/webhook/alertmanager] \
#     [--hmac-secret <secret>] \
#     [--dry-run]
#
# Environment variable fallbacks:
#   AGENT_WEBHOOK_URL         (default: http://localhost:8000/webhook/alertmanager)
#   ALERTMANAGER_HMAC_SECRET  (required unless --hmac-secret passed)
set -euo pipefail

# ---------- defaults -------------------------------------------------------
SCENARIO=""
NAMESPACE="demo"
POD=""
AGENT_URL="${AGENT_WEBHOOK_URL:-http://localhost:8000/webhook/alertmanager}"
HMAC_SECRET="${ALERTMANAGER_HMAC_SECRET:-}"
DRY_RUN=false

# ---------- arg parse -------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --scenario)    SCENARIO="$2";    shift 2 ;;
    --namespace)   NAMESPACE="$2";   shift 2 ;;
    --pod)         POD="$2";         shift 2 ;;
    --agent-url)   AGENT_URL="$2";   shift 2 ;;
    --hmac-secret) HMAC_SECRET="$2"; shift 2 ;;
    --dry-run)     DRY_RUN=true;     shift   ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

# ---------- validation -------------------------------------------------------
if [[ -z "$SCENARIO" ]]; then
  echo "ERROR: --scenario is required (e.g. KubePodCrashLooping)" >&2
  exit 1
fi
if [[ -z "$POD" ]]; then
  # Auto-detect pod name from cluster
  POD=$(kubectl get pod -n "${NAMESPACE}" -l app=podinfo \
    -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
fi
if [[ -z "$POD" ]]; then
  echo "ERROR: --pod is required (or podinfo pod must be running in --namespace)" >&2
  exit 1
fi
if [[ -z "$HMAC_SECRET" ]]; then
  echo "ERROR: --hmac-secret or ALERTMANAGER_HMAC_SECRET env var is required" >&2
  exit 1
fi

STARTS_AT=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
GROUP_KEY="{}/{alertname=\"${SCENARIO}\"}:{namespace=\"${NAMESPACE}\",pod=\"${POD}\"}"

# ---------- build payload ---------------------------------------------------
BODY=$(cat <<EOF
{
  "version": "4",
  "groupKey": "${GROUP_KEY}",
  "status": "firing",
  "receiver": "k8s-debugger",
  "groupLabels": {
    "alertname": "${SCENARIO}",
    "namespace": "${NAMESPACE}",
    "pod": "${POD}"
  },
  "commonLabels": {
    "alertname": "${SCENARIO}",
    "namespace": "${NAMESPACE}",
    "pod": "${POD}",
    "severity": "warning"
  },
  "commonAnnotations": {
    "summary": "Demo scenario: ${SCENARIO} on pod ${POD}"
  },
  "externalURL": "http://localhost:9093",
  "alerts": [
    {
      "status": "firing",
      "labels": {
        "alertname": "${SCENARIO}",
        "namespace": "${NAMESPACE}",
        "pod": "${POD}",
        "severity": "warning"
      },
      "annotations": {
        "description": "Demo-triggered alert: ${SCENARIO}"
      },
      "startsAt": "${STARTS_AT}",
      "endsAt": "0001-01-01T00:00:00Z",
      "generatorURL": "http://localhost:9090/graph"
    }
  ]
}
EOF
)

# ---------- compute HMAC ----------------------------------------------------
SIGNATURE=$(printf '%s' "${BODY}" | openssl dgst -sha256 -hmac "${HMAC_SECRET}" | awk '{print $2}')

if [[ "$DRY_RUN" == "true" ]]; then
  echo "==> DRY RUN — payload (not sent):"
  echo "${BODY}"
  echo ""
  echo "X-Alertmanager-Signature: ${SIGNATURE}"
  exit 0
fi

# ---------- POST --------------------------------------------------------------
echo "==> Firing webhook: scenario=${SCENARIO} pod=${POD} namespace=${NAMESPACE}"
RESPONSE=$(curl -s -w "\n%{http_code}" \
  -X POST "${AGENT_URL}" \
  -H "Content-Type: application/json" \
  -H "X-Alertmanager-Signature: ${SIGNATURE}" \
  -d "${BODY}")

HTTP_CODE=$(echo "${RESPONSE}" | tail -1)
BODY_RESP=$(echo "${RESPONSE}" | head -n -1)

echo "   HTTP ${HTTP_CODE}"
echo "   ${BODY_RESP}"

if [[ "$HTTP_CODE" != "202" ]]; then
  echo "ERROR: webhook returned HTTP ${HTTP_CODE}" >&2
  exit 1
fi

CORRELATION_ID=$(echo "${BODY_RESP}" | grep -o '"correlation_id":"[^"]*"' | cut -d'"' -f4 || true)
if [[ -n "$CORRELATION_ID" ]]; then
  echo ""
  echo "✓ correlation_id: ${CORRELATION_ID}"
fi
