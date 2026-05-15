#!/usr/bin/env python3
"""
scripts/eval_report.py — produce a markdown evaluation report from pytest output.

Runs ``pytest tests/eval/`` with ``--junit-xml`` so we get a structured outcome
record without adding a new dependency.  Combines that with the router
confusion-matrix artifact written by ``tests/eval/router/test_classification.py``
(``.eval/router_confusion.json``) and prints a single markdown report that can
be pasted into ``README.md`` or written to ``docs/EVAL.md``.

Usage:

    # Run the full eval suite and emit the report on stdout
    uv run python scripts/eval_report.py

    # Reuse a previously-produced junit XML (skip the pytest run)
    uv run python scripts/eval_report.py --junit .eval/junit.xml --no-run

    # Write the report to a file
    uv run python scripts/eval_report.py --out docs/EVAL.md
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

ARTIFACT_DIR = Path(".eval")
DEFAULT_JUNIT = ARTIFACT_DIR / "junit.xml"
DEFAULT_CONFUSION = ARTIFACT_DIR / "router_confusion.json"


# ---------------------------------------------------------------------------
# Suite grouping — map a junit <testcase classname>/<file> to a human suite.
# ---------------------------------------------------------------------------
SUITE_RULES: List[Tuple[str, str]] = [
    ("tests/eval/router/", "Router classification"),
    ("tests.eval.router.", "Router classification"),
    ("tests/eval/hallucination_suite", "Hallucination grounding"),
    ("tests.eval.hallucination_suite", "Hallucination grounding"),
    ("tests/eval/audit_completeness", "Audit completeness"),
    ("tests.eval.audit_completeness", "Audit completeness"),
]


def classify_suite(identifier: str) -> str:
    for needle, label in SUITE_RULES:
        if needle in identifier:
            return label
    return "Other eval"


# ---------------------------------------------------------------------------
# pytest invocation
# ---------------------------------------------------------------------------
def run_pytest(junit_path: Path, eval_paths: List[str]) -> int:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    cmd = [
        "uv", "run", "pytest",
        *eval_paths,
        "-q",
        "--tb=short",
        f"--junit-xml={junit_path}",
    ]
    print(f"$ {' '.join(cmd)}", file=sys.stderr)
    proc = subprocess.run(cmd, env={**__import__('os').environ, "EVAL_ARTIFACT_DIR": str(ARTIFACT_DIR)})
    return proc.returncode


# ---------------------------------------------------------------------------
# junit parsing
# ---------------------------------------------------------------------------
def parse_junit(xml_path: Path) -> Dict[str, Dict[str, int]]:
    if not xml_path.exists():
        return {}
    tree = ET.parse(xml_path)
    root = tree.getroot()
    # junit XML may have <testsuites> wrapping <testsuite>, or be a single suite.
    suites = root.findall(".//testcase")
    out: Dict[str, Dict[str, int]] = {}
    for tc in suites:
        classname = tc.get("classname", "")
        filepath = tc.get("file", "")
        ident = f"{filepath} {classname}".strip()
        suite = classify_suite(ident)
        bucket = out.setdefault(suite, {"passed": 0, "failed": 0, "skipped": 0, "errored": 0})
        if tc.find("failure") is not None:
            bucket["failed"] += 1
        elif tc.find("error") is not None:
            bucket["errored"] += 1
        elif tc.find("skipped") is not None:
            bucket["skipped"] += 1
        else:
            bucket["passed"] += 1
    return out


# ---------------------------------------------------------------------------
# markdown rendering
# ---------------------------------------------------------------------------
def render_suite_table(suites: Dict[str, Dict[str, int]]) -> str:
    if not suites:
        return "_No eval results found — run `make eval` or `python scripts/eval_report.py` first._"
    lines = [
        "| Suite | Cases | Passed | Failed | Skipped | Pass rate |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    grand = {"passed": 0, "failed": 0, "skipped": 0, "errored": 0}
    for suite, counts in sorted(suites.items()):
        run = counts["passed"] + counts["failed"] + counts["errored"]
        total = run + counts["skipped"]
        rate = (counts["passed"] / run * 100) if run else 0.0
        lines.append(
            f"| {suite} | {total} | {counts['passed']} | "
            f"{counts['failed'] + counts['errored']} | {counts['skipped']} | "
            f"{rate:.0f}% |"
        )
        for k in grand:
            grand[k] += counts.get(k, 0)
    grand_run = grand["passed"] + grand["failed"] + grand["errored"]
    grand_total = grand_run + grand["skipped"]
    grand_rate = (grand["passed"] / grand_run * 100) if grand_run else 0.0
    lines.append(
        f"| **Overall** | **{grand_total}** | **{grand['passed']}** | "
        f"**{grand['failed'] + grand['errored']}** | **{grand['skipped']}** | "
        f"**{grand_rate:.0f}%** |"
    )
    return "\n".join(lines)


def render_confusion(confusion_path: Path) -> str:
    if not confusion_path.exists():
        return "_Router confusion matrix not available — run `pytest tests/eval/router/` to populate `.eval/router_confusion.json`._"
    data = json.loads(confusion_path.read_text())
    domains: List[str] = data["domains"]
    pairs: List[Tuple[str, str]] = [(p["expected"], p["predicted"]) for p in data["pairs"]]
    cols = list(domains) + ["ERROR", "OTHER"]
    matrix: Dict[str, Dict[str, int]] = {d: {c: 0 for c in cols} for d in domains}
    for expected, predicted in pairs:
        if expected not in matrix:
            continue
        col = predicted if predicted in cols else "OTHER"
        matrix[expected][col] += 1
    header = "| expected \\ predicted | " + " | ".join(cols) + " |"
    sep = "|---|" + "|".join(["---:"] * len(cols)) + "|"
    rows = [header, sep]
    for d in domains:
        rows.append(f"| **{d}** | " + " | ".join(str(matrix[d][c]) for c in cols) + " |")
    return "\n".join(rows)


def render_report(suites: Dict[str, Dict[str, int]], confusion_path: Path) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"""## Evaluation

Last run: **{now} UTC**.  Reproduce with `make eval` (full suite) or
`uv run python scripts/eval_report.py` (regenerates this section).

### Per-suite results

{render_suite_table(suites)}

### Router confusion matrix

Rows are the expected domain for each fixture; columns are the domain the
router actually returned.  Diagonal = correct.  `ERROR` = the router failed
to return a routing decision.  `OTHER` = the router returned a domain not in
the MVP catalog (e.g. `Database` from a deprecated route).

{render_confusion(confusion_path)}

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
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--junit", type=Path, default=DEFAULT_JUNIT,
                        help="Path to junit XML (input/output).")
    parser.add_argument("--confusion", type=Path, default=DEFAULT_CONFUSION,
                        help="Path to the router confusion JSON.")
    parser.add_argument("--no-run", action="store_true",
                        help="Skip the pytest invocation; reuse existing artifacts.")
    parser.add_argument("--paths", nargs="*", default=["tests/eval/"],
                        help="Test paths to feed pytest (default: tests/eval/).")
    parser.add_argument("--out", type=Path, default=None,
                        help="Write the report to this file instead of stdout.")
    args = parser.parse_args()

    if not args.no_run:
        # We deliberately don't fail the report on a non-zero pytest exit —
        # eval failures are part of what we want to report on.
        run_pytest(args.junit, args.paths)

    suites = parse_junit(args.junit)
    report = render_report(suites, args.confusion)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report)
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
