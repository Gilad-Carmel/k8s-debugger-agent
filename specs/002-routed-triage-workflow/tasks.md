---

description: "Task list for feature 002-routed-triage-workflow"
---

# Tasks: Routed Kubernetes Incident Triage and Auto-Remediation Workflow

**Input**: Design documents from `/specs/002-routed-triage-workflow/`

**Prerequisites**: plan.md ✓ • spec.md ✓ • research.md ✓ • data-model.md ✓ • contracts/ ✓ • quickstart.md ✓

**Tests**: INCLUDED. Tests are explicitly mandated by spec.md (Principle IV/VII), plan.md (coverage ≥85% pure logic / ≥95% safety-critical), and research.md §R10 (CI gates: lint, type, unit, contract, integration, eval, hallucination, perf).

**Organization**: Tasks are grouped by user story so each story can be implemented and tested independently. US1 + US2 are both P1 and together form the MVP (assisted triage with read-only behaviour by default). US3 + US4 are P2 (auto-remediation + auditability).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: User story this task belongs to (US1, US2, US3, US4) — phase-only tasks have no story label
- Every task includes the exact file path it touches

## Path Conventions

Python monorepo with two installable packages, per plan.md §Project Structure:

- `src/agent/` — FastAPI + LangGraph runner
- `src/mcp_server/` — separate MCP tool server (per-tool ServiceAccounts)
- `src/shared/` — cross-package contracts (schemas, catalog, labels, errors, correlation)
- `tests/` — `contract/` • `integration/` • `eval/` • `unit/` • `perf/` • `fixtures/`
- `deploy/` — compose stack, Dockerfiles, slack-mock, kind fixtures

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Repo skeleton, language toolchain, lint/type/test runners, container build, dev compose stack.

- [ ] T001 Create repo source layout: `src/agent/{api,graph/nodes/experts}`, `src/mcp_server/tools`, `src/shared`, `tests/{contract,integration,eval,unit,perf,fixtures}`, `deploy/{slack_mock,k8s,sql}` per plan.md §Project Structure
- [ ] T002 Initialize Python 3.11 project in `pyproject.toml` with runtime deps from research.md §R13 (langgraph≥0.2, langchain-core, langchain-openai, openai, mcp, kubernetes, fastapi, uvicorn, pydantic v2, sqlalchemy, asyncpg, aiosqlite, structlog, opentelemetry-api/sdk) and dev deps (pytest, pytest-asyncio, respx, deepeval, ruff, black, mypy)
- [ ] T003 [P] Configure `ruff` (with `C901` cyclomatic-cap 15 per Principle VI) and `black` in `pyproject.toml`
- [ ] T004 [P] Configure `mypy --strict` in `pyproject.toml` with per-package overrides for `tests/`
- [ ] T005 [P] Configure `pytest` + `pytest-asyncio` + coverage gates in `pyproject.toml` (per-module floors from plan.md Principle VII)
- [ ] T006 [P] CI workflow at `.github/workflows/ci.yml` running lint, mypy, unit, contract, integration, eval, hallucination, perf, audit-completeness; coverage gates enforced
- [ ] T007 [P] Create `Makefile` with targets `dev`, `test`, `test-unit`, `eval`, `perf`, `audit`, `audit-check`, `smoke`, `clean` per quickstart.md §Running the test suite
- [ ] T008 [P] Dev env template at `deploy/dev.env` (`LLM_BASE_URL`, `LLM_MODEL`, `LLM_ROUTER_MODEL`, `LLM_EXPERT_MODEL`, `LLM_API_KEY`, `ALERTMANAGER_HMAC_SECRET`, `SLACK_MOCK_SECRET`, `BUDGET_TOKENS_PER_INCIDENT`)
- [ ] T009 [P] Dockerfile for the agent service at `deploy/Dockerfile.agent`
- [ ] T010 [P] Dockerfile for the MCP server at `deploy/Dockerfile.mcp`
- [ ] T011 docker-compose stack at `deploy/docker-compose.yml` (agent, mcp-server, slack-mock, postgres, kind init container, fixture loader)

**Checkpoint**: `make dev` brings the stack up; container builds succeed.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Cross-cutting modules every user story depends on — shared schemas, catalog, redaction, budget, audit infra, DB, LangGraph + checkpointer skeleton, LLM client, MCP client/server skeleton, telemetry, kill switch, logging.

**⚠️ CRITICAL**: No user-story phase begins until this checkpoint is reached.

### Shared contracts (cross-package)

