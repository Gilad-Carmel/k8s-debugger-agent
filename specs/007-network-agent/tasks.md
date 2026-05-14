# Tasks: Network Expert Agent Node

**Feature**: `007-network-agent` | **Spec**: [spec.md](./spec.md) | **Plan**: [plan.md](./plan.md) | **Research**: [research.md](./research.md)

This task list implements the Network Expert by layering minimal changes on top of the already-shipped `BaseExpert` pipeline. No new packages, no new entrypoints, no schema changes.

## Conventions

- `[P]` = can run in parallel with other `[P]` tasks at the same phase (different files, no ordering).
- File paths are absolute or repo-relative; all source paths assume the repo root `/data/carmelg1/k8s-debugger-agent`.
- Tests precede implementation only where they meaningfully drive the design (TDD for the new `_allowed_actions` filter). Prompt text is hand-tuned, so the unit test for `network.py` arrives in the same phase as the implementation.

---

## Phase 1: Setup & guards

- [ ] **T001** Confirm Phase 0/1 plan artifacts are in place and inspect the existing `BaseExpert`, `NetworkExpert` stub, and `application.py` reference implementation. No code changes; this is a context-load checkpoint.
  - Files inspected: `src/agent/graph/nodes/experts/_base.py`, `src/agent/graph/nodes/experts/network.py`, `src/agent/graph/nodes/experts/application.py`, `src/shared/labels.py` (`ACTION_TYPES`, `ActionType`).
  - Expected: `NetworkExpert._system_prompt == ""`, stub uses `delete-pod-to-reschedule` (out of network subset — to be fixed by T010), `_allowed_actions` not yet declared on `BaseExpert`.

## Phase 2: Foundational — per-domain action subset (BaseExpert)

> This phase introduces the runtime safety filter declared in research.md R1 (the FR-011 enforcement). It is foundational — every Expert subclass benefits from it; subsequent tasks rely on its presence.

- [ ] **T002** Add `_allowed_actions: ClassVar[frozenset[str]] = ACTION_TYPES` to `BaseExpert` in `src/agent/graph/nodes/experts/_base.py`. Import `ACTION_TYPES` from `src.shared.labels` (already imported in this file).

- [ ] **T003** In `BaseExpert._run_real_diagnosis` (same file), add a runtime filter immediately after the `validate_action(...)` call: if `validated_action is not None and validated_action not in self._allowed_actions`, log a warning ("Expert %s_expert proposed catalog action %r which is not in the per-domain allowed set %r; dropping fix.") and set both `validated_action = None` and `validated_params = {}`. The downstream `proposed_fix = None` branch handles the rest. Lines added: ≤ 8. Cyclomatic complexity delta: +1 (well under the 15 cap).

- [ ] **T004 [P]** Add three unit tests to `tests/unit/test_base_expert_allowed_actions.py` (new file):
  - `test_default_allowed_actions_is_full_catalog` — subclass with no override accepts all four catalog actions (regression guard).
  - `test_subclass_can_narrow_allowed_actions` — a synthetic subclass that overrides `_allowed_actions = frozenset({"restart-pod"})` and is fed a mocked LLM response with `proposed_action="scale-deployment"` returns `proposed_fix=None`, cited evidence still grounded.
  - `test_out_of_subset_action_logs_warning` — same scenario, capture `caplog` and assert the warning message format.
  - Coverage target: ≥ 95% of the new filter lines (safety-critical floor per Constitution VII).

## Phase 3: Network system prompt and node activation

- [ ] **T005** Draft `_NETWORK_SYSTEM_PROMPT` constant in `src/agent/graph/nodes/experts/network.py` following research.md R4 (same 5-section structure as `_APP_SYSTEM_PROMPT`):
  1. Role framing: Senior Network SRE specializing in Kubernetes connectivity.
  2. Evidence-Backed Triage block (copy from `_APP_SYSTEM_PROMPT`, only the domain word changes).
  3. Three signal classes with example patterns from research.md R2 (DNS / ConnectionRefusedOrTimeout / TLSHandshake).
  4. Catalog-bounded actions — enumerate ONLY `restart-pod` and `rollback-deployment`; state explicitly that anything else maps to `null`; re-state `to_revision: int` requirement.
  5. Output-format block (copy verbatim from `_APP_SYSTEM_PROMPT`; same six-key JSON object; same single-example response shape).
  Target length: ≤ 1200 tokens.

- [ ] **T006** Update `NetworkExpert` in `src/agent/graph/nodes/experts/network.py`:
  - Set `_system_prompt = _NETWORK_SYSTEM_PROMPT` at class level.
  - Set `_allowed_actions: ClassVar[frozenset[str]] = frozenset({"restart-pod", "rollback-deployment"})` at class level (depends on T002).
  - Update the imports as needed (`ClassVar` from `typing`, `PERMISSION_SCOPES` from `_base`, the same shape as `application.py`).

- [ ] **T007** Fix `NetworkExpert._stub_diagnosis` in the same file: change the proposed action from `delete-pod-to-reschedule` (out of network subset) to `restart-pod`. Update the hypothesis text to a transient-network plausible scenario consistent with `restart-pod` (e.g., "Pod's DNS resolver cache is stale; restarting it should clear the bad records and restore connectivity."). Update the `permission_scope` to `PERMISSION_SCOPES["restart-pod"]`. Keep `runner_up_causes` as the existing network-domain alternatives.

