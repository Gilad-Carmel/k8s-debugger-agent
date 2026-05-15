# Implementation Plan: Multi-Agent Workflow GUI

**Branch**: `008-multi-agent-workflow-gui` | **Date**: 2026-05-15 | **Spec**: `specs/008-multi-agent-workflow-gui/spec.md`

**Input**: Feature specification from `specs/008-multi-agent-workflow-gui/spec.md`

---

## Summary

A real-time browser-based GUI that (1) displays live Kubernetes pod health,
(2) provides one-click buttons to trigger the four demo failure scenarios
from feature 003-podinfo-demo, and (3) visualises every step of the
multi-agent triage-and-remediation workflow in real time — including an
in-GUI Approve / Reject panel that replaces the Slack-mock interaction
when the human-in-the-loop gate fires.

The implementation is split between:
- **Frontend** — React + TypeScript SPA (`gui/`)
- **Backend additions** — four new FastAPI routers under `src/agent/api/gui/`
  that expose pod status, scenario triggers, SSE event stream, and a
  GUI-native approval endpoint.

No existing agent logic is modified; the new code only observes the graph
(via LangGraph's `astream_events`) and wraps existing subprocess scripts.

---

## Technical Context

**Language/Version**: Python 3.11 (backend additions) · TypeScript 5 / Node 20 (frontend)

**Primary Dependencies**:
- Backend: FastAPI (existing), `sse-starlette` (SSE transport), `asyncio.Queue` (event bus), `subprocess` (kubectl + demo scripts)
- Frontend: React 18, Vite 5, `reactflow` (workflow diagram), `axios` (HTTP), `@tanstack/react-query` (pod polling)

**Storage**: SQLite (existing incidents DB — read-only from GUI endpoints)

**Testing**: pytest + pytest-asyncio (backend), Vitest (frontend unit), Playwright (e2e smoke)

**Target Platform**: Local Kubernetes kind cluster (same as feature 002/003 dev stack)

**Project Type**: Web application (frontend SPA + backend API extension)

**Performance Goals**:
- Pod status refresh: ≤ 3s polling interval
- Scenario trigger → first SSE event: ≤ 2s (TTFT SLO per Principle IX)
- Workflow node events: ≤ 500ms latency from node completion to GUI update

**Constraints**:
- No new Python package dependencies beyond `sse-starlette` (already common in FastAPI setups)
- No Kubernetes Python SDK — `kubectl` subprocess is sufficient for the demo scope
- GUI approval endpoint is localhost-only; no external auth in demo mode
- Frontend served by Vite dev server during development; FastAPI `StaticFiles` mount in production

**Scale/Scope**: Single-user demo; single namespace (`demo`); one active triage incident at a time in the GUI

---

## Constitution Check

*Gate: must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Assessment |
|---|---|
| **I. Safety-First Autonomy** | ✅ Compliant. The GUI Approve/Reject endpoints call the same `_resume_graph` path as `callbacks.py`. No mutation bypass; all tool calls still require the existing authorization scope. GUI approval endpoint records the same audit event fields (actor.user_id, roles, action). The endpoint is restricted to loopback (127.0.0.1) via middleware so it cannot be called from outside localhost without an explicit opt-in. |
| **II. Cost-Conscious by Design** | ✅ Compliant. Zero new LLM calls. SSE connections are persistent but idle (no polling cost). Pod status uses `kubectl get pods` — one cheap API server request per 3s interval. No token spend beyond the existing triage workflow budget. |
| **III. Developer Experience as a Product** | ✅ Compliant — this feature IS the DX improvement. One-click scenario trigger, real-time node timeline, approval panel eliminates the need to `curl` the callback endpoint manually. `make gui-dev` launches everything in one command. |
| **IV. Evidence-Backed Triage** | ✅ Compliant. The GUI displays exactly what the `Report` object contains: root-cause hypothesis, evidence citations, proposed fix. No extra inference; the GUI is a render layer over the existing agent output. |
| **V. Observability & Reversibility** | ✅ Compliant. All approval events routed through `log_audit_event`. The in-memory SSE event bus is ephemeral — this is acceptable because the audit DB is the system of record; the bus is purely a push-notification transport. SSE event IDs use `correlation_id + sequence_number` so clients can reconnect and request missed events if the server implements replay (deferred; noted in Complexity Tracking). |
| **VI. Code Quality** | ✅ Compliant. TypeScript strict mode; React functional components; cyclomatic complexity ≤ 15 per function; no dead code. Python additions follow the project linter (ruff) and type checker (mypy strict) settings. |
| **VII. Testing Standards (NON-NEGOTIABLE)** | ✅ Compliant. Backend: unit tests for all four new routers (mock subprocess + mock graph); GUI approval endpoint must include refusal-path test (wrong host, unknown correlation_id). Frontend: Vitest for hook and component logic; Playwright smoke test covers the golden path (trigger crash → see workflow nodes → approve → see solver). Coverage floor: 85% backend routes; frontend components 80%. |
| **VIII. User Experience Consistency** | ✅ Compliant. Node labels in the workflow diagram are drawn directly from the LangGraph node names (`ingest`, `router`, `application_expert`, `network_expert`, `database_expert`, `reporter`, `solver`). Status labels match the shared vocabulary from `src/shared/labels.py`. Timestamps rendered in ISO-8601 per Principle VIII. |
| **IX. Performance Requirements (DevOps SLOs)** | ✅ Compliant. TTFT (first SSE event after trigger click) ≤ 2s. End-to-end triage display p50 ≤ 30s (same as existing workflow). Pod grid refresh ≤ 3s. These budgets are asserted in the Playwright smoke test against the local kind cluster fixture. |

---

## Project Structure

### Documentation (this feature)

```text
specs/008-multi-agent-workflow-gui/
├── plan.md              ← this file
├── research.md          ← Phase 0 output
├── data-model.md        ← Phase 1 output
├── quickstart.md        ← Phase 1 output
├── contracts/           ← Phase 1 output
│   ├── sse_events.md
│   ├── pod_status_api.md
│   ├── scenario_trigger_api.md
│   └── gui_approval_api.md
└── tasks.md             ← Phase 2 output (/speckit-tasks)
```

### Source Code

```text
gui/                          # React SPA (new top-level dir)
├── index.html
├── package.json
├── tsconfig.json
├── vite.config.ts
└── src/
    ├── main.tsx
    ├── App.tsx
    ├── components/
    │   ├── PodGrid.tsx           # Pod health cards with status badges
    │   ├── ScenarioButtons.tsx   # S1–S4 trigger buttons
    │   ├── WorkflowDiagram.tsx   # reactflow dag, nodes light up as events arrive
    │   ├── EventLog.tsx          # Scrolling raw event feed (debug panel)
    │   └── ApprovalPanel.tsx     # Approve/Reject modal + report display
    ├── hooks/
    │   ├── useEventStream.ts     # SSE subscription + reconnect logic
    │   └── usePods.ts            # react-query polling for /api/pods
    ├── services/
    │   └── api.ts                # Typed axios wrappers for all backend endpoints
    └── types/
        └── events.ts             # WorkflowEvent, PodStatus, etc. TypeScript types

src/agent/api/gui/            # New Python package (new sub-dir)
├── __init__.py               # exports all four routers
├── pods.py                   # GET /api/pods
├── scenarios.py              # POST /api/demo/trigger/{scenario}
├── stream.py                 # GET /api/events (SSE)
├── approval.py               # POST /api/approval/{cid}/approve|reject
└── event_bus.py              # in-memory asyncio event bus

tests/
├── unit/
│   └── gui/
│       ├── test_pods.py
│       ├── test_scenarios.py
│       ├── test_stream.py
│       └── test_approval.py
└── e2e/
    └── test_gui_smoke.py     # Playwright golden path
```

---

## Implementation Phases

### Phase 0: Research

**Research tasks dispatched** (see `research.md`):

1. **SSE in FastAPI** — `sse-starlette` vs `fastapi.responses.StreamingResponse`; reconnect / event-id replay pattern.
2. **LangGraph `astream_events`** — how to tap node-completion events from an already-running graph invocation; thread-safety with `asyncio.Queue`.
3. **reactflow** — best practices for animating a static DAG as nodes activate; handling the interrupt node state.
4. **kubectl subprocess from FastAPI** — security model; timeout; JSON output parsing.
5. **Vite + FastAPI CORS in dev** — proxy config to avoid cross-origin issues during development.

---

### Phase 1: Design & Contracts

#### data-model.md entities

- **`WorkflowEvent`** — SSE payload emitted for each node activation and status transition.
- **`PodStatus`** — snapshot of a single pod's health from `kubectl get pods -o json`.
- **`ScenarioTriggerRequest / Response`** — request body and response for the trigger endpoint.
- **`GuiApprovalRequest`** — actor identity sent by the GUI when the user clicks Approve/Reject.

#### contracts/

- **`sse_events.md`** — event type enumeration (`node_started`, `node_completed`, `awaiting_approval`, `approved`, `rejected`, `solver_done`), field schema, reconnect semantics.
- **`pod_status_api.md`** — `GET /api/pods` response schema (array of PodStatus, namespace filter).
- **`scenario_trigger_api.md`** — `POST /api/demo/trigger/{scenario}` where `scenario` ∈ `{crash, bad-deploy, oom, scale}`; returns `{correlation_id, scenario, started_at}`.
- **`gui_approval_api.md`** — `POST /api/approval/{correlation_id}/{action}` where `action` ∈ `{approve, reject}`; body: `{actor_name: string}`; returns `{correlation_id, status}`. Loopback-only; no HMAC required.

#### Agent context update

After generating artifacts, update `CLAUDE.md` plan reference to point to
`specs/008-multi-agent-workflow-gui/plan.md`.

---

## Key Design Decisions

### Why SSE instead of WebSocket?

The GUI only needs server→client event push. SSE is unidirectional, requires
no handshake protocol beyond HTTP/1.1, and reconnects automatically in the
browser. WebSocket adds bidirectional complexity we don't need — the Approve
click is a normal `POST`, not a WebSocket frame.

### How events are captured from the running graph

`LangGraph.astream_events` (v2 format) yields `on_chain_start` /
`on_chain_end` events keyed by node name. The new `stream.py` router
wraps the graph invocation with `astream_events` instead of `ainvoke`,
writing each relevant event into a per-`correlation_id` `asyncio.Queue`.
The SSE endpoint drains this queue and formats it as SSE frames.

The existing `_run_graph` helper in `webhook.py` is replaced with a new
`_run_graph_streaming` helper that uses `astream_events` and notifies the
event bus. Existing behavior (non-GUI users hitting the raw webhook) is
unchanged because the event bus discards events if no SSE subscriber is
listening.

### Why GUI approval bypasses Slack HMAC

The Slack-mock HMAC was designed to authenticate callbacks from the
`mock-slack` server. The GUI is served from the same host; requiring an
HMAC secret exchange in the browser would expose the secret in client JS.
Instead, the GUI approval endpoint is gated to loopback only (FastAPI
middleware rejects requests from non-127.0.0.1 origins) and records the
same audit fields. For production, this would be replaced with OAuth2/JWT.

### Why `kubectl` subprocess instead of the Kubernetes Python SDK

The project already avoids new Python dependencies where possible (see
003-podinfo-demo rationale). `kubectl get pods -o json` is one command, its
JSON output is stable, and it respects the user's `KUBECONFIG` environment
variable without additional SDK configuration. The subprocess is invoked
with a hard 5s timeout to avoid blocking the event loop.

### Serving strategy

Development: Vite dev server on port 5173, proxied to the FastAPI agent on
port 8000. Production: `vite build` output mounted at `/` via FastAPI
`StaticFiles`; the four API routers remain under `/api/`.

---

## Complexity Tracking

| Aspect | Why Needed | Simpler Alternative Rejected Because |
|--------|-----------|-------------------------------------|
| SSE event bus (`asyncio.Queue`) | Decouple graph execution from SSE subscriber lifecycle; graph must not block on a slow client | Direct `yield` from inside graph node would couple LangGraph internals to HTTP transport layer |
| `astream_events` wrapper around existing `_run_graph` | Real-time node updates without polling the DB | DB polling at 500ms would miss sub-second nodes and add DB load |
| Loopback middleware for GUI approval | Simple demo security without client-side secrets | Full OAuth2 is out of scope for a hackathon demo; needed some guard to prevent remote abuse |
