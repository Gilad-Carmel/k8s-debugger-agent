# Implementation Plan: API ↔ Discord Bot Integration

**Branch**: `api-bot-integration` | **Date**: 2026-05-15 | **Spec**: *(no separate spec — this plan is self-contained)*

## Summary

Wire the existing FastAPI agent (`src/agent/`) and the existing Discord bot (`deploy/discord_bot/bot.py`) into a single working end-to-end loop. The gap today: `reporter_node` always skips HTTP delivery because it is a *sync* function called inside an *async* LangGraph invocation (`await graph.ainvoke()`), making `asyncio.get_running_loop()` succeed and the `deliver()` call branch-skip. Additionally, `proposed_fix_fingerprint` is never written to the `incidents` table, so the approval callback cannot verify it. The Discord bot's callback format is already compatible with the agent's `/callbacks/slack/{approve|reject}` endpoints.

## Technical Context

**Language/Version**: Python 3.11 (existing project)

**Primary Dependencies** (all already in the lockfile):
- `httpx` — async HTTP client used by `reporter.deliver()`
- `langgraph` — graph runner; supports `async def` nodes natively
- `fastapi` + `uvicorn` — agent and Discord bot HTTP layers
- `discord.py ≥ 2.3` — Discord bot (separate process)
- `pydantic-settings` — `AgentSettings` in `src/agent/settings.py`

**Storage**: SQLite via `aiosqlite` (dev); `incidents` table in `data/agent.sqlite3`

**Testing**: `pytest` + `pytest-asyncio`; `respx` for HTTP mocks

**Target Platform**: local dev (two processes: agent on port 8000, Discord bot on port 8091)

## Constitution Check

| # | Principle | Status | Notes |
|---|---|---|---|
| I | Safety-First Autonomy | ✅ | No new mutation paths. Discord bot still calls the existing HMAC-verified callback endpoint. |
| II | Cost-Conscious | ✅ | No new LLM calls. |
| III | Developer Experience | ✅ | Single `CHAT_SURFACE=discord` env var switches the reporter surface. |
| IV | Evidence-Backed Triage | ✅ | Reporter sidecar unchanged; Discord embed already renders cited evidence. |
| V | Observability & Reversibility | ✅ | Existing audit rows unchanged; `proposed_fix_fingerprint` write is additive. |
| VI | Code Quality | ✅ | Changes are minimal (async fix + settings + one DB helper). |
| VII | Testing Standards | ✅ | Unit tests for new `chat_deliver()` helper; integration smoke test. |
| VIII | UX Consistency | ✅ | Same `Report` sidecar as Slack mock; Discord bot rendering already tested. |
| IX | Performance | ✅ | Async delivery removes the blocking-skip bug; no new latency budget impact. |

**Verdict**: All gates compliant.

## Root Cause Analysis

Three bugs prevent the loop from closing:

| # | Bug | Location | Effect |
|---|---|---|---|
| B1 | `reporter_node` is `def` (sync), but the graph runs via `await graph.ainvoke()` | `reporter.py:reporter_node` | `asyncio.get_running_loop()` succeeds → `deliver()` is SKIPPED every run |
| B2 | `proposed_fix_fingerprint` is never written to `incidents` | `reporter.py` / `db.py` | `callbacks.py` reads a NULL fingerprint → approval token bound to `""` → Solver pre-flight may mismatch |
| B3 | `SLACK_MOCK_URL` is the only configured delivery target | `settings.py` | Discord bot never receives the report → no embed, no buttons |

## Phases

### Phase 0: Research (already resolved)

All technical decisions are made from the existing codebase. No external research needed.

**R1** — LangGraph async nodes: `langgraph` natively supports `async def` node functions. `graph.ainvoke()` runs the event loop; `async def reporter_node(state)` is `await`-able directly.

**R2** — Multi-surface delivery: simplest safe approach is an ordered list of target URLs read from settings; the reporter POSTs to each in sequence (not parallel, to preserve error semantics). A single `CHAT_SURFACE` env var selects `"slack"`, `"discord"`, or `"all"`.

