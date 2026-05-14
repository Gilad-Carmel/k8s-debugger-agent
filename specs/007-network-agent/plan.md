# Implementation Plan: Network Expert Agent Node

**Branch**: `007-network-agent` | **Date**: 2026-05-15 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/007-network-agent/spec.md`

## Summary

Activate the Network Expert node by giving it a real "Senior Network SRE" system prompt and a per-domain action-subset guard, both layered on top of the already-shipped `BaseExpert` pipeline (`src/agent/graph/nodes/experts/_base.py`). The shared base already handles LLM invocation, `_ExpertOutput` structured-output binding, index-based citation resolution, the `_assert_citations_grounded` hallucination guard, action validation against the catalog, and the three fallback paths (no evidence, LLM unreachable, parse failure). This feature adds three small, well-scoped pieces:

1. A Network-domain system prompt (`_NETWORK_SYSTEM_PROMPT`) that prompts the model as a Senior Network SRE, enumerates DNS / connection-refused-or-timeout / TLS-handshake signal classes with example patterns, restricts the model's `proposed_action` to `restart-pod` / `rollback-deployment` / `null`, and forbids free-form remediations.
2. A new `BaseExpert._allowed_actions: ClassVar[frozenset[str]]` (default = the full catalog) with a single new filter step at the end of `_run_real_diagnosis`: any validated action not in the subclass's `_allowed_actions` is dropped (`proposed_fix = None`). This is runtime enforcement of the spec's FR-011 (per-domain action subset), not prompt-only enforcement (Principle I).
3. Update `NetworkExpert` to set `_system_prompt = _NETWORK_SYSTEM_PROMPT` and `_allowed_actions = frozenset({"restart-pod", "rollback-deployment"})`, plus fix the stub diagnosis to use `restart-pod` (the current stub uses `delete-pod-to-reschedule`, which the new per-domain guard would reject).

Test additions: a unit test mirroring the Application Expert's existing pattern (LLM call mocked, citation grounding asserted, out-of-catalog rejection asserted, out-of-subset rejection asserted, fail-closed paths asserted), plus three eval fixtures in `tests/eval/network_expert_golden.jsonl` (one per FR-006 signal class) and at least one adversarial fixture per FR-011 temptation.

No changes to: `WorkflowState`, `ExpertDiagnosis`, the catalog, `PERMISSION_SCOPES`, the LangGraph builder edges (the Router already wires `Network → network_expert_node`), persistence, redaction, audit, the Solver, or any MCP tool.

## Technical Context

**Language/Version**: Python 3.11+ (typed, async — inherited from spec 002).

**Primary Dependencies** (all already on `main`; no new dependencies introduced by this feature):

- `langgraph` ≥ 0.2 — node is registered via `builder.py`'s existing `Network → network_expert_node` conditional edge.
- `langchain-openai` — `ChatOpenAI` built by `_base.build_expert_llm()`; the Expert tier model is `settings.llm_expert_model` (env-driven).
- `langchain-core` — `SystemMessage` / `HumanMessage` for prompt assembly.
- `pydantic` v2 — `_ExpertOutput` (the structured-output schema) and `ExpertDiagnosis` are already defined and reused as-is.

**Storage**: N/A for this node. It writes only to the in-memory `WorkflowState.diagnosis`; the audit record is emitted by the LangGraph runner / `audit.py` using the `tokens`/`model` fields the base class populates from the raw `AIMessage`.

**Testing**: `pytest` with:

- **Unit** (`tests/unit/test_network_expert.py`, new): hard-coded `_ExpertOutput` mock returned from `ChatOpenAI.with_structured_output(...).invoke(...)`; assert (a) valid-citation path produces a `Network`-domain `ExpertDiagnosis` with `restart-pod` or `rollback-deployment` as `proposed_fix`, (b) out-of-subset action (e.g. `scale-deployment`) is dropped to `proposed_fix=None`, (c) out-of-catalog action is dropped (already covered by `_base` but re-asserted at the `NetworkExpert` layer), (d) fabricated citation (`cited_indices=[7]` with only 3 hit_lines) is filtered + diagnosis demoted to low confidence, (e) empty `cited_indices` → `_fallback_diagnosis` path with `proposed_fix=None`, (f) network domain stub remains usable when `_system_prompt` is forced empty (regression test for the BaseExpert stub-guard path). Target coverage ≥ 85% (pure-logic floor) for `src/agent/graph/nodes/experts/network.py` and ≥ 95% (safety-critical floor) for the new `_allowed_actions` filter in `_base.py`.
- **Hallucination suite** (`tests/eval/hallucination_suite.py`, existing): add three new entries — one each for DNS, ConnRefusedOrTimeout, and TLSHandshake fixtures. Each entry asserts every `cited_evidence` line appears verbatim in `FilteredEvidence.hit_lines` (Constitution IV).
- **Eval golden** (`tests/eval/network_expert_golden.jsonl`, existing — currently sparse): extend with at least three positive-path fixtures (one per signal class) and two adversarial fixtures (model tempted to propose `kubectl apply`-style NetworkPolicy edit and `iptables` rules — both must resolve to `proposed_fix=null`).
- **Integration** (`tests/integration/test_e2e_network_flow.py`, existing per plan 002): no changes required; this node now produces a real diagnosis instead of stub output, so the existing end-to-end test starts asserting realistic behavior. Update the fixture's expected `domain == "Network"` assertion if needed.

**Target Platform**: Inherited from spec 002 — Linux container running the agent FastAPI service; the Network Expert runs in-process inside the LangGraph runner.

**Project Type**: Internal LangGraph node within the existing agent service. No new package, no new entrypoint, no new public API.

**Performance Goals**:

- Median `network_expert_node` wall-clock (LLM call included) ≤ 8 s (spec SC-004).
- p95 ≤ 20 s (spec SC-004).
- Per-incident token budget honored via the existing `WorkflowState.budget_remaining_tokens` field; on exhaustion the node returns `_fallback_diagnosis` with `proposed_fix=None` (spec FR-016, plan 002 §Constraints).

These keep end-to-end (Router + Expert + Reporter) inside the spec-002 SC-003 envelope (p50 ≤ 30 s, p95 ≤ 60 s). Approximately: Router ≤ 5 s + Expert ≤ 20 s p95 + Reporter < 1 s ⇒ ≤ 26 s, well inside the 60 s p95 ceiling.

**Constraints**:

- Per-incident token ceiling (spec 002 FR-029) enforced fail-closed by the existing budget machinery — this node does not add a new budget. On exceed → fallback diagnosis with `proposed_fix=null`.
- All cited lines MUST be verbatim from `FilteredEvidence.hit_lines` — runtime asserted by `_assert_citations_grounded` (inherited; treated as Sev-2 per Constitution IV).
- Per-domain action subset enforcement is runtime, not prompt-only (the new `_allowed_actions` filter — Principle I).
- The node does not call MCP tools; it does not mutate cluster state; it does not call `audit.py` directly (the LangGraph runner does that).
- Redaction is already applied at the MCP boundary on `FilteredEvidence`; this node neither adds redaction nor relaxes it.

**Scale/Scope**:

- One LangGraph node, one Python module (≈ 100–150 LOC including the prompt string and stub), one new unit-test module, one extension of an existing eval JSONL.
- Triggers once per incident classified as `Network` by the Router (FR-002).
- Concurrent incidents: up to 10 (plan 002 §Constraints); each Expert invocation is stateless and independent.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Evaluated against `.specify/memory/constitution.md` v1.1.0 (Principles I–IX). This feature is an internal LangGraph node within the framework already approved by spec 002's plan; most gates inherit directly. The two items requiring fresh evaluation are flagged.

| # | Principle | Status | Notes |
|---|---|---|---|
| I | Safety-First Autonomy | ✅ Compliant | Node is read-only — it only produces an `ExpertDiagnosis`. Proposed fix is constrained to the inherited catalog by `_base.validate_action`, and **additionally** constrained to the per-domain subset `{restart-pod, rollback-deployment}` by the new `_allowed_actions` runtime filter (FR-008, FR-009, FR-011). No `--force` / `--grace-period=0` reachable from this node. No mutation. No bypass. |
| II | Cost-Conscious by Design | ✅ Compliant | One LLM call per invocation on the Expert tier (`settings.llm_expert_model`). `tokens` recorded on every `ExpertDiagnosis` from `usage_metadata` (already in `_base`). Per-incident token ceiling enforced fail-closed by the existing budget machinery; on exhaustion the node returns a fallback diagnosis with `proposed_fix=None`. |
| III | Developer Experience as a Product | ✅ Compliant | One-line root-cause hypothesis + cited evidence (the existing Reporter schema). Latency budget ≤ 8 s median / ≤ 20 s p95 keeps end-to-end inside Constitution IX's 3 s TTFT / 30 s p50 / 60 s p95 envelope. Error message on fail-closed follows the shared template from `src/shared/errors.py` (Principle VIII). |
| IV | Evidence-Backed Triage (NON-NEGOTIABLE) | ✅ Compliant | Inherited from `_base._assert_citations_grounded`: every cited `LogExcerpt` MUST be present in `FilteredEvidence.hit_lines` or the run raises a Sev-2 hallucination defect. New hallucination-suite entries for DNS / ConnRefused-Timeout / TLS run in CI (spec SC-001). Empty `cited_indices` → demote to low confidence + drop fix (already implemented in `_base`). |
| V | Observability & Reversibility | ✅ Compliant | `correlation_id` is propagated through `WorkflowState`; `tokens` and `model` are set on the returned `ExpertDiagnosis` from the raw `AIMessage` (already in `_base`). The append-only audit row for this stage is written by the LangGraph runner using these fields — no new audit-table column. The node never mutates cluster state, so there is no pre-state / post-state / Inverse Action obligation specific to this stage; the Solver retains those obligations downstream. |
| VI | Code Quality | ✅ Compliant | `ruff` + `black` + `mypy --strict` already gate the existing `_base.py` and `application.py`; the new prompt + filter additions follow the same style. Cyclomatic complexity stays well under 15 (the new filter is a single early-return). **Two-reviewer rule applies** to this change because it modifies `_base.py` (the shared safety-critical pipeline) — flagged in the PR template. New runtime dependencies: none. |
| VII | Testing Standards (NON-NEGOTIABLE) | ✅ Compliant | New unit-test module covers the six paths listed above. Coverage floor: ≥ 85% for `network.py`, ≥ 95% for the new `_allowed_actions` filter in `_base.py` (the filter is safety-critical — it gates which actions can be proposed). Refusal-path test (out-of-subset action → no Approve button) explicit. Hallucination test mandatory and added. Three new entries in the network-expert golden eval. |
| VIII | User Experience Consistency | ✅ Compliant | No new user-facing labels or strings introduced. The Reporter renders `ExpertDiagnosis` using the existing shared schema. Action names are drawn from the existing `ActionType` `Literal` (`src/shared/labels.py`). Error messages on fail-closed paths reuse the existing `src/shared/errors.py` template. |
| IX | Performance Requirements (DevOps SLOs) | ✅ Compliant | SLOs declared in §Performance Goals and §Constraints (median ≤ 8 s, p95 ≤ 20 s, per-incident token ceiling fail-closed). Performance benchmark fixture: extend `tests/perf/test_latency_benchmark.py` (existing per plan 002) with a `Network`-routed fixture and a CI-enforced budget assertion. Bounded jittered retries on the LLM call are inherited from `ChatOpenAI` / `_base.build_expert_llm()`. No new hot path introduced. |

**Verdict**: All gates compliant. No entries required in the Complexity Tracking table.

Two items to surface on the PR:

1. The change to `_base.py` (adding `_allowed_actions` and the filter step) triggers the **two-reviewer rule** under Principle VI / §Development Workflow & Quality Gates (changes touching mutating-tool authorization scope and the shared Expert pipeline). PR description MUST flag this and request a safety-owner reviewer.
2. The new prompt does **not** introduce a new mutating tool (the catalog is unchanged); the "new mutating tool checklist" therefore does NOT apply. Confirmed against `.specify/memory/constitution.md` §Development Workflow & Quality Gates.

## Project Structure

### Documentation (this feature)

```text
specs/007-network-agent/
├── plan.md                       # This file
├── spec.md                       # Feature specification
├── research.md                   # Phase 0 — three narrow decisions (prompt restriction strategy, signal taxonomy, fixture set)
├── data-model.md                 # Phase 1 — no new entities; documents reuse of ExpertDiagnosis + how _allowed_actions is layered
├── quickstart.md                 # Phase 1 — single-node smoke-test against a Network fixture
├── contracts/                    # (intentionally empty — see §Project Structure note below)
├── checklists/
│   └── requirements.md           # Already exists — spec-quality checklist
└── tasks.md                      # Phase 2 — produced by /speckit-tasks, NOT this command
```

> **Note**: This feature does not expose a new external interface (no new HTTP endpoint, MCP tool, or CLI command), so there are no contract documents under `contracts/`. The skill explicitly says "Skip if project is purely internal." The Router's `Network → network_expert_node` edge contract is already documented in spec 002's `data-model.md` §WorkflowState; this feature does not change it.

### Source Code (repository root)

This feature **modifies** existing files; no new package or module tree is introduced. Touched files:

```text
src/agent/graph/nodes/experts/
├── _base.py                      # MODIFIED — add _allowed_actions ClassVar + filter step at end of _run_real_diagnosis.
└── network.py                    # MODIFIED — add _NETWORK_SYSTEM_PROMPT, set _system_prompt + _allowed_actions on NetworkExpert, fix stub action to "restart-pod".

tests/
├── unit/
│   └── test_network_expert.py    # NEW — six unit paths described in §Technical Context.
├── eval/
│   ├── network_expert_golden.jsonl  # MODIFIED — extend with three positive (DNS / ConnRefused-Timeout / TLS) + two adversarial fixtures.
│   └── hallucination_suite.py    # MODIFIED — register the three new positive fixtures for cited-evidence grounding checks.
└── perf/
    └── test_latency_benchmark.py # MODIFIED — add a Network-routed entry with median ≤ 8 s / p95 ≤ 20 s assertions on the benchmark.

specs/007-network-agent/          # Documentation only — see above.
```

**Structure Decision**: This feature reuses the monorepo + two-package structure decided in spec 002 (agent service + MCP server + `src/shared`). The Network Expert is one Python module under `src/agent/graph/nodes/experts/`, sibling to the already-implemented `application.py`. All shared behavior lives in `_base.py`; the new per-domain action-subset filter is added there (one place), and each subclass declares its allowed subset by overriding a single class variable. No new top-level directories, no new packages, no new build artifacts.

## Complexity Tracking

> All Constitution Check gates evaluated **compliant**. No entries required.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| — | — | — |
