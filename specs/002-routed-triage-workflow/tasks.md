---

description: "Task list — Person 3 scope (Webhook + HITL + Database layer)"
---

# Tasks — Person 3: The Platform Engineer ("The Webhook Guy")

**Input**: Design documents from `/specs/002-routed-triage-workflow/`

**Scope**: This file is a *narrowed* slice of the original 111-task team plan, scoped to the central plumbing one engineer owns during the hackathon. Everything Person 1 (LangGraph nodes / LLM agents), Person 2 (MCP server + tools), and Person 4 (Slack-mock UI / chat surface) own has been removed.

**Hackathon simplifications** (override plan.md for this slice only):

- **SQLite only** via `aiosqlite` + plain SQL. No Postgres, no SQLAlchemy. The audit table lives in the same SQLite file as the LangGraph checkpointer.
- **LangGraph's native `AsyncSqliteSaver`** for state persistence — no custom checkpointer code.
- **`uuid.uuid4().hex`** for `correlation_id` (not UUIDv7) — keeps the dep surface minimal.
- **No pytest gates** — write a smoke script (`scripts/smoke.py`) instead. Reinstate the test pyramid post-hackathon.
- **Stay on branch `003-core-api-setup`** — no new branch.
- The graph nodes Person 1 will fill in are stubbed as no-op placeholders so the graph compiles and the `interrupt`/resume flow can be exercised end-to-end without their code.

## Deliverables (the 4 components)

| # | Component | Owner module(s) |
|---|---|---|
| 1 | Alert Intake — `POST /webhook/alertmanager` | `src/agent/api/webhook.py` |
| 2 | Checkpointer — LangGraph `AsyncSqliteSaver` | `src/agent/checkpointer.py` + `src/agent/graph/builder.py` |
| 3 | Audit Trail — `audit_log` table + `log_audit_event()` | `src/agent/db.py` + `src/agent/audit.py` |
| 4 | HITL Resume — `POST /callbacks/slack/{approve\|reject}` + expiry watcher | `src/agent/api/callbacks.py` + `src/agent/api/expiry.py` |

## Cross-team integration points

- **To Person 1 (LangGraph):** `build_graph(checkpointer)` in `src/agent/graph/builder.py` returns a compiled graph with the `interrupt` after the Reporter node and conditional resume on `approval_status`. Person 1 swaps the placeholder node functions in `src/agent/graph/nodes/_placeholders.py` for real implementations; the wiring stays mine.
- **To Person 2 (MCP):** nothing direct — the agent calls MCP only inside Person 1's nodes.
- **To Person 4 (Slack UI):** the inbound HTTP contracts in `contracts/slack_mock.md`. They `POST /callbacks/slack/approve` (or reject) with the documented body + HMAC. The `actor.roles` field MUST contain the configured approver role.

---

## Tasks

### Phase A — Foundation (shared + agent infra)

- [ ] **P3-01** Reuse existing `src/shared/labels.py` and `src/shared/schemas.py` (already merged) — no edits needed.
- [ ] **P3-02** Add `src/shared/errors.py` — single user-facing error template `error_response(machine_token, message, correlation_id=None)` per Principle VIII / `contracts/slack_mock.md`.
- [ ] **P3-03** Add `src/shared/correlation.py` — `new_correlation_id()` returning `uuid4().hex`, plus a `contextvars.ContextVar` for propagation.
- [ ] **P3-04** Add `src/shared/__init__.py` package marker if missing.
- [ ] **P3-05** Add `src/agent/__init__.py` and `src/agent/settings.py` — pydantic-settings env loader (`ALERTMANAGER_HMAC_SECRET`, `SLACK_MOCK_SECRET`, `APPROVAL_TOKEN_SECRET`, `SQLITE_PATH`, `APPROVAL_WINDOW_MINUTES=30`, `DEDUP_WINDOW_MINUTES=10`, `APPROVER_ROLE="triage-approver"`).
- [ ] **P3-06** Add `src/agent/logging_config.py` — `structlog` configuration that injects `correlation_id` from the contextvar into every log record.

### Phase B — Database + audit

- [ ] **P3-07** Add `src/agent/db.py` — `aiosqlite` connection helpers, `init_db()` that creates two tables on startup:
  - `audit_log(id INTEGER PK AUTOINCREMENT, correlation_id TEXT, sequence_no INTEGER, stage TEXT, outcome TEXT, actor TEXT, payload TEXT, at TEXT)` plus index on `correlation_id` and `UNIQUE(correlation_id, sequence_no)`.
  - `incidents(correlation_id TEXT PK, dedup_fingerprint TEXT UNIQUE, source_alert_id TEXT, namespace TEXT, pod TEXT, status TEXT, received_at TEXT, last_seen_at TEXT, approval_deadline TEXT, proposed_fix_fingerprint TEXT)`.
  - The `AsyncSqliteSaver` creates its own checkpoint tables on first use in the same DB file.
- [ ] **P3-08** Add `src/agent/audit.py` — `async def log_audit_event(correlation_id, stage, outcome="ok", payload=None, actor=None)` that monotonically assigns `sequence_no` per `correlation_id` and writes one row. Stages from `contracts/audit_record.md`.

### Phase C — Checkpointer + graph skeleton