**R3** — `proposed_fix_fingerprint` write timing: the fingerprint is known immediately after `make_report()` (it comes from `report.proposed_fix.fingerprint`). Write it in a single `UPDATE incidents SET proposed_fix_fingerprint = ? WHERE correlation_id = ?` call inside `reporter_node`, using the existing `get_conn()` pattern.

### Phase 1: Implementation

#### Task P1-T1 — Add `discord_bot_url` and `chat_surface` to `AgentSettings`

**File**: `src/agent/settings.py`

Add two new fields to `AgentSettings`:

```python
discord_bot_url: str = Field(
    default="http://localhost:8091",
    description="URL of the Discord bot HTTP receiver (POST /messages).",
)
chat_surface: str = Field(
    default="slack",
    description=(
        "Which chat surface(s) to deliver reports to. "
        "Values: 'slack' | 'discord' | 'all'."
    ),
)
```

Add backward-compat properties:
```python
@property
def DISCORD_BOT_URL(self) -> str:  # noqa: N802
    return self.discord_bot_url

@property
def CHAT_SURFACE(self) -> str:  # noqa: N802
    return self.chat_surface
```

---

#### Task P1-T2 — Add `set_proposed_fix_fingerprint()` to `src/agent/db.py`

**File**: `src/agent/db.py`

```python
async def set_proposed_fix_fingerprint(correlation_id: str, fingerprint: str) -> None:
    async with get_conn() as conn:
        await conn.execute(
            "UPDATE incidents SET proposed_fix_fingerprint = ? WHERE correlation_id = ?",
            (fingerprint, correlation_id),
        )
        await conn.commit()
```

---

#### Task P1-T3 — Fix `reporter_node` (the core fix)

**File**: `src/agent/graph/nodes/reporter.py`

Three sub-changes:

**3a. Make `deliver()` surface-aware**

Replace the current `deliver()` with `chat_deliver()` that targets configured surfaces:

```python
async def chat_deliver(
    report: Report,
    solver_run: SolverRun | None = None,
) -> tuple[str, str]:
    """
    POST the report to each configured chat surface.

    Returns (delivered_at_iso, message_id) from the first successful delivery.
    Raises httpx.HTTPError on all-surface failure.
    """
    from src.agent.settings import settings

    surface = settings.chat_surface  # "slack" | "discord" | "all"

    targets: list[str] = []
    if surface in ("slack", "all"):
        targets.append(settings.slack_mock_url)
    if surface in ("discord", "all"):
        targets.append(settings.discord_bot_url)

    if solver_run:
        blocks = build_followup_blocks(report, solver_run)
    else:
        blocks = build_initial_blocks(report)

    body = build_message_body(report, blocks, solver_run)
    body_bytes = json.dumps(body, default=str).encode()

    delivered_at = datetime.now(timezone.utc).isoformat()
    message_id = "unknown"
    last_exc: Exception | None = None

    for url in targets:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    f"{url}/messages",
                    content=body_bytes,
                    headers={"Content-Type": "application/json"},
                )
                resp.raise_for_status()
                data = resp.json()
                delivered_at = data.get("delivered_at", delivered_at)
                message_id = data.get("message_id", message_id)
                logger.info(
                    "report delivered corr=%s surface=%s solver=%s",
                    report.correlation_id, url, solver_run is not None,
                )
        except Exception as exc:
            logger.warning("delivery failed url=%s corr=%s: %s", url, report.correlation_id, exc)
            last_exc = exc

    if last_exc and not any(True for _ in targets):
        raise last_exc

    return delivered_at, message_id
```

**3b. Make `reporter_node` async**

