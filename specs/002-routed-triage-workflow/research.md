# Phase 0 Research: Routed Triage and Auto-Remediation Workflow

**Feature**: 002-routed-triage-workflow
**Date**: 2026-05-14

This document captures the technology decisions made during Phase 0. There are **no unresolved `NEEDS CLARIFICATION` items** — every gap in the spec / user input was resolved with an informed default below.

---

## R1. Workflow framework — LangGraph

- **Decision**: Use `langgraph` to model the workflow as a typed state machine with conditional edges and an explicit `interrupt` between the Reporter and Solver nodes.
- **Rationale**: The user pinned LangGraph in the input, and its first-class support for `interrupt` + `Command(resume=...)` is exactly the HITL pause/resume semantics the spec requires (FR-015). The built-in checkpointer also gives us crash recovery and an audit-grade replay surface for free, satisfying Principle V.
- **Alternatives considered**:
  - Hand-rolled async state machine — rejected: re-implementing `interrupt`, checkpointing, and replay would absorb most of the MVP's engineering budget for no payoff.
  - Temporal / Prefect — rejected for MVP: heavyweight infra (worker pool, separate UI) for a single tenant + ~100 incidents/day; revisit if we outgrow LangGraph's single-process model.

## R2. LLM provider and model tiering

- **Decision**: Anthropic API for MVP, tiered:
  - **Router** (Node 2) — Haiku-class (`claude-haiku-4-5-20251001`). Cheap, fast, deterministic structured output via pydantic schema.
  - **Experts** (Node 3a/3b) — Sonnet-class (`claude-sonnet-4-6`). Higher reasoning quality for root-cause hypotheses; cost-justified by the lower fan-out (one Expert per incident, not per log line).
  - **Reporter** (Node 4) — Haiku-class. Template-rendered summary; LLM only used for prose polish (skippable if budget tight).
  - **Solver** (Node 5) — NO LLM call. Solver is deterministic code that executes a frozen `ProposedFix`. Putting an LLM in the write path is a Principle I violation; we don't.
- **Rationale**: Satisfies Principle II (cheapest model that meets the bar at each stage). Per-stage choice is recorded in audit and can be re-tuned without re-architecting.
- **Alternatives considered**:
  - Single model for all stages (e.g., Sonnet everywhere) — rejected: ~3× the cost-per-incident for no measurable quality gain on the Router's classification task.
  - OpenAI/Azure as primary — rejected for MVP: the agent codebase is built on the official Anthropic SDK already (see project preferences); we keep one provider for the MVP and add a second behind an interface in v2.
- **Open**: Tier model IDs are configurable via `settings.py`; benchmark drives final pinning before GA.

## R3. State persistence — Postgres in prod, SQLite in dev/CI

- **Decision**: Use LangGraph's Postgres checkpointer (`langgraph.checkpoint.postgres`) in production and the SQLite checkpointer in local/CI. The append-only audit table lives in the same database, separate schema.
- **Rationale**: Postgres gives us atomic writes for `(checkpoint, audit_record)` pairs and durable, queryable audit retention. SQLite keeps `make dev` and CI runs zero-infra. Both back-ends are first-party LangGraph adapters, so no impedance mismatch.
- **Alternatives considered**:
  - Redis checkpointer — rejected: ephemeral by default; durability work would offset the perf benefit, and we don't need sub-ms checkpoint reads.
  - File-based checkpointer — rejected: doesn't scale beyond a single instance and gives weak audit guarantees.

## R4. Webhook intake — FastAPI + HMAC verification

- **Decision**: FastAPI service with a single `POST /webhook/alertmanager` route. Verify Alertmanager's signed-secret HMAC at the handler boundary (FR-002); reject before any further processing.
- **Rationale**: FastAPI is the mainstream async Python HTTP framework, plays well with uvicorn under load, integrates cleanly with pydantic v2 for payload validation, and is already a transitive dep of much of the Python LLM ecosystem.
- **Alternatives considered**:
  - Flask — rejected: sync-first; we want async to coexist cleanly with LangGraph + httpx tool calls.
  - Starlette directly — rejected: FastAPI is a thin wrapper that gives us pydantic-driven validation and OpenAPI for free.

## R5. MCP server — official Python SDK, separate process

- **Decision**: Use the official `mcp` Python SDK to expose tools as an MCP server. Run it as its own process, separate from the agent. Transport: stdio in dev (simplest), HTTP/SSE in production (so the agent can reach it across pods).
- **Rationale**: Physical process separation is the cleanest enforcement boundary for Principle I — the agent literally cannot mutate the cluster without going through MCP. Per-tool ServiceAccounts live in the MCP process; the agent process never holds a write-capable kube token.
- **Tool catalog** (mirrors spec FR-011 and assumption catalog):
  - **Read**: `search_pod_logs` (window, grep pattern, max lines), `get_pod` (status, restart count, container states).
  - **Write**: `restart_pod`, `rollback_deployment`, `scale_deployment` (with min/max bounds enforced server-side), `delete_pod` (used only as a reschedule trigger; admission/PDB-respecting, never `--force`).
- **Alternatives considered**:
  - Have the agent call `kubernetes` directly — rejected: it collapses Principle I's safety boundary into the LLM-adjacent process.
  - Use a generic K8s MCP server — rejected: third-party tools expose broader surface than we want for the MVP catalog. We can revisit once the catalog stabilizes.

## R6. Mock Slack — local FastAPI receiver

- **Decision**: Build a tiny `slack-mock` FastAPI service in `deploy/` exposing:
  - `POST /messages` — agent posts the Report here; the mock stores it and prints to its console.
  - `POST /messages/{id}/approve` and `POST /messages/{id}/reject` — invoked by buttons; the mock turns around and calls `agent: /callbacks/slack/approve|reject`.
