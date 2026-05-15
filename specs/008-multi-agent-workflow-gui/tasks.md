# Tasks: Multi-Agent Workflow GUI

**Input**: Design documents from `specs/008-multi-agent-workflow-gui/`

**Prerequisites**: plan.md ✅ · research.md ✅ · data-model.md ✅ · contracts/ ✅ · quickstart.md ✅

---

## Phase 1: Setup

**Purpose**: Add `sse-starlette` dependency, create directory skeletons, scaffold frontend build tooling.

- [X] T001 Add `sse-starlette` to `pyproject.toml` dependencies
- [X] T002 [P] Create `src/agent/api/gui/` Python package skeleton (`__init__.py`)
- [X] T003 [P] Create `gui/` frontend directory with `package.json`, `tsconfig.json`, `vite.config.ts`, `index.html`
- [X] T004 [P] Create `tests/unit/gui/__init__.py` for backend GUI unit tests

---

## Phase 2: Backend — Event Bus

**Purpose**: In-memory asyncio pub/sub connecting graph runs to SSE subscribers.

- [X] T005 Create `src/agent/api/gui/event_bus.py`

---

## Phase 3: Backend — Pod Status API

- [X] T006 Create `src/agent/api/gui/pods.py` — `GET /api/pods`

---

## Phase 4: Backend — Scenario Trigger API

- [X] T007 Create `src/agent/api/gui/scenarios.py` — `POST /api/demo/trigger/{scenario}`

---

## Phase 5: Backend — SSE Stream

- [X] T008 Create `src/agent/api/gui/stream.py` — `GET /api/events`

---

## Phase 6: Backend — GUI Approval

- [X] T009 Create `src/agent/api/gui/approval.py` — `POST /api/approval/{cid}/approve|reject`

---

## Phase 7: Backend — Wire event bus into graph runs

- [X] T010 Update `src/agent/api/webhook.py` — add `_run_graph_streaming` using `astream_events`
- [X] T011 Update `src/agent/api/__init__.py` — register GUI routers + loopback middleware

---

## Phase 8: Frontend — Types & Services

- [X] T012 [P] Create `gui/src/types/events.ts`
- [X] T013 [P] Create `gui/src/services/api.ts`

---

## Phase 9: Frontend — Hooks

- [X] T014 [P] Create `gui/src/hooks/useEventStream.ts`
- [X] T015 [P] Create `gui/src/hooks/usePods.ts`

---

## Phase 10: Frontend — Components

- [X] T016 [P] Create `gui/src/components/PodGrid.tsx`
- [X] T017 [P] Create `gui/src/components/ScenarioButtons.tsx`
- [X] T018 Create `gui/src/components/WorkflowDiagram.tsx`
- [X] T019 [P] Create `gui/src/components/EventLog.tsx`
- [X] T020 Create `gui/src/components/ApprovalPanel.tsx`

---

## Phase 11: Frontend — App Shell

- [X] T021 Create `gui/src/index.css`
- [X] T022 Create `gui/src/App.tsx`
- [X] T023 Create `gui/src/main.tsx`

---

## Phase 12: Backend Tests

- [X] T024 [P] Create `tests/unit/gui/test_pods.py`
- [X] T025 [P] Create `tests/unit/gui/test_scenarios.py`
- [X] T026 [P] Create `tests/unit/gui/test_approval.py`

---

## Phase 13: Polish

- [X] T027 Add `gui-*` targets to `Makefile`
- [X] T028 Update `.gitignore` with `gui/node_modules/` and `gui/dist/`

---

## Dependencies

- T002–T004 parallel (Phase 1)
- T005 requires T002
- T006–T009 parallel, each requires T005
- T010–T011 require T005–T009
- T012–T015 parallel, require T003
- T016–T020 require T012–T015
- T021–T023 require T016–T020
- T024–T026 parallel, require T010–T011
