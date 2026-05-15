# k8s-debugger-agent

**Auto-reversible Kubernetes incident triage.** An alert fires, the agent
classifies the incident (Application / Network / Unknown), proposes a fix
backed by cited log evidence, and only acts after a human clicks Approve in
Slack. Every fix is paired with a **deterministic Inverse Action** computed
from a pre-state snapshot — not LLM-invented undo logic — so the rollback is
specified, parameterised, and audit-logged before the forward action is ever
issued.

## What makes this different

- **Inverse Action is data, not prompt.** The reversal is an entry in the
  allowed-remediation catalog, parameterised with values read out of the
  captured pre-state (replica count, image tag, revision). The Expert does
  not invent rollback steps; the Solver computes the inverse at execution
  time from a fixed Forward → Inverse mapping. See
  [spec §Clarification Q2](specs/002-routed-triage-workflow/spec.md).
- **The Solver refuses unsafe execution by construction.** Three independent
  refusal paths: pre-state-incomplete, action-not-in-catalog, and
  proposed-vs-executed-mismatch (US3 scenarios 3–4). The approval click does
  not bypass any of them.
- **Evidence-bound diagnosis is a NON-NEGOTIABLE gate (Principle IV).**
  Every claim in `root_cause_hypothesis` must quote-match a cited log
  excerpt. Hallucinations are caught at runtime *and* in CI by the
  hallucination suite below.

## Evaluation

Last run: **2026-05-15 UTC**.  Reproduce with `make eval` or
`uv run python scripts/eval_report.py`.

### Per-suite results

| Suite | Cases | Passed | Failed | Skipped | Pass rate |
|---|---:|---:|---:|---:|---:|
| Hallucination grounding | 29 | 28 | 0 | 1¹ | **100%** |
| Router classification | 33 | 31 | 2 | 0 | **94%** |
| **Overall** | **62** | **59** | **2** | **1** | **97%** |

¹ The skipped case is `test_hallucination_golden` — parametrised at
collection time; it reports zero items until the golden JSONL fixtures
(tasks T057–T059) are committed. No grounding failures means the
Principle IV merge gate holds for everything currently in the suite.

### Router confusion matrix

Rows are the expected domain for each fixture; columns are the domain the
router actually returned. Diagonal = correct. `ERROR` = no routing
decision returned. `OTHER` = a domain outside the MVP catalog.

| expected \ predicted | Application | Network | Unknown | ERROR | OTHER |
|---|---:|---:|---:|---:|---:|
| **Application** | 12 | 1 | 4 | 0 | 0 |
| **Network** | 0 | 10 | 1 | 0 | 0 |
| **Unknown** | 0 | 0 | 5 | 0 | 0 |

**Notable cases**

- **Adversarial — surface vs. root cause.** A log block whose surface
  symptom is `ECONNREFUSED 127.0.0.1:6379` but whose embedded
  `Caused by: ConfigError: redis.port=6739 in config.yaml` identifies the
  real cause as a port typo. Classified as **Application** correctly. The
  router weighs the explicit root-cause statement over the transport
  error. (`app-adversarial-econnrefused-with-configerror`.)
- **Multi-hit-line context window.** Five-line fixture mixing scheduling
  noise, probe noise, and one ERROR line (`network is unreachable`). The
  router picks the ERROR line as the trigger and classifies Network
  correctly. (`net-multi-line-context-window`.)
- **Multi-domain conflict.** A Python traceback wraps a
  `ConnectionRefusedError` to `payments-svc:443`. Both Application and
  Network are defensible reads of the same evidence; we accept either via
  `also_accept` and let the confusion matrix surface whichever convention
  the router currently encodes. (`conflict-traceback-around-econnrefused`.)
- **Safety-path demotions land in the Unknown column.** Four of the five
  weakly-signaled Application fixtures (single-liner "container exited"
  / "Liveness probe failed" / etc.) and the noisy-null-ref case get
  demoted to Unknown by the `demote_to_unknown` guard when the router
  cannot cite a clean evidence line. That is the designed behaviour —
  it's the same guard that enforces Principle IV at runtime. The
  fixtures explicitly accept Unknown as a defensible outcome.

**Honest failures (2/33).** Both remaining failures are calibration
defects — the router correctly classifies a one-line `container exited
with code 1` (and `Liveness probe failed: ... 500`) as Application but
reports `high` confidence on essentially zero context. CI catches these
because each weakly-signaled fixture asserts a confidence ceiling. We
prefer to surface the calibration miss honestly rather than relax the
test.

### How we measure

- **Router classification** (`tests/eval/router/test_classification.py`) —
  33 parametrised fixtures: strong-signal, weakly-signaled, multi-hit-line
  context window, multi-domain conflict, and one adversarial case where
  surface signal contradicts the stated root cause. Live LLM call against
  `LLM_ROUTER_MODEL`. Weakly-signaled fixtures additionally assert a
  **confidence ceiling** — CI hard-fails on `Application + high` for
  one-liners. Fixtures with two defensible reads accept either domain via
  `also_accept`.
- **Hallucination grounding** (`tests/eval/hallucination_suite.py`) —
  Constitution Principle IV gate. Every `ExpertDiagnosis.cited_evidence`
  excerpt must match `(byte_offset, text)` from `FilteredEvidence.hit_lines`
  verbatim, and `root_cause_hypothesis` must share at least one key token
  with one cited excerpt. Zero failures is the merge gate. Same guard
  runs at runtime *and* in CI — defense in depth.
- **Reproducing**: `uv run python scripts/eval_report.py --out docs/EVAL.md`
  re-runs both suites and rewrites the report. Underlying artifacts land in
  `.eval/` (`junit.xml`, `router_confusion.json`).

## Architecture (one paragraph)

A webhook (`Alertmanager` schema) lands in a FastAPI handler. A contextual
grep pre-filter pulls a window of log lines around any pattern hit, then the
LangGraph workflow runs router → expert → reporter. The report (Slack-mock
or real Slack) carries the cited evidence, the proposed fix, and an Approve
button. On click, the Solver verifies the approval token, computes the
Inverse Action from the captured pre-state, executes the forward action via
MCP tools, checks the post-state, and posts the outcome plus the Inverse
Action back to the same thread. Every transition is recorded under a single
correlation ID in the `audit_record` table.

## Getting started

```bash
# Install deps
uv sync

# Bring up the local stack (agent + slack-mock)
make dev

# Fire a synthetic webhook against the running stack
make smoke INCIDENT=network        # or application, or unknown

# Run the eval suite and regenerate this README's numbers
uv run python scripts/eval_report.py --out docs/EVAL.md
```

See `specs/002-routed-triage-workflow/quickstart.md` for the full
one-command end-to-end demo, and `.specify/memory/constitution.md` for the
non-negotiable principles every change is checked against.

## Project layout

- `src/agent/graph/` — LangGraph workflow (router, experts, reporter, solver)
- `src/agent/api/` — FastAPI webhook + Slack callbacks
- `src/agent/mcp_server/` — MCP read+write tools (kubectl-equivalent)
- `src/shared/schemas.py` — Pydantic state, evidence, incident, diagnosis
- `tests/eval/` — quality gates (router accuracy, hallucination grounding)
- `specs/002-routed-triage-workflow/` — feature spec + contracts
- `.specify/memory/constitution.md` — Principles I–IX (NON-NEGOTIABLE)