- [ ] **T008** Verify the stub still parses: instantiate `NetworkExpert()` in a Python REPL (or a one-liner) with the venv active, then invoke `._stub_diagnosis({"filtered_evidence": None})` and assert it returns an `ExpertDiagnosis` with `domain=="Network"` and `proposed_fix.action_type=="restart-pod"`. Sanity check, not a permanent test.

## Phase 4: Unit tests (TDD-style for the real LLM path)

- [ ] **T009** Create `tests/unit/test_network_expert.py` mirroring the patterns in `test_application_expert.py` (if one exists) or following the `_base.py` docstrings. Tests required (six paths from plan.md §Technical Context):
  - **a. valid path / restart-pod**: mock `ChatOpenAI.with_structured_output(...).invoke(...)` to return `(_ExpertOutput(root_cause_hypothesis="DNS lookup ENOTFOUND failures from a stale resolver cache.", cited_indices=[0], confidence="medium", proposed_action="restart-pod", proposed_parameters={}), raw_message_stub)`. Feed a `WorkflowState` with one `LogExcerpt` containing the literal `getaddrinfo ENOTFOUND`. Assert the returned `ExpertDiagnosis` has `domain=="Network"`, `proposed_fix.action_type=="restart-pod"`, `confidence=="medium"`, `cited_evidence[0].text` contains `ENOTFOUND`.
  - **b. valid path / rollback-deployment**: mock the LLM to return `proposed_action="rollback-deployment"`, `proposed_parameters={"to_revision": 7}`. Assert `proposed_fix.action_type=="rollback-deployment"` and `proposed_fix.parameters=={"to_revision": 7}`.
  - **c. out-of-subset action dropped**: mock the LLM to return `proposed_action="scale-deployment"`, `proposed_parameters={"to_replicas": 5}`. Assert `proposed_fix is None`, `confidence` survives at the LLM-stated level (no demotion — the action is dropped but the diagnosis is otherwise valid).
  - **d. out-of-catalog action dropped**: mock the LLM to return `proposed_action="iptables-flush"`. Assert `proposed_fix is None`.
  - **e. fabricated citation**: mock the LLM to return `cited_indices=[7]` against a 3-hit evidence list. Assert `cited_evidence` is reduced to first hit (pin), `confidence=="low"`, `proposed_fix is None` (force_low_confidence pathway in `_base`).
  - **f. empty cited_indices**: mock the LLM to return `cited_indices=[]`. Assert demoted to low + `proposed_fix is None`.
  - **g. fail-closed on LLM exception**: mock `ChatOpenAI(...)` to raise on `invoke`. Assert `_fallback_diagnosis` path: `confidence=="low"`, `proposed_fix is None`, `cited_evidence` non-empty (first hit or synthetic placeholder), no exception escapes.
  - Coverage target: ≥ 85% on `src/agent/graph/nodes/experts/network.py`.

## Phase 5: Cross-check & manual smoke

- [ ] **T010** Run the full unit test suite locally to confirm no regression: `pytest tests/unit/ -q` (with venv active). All existing tests must still pass; new tests must pass.

- [ ] **T011** Run `ruff check src/agent/graph/nodes/experts/` and `mypy --strict src/agent/graph/nodes/experts/` to satisfy Principle VI gates. Fix any new lint or type errors introduced by T002-T007.

- [ ] **T012** Manual integration smoke: invoke the graph against an existing network fixture if one is present in `tests/fixtures/` (or skip with a note). Confirm the Reporter receives a real `ExpertDiagnosis` (not the stub) when `_system_prompt` is now set, assuming the LLM server is reachable. If the LLM server is not reachable in this environment, document that the fallback path is exercised instead and the test still passes (no crash).

## Phase 6: Polish (NOT REQUIRED for MVP merge — call out in PR)

> The remaining items are required for SC compliance per the spec but are larger than a single PR and may be split. Tracked here so they are not lost; left unchecked.

- [ ] **T013** Extend `tests/eval/network_expert_golden.jsonl` per research.md R3 (3 positive + 2 adversarial fixtures). Run through the eval runner and report per-class accuracy.
- [ ] **T014** Register the three new positive fixtures in `tests/eval/hallucination_suite.py` (cited-evidence-grounded check on each).
- [ ] **T015** Extend `tests/perf/test_latency_benchmark.py` with a `Network`-routed scenario asserting median ≤ 8 s / max ≤ 20 s on the three positive fixtures.

---

## Dependency graph

```text
T001 (context) → T002 → T003 → T004 [P]
                         ↓
                  T005 → T006 → T007 → T008
                                  ↓
                                 T009 → T010 → T011 → T012
                                                       ↓
                                              T013 [P], T014 [P], T015 [P]   (Polish — out of MVP merge scope)
```

T002, T003, T005 are sequential (same file edits; T005's prompt text is hand-tuned but does not depend on T002/T003 logically — it could overlap if a second editor were available).

T004 [P] is parallelizable with T005 (different files).

T009 depends on T002 + T006 (it tests the integrated behavior).

## Acceptance for "MVP merge done"

All of T001–T012 marked `[X]`. T013–T015 may land in a follow-up PR with the eval/perf polish.
