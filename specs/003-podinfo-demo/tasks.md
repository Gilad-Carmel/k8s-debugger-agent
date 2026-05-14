---

description: "Task list for feature 003-podinfo-demo"
---

# Tasks: Podinfo Demo Integration

**Input**: Design documents from `/specs/003-podinfo-demo/`

**Prerequisites**: plan.md ✓ • spec.md ✓ • research.md ✓ • data-model.md ✓ • quickstart.md ✓

**Tests**: Smoke test included (single integration script verifying deploy + webhook round-trip).

**Organization**: Tasks are grouped by user story. The four demo scenarios are independent of each other; they all share the same foundation (manifests + fire-webhook.sh).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: US1 = Foundation, US2 = Crash/restart scenario, US3 = Bad-deploy/rollback, US4 = OOM scenario, US5 = Scale scenario
- Every task includes the exact file path it touches

## Path Conventions

- Manifests: `deploy/demo/`
- Scripts: `scripts/demo/`
- Makefile: project root `Makefile`
- Tests: `tests/integration/`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Create directory structure and namespace manifest.

- [ ] T001 Create `deploy/demo/` and `scripts/demo/` directories and add `.gitkeep` placeholders
- [ ] T002 [P] Add `deploy/demo/00-namespace.yaml` — demo namespace with label `purpose: demo`
- [ ] T003 [P] Add `deploy/demo/03-service.yaml` — ClusterIP service for podinfo on port 9898

**Checkpoint**: Directory layout matches `specs/003-podinfo-demo/plan.md §Project Structure`.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core manifests and the `fire-webhook.sh` script that all scenario scripts depend on.

**⚠️ CRITICAL**: No scenario script works until this phase is complete.

- [ ] T004 Add `deploy/demo/01-podinfo-v1.yaml` — v1 Deployment with `app: podinfo` labels, 32Mi memory limit, liveness + readiness probes on `/healthz` and `/readyz`
- [ ] T005 [P] Add `deploy/demo/02-podinfo-v2-bad.yaml` — v2 Deployment patch: same as v1 plus `PODINFO_RUNTIME_ERROR=true` and `PODINFO_UI_MESSAGE="ERROR: database connection refused"`
- [ ] T006 [P] Add `deploy/demo/04-pdb.yaml` — PodDisruptionBudget with `minAvailable: 1` targeting `app: podinfo`
- [ ] T007 Add `scripts/demo/fire-webhook.sh` — constructs Alertmanager v4 JSON, computes HMAC-SHA256 via `openssl dgst`, sets `X-Alertmanager-Hmac` header, POSTs to `$AGENT_WEBHOOK_URL`, prints `correlation_id`; accepts `--scenario`, `--namespace`, `--pod`, `--agent-url`, `--hmac-secret`
- [ ] T008 Add `scripts/demo/deploy.sh` — applies all `deploy/demo/*.yaml` in order, runs `kubectl rollout status deployment/podinfo -n demo --timeout=60s`, prints service URL
- [ ] T009 [P] Add `scripts/demo/teardown.sh` — `kubectl delete namespace demo --ignore-not-found=true`
- [ ] T010 Append `demo-deploy`, `demo-teardown`, `demo-reset` targets to `Makefile` per `plan.md §Phase 4`

**Checkpoint**: `make demo-deploy` deploys podinfo and it becomes Ready; `make demo-teardown` cleans up.

---

## Phase 3: User Story 1 — CrashLoop → restart-pod (Priority: P1) 🎯 MVP

**Goal**: Trigger a `CrashLoopBackOff` in podinfo via `POST /panic` and fire a synthetic
`KubePodCrashLooping` alert. The agent should triage this as Application domain and propose
`restart-pod`.

**Independent Test**: Run `make demo-crash`. Verify (a) pod enters CrashLoopBackOff, (b) webhook
returns 202 with a `correlation_id`, (c) agent log shows `domain=Application`.

- [ ] T011 [US1] Add `scripts/demo/trigger-crash.sh` — port-forwards podinfo, sends `POST /panic`, polls until `restartCount >= 2` or state is `CrashLoopBackOff` (max 30s, 2s interval), calls `fire-webhook.sh --scenario KubePodCrashLooping`, prints `correlation_id`, cleans up port-forward
- [ ] T012 [P] [US1] Append `demo-crash` Makefile target (depends on `demo-deploy`, runs `trigger-crash.sh`)

**Checkpoint**: `make demo-crash` produces a `correlation_id` and the triage agent classifies Application domain with a `restart-pod` proposal.

---

## Phase 4: User Story 2 — Bad Deploy → rollback-deployment (Priority: P1)

**Goal**: Apply the v2 bad deployment and trigger a `KubePodNotReady` alert. The agent should
diagnose `RUNTIME_ERROR` in the logs and propose `rollback-deployment`.

**Independent Test**: Run `make demo-bad-deploy`. Verify pod enters NotReady (500 on readiness),
webhook fires, agent proposes `rollback-deployment`, post-approval pod serves 200s again.