- [x] T012 [P] Label vocabulary (Domain, Confidence, ReportStatus, SolverOutcome, ActionType enums) in `src/shared/labels.py` per data-model.md §Common types
- [x] T013 [P] Single user-facing error template in `src/shared/errors.py` (Principle VIII: machine_token + human readable)
- [x] T014 [P] Correlation ID (UUIDv7) generation + `contextvars` propagation in `src/shared/correlation.py`
- [x] T015 [P] Allowed-remediation catalog with parameter schemas and the fixed Forward → Inverse Action mapping in `src/shared/catalog.py` per spec.md §Assumptions and data-model.md §Allowed-remediation catalog
- [x] T016 Pydantic v2 schemas (`Target`, `TimeWindow`, `LogExcerpt`, `FilteredEvidence`, `RoutingDecision`, `ExpertDiagnosis`, `ProposedFix`, `ReversalRecipe`, `Report`, `ApprovalEvent`, `SolverRun`, `Incident`, `ToolError`) in `src/shared/schemas.py` per data-model.md §Entities (depends on T012, T015)

### Agent core infra

- [x] T017 [P] Pydantic Settings (env-driven config: LLM model IDs, budget ceilings, approval window, dedup window, redaction patterns) in `src/agent/settings.py`
- [ ] T018 [P] Structured logging configuration bound to correlation contextvar in `src/agent/logging_config.py` (structlog)
- [ ] T019 [P] OpenTelemetry tracer + node-entry/exit + MCP-call span helpers in `src/agent/telemetry.py` (Principle IX)
- [x] T020 [P] Double-pass regex redaction (boundary + pre-LLM) in `src/agent/redaction.py` per research.md §R7 (bearer/AWS/GCP/Azure/SA-token/JWT/DB-conn-string patterns); 95% coverage tier
- [ ] T021 [P] Per-incident token + USD-micros budget enforcement (fail-closed) in `src/agent/budget.py` per research.md §R8; 95% coverage tier
- [ ] T022 SQL migration for the append-only `audit_record` table at `deploy/sql/001_audit_record.sql` per contracts/audit_record.md (schema + indexes + REVOKE UPDATE/DELETE/TRUNCATE)
- [ ] T023 SQLAlchemy 2.x engine + async session factory (postgres prod / sqlite dev switch) in `src/agent/db.py` (depends on T022)
- [ ] T024 Append-only audit writer (one row per stage, sequence_no monotonic per correlation_id) in `src/agent/audit.py` per contracts/audit_record.md (depends on T023)
- [x] T025 [P] WorkflowState TypedDict in `src/agent/graph/state.py` per data-model.md §WorkflowState (depends on T016)
- [x] T026 LangGraph builder skeleton + checkpointer wiring (`langgraph.checkpoint.postgres` prod / `langgraph.checkpoint.sqlite` dev) in `src/agent/graph/builder.py` (depends on T023, T025) — nodes registered as no-ops, conditional edges scaffolded
- [ ] T027 Local-LLM client + structured-output helper (`ChatOpenAI` from `langchain-openai` with configurable `base_url`/`api_key`; router profile: low max_tokens + temp=0; expert profile: full-context + temp=0.2; JSON-mode fallback; model recorded for audit; token accounting hook) in `src/agent/llm.py` per research.md §R2 (depends on T021, T024)
- [ ] T028 [P] FastAPI app factory + `/health` endpoint in `src/agent/api/health.py` and app wiring in `src/agent/api/__init__.py`

### MCP server infra

- [x] T029 MCP server entrypoint (stdio dev / HTTP-SSE prod transports) in `src/mcp_server/server.py` per research.md §R5
- [x] T030 [P] Per-tool ServiceAccount loader + scope check in `src/mcp_server/auth.py` (each write tool gets its own kube token; the agent process holds none)
- [ ] T031 Thin MCP client wrapper used by the agent in `src/agent/mcp_client.py` (bounded jittered retries on idempotent reads per research.md §R8; no retry on writes)

### Kill switch + tenant guard

- [x] T032 [P] MCP `POST /admin/kill-switch` endpoint (IP-restricted, ≤5s propagation) in `src/mcp_server/admin.py` per contracts/mcp_tools.md §Kill switch
- [ ] T033 [P] Agent-side kill-switch cache + check helper in `src/agent/kill_switch.py`

### Foundational unit tests

