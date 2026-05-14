# Phase 1 Data Model: Routed Triage and Auto-Remediation Workflow

**Feature**: 002-routed-triage-workflow
**Date**: 2026-05-14

This document defines the typed entities the workflow operates on. All entities are `pydantic v2` BaseModels (frozen where indicated) living in `src/shared/schemas.py`, except `WorkflowState` which is a `TypedDict` in `src/agent/graph/state.py` for LangGraph compatibility.

Implementation note: this is the data-model contract. Field types and validation rules are normative; field-name capitalization may shift slightly between this doc and the code (`snake_case` in Python). Don't take that as a substantive change.

---

## Common types

```text
CorrelationId    : str (UUIDv7 string)        # one per Incident; propagates everywhere
Confidence       : Literal["low", "medium", "high"]
Domain           : Literal["Application", "Network", "Database", "Unknown"]
ReportStatus     : Literal["pending", "approved", "rejected", "expired", "executed", "failed"]
SolverOutcome    : Literal["success", "partial", "failure"]
ApprovalAction   : Literal["approve", "reject"]
ActionType       : Literal[
                    "restart-pod",
                    "rollback-deployment",
                    "scale-deployment",
                    "delete-pod-to-reschedule"
                  ]
```

The `ActionType` enum is the **single source of truth** for the allowed-remediation catalog (spec FR-011). New entries require a constitution VII checklist (refusal-path + reversal-recipe tests + budget/latency declaration) and a two-reviewer PR per Principle VI.

---

## Entities

### 1. Incident

The root entity. One Incident per logical alert. Lives for the life of the triage + remediation + audit-replay surface.

| Field | Type | Notes |
|---|---|---|
| `correlation_id` | `CorrelationId` | Primary key. Stable across all stages. |
| `source_alert_id` | `str` | Upstream alerting system's ID (Alertmanager `groupKey`). |
| `dedup_fingerprint` | `str` | SHA-256 over `(alert_id, namespace, pod, 10-min bucket)` per R12. Used to short-circuit duplicates. |
| `target` | `Target` | Namespace + pod + optional container. |
| `time_window` | `TimeWindow` | `start` / `end` ISO-8601. |
| `received_at` | `datetime` | Timestamp the webhook arrived. |
| `last_seen_at` | `datetime` | Updated on duplicate webhooks within the dedup window. |
| `status` | `ReportStatus` | Mirrors the Report status (kept here for O(1) lookup without joining Report). |

Validation:

- A second webhook with the same `dedup_fingerprint` MUST update `last_seen_at` only; it MUST NOT spawn a second graph run.

### 2. Target

Frozen.

| Field | Type | Notes |
|---|---|---|
| `namespace` | `str` | Kubernetes namespace, RFC-1123 label-validated. |
| `pod` | `str` | Pod name, RFC-1123 label-validated. |
| `container` | `str \| None` | Optional; if omitted, all containers are sampled. |

### 3. TimeWindow

Frozen.

| Field | Type | Notes |
|---|---|---|
| `start` | `datetime` | Inclusive. |
| `end` | `datetime` | Exclusive. `end - start ≤ 1h` by default; configurable cap. |

### 4. FilteredEvidence

The output of the pre-filter inside `search_pod_logs`.

| Field | Type | Notes |
|---|---|---|
| `total_bytes` | `int` | Bytes returned by the K8s log API before filtering. |
| `total_lines` | `int` | Lines before filtering. |
| `hit_lines` | `list[LogExcerpt]` | Lines that matched the network/application/db pattern set. Capped at `max_hit_lines` (default 500). |
| `hit_count` | `int` | Always equals `len(hit_lines)` unless `truncated` is True; if truncated, the count reported is the pre-truncation count. |
| `truncated` | `bool` | True if hits exceeded the cap. |
| `containers_sampled` | `list[str]` | Which container instances we got logs from. |

### 5. LogExcerpt

Frozen.

| Field | Type | Notes |
|---|---|---|
| `timestamp` | `datetime` | Parsed from the log line if available; else the fetch time. |
| `container` | `str` | Container the line came from. |
| `text` | `str` | The single log line. Already redacted at the MCP boundary (R7). |
| `byte_offset` | `int` | Offset into the original log stream (for traceability in audit). |

### 6. RoutingDecision

| Field | Type | Notes |
|---|---|---|
| `domain` | `Domain` | The primary classification. |
| `confidence` | `Confidence` | `low` MUST be paired with `domain == "Unknown"` OR an Expert-skipped Report. |
| `cited_evidence` | `list[LogExcerpt]` | ≥ 1 unless `domain == "Unknown"`. |
| `runners_up` | `list[tuple[Domain, Confidence]]` | Other domains the Router considered, in descending confidence. |
| `model` | `str` | Model ID used (audit). |
| `tokens` | `int` | Total tokens consumed (audit). |

Validation:

