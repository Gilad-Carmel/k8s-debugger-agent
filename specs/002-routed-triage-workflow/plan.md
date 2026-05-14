# Implementation Plan: Routed Kubernetes Incident Triage and Auto-Remediation Workflow

**Branch**: `002-routed-triage-workflow` | **Date**: 2026-05-14 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/002-routed-triage-workflow/spec.md`

## Summary

Build a LangGraph state-machine workflow that (a) receives an Alertmanager-style webhook, (b) fetches and pre-filters logs, Kubernetes events, and resource status via an in-repo MCP server, (c) classifies the incident domain (`Application` / `Network` / `Database` / `Unknown`) with a structured-output LLM call, (d) dispatches to a domain-specific Expert agent for diagnosis + proposed fix, (e) renders a chat report with interactive Approve/Reject controls to a mock-Slack receiver, (f) `interrupt`s the graph until an authorized approval click, then (g) resumes into a deterministic (no-LLM) Solver node that executes the exact approved action through tightly-scoped MCP write tools, captures a pre-state snapshot, verifies the post-state, and reports the outcome with the **Inverse Action** computed at execution time from the pre-state (per the fixed Forward → Inverse mapping in the allowed-remediation catalog). Persistence is Postgres (LangGraph checkpointer + append-only audit table) in production and SQLite for local/CI runs.

## Technical Context

**Language/Version**: Python 3.11+ (typed, async)

**Primary Dependencies**:

- `langgraph` ≥ 0.2 — state machine, interrupt-and-resume, checkpointer integration
- `langchain-core` (only for the structured-output / model-binding helpers LangGraph already depends on)
- `langchain-openai` — `ChatOpenAI` client with configurable `base_url`; used for all LLM calls; provider-agnostic interface to the local OpenAI-compatible inference server (research.md R2)
- `openai` — transitive dep of `langchain-openai`; also used directly for low-level retry/structured-output fallback in `llm.py`
- `mcp` (official Python SDK) — for the in-repo MCP server exposing K8s read + scoped-write tools
- `kubernetes` (official Python client) — used inside MCP tools; never called directly from agent code
- `fastapi` + `uvicorn` — webhook intake, mock-Slack receiver, interactive callback endpoints
- `pydantic` v2 — typed state, structured LLM outputs, shared Report schema, MCP tool schemas
- `sqlalchemy` + `asyncpg` (prod) / `aiosqlite` (dev) — audit table and checkpoint storage
- `structlog` — correlation-ID-bound structured logging
- `opentelemetry-api` + `opentelemetry-sdk` — node-entry/exit and MCP-call span instrumentation for the IX perf budget
- Test: `pytest`, `pytest-asyncio`, `respx` (HTTP mocks), `kind` (real cluster fixture in CI), `deepeval` (LLM evals)

**Storage**:

- LangGraph checkpointer: Postgres (prod) via `langgraph.checkpoint.postgres`; SQLite via `langgraph.checkpoint.sqlite` for dev/CI.
- Audit log: append-only Postgres table `audit_record` keyed by `correlation_id`. Same SQLite path for dev.
- No cluster-state caching in MVP (every triage refetches; satisfies freshness without an SLO obligation).

**Testing**: `pytest` with:

- Unit tests for nodes, redaction, budget, schema validation (coverage floors per constitution VII: 85% pure logic, 95% safety-critical).
- Contract tests against recorded fixtures for Alertmanager webhook payloads, MCP tool requests/responses, and the mock-Slack receiver.
- Integration tests against a `kind` cluster spun up in CI for the Solver write paths (restart-pod, rollback-deployment).
- Eval suite with `deepeval` (or a thin in-house equivalent) for Router classification and per-Expert diagnosis quality.
- Hallucination tests: every Expert response is checked to ensure every factual claim is backed by a quoted log line.

**Target Platform**: Linux container (Python 3.11 slim base). Two long-running services deployed side-by-side:

1. The agent service (FastAPI + LangGraph runner).
2. The MCP server (separate process, talks to the agent over MCP transport — stdio in dev, HTTP/SSE in prod).

Plus, in dev only: a mock-Slack FastAPI receiver and a `kind` cluster.

**Project Type**: web-service (the agent) + a separate tool-server (the MCP). Single Python monorepo, two installable packages.

**Performance Goals** (from spec SC-003 and constitution IX):

- TTFT (first user-visible acknowledgement in chat — an interim "Triage started — correlation `<id>`" message emitted at the end of the Ingest node, before any LLM call) ≤ 3 s.
- p50 end-to-end (webhook → final Report with proposed fix + Approve/Reject controls delivered) ≤ 30 s.
- p95 end-to-end ≤ 60 s.
- Solver execution (API call) ≤ 10 s; post-state verification window ≤ 60 s per spec assumption (verification is bounded independently of the forward call).

**Constraints**:

- Per-incident cost ceiling enforced fail-closed (spec FR-029); ceiling is **token-count only** for local inference (no per-token USD cost); token default 50k — tunable per tenant. USD-micros field in `WorkflowState` is retained for forward-compatibility with cloud providers but defaults to unlimited (`-1`) when `LLM_BASE_URL` points to a local server.
- Kill switch halts all in-flight Solver actions for a tenant within 5 s (FR-030).
- Zero unredacted secrets reach the LLM or the audit record (SC-009).
- All MCP write tools are individually scoped per action type and per namespace; no broad cluster-admin token.
- All retries are bounded with jitter; no unbounded loops.
- **Memory budget**: agent service process ≤ 512 MiB RSS; MCP server process ≤ 256 MiB RSS. Exceeding these in CI or production is a defect (Principle IX).
- **Concurrency budget**: up to 10 in-flight incidents simultaneously; per-target remediation serialized via `solver_lock.py` (FR-026). Unbounded fan-out is a defect.

**Scale/Scope**:

- MVP: single tenant, single cluster, up to ~100 incidents/day.
- Concurrent in-flight incidents: target 10; serialize remediations per target (FR-026).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Evaluated against `.specify/memory/constitution.md` v1.1.0 (Principles I–IX).

| # | Principle | Status | Notes |
|---|---|---|---|
| I | Safety-First Autonomy | ✅ Compliant | Read-only by default (MCP read tools). Mutations gated on HITL approval via LangGraph `interrupt` (spec FR-015). Catalog-only writes (FR-011 / FR-021). Pre-state snapshot captured immediately before every forward action; the **Inverse Action** is *computed* by the Solver at execution time from that snapshot using the fixed Forward → Inverse mapping in the catalog (FR-022, never an Expert-authored ad-hoc script). Per-target serialization (FR-026). Kill switch within 5 s (FR-030). No `--force` / `--grace-period=0` bypass. |
| II | Cost-Conscious by Design | ✅ Compliant | Local inference eliminates per-token USD cost; Principle II still satisfied: token ceiling is enforced fail-closed (latency + memory budget), per-stage token usage is recorded in audit, and the USD-micros field is retained for cloud-provider forward-compatibility. The "cheapest model that meets the bar" principle maps to lowest max_tokens + temperature=0 for the Router vs. fuller context for Experts. |
| III | Developer Experience as a Product | ✅ Compliant | Single Slack-style chat message with TL;DR + cited evidence + interactive controls (FR-013/FR-014). Latency SLOs from constitution IX adopted directly (see Performance Goals). One-command local setup via `docker-compose up` (quickstart.md). |
| IV | Evidence-Backed Triage (NON-NEGOTIABLE) | ✅ Compliant | Router cites (FR-007), Experts cite (FR-010), 100% of user-facing claims cited (SC-005). Hallucination tests run in CI. |
| V | Observability & Reversibility | ✅ Compliant | Single `correlation_id` joins every stage (FR-028). Audit table records prompt, response, model, tokens, cost, redactions, pre/action/post-state, and the **Inverse Action** computed at Solver execution time (FR-022, FR-023). LangGraph checkpoints provide an additional crash-recovery audit surface. |
| VI | Code Quality | ✅ Compliant | `ruff` + `black` + `mypy --strict` in CI. Cyclomatic complexity cap (15) enforced via `ruff` `C901`. Dependency vetting captured in `research.md` §R13. Two-reviewer rule applies to PRs touching MCP write tools, redaction, budget enforcement, authorization (`auth.py` + role-check), **model selection / provider dependencies (`llm.py`, `settings.py:LLM_*`)**, and this plan. Switch from `anthropic` → `langchain-openai` is a model-dependency change; this plan update counts as the first required review. |
| VII | Testing Standards (NON-NEGOTIABLE) | ✅ Compliant | Coverage floors 85% / 95% enforced in CI per safety-critical module list (`redaction`, `budget`, `approval`, `auth`, `solver._guards`, MCP `tools/_guards`, MCP write tools). LLM eval suite for Router and per-Expert (all three: Application, Network, Database). Hallucination test on every Expert response. Refusal-path + Inverse-Action-recipe tests for every MCP write tool. |
| VIII | User Experience Consistency | ✅ Compliant | One pydantic `Report` model produced by the Reporter node; rendered for Slack-mock today, web/CLI later. Shared label vocabulary (`Application` / `Network` / `Database` / `Unknown`). Single error-message template. Timestamps ISO-8601; bytes IEC. |
| IX | Performance Requirements (DevOps SLOs) | ✅ Compliant | TTFT/p50/p95/cost SLOs declared above and CI-enforced on the benchmark. Bounded jittered retries on K8s + LLM calls. Hot paths (LangGraph node entry/exit, MCP tool calls) profiled via `opentelemetry`. Freshness SLO N/A (MVP refetches every incident). |

**Verdict**: All gates compliant. No entries required in the Complexity Tracking table.

## Project Structure

### Documentation (this feature)

```text
specs/002-routed-triage-workflow/
├── plan.md                            # This file
├── research.md                        # Phase 0 — decisions on stack, model tiering, persistence, MCP framing
├── data-model.md                      # Phase 1 — pydantic entities + state-machine transitions
├── quickstart.md                      # Phase 1 — one-command local run end-to-end
├── contracts/
│   ├── alertmanager_webhook.md        # Inbound webhook schema (POST /webhook/alertmanager)
│   ├── mcp_tools.md                   # MCP read + scoped-write tool contracts
│   ├── slack_mock.md                  # Outbound chat message + inbound approve/reject callback
│   └── audit_record.md                # Append-only audit table row schema
├── checklists/
│   └── requirements.md                # Already exists — spec-quality checklist
└── tasks.md                           # Phase 2 output — produced by /speckit-tasks (NOT this command)
```

### Source Code (repository root)

```text
src/
├── agent/                             # LangGraph workflow service (FastAPI + graph runner)
│   ├── api/
│   │   ├── webhook.py                 # POST /webhook/alertmanager (HMAC-verified)
│   │   ├── callbacks.py               # POST /callbacks/slack/approve|reject
│   │   ├── expiry.py                  # Background task: PENDING → EXPIRED at approval_deadline
│   │   └── health.py
│   ├── graph/
│   │   ├── builder.py                 # build_graph() — assembles nodes + conditional edges + interrupt
│   │   ├── state.py                   # WorkflowState (TypedDict)
│   │   └── nodes/
│   │       ├── ingest.py              # Node 1: dedup + emit TTFT ack + MCP search_pod_logs + get_pod_events + get_pod
│   │       ├── router.py              # Node 2: structured-output classifier (App / Net / DB / Unknown)
│   │       ├── experts/
│   │       │   ├── application.py     # Node 3a
│   │       │   ├── network.py         # Node 3b
│   │       │   ├── database.py        # Node 3c
│   │       │   └── _base.py           # shared expert protocol
│   │       ├── reporter.py            # Node 4: assemble Report + send to slack-mock
│   │       └── solver.py              # Node 5 (post-interrupt): NO LLM — deterministic execution of the frozen ProposedFix via MCP write tools; computes Inverse Action from captured pre-state
│   ├── mcp_client.py                  # Thin wrapper around the MCP Python SDK
│   ├── llm.py                         # Tiered model selection + structured output helpers
│   ├── redaction.py                   # Secret-shaped pattern redaction (applied at tool boundary + pre-LLM)
│   ├── budget.py                      # Per-incident token/$ ceiling enforcement (fail-closed)
│   ├── audit.py                       # Append-only audit writes keyed by correlation_id
│   ├── auth.py                        # Approver role check (95% coverage tier)
│   ├── approval_token.py              # Short-lived signed token carrying proposed_fix_fingerprint + correlation_id + exp
│   ├── kill_switch.py                 # Agent-side kill-switch cache + check helper
│   ├── logging_config.py              # structlog configuration bound to correlation contextvar
│   ├── solver_lock.py                 # Per-target serialization lock (FR-026)
│   ├── telemetry.py                   # OpenTelemetry tracer + node-entry/exit + MCP-call span helpers
│   └── settings.py                    # Pydantic Settings (env-driven)
│
├── mcp_server/                        # Separate process: in-repo MCP server
│   ├── server.py                      # MCP server entrypoint (stdio dev / HTTP-SSE prod)
│   ├── admin.py                       # POST /admin/kill-switch (IP-restricted, ≤5s propagation)
│   ├── tools/
│   │   ├── search_pod_logs.py         # READ — fetch + local contextual grep pre-filter
│   │   ├── get_pod_events.py          # READ — Kubernetes events for the target (last N minutes)
│   │   ├── get_pod.py                 # READ — pod status / phase / restart count
│   │   ├── restart_pod.py             # WRITE — catalog: restart-pod
│   │   ├── rollback_deployment.py     # WRITE — catalog: rollback-deployment
│   │   ├── scale_deployment.py        # WRITE — catalog: scale-deployment (bounded min/max enforced server-side)
│   │   ├── delete_pod_to_reschedule.py # WRITE — catalog: delete-pod-to-reschedule; never with --force
│   │   └── _guards.py                 # admission / PDB / quota refusal handling, no --force ever
│   └── auth.py                        # Per-tool ServiceAccount loading + scope check
│
└── shared/                            # Cross-package contracts and catalogs
    ├── schemas.py                     # Report, RoutingDecision, ExpertDiagnosis, ProposedFix, ApprovalEvent, SolverRun
    ├── catalog.py                     # Allowed-remediation catalog (action ID → signature) + INVERSE_ACTIONS (fixed Forward → Inverse mapping per spec.md §Assumptions)
    ├── labels.py                      # Single source of truth for domain (App/Net/DB/Unknown) / severity / outcome strings
    ├── errors.py                      # Single user-facing error-message template (what failed / why / what to try next) per Principle VIII
    └── correlation.py                 # correlation_id generation + contextvar propagation

