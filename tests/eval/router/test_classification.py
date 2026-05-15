"""
tests/eval/router/test_classification.py

Evaluates the router_node's ability to classify logs into Application,
Network, or Unknown domains.

Each fixture is a ``_Case`` carrying:

  * ``log_text``  — a single string or a list of strings.  When a list is
    given, each entry becomes its own ``LogExcerpt`` line with a monotonically
    increasing ``byte_offset``, so the router sees realistic multi-line
    evidence (real ``FilteredEvidence`` typically has 5–20 hit lines around
    the trigger, not one).
  * ``expected_domain`` — the ground-truth label.
  * ``max_confidence`` — when set, the router's reported confidence MUST be
    at or below this level.  Used for *weakly-signaled* fixtures where the
    correct domain is plausible but the router should not over-commit
    (e.g. ``container exited with code 1`` — defensibly Application, but
    not with ``high`` confidence).
  * ``note`` — short human-readable id used as the pytest parameter id;
    also doubles as the slide-friendly label for the confusion matrix.

Methodology note (also documented in README):

  The 10/10/10 class balance is artificially uniform.  Production traffic
  is heavily Application-skewed, so the router's bias toward Application
  for ambiguous logs may reflect a learned prior that is *correct in
  deployment*.  Treat the per-domain pass rates as upper-bound stress
  tests, not as a deployment-fidelity proxy.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import pytest

from src.agent.graph.nodes.router import router_node
from src.agent.graph.state import WorkflowState
from src.shared.schemas import (
    FilteredEvidence,
    Incident,
    LogExcerpt,
    Target,
    TimeWindow,
)


pytestmark = [pytest.mark.eval]


# ---------------------------------------------------------------------------
# Types / constants
# ---------------------------------------------------------------------------

DOMAINS = ["Application", "Network", "Unknown"]
CONF_RANK = {"low": 0, "medium": 1, "high": 2}


@dataclass(frozen=True)
class _Case:
    """One router-evaluation fixture.

    ``expected_domain`` + ``max_confidence`` describe the *preferred*
    outcome.  ``also_accept`` lists additional ``(domain, max_confidence)``
    pairs that are considered defensible — typically used when:

      * The router's safety path may legitimately demote a weakly-cited
        non-Unknown result to ``Unknown`` (the ``demote_to_unknown`` log
        line in router.py).  In that case ``Unknown`` is the *safe*
        outcome, not a misclassification.
      * Two domains are both honest reads of the same evidence
        (e.g. a stack trace surrounding a connection error — the
        ownership boundary between app code and network is a convention,
        and reasonable engineers disagree).

    Confusion-matrix bookkeeping always uses ``expected_domain`` as the
    row label, so the matrix continues to reflect the preferred ground
    truth even when an alternate is accepted.
    """

    log_text: Union[str, List[str]]
    expected_domain: str
    max_confidence: Optional[str] = None
    also_accept: Tuple[Tuple[str, Optional[str]], ...] = ()
    note: str = ""


# ---------------------------------------------------------------------------
# Session-scoped metrics + artifact
# ---------------------------------------------------------------------------

_results: Dict[str, Dict[str, int]] = {d: {"pass": 0, "fail": 0} for d in DOMAINS}
_pairs: List[Tuple[str, str]] = []


def _render_confusion_matrix(pairs: List[Tuple[str, str]]) -> str:
    cols = list(DOMAINS) + ["ERROR", "OTHER"]
    matrix: Dict[str, Dict[str, int]] = {d: {c: 0 for c in cols} for d in DOMAINS}
    for expected, predicted in pairs:
        if expected not in matrix:
            continue
        col = predicted if predicted in cols else "OTHER"
        matrix[expected][col] += 1
    label = "expected \\ pred"
    header = f"{label:<18}" + "".join(f"{c:>12}" for c in cols)
    lines = [header, "-" * len(header)]
    for d in DOMAINS:
        row = f"{d:<18}" + "".join(f"{matrix[d][c]:>12}" for c in cols)
        lines.append(row)
    return "\n".join(lines)


@pytest.fixture(scope="session", autouse=True)
def print_metrics() -> Any:
    yield
    print("\n" + "=" * 60, file=sys.stderr)
    print("CLASSIFICATION EVAL METRICS", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    total_pass = sum(d["pass"] for d in _results.values())
    total_fail = sum(d["fail"] for d in _results.values())
    total = total_pass + total_fail
    if total > 0:
        for domain, stats in _results.items():
            domain_total = stats["pass"] + stats["fail"]
            pct = (stats["pass"] / domain_total * 100) if domain_total > 0 else 0
            print(f"{domain:15}: {stats['pass']:2}/{domain_total:2} passed ({pct:3.0f}%)", file=sys.stderr)
        overall_pct = (total_pass / total * 100) if total > 0 else 0
        print("-" * 60, file=sys.stderr)
        print(f"{'OVERALL':15}: {total_pass:2}/{total:2} passed ({overall_pct:3.0f}%)", file=sys.stderr)
        print("\nConfusion matrix (rows = expected, cols = predicted):", file=sys.stderr)
        print(_render_confusion_matrix(_pairs), file=sys.stderr)

        artifact_dir = Path(os.environ.get("EVAL_ARTIFACT_DIR", ".eval"))
        artifact_dir.mkdir(parents=True, exist_ok=True)
        (artifact_dir / "router_confusion.json").write_text(
            json.dumps(
                {
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "domains": DOMAINS,
                    "pairs": [{"expected": e, "predicted": p} for e, p in _pairs],
                    "per_domain": _results,
                    "totals": {"pass": total_pass, "fail": total_fail, "total": total},
                },
                indent=2,
            )
        )
    print("=" * 60 + "\n", file=sys.stderr)


# ---------------------------------------------------------------------------
# State builder
# ---------------------------------------------------------------------------

_T0 = datetime(2026, 5, 14, 10, 0, 0, tzinfo=timezone.utc)


def _build_state(log_text: Union[str, List[str]]) -> WorkflowState:
    """Construct a WorkflowState whose FilteredEvidence carries either a
    single hit line (``log_text`` is ``str``) or several (``log_text`` is a
    list, used for multi-hit-line and multi-domain-conflict fixtures).
    """
    if isinstance(log_text, str):
        lines = [log_text] if log_text else []
    else:
        lines = list(log_text)

    hit_lines: List[LogExcerpt] = []
    offset = 0
    for line in lines:
        hit_lines.append(
            LogExcerpt(
                timestamp=_T0,
                container="app",
                text=line,
                byte_offset=offset,
            )
        )
        offset += len(line.encode("utf-8")) + 1  # +1 for the newline separator

    total_bytes = sum(len(line.encode("utf-8")) for line in lines) + max(0, len(lines) - 1)
    evidence = FilteredEvidence(
        total_bytes=total_bytes,
        total_lines=len(lines),
        hit_lines=hit_lines,
        hit_count=len(hit_lines),
        truncated=False,
        containers_sampled=["app"] if lines else [],
    )

    incident = Incident(
        correlation_id="test-correlation-id",
        source_alert_id="test-alert-id",
        dedup_fingerprint="test-dedup",
        target=Target(
            namespace="default",
            pod="test-app-123",
            container="app",
        ),
        time_window=TimeWindow(
            start=_T0,
            end=_T0 + timedelta(minutes=5),
        ),
        received_at=_T0,
        last_seen_at=_T0,
    )

    return {"filtered_evidence": evidence, "incident": incident}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TEST_CASES: List[_Case] = [
    # -------------------------------------------------------------------------
    # Application — high-confidence, unambiguous signal
    # -------------------------------------------------------------------------
    # NOTE: OOMKilled is technically a kubelet-level event.  We label it
    # Application because the actionable owner is the app team (memory leak
    # or undersized limit declared in the workload manifest).
    _Case("OOMKilled: memory limit exceeded, exit code 137", "Application", note="app-oomkilled"),
    _Case(
        "goroutine 1 [running]:\nruntime: panic: runtime error: index out of range\nmain.go:12",
        "Application",
        note="app-go-panic",
    ),
    _Case(
        "Traceback (most recent call last):\n  File 'app.py', line 10, in <module>\nValueError: invalid input",
        "Application",
        note="app-python-traceback",
    ),
    _Case(
        "java.lang.NullPointerException\n\tat com.example.App.main(App.java:10)\n\t... 15 more",
        "Application",
        note="app-java-npe",
    ),
    _Case(
        "UnhandledPromiseRejectionWarning: TypeError: Cannot read properties of undefined (reading 'id')",
        "Application",
        note="app-node-unhandled-rejection",
    ),
    _Case(
        "Error parsing config.yaml: expected mapping, but found scalar at line 5 column 1",
        "Application",
        note="app-config-parse-error",
    ),
    _Case(
        "deadlock detected: thread 1 and thread 2 waiting on mutual locks",
        "Application",
        note="app-deadlock",
    ),
    _Case("Segmentation fault (core dumped)", "Application", note="app-segfault"),
    _Case(
        "INFO: Starting up...\nDEBUG: Loaded config...\nERROR: Null reference encountered in compute_metrics\nINFO: Shutting down...",
        "Application",
        # Single-line ERROR buried in INFO/DEBUG noise — the router may
        # decline to cite (no `[]`-bracketed line index to anchor against)
        # and then the safety path demotes to Unknown.  That is the
        # designed behaviour, not a misclassification.
        also_accept=(("Unknown", None),),
        note="app-noisy-null-ref",
    ),
    _Case(
        "ERR_APP_500: Failed to process user transaction due to invariant violation in account_balance.",
        "Application",
        note="app-invariant-violation",
    ),

    # -------------------------------------------------------------------------
    # Application — adversarial: network-looking surface, app-config root cause.
    # ECONNREFUSED is the strongest "Network" signal in the corpus, but the
    # embedded ``Caused by: ConfigError`` line identifies the real owner as
    # the app team (port typo in their config).  The router should weigh
    # explicit root-cause statements over surface transport errors.
    # -------------------------------------------------------------------------
    _Case(
        log_text=(
            "ECONNREFUSED 127.0.0.1:6379\n"
            "Caused by: ConfigError: redis.port=6739 in config.yaml does not match "
            "service redis on port 6379 — typo in application config"
        ),
        expected_domain="Application",
        note="app-adversarial-econnrefused-with-configerror",
    ),

    # -------------------------------------------------------------------------
    # Application — weakly-signaled.  Defensibly Application, but the router
    # MUST express its uncertainty (``confidence <= low``) rather than fire
    # the App expert with high confidence on a one-liner.
    # -------------------------------------------------------------------------
    # For all weak cases: Application is the *preferred* read, but the
    # only thing CI should hard-fail on is **Application + high
    # confidence** (genuine over-commit).  ``Unknown`` at any confidence
    # is also acceptable — abstaining on a one-liner is the safe call,
    # not a defect.
    _Case(
        "container exited with code 1",
        "Application",
        max_confidence="medium",
        also_accept=(("Unknown", None),),
        note="app-weak-exit1",
    ),
    _Case(
        "Liveness probe failed: HTTP probe failed with statuscode: 500",
        "Application",
        max_confidence="medium",
        also_accept=(("Unknown", None),),
        note="app-weak-liveness-500",
    ),
    _Case(
        "WARN: Something went wrong processing request, skipping",
        "Application",
        max_confidence="medium",
        also_accept=(("Unknown", None),),
        note="app-weak-vague-request-error",
    ),
    _Case(
        "Error in processing data: ",
        "Application",
        max_confidence="medium",
        also_accept=(("Unknown", None),),
        note="app-weak-truncated-error",
    ),
    _Case(
        "Timeout occurred",
        "Application",
        max_confidence="medium",
        also_accept=(("Unknown", None),),
        note="app-weak-bare-timeout",
    ),

    # -------------------------------------------------------------------------
    # Network — clear transport-layer failures.
    # -------------------------------------------------------------------------
    _Case("ECONNREFUSED connecting to 10.0.0.5:8080", "Network", note="net-econnrefused"),
    _Case(
        "getaddrinfo ENOTFOUND my-service.default.svc.cluster.local",
        "Network",
        note="net-dns-notfound",
    ),
    _Case(
        "tls: failed to verify certificate: x509: certificate has expired or is not yet valid",
        "Network",
        note="net-tls-expired",
    ),
    _Case("dial tcp 10.0.0.5:5432: i/o timeout after 30s", "Network", note="net-tcp-timeout"),
    _Case(
        "context deadline exceeded while awaiting headers from upstream",
        "Network",
        note="net-context-deadline",
    ),
    # Disambiguated 502: explicitly upstream-received, not emitted by the app
    # under test.  The previous bare ``HTTP/1.1 502 Bad Gateway`` was
    # genuinely under-specified (could be the app emitting 502 itself).
    _Case(
        "received HTTP/1.1 502 Bad Gateway from upstream backend at 10.0.0.7:80",
        "Network",
        note="net-upstream-502",
    ),
    _Case(
        "read tcp 10.0.0.2:8080->10.0.0.3:5000: read: connection reset by peer",
        "Network",
        note="net-conn-reset",
    ),
    _Case("connect: no route to host", "Network", note="net-no-route"),
    # NOTE: A Redis connection timeout would conventionally route to a
    # Database expert, but the MVP collapses DB into the Reporter without a
    # dedicated expert (spec 002 §Database expert removed).  We label this
    # Network because it is the transport-layer signal the router can act on
    # in the current architecture.  Revisit when DB expert is reintroduced.
    _Case(
        "DEBUG: resolving hostname\nDEBUG: dial started\nERROR: Failed to connect to redis: connection timeout\nDEBUG: retrying",
        "Network",
        # The router may demote to Unknown when it cannot cite a clean
        # ERROR line (the redis/connection signal is one line of four).
        # Abstention is acceptable for an MVP that has no DB expert.
        also_accept=(("Unknown", None),),
        note="net-redis-timeout-mvp",
    ),
    _Case(
        "upstream_reset_before_response_started{connection_failure}",
        "Network",
        note="net-envoy-upstream-reset",
    ),

    # -------------------------------------------------------------------------
    # Multi-hit-line fixture.  Real FilteredEvidence carries a context window
    # around each trigger; classifying on the *primary* line while ignoring
    # boilerplate is the actual router skill.  Here only one line carries
    # signal; the others are scheduling/probe noise.
    # -------------------------------------------------------------------------
    _Case(
        log_text=[
            "INFO scheduling pod test-app-123 to node-7",
            "INFO image pulled successfully",
            "INFO readiness probe succeeded",
            "ERROR dial tcp 10.0.0.5:5432: connect: network is unreachable",
            "INFO readiness probe failed: connection refused",
        ],
        expected_domain="Network",
        note="net-multi-line-context-window",
    ),

    # -------------------------------------------------------------------------
    # Multi-domain-conflict fixture.  Two failures co-occur: a Python
    # traceback (Application) AND a connection-refused error (Network).
    # The convention we encode: the deeper, more specific root cause wins,
    # and here the traceback contains the stack frame where the connection
    # call was issued — the app code is the actionable owner.  If the
    # router disagrees, document the decision and update either this
    # ground-truth or the router prompt — but it should be deterministic.
    # -------------------------------------------------------------------------
    _Case(
        log_text=[
            "ERROR Failed to connect to payments-svc:443: connection refused",
            "Traceback (most recent call last):",
            "  File 'app/billing.py', line 88, in charge_card",
            "    response = http.post(PAYMENTS_URL, json=payload, timeout=2)",
            "ConnectionRefusedError: [Errno 111] Connection refused",
        ],
        expected_domain="Application",
        max_confidence="medium",
        # Network is also a defensible read of the same evidence: the
        # transport-level error is the root cause from the *cluster's*
        # perspective even though the stack frame lives in app code.
        # We accept either domain and let the confusion matrix surface
        # whichever convention the router currently encodes.
        also_accept=(("Network", None),),
        note="conflict-traceback-around-econnrefused",
    ),

    # -------------------------------------------------------------------------
    # Unknown — genuinely insufficient signal.  No reasonable classifier
    # should commit to a domain on these.
    # -------------------------------------------------------------------------
    _Case(
        '10.0.0.1 - - [14/May/2026:10:00:00 +0000] "GET /health HTTP/1.1" 200 15 "-" "kube-probe/1.28"',
        "Unknown",
        note="unk-healthy-access-log",
    ),
    _Case("WARN: CPU usage is high: 95%", "Unknown", note="unk-cpu-warning"),
    _Case("", "Unknown", note="unk-empty"),
    _Case(
        "Fluent Bit v2.1.0\n* Copyright (C) 2015-2022 The Fluent Bit Authors",
        "Unknown",
        note="unk-fluentbit-banner",
    ),
    _Case("\x00\x01\x02\x03garbage\x04\x05", "Unknown", note="unk-binary-garbage"),
]


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", TEST_CASES, ids=[c.note for c in TEST_CASES])
def test_router_classification(case: _Case) -> None:
    """Run the router on each fixture and assert (a) the predicted domain
    matches ``expected_domain``, and (b) when ``max_confidence`` is set, the
    router's reported confidence is at or below that ceiling.

    Failing (b) but passing (a) means the router got the label right but
    over-committed — for weakly-signaled inputs that's a calibration defect,
    not a hallucination, and we still want CI to surface it.
    """
    state = _build_state(case.log_text)

    result_state = router_node(state)

    routing = result_state.get("routing")
    if routing is None:
        _pairs.append((case.expected_domain, "ERROR"))
        _results[case.expected_domain]["fail"] += 1
        pytest.fail(f"[{case.note}] router_node did not return a routing decision")

    predicted = routing.domain
    _pairs.append((case.expected_domain, predicted))

    def _matches(domain: str, ceiling: Optional[str]) -> bool:
        if predicted != domain:
            return False
        if ceiling is None:
            return True
        return CONF_RANK[routing.confidence] <= CONF_RANK[ceiling]

    candidates: List[Tuple[str, Optional[str]]] = [
        (case.expected_domain, case.max_confidence),
        *case.also_accept,
    ]

    try:
        if not any(_matches(d, c) for d, c in candidates):
            # Build a human-readable failure summary that distinguishes
            # the two failure modes (wrong domain vs over-commit on the
            # right domain).
            domain_match = any(predicted == d for d, _ in candidates)
            if domain_match:
                reason = (
                    f"Domain is acceptable ({predicted}) but confidence "
                    f"{routing.confidence!r} exceeds every ceiling allowed "
                    f"by this fixture."
                )
            else:
                reason = (
                    f"Domain {predicted!r} is not among the accepted outcomes."
                )
            accepted_repr = ", ".join(
                f"{d}({c or 'any'})" for d, c in candidates
            )
            raise AssertionError(
                f"[{case.note}] Router outcome rejected.\n"
                f"Input log:\n{case.log_text}\n\n"
                f"Predicted:  {predicted} ({routing.confidence})\n"
                f"Accepted:   {accepted_repr}\n"
                f"Reason:     {reason}\n"
                f"Runners up: {routing.runners_up}"
            )
        _results[case.expected_domain]["pass"] += 1
    except Exception:
        _results[case.expected_domain]["fail"] += 1
        raise
