# Solver Implementation Tasks

## Phase 5: User Story 3 — Solver Execution

### MCP Write Tools (can run in parallel)

- [X] T077 [P] [US3] MCP write tool restart_pod
- [X] T078 [P] [US3] MCP write tool rollback_deployment
- [X] T079 [P] [US3] MCP write tool scale_deployment
- [X] T080 [P] [US3] MCP write tool delete_pod_to_reschedule

### Support & Coordination

- [X] T081 [P] [US3] Write-tool guards in _guards.py
- [X] T082 [P] [US3] Per-target Solver serialization lock

### Core Solver

- [X] T083 [US3] Solver node implementation
- [X] T084 [US3] Reporter follow-up message
- [X] T085 [US3] Wire Solver into graph

### Testing

- [X] T086 [P] [US3] Unit tests for Solver guards
- [X] T087 [P] [US3] Unit tests for Inverse Action
- [X] T088 [P] [US3] Unit tests for Solver lock
- [X] T089 [P] [US3] Integration test success path
- [X] T090 [P] [US3] Integration test partial path
- [X] T091 [P] [US3] Integration test admission denied
- [X] T092 [P] [US3] Integration test kill switch
- [X] T093 [P] [US3] Integration test fingerprint mismatch