- [ ] T013 [US2] Add `scripts/demo/trigger-bad-deploy.sh` — applies `deploy/demo/02-podinfo-v2-bad.yaml`, polls until readiness probe returns non-200 or pod is NotReady (max 30s), calls `fire-webhook.sh --scenario KubePodNotReady`, prints `correlation_id`; on completion re-applies v1 is NOT done by this script (left to operator after approval demo)
- [ ] T014 [P] [US2] Append `demo-bad-deploy` Makefile target

**Checkpoint**: `make demo-bad-deploy` produces a `correlation_id` and agent proposes `rollback-deployment` citing `runtime error` log lines.

---

## Phase 5: User Story 3 — OOMKill → restart-pod (Priority: P2)

**Goal**: Stress podinfo memory past its 32Mi limit to trigger `OOMKilled` (exit code 137).
The agent should cite the OOMKilled event and propose `restart-pod`.

**Independent Test**: Run `make demo-oom`. Verify pod shows `OOMKilled` in events or exit code 137,
webhook fires with `KubePodOOMKilled`, agent cites the exit code.

- [ ] T015 [US3] Add `scripts/demo/trigger-oom.sh` — port-forwards podinfo, sends `GET /stress?mem=50&duration=30`, polls until pod state is `Terminated` with exit code 137 or OOMKilled event visible (max 45s, 3s interval), calls `fire-webhook.sh --scenario KubePodOOMKilled`, cleans up port-forward
- [ ] T016 [P] [US3] Append `demo-oom` Makefile target

**Checkpoint**: `make demo-oom` produces a `correlation_id` and agent cites an OOMKilled event or exit code 137.

---

## Phase 6: User Story 4 — Scale Pressure → scale-deployment (Priority: P2)

**Goal**: Run a bombardier load test inside the cluster to overwhelm the single-replica podinfo
deployment and trigger timeout errors. The agent should propose `scale-deployment`.

**Independent Test**: Run `make demo-scale`. Verify bombardier is launched, error logs appear in
podinfo, webhook fires with `KubeContainerWaiting`, agent proposes `scale-deployment`.

- [ ] T017 [US4] Add `scripts/demo/trigger-scale.sh` — runs `kubectl run bombardier --image=alpine/bombardier --restart=Never -n demo -- -c 50 -d 30s http://podinfo.demo.svc.cluster.local:9898`, waits 10s for errors to accumulate, calls `fire-webhook.sh --scenario KubeContainerWaiting`, waits for bombardier job to finish, deletes the job
- [ ] T018 [P] [US4] Append `demo-scale` Makefile target

**Checkpoint**: `make demo-scale` produces a `correlation_id` and agent proposes `scale-deployment`.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Smoke test, idempotency guard, and documentation.

- [ ] T019 [P] Add `tests/integration/test_demo_scenarios.sh` — smoke test: runs `make demo-deploy`, asserts pod Ready, runs `fire-webhook.sh --dry-run` (validate JSON without POSTing), runs `make demo-teardown`, asserts namespace gone; exit 0 on pass
- [ ] T020 [P] Add guard to `scripts/demo/deploy.sh`: detect if `kubectl` is not in PATH or cluster is unreachable and print a clear error message (Principle III)
- [ ] T021 [P] Update `deploy/dev.env` to document `AGENT_WEBHOOK_URL` and `ALERTMANAGER_HMAC_SECRET` variables with example values
- [ ] T022 Validate quickstart.md against the implementation: run through `specs/003-podinfo-demo/quickstart.md` step-by-step and fix any command discrepancies

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies — start immediately
- **Phase 2 (Foundation)**: Depends on Phase 1 — blocks all scenario scripts
- **Phase 3–6 (Scenarios)**: All depend on Phase 2; each scenario is independent of the others
- **Phase 7 (Polish)**: Depends on all scenarios being complete

### User Story Dependencies

- **US1 (crash)**: Can start after Phase 2
- **US2 (bad-deploy)**: Can start after Phase 2 — independent of US1
- **US3 (oom)**: Can start after Phase 2 — independent of US1/US2
- **US4 (scale)**: Can start after Phase 2 — independent of all others

### Parallel Opportunities

Within Phase 2, T005 and T006 can be written in parallel with T007.
Phases 3–6 can be implemented simultaneously (different files, no shared state).

---

## Implementation Strategy

### MVP (US1 only)

1. Phase 1 + Phase 2
2. Phase 3 (crash scenario)
3. **Validate**: `make demo-crash` → agent report → approve → pod restarts

### Full demo

Complete all phases in order. Each `make demo-<scenario>` is a self-contained demo beat.

### Demo run order (suggested for live presentation)

1. `make demo-deploy` — show podinfo running
2. `make demo-crash` — most visual (CrashLoopBackOff in `kubectl get pods -w`)
3. `make demo-bad-deploy` — shows rollback capability
4. `make demo-reset` — clean slate
5. `make demo-oom` / `make demo-scale` — if time permits
