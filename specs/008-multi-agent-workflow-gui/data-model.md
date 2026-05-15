# Data Model: Multi-Agent Workflow GUI

**Feature**: 008-multi-agent-workflow-gui | **Date**: 2026-05-15

---

## Entities

### `WorkflowEvent` (SSE payload)

Emitted by `event_bus.py` for every significant state change in the graph run.

```python
class WorkflowEventType(str, Enum):
    NODE_STARTED    = "node_started"
    NODE_COMPLETED  = "node_completed"
    NODE_FAILED     = "node_failed"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED        = "approved"
    REJECTED        = "rejected"
    EXPIRED         = "expired"
    SOLVER_DONE     = "solver_done"
    RUN_FAILED      = "run_failed"

class WorkflowEvent(BaseModel):
    seq: int                       # monotonic sequence number within the run
    correlation_id: str
    type: WorkflowEventType
    node: str | None = None        # LangGraph node name, if applicable
    ts: datetime                   # UTC timestamp of the event
    data: dict[str, Any] = {}      # node-specific payload (see below)
```

**`data` payloads by event type**:
- `NODE_STARTED`: `{}`
- `NODE_COMPLETED / router`: `{domain: str, confidence: float}`
- `NODE_COMPLETED / {expert}`: `{root_cause_label: str, severity: str}`
- `NODE_COMPLETED / reporter`: `{summary: str, proposed_fix_title: str}`
- `AWAITING_APPROVAL`: `{report_id: str, proposed_fix_title: str, proposed_fix_description: str, deadline_iso: str}`
- `APPROVED / REJECTED / EXPIRED`: `{actor_name: str}`
- `SOLVER_DONE`: `{tool_called: str, outcome: str}`
- `RUN_FAILED`: `{error: str}`

---

### `PodStatus`

Snapshot of a single Kubernetes pod's health, derived from `kubectl get pods -o json`.

```python
class PodPhase(str, Enum):
    PENDING   = "Pending"
    RUNNING   = "Running"
    SUCCEEDED = "Succeeded"
    FAILED    = "Failed"
    UNKNOWN   = "Unknown"

class PodStatus(BaseModel):
    name: str
    namespace: str
    phase: PodPhase
    ready: bool               # True iff all containers are Ready
    restart_count: int        # sum of container restartCounts
    message: str | None       # last container state message if not running
    ts: datetime              # resourceVersion timestamp (or now() if absent)
```

---

### `ScenarioTriggerRequest` / `ScenarioTriggerResponse`

```python
class DemoScenario(str, Enum):
    CRASH      = "crash"
    BAD_DEPLOY = "bad-deploy"
    OOM        = "oom"
    SCALE      = "scale"

class ScenarioTriggerResponse(BaseModel):
    correlation_id: str
    scenario: DemoScenario
    started_at: datetime
```

No request body — the scenario is a path parameter.

---

### `GuiApprovalRequest`

```python
class GuiApprovalRequest(BaseModel):
    actor_name: str = "gui-user"   # display name recorded in the audit log
```

The GUI approval endpoint synthesises a `CallbackBody`-compatible struct
internally, using `actor_name` as both `user_id` and `name`, with
`roles=["approver"]` hardcoded in demo mode.

---

## State Machine: `WorkflowEvent.type` sequence

```
NODE_STARTED(ingest)
NODE_COMPLETED(ingest)
NODE_STARTED(router)
NODE_COMPLETED(router)         ← data.domain tells us which expert branch
NODE_STARTED({expert})
NODE_COMPLETED({expert})
NODE_STARTED(reporter)
NODE_COMPLETED(reporter)
AWAITING_APPROVAL              ← GUI renders Approve/Reject panel
  ├─ APPROVED  → NODE_STARTED(solver) → NODE_COMPLETED(solver) → SOLVER_DONE
  ├─ REJECTED  → (run ends)
  └─ EXPIRED   → (run ends)
```

On error at any node: `NODE_FAILED` → `RUN_FAILED`.

---

## Frontend TypeScript types (mirroring the above)

```typescript
// gui/src/types/events.ts

export type WorkflowEventType =
  | "node_started"
  | "node_completed"
  | "node_failed"
  | "awaiting_approval"
  | "approved"
  | "rejected"
  | "expired"
  | "solver_done"
  | "run_failed";

export interface WorkflowEvent {
  seq: number;
  correlation_id: string;
  type: WorkflowEventType;
  node?: string;
  ts: string;           // ISO-8601
  data: Record<string, unknown>;
}

export type PodPhase = "Pending" | "Running" | "Succeeded" | "Failed" | "Unknown";

export interface PodStatus {
  name: string;
  namespace: string;
  phase: PodPhase;
  ready: boolean;
  restart_count: number;
  message?: string;
  ts: string;
}

export type DemoScenario = "crash" | "bad-deploy" | "oom" | "scale";

export interface ScenarioTriggerResponse {
  correlation_id: string;
  scenario: DemoScenario;
  started_at: string;
}
```
