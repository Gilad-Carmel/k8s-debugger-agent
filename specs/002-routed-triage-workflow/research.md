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

- **Decision**: Local OpenAI-compatible inference server for MVP. The agent calls
  `http://localhost:52984/v1/chat/completions` (or the value of `LLM_BASE_URL`)
  using the `openai` Python SDK via `langchain-openai`'s `ChatOpenAI`. No
  external API key or cloud dependency is required.
  - **Router** (Node 2) — same local model, fast sampling profile: low
    `max_tokens` (structured JSON label only), temperature 0. Env var:
    `LLM_ROUTER_MODEL` (default: `LLM_MODEL`).
  - **Experts** (Node 3a/3b) — same local model, full-context profile: higher
    `max_tokens` (room for root-cause + cited evidence), temperature 0.2. Env
    var: `LLM_EXPERT_MODEL` (default: `LLM_MODEL`).
  - **Reporter** (Node 4) — same local model, prose-summary profile. LLM call
    is skippable if budget (token ceiling) is tight; template fallback applies.
  - **Solver** (Node 5) — NO LLM call. Solver is deterministic code that
    executes a frozen `ProposedFix`. Putting an LLM in the write path is a
    Principle I violation; we don't.
- **Client layer**: `langchain-openai` (`ChatOpenAI`) is used for all calls.
  `base_url` and `api_key` are injected from settings. For local servers that
  require no authentication, `api_key` defaults to `"not-required"`. The
  `.with_structured_output(PydanticModel)` helper generates the JSON-mode or
  tool-calling schema automatically; if the served model does not support
  native tool calling, `method="json_mode"` is used as a fallback.
- **"Tiering" with a local server**: Because a single inference process
  typically serves one model, "tiering" here means sampling-parameter
  differentiation (max_tokens, temperature) rather than model-class switching.
  Both `LLM_ROUTER_MODEL` and `LLM_EXPERT_MODEL` default to `LLM_MODEL` so
  a minimal local setup sets exactly one env var. If two separate local
  populations are available (e.g., a small fast model + a large slow model on
  different ports), the two env vars can point to different `LLM_BASE_URL`
  values via `LLM_ROUTER_BASE_URL` / `LLM_EXPERT_BASE_URL` overrides.
- **Rationale**: Eliminates cloud API dependency and per-token cost for
  development and self-hosted production, while keeping the same
  OpenAI-compatible interface that every major local inference server
  (Ollama, vLLM, LM Studio, llama.cpp server) already exposes. The
  `langchain-openai` wrapper keeps the rest of the codebase provider-agnostic;
  switching to a cloud provider later is a one-line settings change.
  Satisfies Principle II (token ceiling enforced fail-closed; no USD ceiling
  needed for local inference, but the ceiling mechanism is retained for
  latency and memory budget reasons).
- **Alternatives considered**:
  - Anthropic SDK — rejected: requires an external API key and incurs per-token
    cost; unsuitable for air-gapped or cost-sensitive environments.
  - Hugging Face `transformers` in-process — rejected: loads model weights into
    the agent process, blowing the 512 MiB RSS budget and coupling model
    lifecycle to service lifecycle.
  - Raw `httpx` against the local endpoint — rejected: `langchain-openai`
    provides structured-output, retry, and provider-abstraction for free;
    re-implementing those is wasted effort.
- **Open**: Structured-output reliability varies by local model. If the served
  model does not reliably emit valid JSON, the `llm.py` client will add a
  retry-with-correction loop (prompt the model with the parse error; max 2
  retries). The eval suite (T056–T060) gates on json-parse success rate ≥ 99%
  before merge.

## R3. State persistence — SQLite (all environments)