tests/
├── contract/
│   ├── test_alertmanager_payload.py
│   ├── test_mcp_tools.py              # both read and write tool contracts (incl. get_pod_events)
│   └── test_slack_mock_protocol.py
├── integration/
│   ├── test_e2e_application_flow.py   # webhook → report → approve → solver → success
│   ├── test_e2e_network_flow.py
│   ├── test_e2e_database_flow.py
│   ├── test_e2e_unknown_low_confidence.py
│   ├── test_hitl_gating.py            # no mutation without approval; expiry; role-check
│   └── test_kill_switch.py
├── eval/
│   ├── router_golden.jsonl            # labeled router classification fixtures
│   ├── application_expert_golden.jsonl
│   ├── network_expert_golden.jsonl
│   ├── database_expert_golden.jsonl
│   ├── solver_golden.jsonl            # labeled remediation scenarios for SC-008 benchmark
│   ├── hallucination_suite.py         # every claim must cite an excerpt present in the input
│   └── runner.py
├── perf/
│   ├── test_latency_benchmark.py      # p50 ≤30s, p95 ≤60s, TTFT ≤3s (SC-003)
│   └── test_cost_budget.py            # 95% of incidents under per-incident ceiling (SC-007)
└── unit/
    ├── test_redaction.py
    ├── test_budget.py
    ├── test_auth.py                   # role-check positive + negative paths (95% coverage tier)
    ├── test_solver_guards.py          # refusal-path + Inverse-Action computation tests
    ├── test_inverse_actions.py        # Forward → Inverse mapping table (catalog.py)
    ├── test_audit_record.py
    └── test_graph_state_transitions.py

