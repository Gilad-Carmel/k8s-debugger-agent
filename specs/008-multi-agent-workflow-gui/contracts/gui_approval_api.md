# Contract: GUI Approval API

**Endpoints**:
- `POST /api/approval/{correlation_id}/approve`
- `POST /api/approval/{correlation_id}/reject`

**Auth**: Loopback-only (FastAPI middleware rejects requests from non-127.0.0.1 sources).

**Note**: These endpoints are GUI-internal alternatives to the Slack-mock callback
endpoints. They go through the same `_resume_graph` path and emit the same audit
events, but skip the Slack HMAC check (since the secret would be exposed in
browser JS if we included it client-side).

---

## Path Parameters

| Param | Description |
|-------|-------------|
| `correlation_id` | The incident to approve or reject |

---

## Request Body

```json
{
  "actor_name": "alice"
}
```

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `actor_name` | no | `"gui-user"` | Display name recorded in audit log |

The server synthesises `roles=["approver"]` for approve calls (demo mode).

---

## Response 200

```json
{
  "correlation_id": "01JVXYZ...",
  "status": "approved"
}
```

or

```json
{
  "correlation_id": "01JVXYZ...",
  "status": "rejected"
}
```

---

## Response 404

```json
{
  "error": "report_not_found",
  "message": "Unknown correlation_id."
}
```

## Response 409

```json
{
  "error": "report_approved",
  "message": "Report is already approved."
}
```

(Also `report_rejected`, `approval_expired`, etc. — same status codes as `callbacks.py`.)

## Response 403

Returned only if the request comes from a non-loopback address.

```json
{
  "error": "forbidden",
  "message": "GUI approval endpoint is only accessible from localhost."
}
```

---

## Audit Trail

The endpoint calls `log_audit_event` with the same `stage="approval_event"`
fields as `callbacks.py`, so the audit chain is identical regardless of
whether approval came from Slack mock or the GUI.