- [x] T034 [P] Unit tests for redaction patterns (positive + negative) in `tests/unit/test_redaction.py` (95% coverage gate)
- [ ] T035 [P] Unit tests for budget fail-closed behavior in `tests/unit/test_budget.py` (95% coverage gate)
- [x] T036 [P] Unit tests for catalog Forward → Inverse Action mapping table in `tests/unit/test_inverse_actions.py`
- [ ] T037 [P] Unit tests for audit append-only invariants (REVOKE enforcement, sequence_no monotonicity, redaction in `prompt`/`response`) in `tests/unit/test_audit_record.py`

**Checkpoint**: Foundation ready — user story implementation can now begin.

---

## Phase 3: User Story 1 — Webhook to triage report in chat (Priority: P1) 🎯 MVP

**Goal**: Webhook → contextual log pre-filter + events + status → routed domain classification → domain Expert diagnosis with cited evidence → single Slack-style report in chat with a proposed fix and an `Approve Remediation` button (button is rendered only; no execution yet — that is US2/US3).

**Independent Test**: Fire a synthetic webhook against a fixture pod with seeded `connection-refused` logs. Verify a chat report arrives within p50≤30s naming Network domain, citing the refused-connection lines, listing a plausible fix, and showing the Approve button in a not-yet-clicked state. No cluster mutation occurs.

### Contract tests for User Story 1 (write FIRST; ensure they FAIL before implementation)

- [ ] T038 [P] [US1] Contract test for Alertmanager webhook (HMAC, 202/400/401/422/503, dedup flag) in `tests/contract/test_alertmanager_payload.py` per contracts/alertmanager_webhook.md
- [ ] T039 [P] [US1] Contract test for MCP read tools (`search_pod_logs`, `get_pod_events`, `get_pod`) in `tests/contract/test_mcp_tools.py` per contracts/mcp_tools.md
- [ ] T040 [P] [US1] Contract test for slack-mock outbound `POST /messages` (Block Kit shape, `actions` omitted when no fix) in `tests/contract/test_slack_mock_protocol.py` per contracts/slack_mock.md

### Implementation for User Story 1

- [x] T041 [P] [US1] MCP read tool `search_pod_logs` with additive contextual N-line window + K-line fallback + boundary redaction in `src/mcp_server/tools/search_pod_logs.py` (FR-004, R7)
- [x] T042 [P] [US1] MCP read tool `get_pod_events` (last N minutes of Kubernetes events for target) in `src/mcp_server/tools/get_pod_events.py`
- [x] T043 [P] [US1] MCP read tool `get_pod` (phase, container states, restart counts, resource_version) in `src/mcp_server/tools/get_pod.py`
- [ ] T044 [US1] Webhook intake `POST /webhook/alertmanager` (HMAC verify constant-time, parse Alertmanager v4 subset, dedup fingerprint via R12, 202 with `correlation_id`) in `src/agent/api/webhook.py` (depends on T016, T024, T028)
- [ ] T045 [US1] Ingest node: TTFT ack emitted to slack-mock before any LLM call; calls the three MCP read tools concurrently; populates `evidence` on `WorkflowState` in `src/agent/graph/nodes/ingest.py` (depends on T031, T041-T043)
- [x] T046 [US1] Router node (fast-sampling profile, structured pydantic output via `.with_structured_output()`: `domain`, `confidence`, `cited_evidence ≥1` unless Unknown, `runners_up`) in `src/agent/graph/nodes/router.py` (FR-005..FR-008; depends on T027)
- [x] T047 [US1] Expert base protocol + shared prompt builder in `src/agent/graph/nodes/experts/_base.py`
- [x] T048 [P] [US1] Application Expert node in `src/agent/graph/nodes/experts/application.py` (full-context profile via `llm.py`; produces `ExpertDiagnosis` with cited evidence + `ProposedFix | None`)
- [ ] T049 [P] [US1] Network Expert node in `src/agent/graph/nodes/experts/network.py`
- [ ] T050 [P] [US1] Database Expert node in `src/agent/graph/nodes/experts/database.py`
- [ ] T051 [US1] Reporter node: assemble `Report`, render Block Kit blocks, POST to slack-mock, set `delivered_at` + `approval_deadline`, persist `report_delivered` audit row in `src/agent/graph/nodes/reporter.py` (FR-013, FR-014; depends on T024)
- [ ] T052 [US1] Slack-mock FastAPI service (`POST /messages`, `POST /messages/{id}/approve|reject` → signed callback to agent) in `deploy/slack_mock/app.py` + `deploy/slack_mock/Dockerfile`
- [ ] T053 [US1] Wire the graph in `src/agent/graph/builder.py`: `ingest → router → {application|network|database|unknown-short-circuit} → reporter`; conditional edge keyed on `classification` only (depends on T026, T045, T046, T047-T050, T051)
- [ ] T054 [US1] Fixture cluster manifests (App/Network/Database failure modes) at `tests/fixtures/cluster/{application,network,database}.yaml`
- [ ] T055 [US1] `fire_webhook` helper + HMAC sign script at `tests/fixtures/fire_webhook.py` and `scripts/sign.sh`

