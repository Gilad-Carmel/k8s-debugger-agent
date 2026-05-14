# Contract: Slack-Mock Surface (Outbound Report + Inbound Approve/Reject)

**Feature**: 002-routed-triage-workflow
**Owner**: agent → `src/agent/graph/nodes/reporter.py` (outbound) and `src/agent/api/callbacks.py` (inbound); mock → `deploy/slack-mock/`
**Spec refs**: FR-013 – FR-019

The mock-Slack receiver is a thin FastAPI service that mimics the shape of Slack Block Kit + interactive callbacks. Replacing it with real Slack later is a contract-conforming substitution, not a rewrite.

---

## Outbound: agent → Slack-mock

### Endpoint (on the mock)

```text
POST /messages
Content-Type: application/json
X-Tenant-Id: <tenant>
```

### Body

Block Kit-compatible JSON, with the structured `Report` fields preserved in a `report` sidecar so the mock (and a future real Slack adapter) can pick what to render:

```json
{
  "correlation_id": "01J...",
  "channel": "#k8s-incidents",
  "report": {
    "status": "pending",
    "delivered_at": "2026-05-14T10:01:12Z",
    "approval_deadline": "2026-05-14T10:31:12Z",
    "routing": { "...": "see data-model.md" },
    "diagnosis": { "...": "see data-model.md" },
    "proposed_fix": { "...": "see data-model.md or null" }
  },
  "blocks": [
    { "type": "header", "text": { "type": "plain_text", "text": "🚨 Incident — Application" } },
    { "type": "section", "text": { "type": "mrkdwn", "text": "*Root cause:* …" } },
    { "type": "section", "text": { "type": "mrkdwn", "text": "*Evidence:*\n```…```" } },
    { "type": "section", "text": { "type": "mrkdwn", "text": "*Proposed fix:* `restart-pod` on `checkout/checkout-7b5d-x29`" } },
    {
      "type": "actions",
      "elements": [
        {
          "type": "button",
          "style": "primary",
          "text": { "type": "plain_text", "text": "Approve Remediation" },
          "value": "approve",
          "action_id": "approve_<correlation_id>"
        },
        {
          "type": "button",
          "style": "danger",
          "text": { "type": "plain_text", "text": "Reject" },
          "value": "reject",
          "action_id": "reject_<correlation_id>"
        }
      ]
    },
    { "type": "context", "elements": [{ "type": "mrkdwn", "text": "Confidence: *high*  •  Runner-ups: Network (low)  •  ID: `01J…`" }] }
  ]
}
```

Rules:

- The `actions` block MUST be omitted if `report.proposed_fix` is `null` (FR-014 — Approve only renders when a fix exists).
- The `actions` block MUST also be omitted (or both buttons disabled) when `status != "pending"`.
- The `blocks` array MUST always include `header`, `section` (root cause), `section` (evidence), and `context`. Other sections are surface-specific.

### Response (from the mock)

```text
200 OK
{
  "delivered_at": "2026-05-14T10:01:12.345Z",
  "message_id": "<mock-side id>"
}
```

The agent treats successful delivery as the wall-clock for the `delivered_at` and `approval_deadline` computations.

### Failure handling

- 5xx, timeout, or non-2xx: the agent's Reporter node MUST persist the Report to audit with status `pending` and surface a system alert ("chat delivery failed; triage still proceeded"). It MUST NOT auto-approve.

---

## Inbound: Slack-mock → agent

### Endpoints (on the agent)

```text
POST /callbacks/slack/approve
POST /callbacks/slack/reject
Content-Type: application/json
X-Slack-Mock-Signature: <hex-encoded HMAC-SHA256 of body, using shared mock secret>
```

### Body

```json
{
  "correlation_id": "01J...",
  "actor": { "user_id": "U123", "name": "alice", "roles": ["triage-approver", "sre"] },
  "action_id": "approve_01J...",
  "reason": "looks right, go",
  "clicked_at": "2026-05-14T10:04:55Z"
}
```

### Validation rules (Principle I)

The agent MUST, in this order:

1. Verify `X-Slack-Mock-Signature` (HMAC of body, constant-time compare). Fail ⇒ **401**.
2. Resolve `correlation_id` to a Report. If unknown ⇒ **404**.
3. Reject if `Report.status != "pending"` ⇒ **409** with `{"error": "report_<status>"}`.
4. Reject if `clicked_at >= approval_deadline` ⇒ **409** `{"error": "approval_expired"}`. Persist an `ApprovalEvent` with `action: "reject", reason: "expired"` for audit.
5. Role-check: the `actor.roles` MUST contain the role mapped to the proposed fix's `action_type` (default `triage-approver` for MVP per R11). Fail ⇒ **403**, persist an `ApprovalEvent` with `role_check_passed: false`.
6. On success: persist `ApprovalEvent`, transition Report to `approved` (or `rejected`), and `Command(resume=...)` the LangGraph run.

### Response

```text
200 OK
{
  "correlation_id": "01J...",
  "status": "approved" | "rejected"
}
```

Error responses follow the consistent error template:

```text
{ "error": "<machine_token>", "message": "<human readable>", "correlation_id": "01J..." }
```

`<machine_token>` is one of:
`signature_invalid`, `report_not_found`, `approval_expired`, `report_already_approved`,
`report_already_rejected`, `report_executed`, `report_failed`, `role_check_failed`,
`fingerprint_mismatch`, `internal_error`.

---

## Notes for a future real-Slack adapter

- Block Kit JSON above is already compatible; the difference is:
  - The signature header changes to Slack's (`X-Slack-Signature` + `X-Slack-Request-Timestamp`).
  - Inbound payloads come URL-encoded with a `payload` form field per Slack's interactive-action contract; an adapter unwraps that into the JSON shape this contract specifies.
- `actor.roles` for real Slack would come from a tenant-side mapping of Slack user IDs to platform roles, not from the click payload.
- The `report` sidecar fields stay identical; only the rendering/transport changes (Principle VIII — consistency across surfaces).