- `domain == "Unknown"` ⇒ `cited_evidence` may be empty AND no Expert node runs.
- Otherwise `len(cited_evidence) ≥ 1`.

### 7. ExpertDiagnosis

| Field | Type | Notes |
|---|---|---|
| `domain` | `Domain` | Which Expert produced this (`Application` or `Network`; `Database` post-MVP). |
| `root_cause_hypothesis` | `str` | One sentence, user-readable. |
| `cited_evidence` | `list[LogExcerpt]` | MUST be ≥ 1. |
| `confidence` | `Confidence` | |
| `runner_up_causes` | `list[str]` | Alternative hypotheses considered. |
| `proposed_fix` | `ProposedFix \| None` | None ⇒ "no automatic fix available." |
| `model` | `str` | Model ID used (audit). |
| `tokens` | `int` | |

Validation (Principle IV, NON-NEGOTIABLE):

- `len(cited_evidence) ≥ 1` always.
- Every claim in `root_cause_hypothesis` MUST be quote-matchable against `cited_evidence` in the hallucination test suite.

### 8. ProposedFix

**Frozen** once shown in the Report (FR-016, FR-020). If anything would change post-display, the workflow MUST emit a new Report.

| Field | Type | Notes |
|---|---|---|
| `action_type` | `ActionType` | Catalog entry. |
| `target` | `Target` | Same `Target` as the Incident, by construction. |
| `parameters` | `dict[str, Any]` | Action-specific (e.g., `revision` for rollback, `replicas` for scale). Schema per `action_type` lives in `shared/catalog.py`. |
| `reversal_recipe` | `ReversalRecipe` | Pre-computed; what undoes this if the user wants to roll back. |
| `permission_scope` | `str` | Identifier of the ServiceAccount the MCP write tool will use. |
| `fingerprint` | `str` | SHA-256 over the canonical JSON of `(action_type, target, parameters)`. Used by the Solver to verify nothing changed between approval and execution (FR-020). |

### 9. ReversalRecipe

Frozen.

| Field | Type | Notes |
|---|---|---|
| `description` | `str` | Human-readable: "Re-scale to 3 replicas" / "Rollback to revision 7." |
| `inverse_action` | `ActionType \| Literal["manual"]` | The action that undoes this one, if any. `"manual"` means there is no clean automated inverse (rare; admin must intervene). |
| `inverse_parameters` | `dict[str, Any]` | Parameters for `inverse_action` if applicable. |

### 10. Report

| Field | Type | Notes |
|---|---|---|
| `correlation_id` | `CorrelationId` | |
| `routing` | `RoutingDecision` | |
| `diagnosis` | `ExpertDiagnosis \| None` | `None` iff `routing.domain == "Unknown"`. |
| `proposed_fix` | `ProposedFix \| None` | Mirrors `diagnosis.proposed_fix` for ease of rendering. |
| `status` | `ReportStatus` | Initially `pending`. State machine below. |
| `delivered_at` | `datetime` | Wall-clock when the chat surface acknowledged receipt. |
| `approval_deadline` | `datetime` | `delivered_at + 30min` by default; tenant-configurable. |
| `runner_up_domains` | `list[tuple[Domain, Confidence]]` | Copied from `routing.runners_up` for the chat caveat block. |

**Status state machine** (Report):

```text
                              ┌──────────► rejected     (terminal)
pending ──┬──► approved ──────┼──► executed             (terminal, solver success)
          │                   ├──► failed               (terminal, solver failure)
          │                   └──► partial → executed?  (verification re-run; success ⇒ executed, else failed)
          ├──► expired                                 (terminal, deadline passed)
          └──► rejected                                (terminal)
```

Transitions:

- `pending → approved`: requires an `ApprovalEvent` with `action == "approve"` from an authorized clicker before `approval_deadline`.
- `pending → rejected`: any `ApprovalEvent` with `action == "reject"`.
- `pending → expired`: deadline passes with no `ApprovalEvent`. Approval clicks after this point are rejected with a clear message.
- `approved → executed`: Solver ran and post-state verification passed → `SolverOutcome == "success"`.
- `approved → failed`: Solver action failed at the API level, OR verification failed and there's no automated retry path → `SolverOutcome == "failure"`.
- Intermediate `partial`: Solver succeeded at the API level but the post-state verification failed within the window. Reported to the user immediately. May resolve to `executed` if a follow-up verification within a small extension passes, otherwise lands at `failed`.

Per FR-016: approved/executed/failed/etc. are scoped to **this** Report's `correlation_id`. A later Incident requires a fresh Report and a fresh approval.

### 11. ApprovalEvent