### Eval + hallucination harness for US1

- [ ] T056 [P] [US1] Router golden labelled set at `tests/eval/router_golden.jsonl` and eval runner at `tests/eval/runner.py` (top-1 ≥85%, top-2 ≥97% per SC-001)
- [ ] T057 [P] [US1] Application expert golden set at `tests/eval/application_expert_golden.jsonl`
- [ ] T058 [P] [US1] Network expert golden set at `tests/eval/network_expert_golden.jsonl`
- [ ] T059 [P] [US1] Database expert golden set at `tests/eval/database_expert_golden.jsonl`
- [ ] T060 [P] [US1] Hallucination test (every Expert claim must quote-match `cited_evidence`) at `tests/eval/hallucination_suite.py` (Principle IV NON-NEGOTIABLE, SC-005)

### Integration tests for US1

- [ ] T061 [P] [US1] E2E application flow (webhook → report; no approval/execution) at `tests/integration/test_e2e_application_flow.py`
- [ ] T062 [P] [US1] E2E network flow at `tests/integration/test_e2e_network_flow.py`
- [ ] T063 [P] [US1] E2E database flow at `tests/integration/test_e2e_database_flow.py`
- [ ] T064 [P] [US1] E2E unknown / low-confidence flow (no fix, Approve button absent) at `tests/integration/test_e2e_unknown_low_confidence.py`
- [ ] T065 [P] [US1] Webhook auth rejection path (no LLM call, audit row written) at `tests/integration/test_webhook_auth_rejection.py`

**Checkpoint**: US1 is fully functional and independently testable. Reports are delivered with cited evidence and a not-yet-clicked Approve button. No cluster mutation is possible (no callback handler, no Solver yet).

---

## Phase 4: User Story 2 — HITL approval before any remediation (Priority: P1)

**Goal**: The Approve / Reject buttons in the US1 report become a real control surface. Authorized approval is required, per-fix-per-incident, role-checked, time-bounded, and audited. Until approval, the LangGraph run is paused at an `interrupt`; no mutation is ever issued.

**Independent Test**: Run US1 to produce a pending report. Verify (a) no mutation occurs without a click, (b) approval expires after the deadline and post-deadline clicks are rejected with `approval_expired`, (c) an unauthorized user's click is rejected with `role_check_failed` and is itself audited, (d) a second incident requires its own approval.

### Contract test for US2

- [ ] T066 [P] [US2] Contract test for inbound `POST /callbacks/slack/approve|reject` (HMAC, 401/403/404/409 with stable `error` tokens, audit side-effects) extending `tests/contract/test_slack_mock_protocol.py` (same file as T040 — must run sequentially with T040)

### Implementation for US2

- [ ] T067 [P] [US2] Approver role-check (default mapping: `triage-approver` → all MVP catalog actions per research.md §R11) in `src/agent/auth.py` (95% coverage tier)
- [ ] T068 [P] [US2] Short-lived signed approval token (carries `proposed_fix_fingerprint` + `correlation_id` + `exp`) issuer/verifier in `src/agent/approval_token.py` (used by Solver pre-flight per contracts/mcp_tools.md)
- [ ] T069 [US2] Approval callbacks `POST /callbacks/slack/approve|reject` (HMAC verify → resolve Report → status guard → deadline check → role check → persist `ApprovalEvent` → flip Report status → `Command(resume=...)` graph) in `src/agent/api/callbacks.py` (depends on T024, T026, T067, T068)
- [ ] T070 [US2] LangGraph `interrupt` after Reporter + conditional resume edge keyed on `approval_status` only, in `src/agent/graph/builder.py` (extends T053; rejected/expired terminate without Solver)
- [ ] T071 [US2] Approval-deadline watcher (background task that flips PENDING → EXPIRED at `approval_deadline` and writes an `approval_event` audit row with `action: reject, reason: expired`) in `src/agent/api/expiry.py`
- [ ] T072 [US2] Slack-mock click → signed callback handler in `deploy/slack_mock/app.py` (extends T052; same file — must run sequentially with T052)