- [ ] **P3-09** Add `src/agent/checkpointer.py` — factory returning `langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver` bound to `settings.SQLITE_PATH`.
- [ ] **P3-10** Add `src/agent/graph/__init__.py` and `src/agent/graph/state.py` — `WorkflowState` TypedDict per data-model.md §WorkflowState.
- [ ] **P3-11** Add `src/agent/graph/builder.py` — `build_graph(checkpointer)` that wires `ingest → router → {experts | END} → reporter → INTERRUPT → {solver | END}` with placeholder node functions Person 1 will replace. Conditional edge after `router` uses `classification`; conditional edge after `reporter` uses `approval_status`.
- [ ] **P3-12** Add `src/agent/graph/nodes/__init__.py` and `src/agent/graph/nodes/_placeholders.py` — async no-op functions returning a partial `WorkflowState` so the graph compiles. Each calls `log_audit_event` with stage `<node>_placeholder` so the wiring is visible end-to-end.

### Phase D — Webhook intake

- [ ] **P3-13** Add `src/agent/api/__init__.py` — FastAPI `create_app()` factory whose `lifespan` runs `init_db()`, builds the graph, and starts the expiry watcher.
- [ ] **P3-14** Add `src/agent/api/health.py` — `GET /health` returning `{"status": "ok"}`.
- [ ] **P3-15** Add `src/agent/api/webhook.py` — `POST /webhook/alertmanager` per `contracts/alertmanager_webhook.md`:
  - HMAC-SHA256 verification of raw body using `ALERTMANAGER_HMAC_SECRET` (constant-time). Reject with 401 + audit `webhook_rejected`.
  - Parse Alertmanager v4 subset (groupKey, groupLabels.namespace, groupLabels.pod, alerts[0].startsAt, status). Reject with 400/422.
  - Compute dedup fingerprint = `sha256(groupKey|namespace|pod|floor(startsAt/600))`. If row exists with same fingerprint inside the dedup window, update `last_seen_at`, audit `incident_deduped`, return 202 with `deduplicated: true`.
  - Otherwise generate `correlation_id` (`uuid4().hex`), insert into `incidents`, audit `webhook_received`, kick off graph run as a background asyncio task, return 202 with `deduplicated: false`.

### Phase E — HITL callbacks (resume)

- [ ] **P3-16** Add `src/agent/auth.py` — `check_approver_role(actor_roles, action_type)` returning True iff `settings.APPROVER_ROLE` ∈ `actor_roles` (default mapping per research.md §R11).
- [ ] **P3-17** Add `src/agent/approval_token.py` — `issue_token(correlation_id, fingerprint, exp_unix)` and `verify_token(token, expected_correlation_id, expected_fingerprint)` using HMAC-SHA256. Token format: `<exp_unix>.<hex_sig>`. (Person 1's Solver pre-flight will call `verify_token`.)
- [ ] **P3-18** Add `src/agent/api/callbacks.py` — `POST /callbacks/slack/approve` and `POST /callbacks/slack/reject` per `contracts/slack_mock.md`, in this order:
  1. HMAC verify body w/ `SLACK_MOCK_SECRET` → 401 `signature_invalid`.
  2. Lookup incident by `correlation_id` → 404 `report_not_found`.
  3. Status guard (only `pending` accepts) → 409 `report_<status>`.
  4. Deadline check → 409 `approval_expired` + audit `approval_event(action=reject, reason=expired)`.
  5. Role check (approve only) → 403 `role_check_failed` + audit row with `role_check_passed=false`.
  6. Audit `approval_event(action=approve|reject, role_check_passed=true)`, flip incident status, then resume the LangGraph run via `graph.ainvoke(Command(resume={"approval_status": "APPROVED"|"REJECTED"}), config={"configurable": {"thread_id": correlation_id}})` in a background task. Issue and embed the signed approval token in the resume payload.
  7. Return 200 `{correlation_id, status}`.
- [ ] **P3-19** Add `src/agent/api/expiry.py` — async background loop (asyncio.create_task in `lifespan`) that every 30s scans `incidents WHERE status='pending' AND approval_deadline < now()`, flips them to `expired`, writes the audit row, and resumes the graph thread with `approval_status="EXPIRED"`.

### Phase F — Smoke test (no pytest gate for hackathon)

- [ ] **P3-20** Add `scripts/smoke.py` — boots the app in-process via `httpx.ASGITransport`, posts a signed fake Alertmanager payload, sleeps briefly so the graph hits its interrupt, posts a signed approve callback, then prints all `audit_log` rows joined by `correlation_id`. This is the integration handshake I demo to teammates.

---

## Out of scope for me (other people own these)

- Person 1: every node body in `src/agent/graph/nodes/{ingest,router,experts/*,reporter,solver}.py`, `llm.py`, redaction, budget, eval suite, hallucination tests.
- Person 2: every file under `src/mcp_server/` — read tools, write tools, kill switch, ServiceAccount auth, MCP transport.
- Person 4: `deploy/slack_mock/` — the chat surface that POSTs to my callbacks.
- Shared (post-hackathon): `docker-compose.yml`, `kind` fixtures, perf benchmarks, eval golden sets, CI workflow, Postgres migration.
