<!-- SPECKIT START -->
For additional context about technologies to be used, project structure,
shell commands, and other important information, read the current plan
at `specs/008-multi-agent-workflow-gui/plan.md` and its sibling artifacts:

- `specs/008-multi-agent-workflow-gui/spec.md` — the feature specification.
- `specs/008-multi-agent-workflow-gui/research.md` — Phase 0 stack decisions
  (SSE via sse-starlette, LangGraph astream_events, reactflow workflow diagram,
  kubectl subprocess for pod status, Vite + FastAPI serving strategy).
- `specs/008-multi-agent-workflow-gui/data-model.md` — WorkflowEvent, PodStatus,
  ScenarioTriggerResponse, GuiApprovalRequest entities and event state machine.
- `specs/008-multi-agent-workflow-gui/contracts/` — SSE events, pod status API,
  scenario trigger API, GUI approval API.
- `specs/008-multi-agent-workflow-gui/quickstart.md` — one-command local GUI run.

Project-wide rules live in `.specify/memory/constitution.md` (v1.1.0,
Principles I–IX). Plans MUST enumerate the Constitution Check gates
against this file.
<!-- SPECKIT END -->
