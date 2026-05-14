# Implementation Plan: Routed Kubernetes Incident Triage and Auto-Remediation Workflow

**Branch**: `002-routed-triage-workflow` | **Date**: 2026-05-14 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/002-routed-triage-workflow/spec.md`

## Summary

Build a LangGraph state-machine workflow that (a) receives an Alertmanager-style webhook, (b) fetches and pre-filters logs via an in-repo MCP server, (c) classifies the incident domain with a structured-output LLM call, (d) dispatches to a domain-specific Expert agent for diagnosis + proposed fix, (e) renders a chat report with interactive Approve/Reject controls to a mock-Slack receiver, (f) `interrupt`s the graph until an authorized approval click, then (g) resumes into a Solver node that executes the exact approved action through tightly-scoped MCP write tools and reports the outcome with a reversal recipe. Persistence is Postgres (LangGraph checkpointer + append-only audit table) in production and SQLite for local/CI runs.

## Technical Context

**Language/Version**: Python 3.11+ (typed, async)

**Primary Dependencies**:

- `langgraph` ≥ 0.2 — state machine, interrupt-and-resume, checkpointer integration
- `langchain-core` (only for the structured-output / model-binding helpers LangGraph already depends on)
- `mcp` (official Python SDK) — for the in-repo MCP server exposing K8s read + scoped-write tools
- `kubernetes` (official Python client) — used inside MCP tools; never called directly from agent code
- `fastapi` + `uvicorn` — webhook intake, mock-Slack receiver, interactive callback endpoints
- `pydantic` v2 — typed state, structured LLM outputs, shared Report schema, MCP tool schemas
- `anthropic` (primary) — Haiku-class for Router/Reporter, Sonnet-class for Experts. Model IDs configurable.
- `sqlalchemy` + `asyncpg` (prod) / `aiosqlite` (dev) — audit table and checkpoint storage
- `structlog` — correlation-ID-bound structured logging
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

- TTFT (first chat message rendered to the user) ≤ 3 s.
- p50 end-to-end (webhook → report delivered) ≤ 30 s.
- p95 end-to-end ≤ 60 s.
- Solver run + verification ≤ 60 s (default verification window from spec assumptions).

**Constraints**:

- Per-incident cost ceiling enforced fail-closed (spec FR-029); default $0.50 / 50k tokens — tunable per tenant.
- Kill switch halts all in-flight Solver actions for a tenant within 5 s (FR-030).
- Zero unredacted secrets reach the LLM or the audit record (SC-009).
- All MCP write tools are individually scoped per action type and per namespace; no broad cluster-admin token.
- All retries are bounded with jitter; no unbounded loops.

**Scale/Scope**:

- MVP: single tenant, single cluster, up to ~100 incidents/day.
- Concurrent in-flight incidents: target 10; serialize remediations per target (FR-026).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Evaluated against `.specify/memory/constitution.md` v1.1.0 (Principles I–IX).

| # | Principle | Status | Notes |
|---|---|---|---|
| I | Safety-First Autonomy | ✅ Compliant | Read-only by default (MCP read tools). Mutations gated on HITL approval via LangGraph `interrupt` (spec FR-015). Catalog-only writes (FR-011 / FR-021). Reversal recipe captured pre-flight and stored (FR-022). Per-target serialization (FR-026). Kill switch within 5 s (FR-030). No `--force` / `--grace-period=0` bypass. |
| II | Cost-Conscious by Design | ✅ Compliant | Tiered model selection: Router on Haiku-class, Experts on Sonnet-class, Reporter on Haiku-class. Per-incident token + cost ceiling (FR-029). Cached/summarized state passes between nodes via LangGraph state (not re-prompted). Cost is recorded per-stage in audit. |
| III | Developer Experience as a Product | ✅ Compliant | Single Slack-style chat message with TL;DR + cited evidence + interactive controls (FR-013/FR-014). Latency SLOs from constitution IX adopted directly (see Performance Goals). One-command local setup via `docker-compose up` (quickstart.md). |
| IV | Evidence-Backed Triage (NON-NEGOTIABLE) | ✅ Compliant | Router cites (FR-007), Experts cite (FR-010), 100% of user-facing claims cited (SC-005). Hallucination tests run in CI. |
| V | Observability & Reversibility | ✅ Compliant | Single `correlation_id` joins every stage (FR-028). Audit table records prompt, response, model, tokens, cost, redactions, pre/action/post-state, and reversal recipe (FR-022). LangGraph checkpoints provide an additional crash-recovery audit surface. |
| VI | Code Quality | ✅ Compliant | `ruff` + `black` + `mypy --strict` in CI. Cyclomatic complexity cap (15) enforced via `ruff` `C901`. Dependency vetting captured in research.md. Two-reviewer rule applies to PRs touching MCP write tools, redaction, budget enforcement, and authorization. |
| VII | Testing Standards (NON-NEGOTIABLE) | ✅ Compliant | Coverage floors 85% / 95% enforced in CI per safety-critical module list (redaction, budget, approval, solver-guard, MCP-write). LLM eval suite for Router and per-Expert. Hallucination test on every Expert response. Refusal-path + reversal-recipe tests for every MCP write tool. |
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
│   │   └── health.py
│   ├── graph/
│   │   ├── builder.py                 # build_graph() — assembles nodes + conditional edges + interrupt
│   │   ├── state.py                   # WorkflowState (TypedDict)
│   │   └── nodes/
│   │       ├── ingest.py              # Node 1: dedup + MCP search_pod_logs
│   │       ├── router.py              # Node 2: structured-output classifier
│   │       ├── experts/
│   │       │   ├── application.py     # Node 3a
│   │       │   ├── network.py         # Node 3b
│   │       │   └── _base.py           # shared expert protocol
│   │       ├── reporter.py            # Node 4: assemble Report + send to slack-mock
│   │       └── solver.py              # Node 5 (post-interrupt): execute via MCP write tools
│   ├── mcp_client.py                  # Thin wrapper around the MCP Python SDK
│   ├── llm.py                         # Tiered model selection + structured output helpers
│   ├── redaction.py                   # Secret-shaped pattern redaction (applied at tool boundary + pre-LLM)
│   ├── budget.py                      # Per-incident token/$ ceiling enforcement (fail-closed)
│   ├── audit.py                       # Append-only audit writes keyed by correlation_id
│   ├── auth.py                        # Approver role check
│   └── settings.py                    # Pydantic Settings (env-driven)
│
├── mcp_server/                        # Separate process: in-repo MCP server
│   ├── server.py                      # MCP server entrypoint (stdio dev / HTTP-SSE prod)
│   ├── tools/
│   │   ├── search_pod_logs.py         # READ — fetch + local grep pre-filter
│   │   ├── get_pod.py                 # READ — pod status / phase / restart count
│   │   ├── restart_pod.py             # WRITE — scoped to namespace via per-tool SA
│   │   ├── rollback_deployment.py     # WRITE — rollout undo
│   │   ├── scale_deployment.py        # WRITE — bounded scale range
│   │   ├── delete_pod.py              # WRITE — used as "reschedule" trigger; never with --force
│   │   └── _guards.py                 # admission / PDB / quota refusal handling, no --force ever
│   └── auth.py                        # Per-tool ServiceAccount loading + scope check
│
└── shared/                            # Cross-package contracts and catalogs
    ├── schemas.py                     # Report, RoutingDecision, ExpertDiagnosis, ProposedFix, ApprovalEvent, SolverRun
    ├── catalog.py                     # Allowed-remediation catalog (ID → action signature + reversal recipe template)
    ├── labels.py                      # Single source of truth for domain / severity / outcome strings
    └── correlation.py                 # correlation_id generation + contextvar propagation

tests/
├── contract/
│   ├── test_alertmanager_payload.py
│   ├── test_mcp_tools.py              # both read and write tool contracts
│   └── test_slack_mock_protocol.py
├── integration/
│   ├── test_e2e_application_flow.py   # webhook → report → approve → solver → success
│   ├── test_e2e_network_flow.py
│   ├── test_e2e_unknown_low_confidence.py
│   ├── test_hitl_gating.py            # no mutation without approval; expiry; role-check
│   └── test_kill_switch.py
├── eval/
│   ├── router_golden.jsonl            # labeled router classification fixtures
│   ├── application_expert_golden.jsonl
│   ├── network_expert_golden.jsonl
│   ├── hallucination_suite.py         # every claim must cite an excerpt present in the input
│   └── runner.py
└── unit/
    ├── test_redaction.py
    ├── test_budget.py
    ├── test_solver_guards.py          # refusal-path + reversal-recipe tests
    ├── test_audit_record.py
    └── test_graph_state_transitions.py

deploy/
├── docker-compose.yml                 # agent + mcp + postgres + slack-mock + kind (dev)
├── Dockerfile.agent
├── Dockerfile.mcp
└── k8s/
    ├── agent-deployment.yaml
    └── mcp-deployment.yaml

docs/
└── (created by /speckit-tasks polish phase if needed)
```

**Structure Decision**: Python monorepo with two installable packages — `src/agent` (the FastAPI + LangGraph service) and `src/mcp_server` (the MCP tool server) — sharing `src/shared` for the cross-package contracts (Report schema, catalog, label vocabulary). This is closest to the "web application" option in the template (backend + a second runtime), but no frontend; the mock-Slack is a tiny FastAPI receiver living inside `deploy/` rather than its own package. Two packages instead of one keeps the write-tools (and their per-tool ServiceAccounts) physically separated from the agent process, which directly supports Principles I and V — the agent cannot mutate a cluster without going through the MCP boundary.

## Complexity Tracking

> All Constitution Check gates evaluated **compliant**. No entries required.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| — | — | — |