### Unit + integration tests for US2

- [ ] T073 [P] [US2] Unit tests for role check (positive + negative + unknown role) in `tests/unit/test_auth.py` (95% coverage tier)
- [ ] T074 [P] [US2] Unit tests for approval-token signing/verification (tampering, expiry, fingerprint mismatch) in `tests/unit/test_approval_token.py`
- [ ] T075 [P] [US2] Integration test HITL gating (no mutation without approval; expiry; role-fail; second incident requires its own approval; reject path) in `tests/integration/test_hitl_gating.py`

**Checkpoint**: MVP complete. US1 + US2 together deliver assisted-triage + safety gate. The platform cannot mutate the cluster under any condition (no Solver wired yet).

---

## Phase 5: User Story 3 — Solver executes the approved fix and reports the outcome (Priority: P2)

**Goal**: On an authorized approval, a deterministic (NO-LLM) Solver node executes the frozen `ProposedFix` through a scoped MCP write tool, captures a pre-state snapshot carrying the FR-022-mandated fields, verifies post-state within the window, computes the Inverse Action from pre-state via the fixed catalog mapping, and posts a follow-up message reporting outcome (`success`/`partial`/`failure`) plus the Inverse Action (or `None` for transient actions).

**Independent Test**: With US1 + US2 in place, fire an incident whose proposed fix is `restart-pod`, approve as a `triage-approver`, verify the pod is restarted on the fixture cluster, verify the follow-up message reports `success` with `Inverse Action: None`, and verify the audit chain contains `solver_preflight → mcp_write → solver_postcheck`.

### Contract test for US3

- [ ] T076 [P] [US3] Contract test for MCP write tools (approval_token validation, fingerprint claim, admission/PDB refusal returns `admission_denied`, no `--force` retry) at `tests/contract/test_mcp_write_tools.py` per contracts/mcp_tools.md

### Implementation for US3

- [x] T077 [P] [US3] MCP write tool `restart_pod` with pre-flight token+fingerprint check, default-grace-period delete, post-state verification window in `src/mcp_server/tools/restart_pod.py` (FR-020..FR-025)
- [x] T078 [P] [US3] MCP write tool `rollback_deployment` (verifies `to_revision` exists; `pre_state.current_revision ≠ post_state.current_revision` on `applied`) in `src/mcp_server/tools/rollback_deployment.py`
- [x] T079 [P] [US3] MCP write tool `scale_deployment` (tenant `[min,max]` bound enforced server-side; `out_of_bounds` error) in `src/mcp_server/tools/scale_deployment.py`
- [x] T080 [P] [US3] MCP write tool `delete_pod_to_reschedule` (admission/PDB-respecting; NEVER `--force`, NEVER `--grace-period=0`) in `src/mcp_server/tools/delete_pod_to_reschedule.py`
- [x] T081 [US3] Write-tool guards (admission/PDB/quota refusal handler, kill-switch check, no-force enforcement) shared across write tools in `src/mcp_server/tools/_guards.py` (95% coverage tier; depends on T030, T033)
- [ ] T082 [P] [US3] Per-target Solver serialization lock (no concurrent mutations on the same resource per FR-026) in `src/agent/solver_lock.py`
- [ ] T083 [US3] Solver node (NO LLM): verify `proposed_fix.fingerprint` against frozen Report; capture pre-state via `get_pod`/Deployment read; refuse with `failure: pre-state-incomplete` if FR-022 fields missing; call the matching MCP write tool with the signed approval token; wait verification window; capture post-state; compute Inverse Action via catalog mapping over `pre_state`; build `SolverRun` in `src/agent/graph/nodes/solver.py` (depends on T015, T016, T024, T031, T068, T077-T082)
- [ ] T084 [US3] Reporter follow-up message (success/partial/failure + post-state summary + Inverse Action; on `partial` surface Inverse prominently) extending `src/agent/graph/nodes/reporter.py` (extends T051 — same file, run sequentially)
- [ ] T085 [US3] Wire Solver into graph (resume after interrupt when `approval_status == APPROVED` → Solver → Reporter follow-up; APPROVED+catalog-mismatch path terminates with `failed`) in `src/agent/graph/builder.py` (extends T070 — same file, run sequentially)

### Unit + integration tests for US3

