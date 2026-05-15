# Research: Multi-Agent Workflow GUI

**Feature**: 008-multi-agent-workflow-gui | **Date**: 2026-05-15

---

## R-001: SSE in FastAPI

**Decision**: Use `sse-starlette` (`EventSourceResponse`) for the SSE endpoint.

**Rationale**: `sse-starlette` integrates cleanly with FastAPI/Starlette's
async generator pattern, handles client disconnection detection, and supports
the `id:` field required for browser-native reconnect (`Last-Event-ID` header).
`StreamingResponse` with `text/event-stream` works too but requires manual
framing of each SSE field (`data:`, `event:`, `id:`, `\n\n`).

**Reconnect / replay**: The browser's `EventSource` automatically reconnects
and sends `Last-Event-ID`. The server uses the sequence number encoded in the
event ID to replay missed events from the in-memory queue (if the queue still
holds them). Events are ephemeral — if the queue has been drained beyond the
last-seen ID, the client receives a `reconnect_missed` synthetic event and
re-fetches the current incident state via `GET /api/incidents/{cid}`.

**Alternatives considered**:
- Raw `StreamingResponse` — more boilerplate; no built-in disconnection detection.
- WebSocket — bidirectional; unnecessary complexity for a push-only channel.

---

## R-002: LangGraph `astream_events` for node telemetry

**Decision**: Replace `graph.ainvoke` with `graph.astream_events(…, version="v2")`
in the graph-run helper to capture per-node `on_chain_start` / `on_chain_end`
events.

**Rationale**: LangGraph's v2 event stream emits typed events for each node
boundary. Filtering on `event == "on_chain_start"` and `name in NODE_NAMES`
gives us the exact signal we need (node entered, node exited). The event dict
includes the node's output in `data["output"]` on `on_chain_end`, letting us
forward the relevant fields (routing decision, diagnosis domain, report summary)
to the SSE client without an extra DB read.

**Thread-safety**: Each `correlation_id` gets its own `asyncio.Queue[WorkflowEvent]`
in the event bus (a plain `dict[str, Queue]` guarded by no lock — safe because
Python's asyncio event loop is single-threaded). The SSE handler awaits the
queue; the graph task puts into it.

**Interrupt detection**: The graph pauses at `interrupt_before=["solver"]`.
After the last `on_chain_end` for `reporter`, the `astream_events` async
generator simply stops yielding until the graph is resumed. The `stream.py`
wrapper detects this via a sentinel put into the queue by the graph runner
(`GraphEvent(type="awaiting_approval", …)`) immediately before the
`aupdate_state` call.

**Alternatives considered**:
- Polling the `audit_log` table — adds 500ms+ latency and extra DB load.
- Patching node functions to emit events inline — tight coupling; would require
  modifying core node files which this feature must not touch.

---

## R-003: reactflow for the workflow diagram

**Decision**: Use `@xyflow/react` (reactflow v12) with a static DAG layout
computed once at mount. Nodes animate to an `active` / `completed` / `failed`
class as SSE events arrive.

**Node layout**: Fixed positions computed from the known graph topology:
`START → ingest → router → {application_expert | network_expert | database_expert}
→ reporter → [HITL] → solver → END`. The three expert nodes are rendered in a
column; only the active one is highlighted. Edges are straight (Bezier) to keep
the layout stable when multiple experts exist.

**HITL gate node**: A custom `HitlNode` component renders the pending/approved
/rejected state and embeds a mini Approve/Reject button pair when the
`awaiting_approval` event arrives. This keeps the approval action co-located
with the workflow visual.

**Alternatives considered**:
- D3.js — powerful but 5× the boilerplate for a fixed topology.
- Mermaid.js rendered to SVG — no runtime interactivity; can't animate nodes.
- Hand-rolled SVG — too much work for no added value.

---

## R-004: `kubectl` subprocess from FastAPI

**Decision**: Use `asyncio.create_subprocess_exec` (not `subprocess.run`) so
the event loop is not blocked while kubectl talks to the API server.

**Security**: Args are passed as a list (never shell=True), so there is no
injection surface. The `demo` namespace is hardcoded; no user-supplied strings
are interpolated into the command.

**Timeout**: Hard 5s timeout via `asyncio.wait_for`. If kubectl times out
(cluster unreachable), `GET /api/pods` returns `503` with a structured error
body rather than hanging the SSE client.

**Output parsing**: `kubectl get pods -n demo -o json` returns a Kubernetes
`PodList` object. We extract `items[].metadata.name`, `items[].status.phase`,
and `items[].status.conditions[?type==Ready].status` to build `PodStatus` records.

**Alternatives considered**:
- `kubernetes` Python SDK — adds a non-trivial dependency and requires
  explicit kubeconfig loading; overkill for a demo.
- `kubectl get pods --output=jsonpath=…` — fragile jsonpath; full JSON is
  more stable and easier to parse.

---

## R-005: Vite + FastAPI CORS in development

**Decision**: Vite proxy (`vite.config.ts` `server.proxy`) forwards `/api`
and `/api/events` to `http://localhost:8000`. No CORS headers needed on the
FastAPI side for dev; in production the SPA is served from the same origin.

**FastAPI CORS middleware**: Added only for the `/api/events` SSE endpoint
and only when `settings.GUI_DEV_MODE=true`. In production, `StaticFiles`
mount at `/` means all requests (SPA + API) share the same origin.

**Makefile targets**:
```makefile
gui-install:
    cd gui && npm install

gui-dev:
    cd gui && npm run dev &
    uvicorn src.agent.api:create_app --factory --reload --port 8000

gui-build:
    cd gui && npm run build

gui-serve:
    GUI_STATIC_DIR=gui/dist uvicorn src.agent.api:create_app --factory --port 8000
```

**Alternatives considered**:
- Next.js — SSR not needed; adds build complexity.
- Serving SPA from a separate nginx — unnecessary for a local demo.