| Field | Type | Notes |
|---|---|---|
| `correlation_id` | `CorrelationId` | Joins back to one Report only. |
| `action` | `ApprovalAction` | `approve` or `reject`. Expirations are recorded as a separate `ExpirationEvent`-style row with the same shape and `action == "reject"` and `reason == "expired"`. |
| `actor_id` | `str` | Approver identity from the callback payload. |
| `actor_roles` | `list[str]` | Roles held at click time (audit-relevant). |
| `role_check_passed` | `bool` | False ⇒ no transition, but the event IS recorded (audit / abuse signal). |
| `reason` | `str \| None` | Optional free-text supplied by the user. |
| `at` | `datetime` | ISO-8601. |

### 12. SolverRun

| Field | Type | Notes |
|---|---|---|
| `correlation_id` | `CorrelationId` | |
| `proposed_fix_fingerprint` | `str` | Echoes `ProposedFix.fingerprint`. Solver MUST refuse if the fingerprint doesn't match what the approver saw. |
| `pre_state` | `dict` | Snapshot from MCP read tools before the action. |
| `action_issued` | `dict` | Canonical JSON of the action sent to MCP. |
| `post_state` | `dict` | Snapshot after the verification window. |
| `outcome` | `SolverOutcome` | |
| `reversal_recipe` | `ReversalRecipe` | Echoes `ProposedFix.reversal_recipe`. |
| `error` | `str \| None` | Populated on `outcome == "failure"`. |
| `started_at` | `datetime` | |
| `finished_at` | `datetime` | |

Validation:

- If `proposed_fix_fingerprint` doesn't match the current Report's `ProposedFix.fingerprint`, Solver MUST refuse, emit a `SolverRun` with `outcome == "failure"` and `error == "fingerprint-mismatch"`, and transition Report to `failed`.

### 13. AuditRecord

Append-only. One row per stage transition. The shape is documented separately in [`contracts/audit_record.md`](./contracts/audit_record.md); the entity is mentioned here for completeness.

---

## WorkflowState (TypedDict)

The state object passed between LangGraph nodes. NOT persisted as-is — LangGraph persists it via the configured checkpointer, and we mirror critical fields into the audit table.

```text
class WorkflowState(TypedDict, total=False):
    correlation_id: CorrelationId            # set in Ingest, never overwritten
    incident: Incident                       # set in Ingest
    filtered_evidence: FilteredEvidence      # set in Ingest after search_pod_logs
    routing: RoutingDecision                 # set by Router
    diagnosis: ExpertDiagnosis               # set by Expert (None for Unknown route)
    report: Report                           # set by Reporter, mutated for status transitions
    approval: ApprovalEvent                  # set when interrupt resumes
    solver_run: SolverRun                    # set by Solver
    budget_remaining_tokens: int             # decremented at every LLM call
    budget_remaining_usd_micros: int         # decremented at every LLM call (cost ceiling)
```

State invariants enforced in unit tests (Principle VII):

- Once `report` is set, its `proposed_fix.fingerprint` is immutable for the remainder of the graph.
- `approval.correlation_id` MUST equal `correlation_id`.
- `solver_run.proposed_fix_fingerprint` MUST equal `report.proposed_fix.fingerprint`.
- `budget_remaining_tokens` and `budget_remaining_usd_micros` are monotonically non-increasing.

---

## Allowed-remediation catalog (parameter schemas)

Defined in `src/shared/catalog.py` and validated server-side in the MCP write tool of the same name. Reproduced here for spec ↔ implementation alignment.

| `action_type` | Parameters | Reversal |
|---|---|---|
| `restart-pod` | `{}` (target identifies the pod) | `inverse_action="manual"` — restart is generally self-reversing; pre-state container UIDs recorded so a stuck restart can be inspected. |
| `rollback-deployment` | `{ "to_revision": int }` | `inverse_action="rollback-deployment"`, `inverse_parameters={"to_revision": <pre_state.current_revision>}` |
| `scale-deployment` | `{ "to_replicas": int }`; constrained to `[min, max]` from tenant config | `inverse_action="scale-deployment"`, `inverse_parameters={"to_replicas": <pre_state.replicas>}` |
| `delete-pod-to-reschedule` | `{}` (admission/PDB-respecting; never `--force`, never `--grace-period=0`) | `inverse_action="manual"` — the controller-driven reschedule produces a new pod; reversal is "investigate the new pod" |

---

## Persistence summary

| Entity | Where it lives |
|---|---|
| `Incident`, `Report`, `ApprovalEvent`, `SolverRun` | `audit_record` rows (Postgres prod / SQLite dev), joined by `correlation_id` |
| LangGraph checkpoints | LangGraph's adapter tables in the same DB |
| `ProposedFix` (frozen) | Embedded inside the `Report` row's audit record; `fingerprint` is indexed for Solver-side equality checks |
| `FilteredEvidence` | Summary fields in audit (`total_bytes`, `hit_count`, `truncated`); the actual `LogExcerpt` lines are stored in a child table only for incidents flagged as benchmark/golden, to control storage cost. The full LLM prompt and response are stored regardless. |