deploy/
├── docker-compose.yml                 # agent + mcp + postgres + slack-mock + kind (dev)
├── Dockerfile.agent
├── Dockerfile.mcp
├── slack_mock/
│   ├── app.py                         # tiny FastAPI receiver: POST /messages, POST /messages/{id}/approve|reject
│   └── Dockerfile
└── k8s/
    ├── agent-deployment.yaml
    └── mcp-deployment.yaml

docs/
└── (created by /speckit-tasks polish phase if needed)
```

**Structure Decision**: Python monorepo with two installable packages — `src/agent` (the FastAPI + LangGraph service) and `src/mcp_server` (the MCP tool server) — sharing `src/shared` for the cross-package contracts (Report schema, allowed-remediation catalog + Inverse Action mapping, domain/severity/outcome label vocabulary, user-facing error template). The MVP supports three domain Experts (Application, Network, Database) matching the spec's four-way taxonomy (the fourth, `Unknown`, short-circuits past the Experts). The Ingest node draws evidence from three MCP read tools (`search_pod_logs`, `get_pod_events`, `get_pod`) and emits a TTFT acknowledgement to chat before any LLM call. The mock-Slack receiver is a tiny FastAPI service under `deploy/slack_mock/` rather than its own installable package. Two packages instead of one keeps the write-tools (and their per-tool ServiceAccounts) physically separated from the agent process, which directly supports Principles I and V — the agent cannot mutate a cluster without going through the MCP boundary.

## Complexity Tracking

> All Constitution Check gates evaluated **compliant**. No entries required.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| — | — | — |
