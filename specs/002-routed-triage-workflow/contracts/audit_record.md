# Contract: Audit Record (append-only)

**Feature**: 002-routed-triage-workflow
**Owner**: `src/agent/audit.py` + the MCP server's audit writer
**Spec refs**: FR-008, FR-009, FR-022, FR-028, SC-005, SC-006, SC-009

The audit log is the platform's primary "what happened, why, how to undo it" record. It is **append-only** at the application layer; `audit.py` is the sole writer and no UPDATE/DELETE SQL targeting `audit_record` exists anywhere in the codebase (enforced by a unit test in `tests/unit/test_audit_record.py`). Joined by `correlation_id` to reconstruct an Incident end-to-end.

---

## Table: `audit_record`

| Column | Type | Notes |
|---|---|---|
| `id` | `INTEGER PRIMARY KEY AUTOINCREMENT` | DB-assigned. |
| `correlation_id` | `TEXT NOT NULL` | Indexed. Joins all rows for one Incident. |
| `sequence_no` | `INTEGER NOT NULL` | Monotonic within `correlation_id`. `(correlation_id, sequence_no)` is UNIQUE. |
| `stage` | `TEXT NOT NULL` | One of the `Stage` enum values below. |
| `outcome` | `TEXT NOT NULL` | One of `ok` / `refused` / `error` / `partial`. |
| `actor` | `TEXT NOT NULL` | JSON string: `{type: "system"|"user"|"mcp_tool", id: "...", roles?: [...]}`. |
| `prompt` | `TEXT` | LLM stages only. Already-redacted. NULL otherwise. |
| `response` | `TEXT` | LLM stages only. NULL otherwise. |
| `model` | `TEXT` | LLM stages only. |
| `tokens_in` | `INTEGER` | LLM stages only. |
| `tokens_out` | `INTEGER` | LLM stages only. |
| `cost_usd_micros` | `INTEGER` | LLM stages only. Cost in micro-USD (10⁻⁶) to avoid float drift. |
| `payload` | `TEXT NOT NULL` | JSON string; stage-specific structured detail (see below). |
| `redactions_applied` | `TEXT NOT NULL` | JSON array string: `[{pattern_id: "...", count: N}, ...]`. Empty array if none. |
| `at` | `TEXT NOT NULL` | ISO-8601 UTC timestamp (`DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))`). |

Indexes:

- `(correlation_id, sequence_no)` UNIQUE
- `(at)` for time-range scans
- `(stage)` for stage-specific queries

Note: SQLite does not support `jsonb` or GIN indexes. `actor`, `payload`, and `redactions_applied` are stored as JSON strings; callers parse via `json.loads()`. Ad-hoc payload queries use SQLite's `json_extract()` function.

---

## Stage enum and payload shapes

```text
Stage = Literal[
  "webhook_received",      # FR-001
  "webhook_rejected",      # FR-002, FR-011 (auth/role failures recorded too)
  "incident_deduped",      # FR-003
  "mcp_read",              # search_pod_logs / get_pod
  "router_decision",       # FR-005..FR-008
  "expert_diagnosis",      # FR-009..FR-012
  "report_delivered",      # FR-013, FR-014
  "report_delivery_failed",
  "approval_event",        # FR-018, FR-019 (approve/reject/expire/role-fail)
  "solver_preflight",      # FR-020 (incl. fingerprint check)
  "mcp_write",             # restart_pod / rollback_deployment / scale_deployment / delete_pod
  "solver_postcheck",      # FR-022..FR-024
  "budget_exceeded",       # FR-029
  "kill_switch_engaged"    # FR-030
]
```

### Payload by stage

`webhook_received` / `webhook_rejected`:

```text
{
  "source_alert_id": "...",
  "namespace": "...",
  "pod": "...",
  "headers_signed": true,
  "reason": "...?"
}
```

`incident_deduped`:

```text
{
  "dedup_fingerprint": "...",
  "first_seen_correlation_id": "...",
  "last_seen_at": "..."
}
```

`mcp_read`:

```text
{
  "tool": "search_pod_logs" | "get_pod",
  "request": { "...": "...redaction applied to free-text fields..." },
  "result_summary": {
    "total_bytes": 1234,
    "total_lines": 567,
    "hit_count": 42,
    "truncated": false,
    "containers_sampled": ["app", "sidecar"]
  }
}
```

`router_decision`:

```text
{
  "domain": "Application",
  "confidence": "high",
  "runners_up": [["Network", "low"]],
  "cited_evidence_ids": ["...", "..."]
}
```

`expert_diagnosis`:

```text
{
  "expert": "Application",
  "root_cause_hypothesis": "...",
  "cited_evidence_ids": [...],
  "confidence": "high",
  "proposed_fix": { "action_type": "...", "fingerprint": "...", "...": "..." } | null,
  "runner_up_causes": [...]
}
```

`report_delivered` / `report_delivery_failed`:

```text
{
  "delivered_at": "...",
  "approval_deadline": "...",
  "channel": "#k8s-incidents",
  "error": "...?"
}
```

`approval_event`:

```text
{
  "action": "approve" | "reject" | "expired",
  "actor_id": "U123",
  "actor_roles": ["triage-approver"],
  "role_check_passed": true,
  "reason": "..."
}
```

`solver_preflight`:

```text
{
  "proposed_fix_fingerprint_match": true,
  "approval_token_valid": true,
  "tenant_kill_switch_engaged": false
}
```

`mcp_write`:

```text
{
  "tool": "restart_pod" | "rollback_deployment" | "scale_deployment" | "delete_pod_to_reschedule",
  "request": { "...": "..." },
  "pre_state": { "...": "..." },
  "action_outcome": "applied" | "refused" | "error",
  "reason": "...?"
}
```

`solver_postcheck`:

```text
{
  "post_state": { "...": "..." },
  "outcome": "success" | "partial" | "failure",
  "reversal_recipe": { "...": "..." },
  "verification_window_sec": 60,
  "error": "...?"
}
```

`budget_exceeded`:

```text
{
  "at_stage": "router" | "expert" | "reporter",
  "budget_kind": "tokens" | "usd",
  "remaining": 0,
  "would_have_been": 1234
}
```

`kill_switch_engaged`:

```text
{
  "tenant": "...",
  "engaged_by": "user_or_system_id",
  "in_flight_correlation_ids": ["...", "..."]
}
```

---

## Invariants (verified by `tests/eval/audit_completeness.py` in CI)

1. Every `correlation_id` that produced a `mcp_write` row MUST also have a `solver_preflight`, a `solver_postcheck`, and an `approval_event` with `action == "approve"` and `role_check_passed == true`, all chronologically before the `mcp_write`. (Spec FR-015, SC-006.)
2. Every `correlation_id` MUST start with `webhook_received` (or `webhook_rejected`); no orphan stage rows.
3. No row anywhere has a non-empty `prompt` or `response` containing a substring that matches the redaction pattern set. (SC-005, SC-009.)
4. `sequence_no` is dense per `correlation_id` (no gaps).
5. The first `mcp_write` for a `correlation_id` happens strictly after a successful `approval_event`; if no such approval exists, no `mcp_write` rows exist for that correlation_id.

A nightly job runs these invariants over the last 24 h and fails the release if any is violated.