- **Rationale**: The spec says "Slack-style report" / "mock Slack UI" — we keep the same shape (Block Kit-compatible JSON) and the same approve/reject callback flow, so swapping in real Slack later is a contract-conforming substitution, not a rewrite. Principle VIII satisfied by a single shared schema.
- **Alternatives considered**:
  - Use Slack itself with a free workspace — rejected for MVP: adds OAuth, signing-secret rotation, and a non-local dependency to every dev environment.

## R7. Redaction — boundary-applied, double-pass

- **Decision**: Regex-based redaction applied (a) inside the MCP read tool _before_ the log payload crosses the MCP boundary back to the agent, and (b) again immediately before any LLM call. Patterns: bearer tokens, AWS/GCP/Azure key formats, K8s ServiceAccount tokens, `Authorization:` header values, JWT-shaped strings, common DB connection strings.
- **Rationale**: Principle I + Principle V + SC-009 all require zero unredacted secrets in prompts or audit. Two passes are insurance against single-point failure; the boundary pass also means even an agent-process bug can't leak unredacted content to an LLM call.
- **Alternatives considered**:
  - LLM-driven redaction — rejected: the constitution explicitly says redaction MUST NOT rely on model behavior.
  - Single-pass at the LLM call site — rejected: defense in depth is cheap, and the boundary pass means audit records are clean by construction.

## R8. Budget and latency enforcement

- **Decision**:
  - Per-incident token counter increments at every LLM call site; once `budget.tokens_remaining <= 0`, the next call short-circuits with a `BudgetExceeded` exception that the graph turns into a "partial result" report.
  - Per-stage wall-clock timeout enforced via `asyncio.wait_for` inside each node; node-level timeout < graph-level total so a single stuck stage can't blow the SLO.
  - Bounded jittered retries (max 3 attempts, exponential 200ms → 1s → 5s, ±50% jitter) on idempotent tool/LLM calls. No retries on write tools — write tools are single-shot; failure is reported, not silently retried.
- **Rationale**: Principle II (fail-closed cost) and Principle IX (CI-enforced perf budgets). No `--force` retry path satisfies Principle I.

## R9. Audit log shape

- **Decision**: Single append-only `audit_record` table; one row per stage transition. Schema in [`contracts/audit_record.md`](./contracts/audit_record.md). Joined by `correlation_id`; sortable by `(correlation_id, sequence_no)`.
- **Rationale**: Append-only is the simplest construct that satisfies Principle V's "MUST NOT be silently truncated" rule. Putting the audit in the same Postgres as the LangGraph checkpointer means transactional consistency between "what the graph did" and "what the audit says we did" is free.

## R10. Test strategy and CI gates

- **Decision**:
  - Unit / contract / integration / eval / hallucination test suites organized as listed in `plan.md`.
  - CI gates (per constitution VII + IX):
    - `ruff` + `black --check` + `mypy --strict` MUST pass.
    - Coverage ≥ 85% pure logic, ≥ 95% safety-critical (`redaction`, `budget`, `auth`, `solver._guards`, MCP `tools/_guards`, MCP write tools).
    - Eval suite: Router top-1 ≥ 85%, top-2 ≥ 97% on the labeled fixture; Expert proposed-fix match ≥ 70%.
    - Hallucination suite: zero failures.
    - Latency benchmark on a recorded fixture: p50 ≤ 30 s, p95 ≤ 60 s; regression beyond budget blocks merge.
    - Redaction audit job: zero unredacted secrets in fixture audit table.
- **Rationale**: Matches the constitution's CI gate list literally; nothing about the workflow's complexity argues for relaxing any threshold.

## R11. Authorization model

- **Decision**: Approver role check at the `/callbacks/slack/approve` boundary. Default mapping: any user holding the `triage-approver` role may approve any action currently in the MVP catalog. Approver identity comes from the signed callback payload (mock for now; real Slack OAuth in v2).
- **Rationale**: Tightest model that satisfies Principle I (FR-018) without over-engineering. Per-action-type role mapping is a v2 extension and is captured as `roles_by_action` in `settings.py` but defaults to a single role.

## R12. Dedup window

- **Decision**: Fingerprint = `sha256(alert_id || target_namespace || target_pod || floor(timestamp / 600s))`. First webhook in the bucket creates the Incident; subsequent webhooks update its `last_seen` but do not re-trigger the graph.
- **Rationale**: Matches the spec's 10-min dedup assumption with a deterministic, replayable function. Bucketing on a 10-min floor is coarser than a sliding window but is dramatically simpler and adequate for the MVP's incident volume.

---

## Summary of resolved unknowns

| Item | Resolved by |
|---|---|
| Workflow framework | R1 (LangGraph) |
| Model selection per stage | R2 (Haiku for router/reporter, Sonnet for experts, no LLM in solver) |
| State persistence | R3 (Postgres prod / SQLite dev, shared with audit) |
| Webhook framework | R4 (FastAPI + HMAC) |
| MCP framing | R5 (official SDK, separate process, per-tool SAs) |
| Mock Slack | R6 (local FastAPI receiver matching Block Kit shape) |
| Redaction | R7 (double-pass regex, never LLM-based) |
| Budget / retries | R8 (token + wall-clock + bounded jittered retries; no retry on writes) |
| Audit shape | R9 (append-only, joined by correlation_id) |
| CI gates | R10 (per constitution VII + IX) |
| Approver role | R11 (single `triage-approver` role for MVP) |
| Dedup | R12 (10-min bucketed fingerprint) |

No `NEEDS CLARIFICATION` items remain. Phase 1 may proceed.