- [ ] T086 [P] [US3] Unit tests for Solver guards (refusal paths: admission, PDB, kill-switch, fingerprint mismatch, pre-state-incomplete) at `tests/unit/test_solver_guards.py` (95% coverage tier)
- [ ] T087 [P] [US3] Unit tests for Inverse Action computation per catalog entry (Forward → Inverse table coverage) at `tests/unit/test_inverse_actions.py` (extends T036 — same file, run sequentially)
- [ ] T088 [P] [US3] Unit tests for per-target serialization lock at `tests/unit/test_solver_lock.py`
- [ ] T089 [P] [US3] Integration test: Solver success path (`restart-pod` approved → executed → success follow-up with Inverse `None`) at `tests/integration/test_solver_success.py`
- [ ] T090 [P] [US3] Integration test: Solver `partial` path (action applies but post-state verification fails) at `tests/integration/test_solver_partial.py`
- [ ] T091 [P] [US3] Integration test: Solver admission-denied refusal (PDB blocks delete; tool returns `admission_denied`, no `--force` retry) at `tests/integration/test_solver_admission_denied.py`
- [ ] T092 [P] [US3] Integration test: kill switch halts in-flight Solver actions for tenant within 5s at `tests/integration/test_kill_switch.py` (FR-030)
- [ ] T093 [P] [US3] Integration test: late-substitution refusal (Expert re-ran post-approval, new fingerprint) → Solver refuses with `fingerprint_mismatch` at `tests/integration/test_solver_fingerprint_mismatch.py`

**Checkpoint**: Auto-remediation is functional. US1 + US2 + US3 deliver the full closed-loop on the four-action catalog. All mutations are gated, scoped, verified, and reversible (or explicitly transient).

---

## Phase 6: User Story 4 — Full audit trail (Priority: P2)

**Goal**: Every stage of every incident is recoverable from one query keyed by `correlation_id`. An on-call lead can reconstruct exactly what happened, who approved what, and how to undo it.

**Independent Test**: Run a full US1 → US3 scenario, then query `audit_record` by `correlation_id`. Verify every stage from `webhook_received` through `solver_postcheck` is present, ordered, contains pre-state with FR-022 fields, and contains the computed Inverse Action; verify no redaction-pattern matches in any `prompt`/`response`.

### Implementation for US4

- [ ] T094 [P] [US4] Audit-completeness invariant runner (rules 1-5 from contracts/audit_record.md §Invariants) at `tests/eval/audit_completeness.py`
- [ ] T095 [P] [US4] Audit query CLI (`make audit CORRELATION=...`) at `scripts/audit_query.py`
- [ ] T096 [P] [US4] Continuously running redaction-audit job (scans `prompt`/`response` for any pattern match; release-blocking) at `scripts/redaction_audit.py` (SC-009)
- [ ] T097 [P] [US4] Unit tests for graph state-machine transitions (Report status diagram from data-model.md §Status state machine) at `tests/unit/test_graph_state_transitions.py`
- [ ] T098 [P] [US4] Integration test: end-to-end audit chain join (webhook_received → mcp_read → router_decision → expert_diagnosis → report_delivered → approval_event → solver_preflight → mcp_write → solver_postcheck) at `tests/integration/test_audit_chain.py`
- [ ] T099 [P] [US4] Integration test: rejected incident audit chain ends at `approval_event(reject)` with no `mcp_write` row (SC-004) at `tests/integration/test_audit_rejected_path.py`

**Checkpoint**: All four user stories are independently functional. Audit completeness is enforced in CI.

---

## Phase 6b: Edge-Case Coverage (Cross-cutting)

**Purpose**: Close the four edge-case test gaps identified in the spec §Edge Cases that are not covered by earlier story-specific integration tests.

- [ ] T108 [P] Integration test: duplicate webhook dedup path — fire the same webhook fingerprint twice within the dedup window; verify exactly one Report is delivered and the second webhook only updates `last_seen_at` (spec §Edge Cases "Duplicate webhooks", FR-003) at `tests/integration/test_duplicate_webhook.py`
- [ ] T109 [P] Integration test: "target not found" error path — fire a webhook referencing a pod that does not exist on the fixture cluster; verify the user sees a clear "target not found" error in chat, no LLM call is made, and an audit row records the rejection (spec §Edge Cases "Webhook for a resource that no longer exists") at `tests/integration/test_target_not_found.py`
- [ ] T110 [P] Integration test: concurrent incidents on the same target serialize — fire two incidents against the same pod with both approvals granted; verify the second Solver action only starts after the first completes and the audit trail shows non-overlapping `started_at` / `finished_at` (spec §Edge Cases "Concurrent incidents on the same target", FR-026) at `tests/integration/test_concurrent_target_serialization.py`
- [ ] T111 [P] Integration test + fallback implementation: chat surface unavailable — kill the slack-mock service mid-incident; verify the Report is persisted in the DB incident view, the delivery-failed alert is raised, and the triage run is NOT aborted (spec §Edge Cases "Slack/chat surface is unavailable") at `tests/integration/test_slack_unavailable_fallback.py`; implement the fallback delivery path in `src/agent/graph/nodes/reporter.py` (extends T051/T084 — same file, run sequentially)