```python
async def reporter_node(state: WorkflowState) -> WorkflowState:
    """Assemble and deliver the report, returning the report field in state."""
    from src.agent.db import set_proposed_fix_fingerprint

    correlation_id = state["correlation_id"]
    routing = state["routing"]
    diagnosis = state.get("diagnosis")

    tentative_delivered_at = datetime.now(tz=timezone.utc)
    report = make_report(
        correlation_id=correlation_id,
        routing=routing,
        diagnosis=diagnosis,
        delivered_at=tentative_delivered_at,
    )

    # Persist fingerprint so the callback handler can verify it (B2 fix).
    if report.proposed_fix:
        await set_proposed_fix_fingerprint(correlation_id, report.proposed_fix.fingerprint)

    try:
        delivered_at_iso, _ = await chat_deliver(report)  # B1 fix: await, no loop dance
        delivered_at = datetime.fromisoformat(delivered_at_iso.replace("Z", "+00:00"))
        report = report.model_copy(
            update={
                "delivered_at": delivered_at,
                "approval_deadline": delivered_at + timedelta(minutes=APPROVAL_WINDOW_MINUTES),
            }
        )
    except Exception:
        logger.exception("report delivery failed corr=%s", correlation_id)
        report = report.model_copy(update={"status": "failed"})

    return {"report": report}  # type: ignore[return-value]
```

---

#### Task P1-T4 — Update `.env.example`

**File**: `.env.example`

Add:
```
CHAT_SURFACE=discord
DISCORD_BOT_URL=http://localhost:8091
```

---

#### Task P1-T5 — Integration smoke test

**File**: `tests/integration/test_discord_delivery.py`

Test plan (using `respx` to mock HTTP):
1. Mock `POST http://localhost:8091/messages` → 200 `{"delivered_at": "...", "message_id": "abc"}`
2. Set `settings.chat_surface = "discord"` and `settings.discord_bot_url = "http://localhost:8091"`
3. Build a minimal `Report` and call `await chat_deliver(report)`
4. Assert the mock was called once with a body containing `report.correlation_id`
5. Assert no call to the Slack mock URL

---

#### Task P1-T6 — Verify `callbacks.py` HMAC secret alignment

**Verification only** (no code change expected):

The Discord bot signs callbacks with `SLACK_MOCK_SECRET` (env var). `callbacks.py` verifies with `settings.SLACK_MOCK_SECRET`. Both must share the same value. Document in `.env.example`:

```
# Must match SLACK_MOCK_SECRET in the Discord bot environment
SLACK_MOCK_SECRET=dev-mock-secret
```

### Phase 2: End-to-End Wiring

With P1 complete, the end-to-end flow is:

```
Alertmanager →  POST /webhook/alertmanager
                  ↓ (background)
             graph.ainvoke()
                  ↓
             ingest_node → router_node → expert_node
                  ↓
             reporter_node  (async, B1 fixed)
                  ├─ chat_deliver() → POST http://localhost:8091/messages
                  │     ↓
                  │  Discord bot renders embed with [Approve] [Reject]
                  │     ↓ (user clicks)
                  │  Discord bot → POST /callbacks/slack/approve  (HMAC signed)
                  │     ↓
                  │  callbacks.py verifies HMAC, checks fingerprint (B2 fixed)
                  │  → resume graph → solver_node
                  │     ↓
                  └─ reporter_node (follow-up) → chat_deliver() → Discord embed updated
```

## Project Structure (changes only)

```
src/agent/
  settings.py               ← +discord_bot_url, +chat_surface
  db.py                     ← +set_proposed_fix_fingerprint()
  graph/nodes/reporter.py   ← async reporter_node, chat_deliver()

tests/integration/
  test_discord_delivery.py  ← smoke test for multi-surface delivery

.env.example                ← +CHAT_SURFACE, +DISCORD_BOT_URL
```

## Complexity Tracking

| Entry | Why Needed | Simpler Alternative Rejected Because |
|---|---|---|
| `chat_surface` selects delivery target at runtime | Supports both Slack mock and Discord bot without code changes | Hard-coding Discord URL would break Slack-mock-only setups |
| Sequential (not parallel) delivery to multiple surfaces | First-surface response sets `delivered_at` / `message_id` | Parallel delivery complicates error handling with no latency benefit for ≤2 surfaces |