- **Decision**: Use SQLite (`aiosqlite`, WAL mode) for both LangGraph checkpoints and the append-only audit table in all environments — local development, CI, and production MVP. The LangGraph `AsyncSqliteSaver` adapter is used for checkpointing. The audit table lives in the same `.sqlite3` file.
- **Rationale**: Postgres was originally planned for production to gain DB-level role enforcement of the append-only invariant and atomic `(checkpoint, audit_record)` writes. For a single-tenant MVP at ~100 incidents/day these guarantees are achievable at the application layer at a fraction of the operational cost: `audit.py` is the sole writer, a unit test asserts no UPDATE/DELETE SQL exists in any module that touches `audit_record`, and SQLite WAL provides crash-safe sequential writes. Removing Postgres eliminates a compose service, `asyncpg` and `sqlalchemy` dependencies, `libpq-dev` from Docker images, and the Postgres CI service blocks — meaningfully reducing both image size and CI complexity. Migration to Postgres is straightforward if we outgrow single-instance SQLite.
- **Append-only enforcement**: Application-layer only. `audit.py` is the sole caller of INSERT on `audit_record`. A dedicated unit test (`tests/unit/test_audit_record.py`) asserts that no other source file contains `UPDATE` or `DELETE` SQL targeting `audit_record`.
- **Alternatives considered**:
  - Postgres — rejected for MVP: operational overhead (extra compose service, `asyncpg`/`libpq` build deps, CI postgres container) outweighs the benefit when single-tenant ~100 incidents/day fits comfortably in SQLite WAL; revisit at multi-tenant or high-availability scale.
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
  - **Read**: `search_pod_logs` (window, grep pattern, max lines, contextual N-line window around each match per FR-004), `get_pod_events` (Kubernetes events for the target, last N minutes — required by `spec.md` §Assumptions and by the `evidence.events` field on `WorkflowState`), `get_pod` (status, restart count, container states).
  - **Write**: `restart_pod`, `rollback_deployment`, `scale_deployment` (with min/max bounds enforced server-side), `delete_pod_to_reschedule` (used only as a reschedule trigger; admission/PDB-respecting, never `--force`). Each write tool has a fixed Forward → Inverse Action mapping registered in `shared/catalog.py` per `spec.md` §Assumptions.
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
    - Eval suite: Router top-1 ≥ 85%, top-2 ≥ 97% on the labeled fixture; Expert proposed-fix match ≥ 70% per domain (Application, Network, Database — reported separately per SC-002).
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

## R13. Dependency vetting (Principle VI)

Each new runtime dependency below was evaluated for (a) OSI-approved license, (b) active maintenance (commit in the last 90 days or stable LTS posture), (c) no open critical CVEs as of the date of this document, and (d) supply-chain posture (signed releases or a maintained PyPI account with 2FA).

| Package | License | Maintenance | Notes |
|---|---|---|---|
| `langgraph` | MIT | Active (LangChain org) | Pin to ≥ 0.2; checkpointer adapters tracked on the same release cadence. |
| `langchain-core` | MIT | Active | Used only for structured-output / model-binding helpers transitive to LangGraph. |
| `mcp` (official Python SDK) | MIT | Active (Anthropic) | Project preference per `CLAUDE.md` is the official SDK. |
| `kubernetes` (official client) | Apache-2.0 | Active (CNCF) | Used inside MCP tools only; never imported from the agent process. |
| `fastapi` | MIT | Active | Mainstream async HTTP framework; pulled in transitively by much of the LLM stack. |
| `uvicorn` | BSD-3-Clause | Active | Standard FastAPI runner. |
| `pydantic` v2 | MIT | Active | Required for structured-output / Settings. |
| `langchain-openai` | MIT | Active (LangChain org) | `ChatOpenAI` wrapper for local OpenAI-compatible inference; `.with_structured_output()` used for Router/Expert nodes. Two-reviewer rule applies (model dependency). |
| `openai` | MIT | Active (OpenAI) | Transitive dep of `langchain-openai`; also used directly in `llm.py` for low-level retry logic. Two-reviewer rule applies (model dependency). |
| `aiosqlite` | MIT | Active | SQLite async driver (all environments). |
| `structlog` | Apache-2.0 / MIT | Active | Structured logging with contextvars. |
| `opentelemetry-api` / `opentelemetry-sdk` | Apache-2.0 | Active (CNCF) | Spans on node entry/exit + MCP tool calls per Principle IX. |
| `respx` (test only) | BSD-3-Clause | Active | HTTPX mock for contract tests. |
| `deepeval` (test only) | Apache-2.0 | Active | LLM eval runner; thin in-house equivalent acceptable if `deepeval`'s API changes. |

Two-reviewer rule (Principle VI / Governance) applies to any PR that adds, removes, or version-bumps a package in the `langchain-openai`, `openai`, `langgraph`, `langchain-core`, or `mcp` lines (per "model dependencies").

---

## Summary of resolved unknowns

| Item | Resolved by |
|---|---|
| Workflow framework | R1 (LangGraph) |
| Model selection per stage | R2 (local OpenAI-compatible endpoint via `langchain-openai`; single `LLM_MODEL` env var; Router uses fast sampling, Experts use full context; no LLM in Solver) |
| State persistence | R3 (SQLite all environments; application-layer append-only enforcement) |
| Webhook framework | R4 (FastAPI + HMAC) |
| MCP framing | R5 (official SDK, separate process, per-tool SAs) |
| Mock Slack | R6 (local FastAPI receiver matching Block Kit shape) |
| Redaction | R7 (double-pass regex, never LLM-based) |
| Budget / retries | R8 (token + wall-clock + bounded jittered retries; no retry on writes) |
| Audit shape | R9 (append-only, joined by correlation_id) |
| CI gates | R10 (per constitution VII + IX) |
| Approver role | R11 (single `triage-approver` role for MVP) |
| Dedup | R12 (10-min bucketed fingerprint) |
| Dependency vetting | R13 (license, maintenance, supply-chain posture per Principle VI) |

No `NEEDS CLARIFICATION` items remain. Phase 1 may proceed.