**Checkpoint**: All spec §Edge Cases have integration-test coverage.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Performance budget enforcement, eval threshold gating, quickstart validation, documentation.

- [ ] T100 [P] Latency benchmark on recorded fixture (p50 ≤30s, p95 ≤60s, TTFT ≤3s; merge-blocking) at `tests/perf/test_latency_benchmark.py` (SC-003, Principle IX)
- [ ] T101 [P] Cost-budget benchmark (95% of incidents stay under per-incident ceiling per SC-007) at `tests/perf/test_cost_budget.py`
- [ ] T102 [P] Per-module coverage gate enforcement in CI (85% pure logic floor; 95% on `redaction`, `budget`, `auth`, `solver._guards`, MCP `tools/_guards`, MCP write tools) wired in `.github/workflows/ci.yml` (extends T006 — same file)
- [ ] T103 [P] CONTRIBUTING + architecture overview at `docs/architecture.md` and `docs/CONTRIBUTING.md` (two-reviewer rule list per Principle VI / plan.md Constitution Check row VI; include **contract-fixture refresh cadence**: fixtures refreshed when the upstream API contract changes or every 90 days, whichever is sooner; reviewer responsible for confirming cadence on each contract-test PR)
- [ ] T104 [P] Document the allowed-remediation catalog and the "new mutating tool" checklist at `docs/catalog.md`
- [ ] T105 [P] Operator runbook (kill switch, common failure modes from quickstart.md §Common failure modes) at `docs/runbook.md`
- [ ] T106 Run `quickstart.md` end-to-end on a clean checkout; confirm `make smoke` + Approve produces a `success` follow-up and a complete audit chain in under 5 minutes of human time
- [ ] T107 [P] Solver accuracy benchmark: labeled set at `tests/eval/solver_golden.jsonl` (fixture incidents with expected `outcome` per catalog action type) and extend `tests/eval/runner.py` to report Solver success-or-partial rate with CI gate ≥95% per SC-008; covers at least one fixture per catalog action (`restart-pod`, `rollback-deployment`, `scale-deployment`, `delete-pod-to-reschedule`)

---

## Dependencies & Execution Order

### Phase dependencies

- **Phase 1 (Setup)**: no dependencies — start immediately
- **Phase 2 (Foundational)**: depends on Phase 1 — BLOCKS every user story
- **Phase 3 (US1)** + **Phase 4 (US2)**: depend on Phase 2. US2 reuses US1's Reporter and slack-mock, so US2 is best done immediately after US1 lands. Both together are the P1 MVP.
- **Phase 5 (US3)**: depends on Phase 2 AND US2 (interrupt/resume + signed approval token + role check). Cannot ship before US2.
- **Phase 6 (US4)**: depends on Phase 5 for the full audit chain to be observable end-to-end.
- **Phase 6b (Edge-Case Coverage)**: depends on US1+US2+US3+US4. T111 (Slack unavailable fallback) extends reporter.py — run sequentially after T084.
- **Phase 7 (Polish)**: depends on US1+US2+US3+US4+Phase 6b being complete for meaningful perf/coverage/quickstart validation.

### Cross-story dependencies

- **US1 → US2**: US2's Approve callback reads the `Report` and `ProposedFix.fingerprint` produced by US1's Reporter. The slack-mock service from US1 (T052) is extended in US2 (T072) — both touch the same file; run sequentially.
- **US2 → US3**: US3's Solver depends on the signed approval token (T068), the per-fix fingerprint guard (T068), and the `Command(resume=...)` interrupt edge (T070).
- **US3 → US4**: US4's audit-completeness invariants depend on the full chain `solver_preflight → mcp_write → solver_postcheck` being emitted by US3.

### Within each user story

- Contract tests written FIRST and FAIL before implementation lands (Principle VII).
- Models / shared types before services; services before nodes; nodes before graph wiring.
- Story complete (acceptance scenarios pass) before moving on.

### Same-file sequencing (cannot be [P])

