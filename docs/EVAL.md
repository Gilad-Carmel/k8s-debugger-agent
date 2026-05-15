## Evaluation

Last run: **2026-05-15 UTC**.  Reproduce with `make eval` (full suite) or
`uv run python scripts/eval_report.py` (regenerates this section).

### Per-suite results

| Suite | Cases | Passed | Failed | Skipped | Pass rate |
|---|---:|---:|---:|---:|---:|
| Hallucination grounding | 29 | 28 | 0 | 1 | 100% |
| Router classification | 33 | 31 | 2 | 0 | 94% |
| **Overall** | **62** | **59** | **2** | **1** | **97%** |

### Router confusion matrix

Rows are the expected domain for each fixture; columns are the domain the
router actually returned.  Diagonal = correct.  `ERROR` = the router failed
to return a routing decision.  `OTHER` = the router returned a domain not in
the MVP catalog (e.g. `Database` from a deprecated route).

| expected \ predicted | Application | Network | Unknown | ERROR | OTHER |
|---|---:|---:|---:|---:|---:|
| **Application** | 12 | 1 | 4 | 0 | 0 |
| **Network** | 0 | 10 | 1 | 0 | 0 |
| **Unknown** | 0 | 0 | 5 | 0 | 0 |

### How we measure

- **Router classification** (`tests/eval/router/test_classification.py`) —
  33 parametrised fixtures per domain (strong-signal, weakly-signaled,
  multi-hit-line context window, and multi-domain conflict cases), plus
  one adversarial case where surface signal (`ECONNREFUSED`) contradicts
  the true root cause (`Caused by: ConfigError`).  Live LLM call.
  Weakly-signaled fixtures additionally assert a **confidence ceiling**:
  the router can be wrong about the domain *or* over-confident about a
  one-liner, but it cannot be both right *and* over-confident — CI hard-
  fails on `Application + high` for fixtures like `container exited with
  code 1`.  Fixtures with two defensible reads (e.g. transport error
  inside a stack trace) accept either domain via `also_accept`.
- **Hallucination grounding** (`tests/eval/hallucination_suite.py`) —
  Constitution Principle IV gate.  Every `ExpertDiagnosis.cited_evidence`
  excerpt must match `(byte_offset, text)` from `FilteredEvidence.hit_lines`
  verbatim, and the `root_cause_hypothesis` must share at least one key
  token with one cited excerpt.  Zero failures is the merge gate.
