# Contract: SSE Event Stream

**Endpoint**: `GET /api/events`

**Transport**: `text/event-stream` (Server-Sent Events, W3C spec)

**Auth**: None (loopback-only in demo mode; loopback middleware enforced server-side)

---

## Query Parameters

| Param | Required | Description |
|-------|----------|-------------|
| `correlation_id` | yes | Subscribe to events for this run |
| `last_event_id` | no | Resume from this sequence number (reconnect) |

---

## Event Fields

Each SSE frame uses the `id:`, `event:`, and `data:` fields:

```
id: {correlation_id}:{seq}
event: {WorkflowEventType}
data: {JSON-encoded WorkflowEvent}

```

(blank line terminates each frame per SSE spec)

---

## Event Types

| `event:` value | When emitted | Key `data` fields |
|----------------|-------------|-------------------|
| `node_started` | Immediately before a LangGraph node begins | `node` |
| `node_completed` | After a node returns without error | `node`, node-specific fields |
| `node_failed` | After a node raises an exception | `node`, `error` |
| `awaiting_approval` | After `reporter` completes; graph paused at HITL gate | `proposed_fix_title`, `proposed_fix_description`, `deadline_iso` |
| `approved` | After GUI or Slack mock approves | `actor_name` |
| `rejected` | After GUI or Slack mock rejects | `actor_name` |
| `expired` | After approval deadline passes | — |
| `solver_done` | After solver node completes | `tool_called`, `outcome` |
| `run_failed` | Unrecoverable graph error | `error` |

---

## Reconnect Semantics

1. Browser `EventSource` sends `Last-Event-ID: {correlation_id}:{seq}` on reconnect.
2. Server replays all events with `seq > last_seen_seq` that are still in the
   in-memory queue (capacity: 200 events per run).
3. If the queue has been evicted (seq too old), the server emits a synthetic
   `reconnect_missed` event; the client calls `GET /api/incidents/{cid}` to
   reconstruct current state from the DB.

---

## Lifecycle

The SSE connection for a given `correlation_id` closes automatically when
one of the terminal events is sent: `solver_done`, `rejected`, `expired`,
or `run_failed`. The client may also close at any time.