- `src/agent/graph/builder.py`: T026 → T053 → T070 → T085
- `src/agent/graph/nodes/reporter.py`: T051 → T084 → T111
- `tests/contract/test_slack_mock_protocol.py`: T040 → T066
- `deploy/slack_mock/app.py`: T052 → T072
- `tests/unit/test_inverse_actions.py`: T036 → T087
- `.github/workflows/ci.yml`: T006 → T102

### Parallel opportunities

- All Phase 1 [P] tasks run in parallel after T001+T002.
- All Phase 2 [P] shared-contract tasks (T012-T015) run in parallel after T001.
- All three MCP read tools (T041-T043) run in parallel.
- All three Expert nodes (T048-T050) run in parallel.
- All four MCP write tools (T077-T080) run in parallel.
- All eval golden sets + hallucination harness (T056-T060) run in parallel.
- All Integration tests within a story run in parallel (different files).

---

## Parallel Example: User Story 1

```bash
# After Foundational (Phase 2) is complete, launch in parallel:
Task: "T038 Contract test for Alertmanager webhook in tests/contract/test_alertmanager_payload.py"
Task: "T039 Contract test for MCP read tools in tests/contract/test_mcp_tools.py"
Task: "T040 Contract test for slack-mock outbound in tests/contract/test_slack_mock_protocol.py"

# Then in parallel:
Task: "T041 MCP read tool search_pod_logs in src/mcp_server/tools/search_pod_logs.py"
Task: "T042 MCP read tool get_pod_events in src/mcp_server/tools/get_pod_events.py"
Task: "T043 MCP read tool get_pod in src/mcp_server/tools/get_pod.py"

# Then in parallel after T047 base lands:
Task: "T048 Application Expert node in src/agent/graph/nodes/experts/application.py"
Task: "T049 Network Expert node in src/agent/graph/nodes/experts/network.py"
Task: "T050 Database Expert node in src/agent/graph/nodes/experts/database.py"
```

---

## Implementation Strategy

### MVP first (US1 + US2 together; both P1)

1. Phase 1: Setup
2. Phase 2: Foundational — **CRITICAL gate**
3. Phase 3: US1 — verify a Slack-shape report appears with cited evidence and an inert Approve button
4. Phase 4: US2 — make the Approve / Reject buttons real and audited
5. **STOP and VALIDATE**: At this point the platform delivers assisted triage with a safety gate and CANNOT mutate the cluster. Ship.

### Incremental delivery to full auto-remediation

6. Phase 5: US3 — wire the Solver and the four MCP write tools; ship behind a tenant flag
7. Phase 6: US4 — audit completeness as a CI gate; required for production rollout
8. Phase 7: Polish — perf benchmark, coverage gates, quickstart, docs

### Parallel team strategy

Once Phase 2 lands, US1, US2 can be drafted by two people in parallel against the same `WorkflowState` contract; US3's write tools (T077-T080) can be drafted by four people in parallel. The same-file sequencing list above identifies the points where work must serialize.

---

## Notes

- **No LLM in the Solver** (research.md §R2): Putting a model in the write path is a Principle I violation. The Solver is deterministic; the only "decision" it makes is fingerprint equality + pre-state-completeness + admission outcome.
- **Inverse Action is computed at execution time** from the captured pre-state via the fixed catalog mapping in `shared/catalog.py`. The Expert never authors an ad-hoc reversal script (spec.md §Clarifications 2026-05-14 Q2).
- **`--force` is never permitted** in any write path (FR-025).
- Two-reviewer rule (Principle VI) applies to PRs touching: `redaction`, `budget`, `auth`, `solver._guards`, MCP `tools/_guards`, all MCP write tools, `llm.py`, `settings.py:LLM_*`, and any plan change.
- Commit after each task or logical group. Stop at any checkpoint to validate the user story independently.

---

## Summary

- **Total tasks**: 111
- **Setup (Phase 1)**: 11
- **Foundational (Phase 2)**: 26 (T012-T037)
- **US1 (Phase 3, P1)**: 28 (T038-T065)
- **US2 (Phase 4, P1)**: 10 (T066-T075)
- **US3 (Phase 5, P2)**: 18 (T076-T093)
- **US4 (Phase 6, P2)**: 6 (T094-T099)
- **Edge-Case Coverage (Phase 6b)**: 4 (T108-T111)
- **Polish (Phase 7)**: 8 (T100-T107)
- **MVP scope (US1 + US2)**: 38 tasks across phases 3 + 4 (assisted triage + HITL safety gate — no cluster mutation possible)
- **Parallel opportunities**: ~79 tasks marked [P] across all phases
