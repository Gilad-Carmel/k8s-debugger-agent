"""
tests/eval/router/test_classification.py

Evaluates the router_node's ability to classify logs into Application, Network, or Unknown domains.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone, timedelta
from typing import Dict, Any

import pytest

from src.agent.graph.nodes.router import router_node
from src.agent.graph.state import WorkflowState
from src.shared.schemas import FilteredEvidence, LogExcerpt, Incident, Target, TimeWindow


pytestmark = [pytest.mark.eval]

_results: Dict[str, Dict[str, int]] = {
    "Application": {"pass": 0, "fail": 0},
    "Network": {"pass": 0, "fail": 0},
    "Unknown": {"pass": 0, "fail": 0}
}

@pytest.fixture(scope="session", autouse=True)
def print_metrics() -> Any:
    yield
    print("\n" + "="*50, file=sys.stderr)
    print("CLASSIFICATION EVAL METRICS", file=sys.stderr)
    print("="*50, file=sys.stderr)
    total_pass = sum(d["pass"] for d in _results.values())
    total_fail = sum(d["fail"] for d in _results.values())
    total = total_pass + total_fail
    if total > 0:
        for domain, stats in _results.items():
            domain_total = stats["pass"] + stats["fail"]
            pct = (stats["pass"] / domain_total * 100) if domain_total > 0 else 0
            print(f"{domain:15}: {stats['pass']:2}/{domain_total:2} passed ({pct:3.0f}%)", file=sys.stderr)
        overall_pct = (total_pass / total * 100) if total > 0 else 0
        print("-" * 50, file=sys.stderr)
        print(f"{'OVERALL':15}: {total_pass:2}/{total:2} passed ({overall_pct:3.0f}%)", file=sys.stderr)
    print("="*50 + "\n", file=sys.stderr)

_T0 = datetime(2026, 5, 14, 10, 0, 0, tzinfo=timezone.utc)

def _build_state(log_text: str) -> WorkflowState:
    """Helper to construct a WorkflowState with a single log text."""
    hit_lines = []
    if log_text:
        hit_lines.append(LogExcerpt(
            timestamp=_T0,
            container="app",
            text=log_text,
            byte_offset=0
        ))
        
    evidence = FilteredEvidence(
        total_bytes=len(log_text),
        total_lines=1 if log_text else 0,
        hit_lines=hit_lines,
        hit_count=len(hit_lines),
        truncated=False,
        containers_sampled=["app"] if log_text else [],
    )
    
    incident = Incident(
        correlation_id="test-correlation-id",
        source_alert_id="test-alert-id",
        dedup_fingerprint="test-dedup",
        target=Target(
            namespace="default",
            pod="test-app-123",
            container="app"
        ),
        time_window=TimeWindow(
            start=_T0,
            end=_T0 + timedelta(minutes=5)
        ),
        received_at=_T0,
        last_seen_at=_T0
    )
    
    return {"filtered_evidence": evidence, "incident": incident}


TEST_CASES = [
    # -------------------------------------------------------------------------
    # Application (10 variations)
    # -------------------------------------------------------------------------
    ("OOMKilled: memory limit exceeded, exit code 137", "Application"),
    ("goroutine 1 [running]:\nruntime: panic: runtime error: index out of range\nmain.go:12", "Application"),
    ("Traceback (most recent call last):\n  File 'app.py', line 10, in <module>\nValueError: invalid input", "Application"),
    ("java.lang.NullPointerException\n\tat com.example.App.main(App.java:10)\n\t... 15 more", "Application"),
    ("UnhandledPromiseRejectionWarning: TypeError: Cannot read properties of undefined (reading 'id')", "Application"),
    ("Error parsing config.yaml: expected mapping, but found scalar at line 5 column 1", "Application"),
    ("deadlock detected: thread 1 and thread 2 waiting on mutual locks", "Application"),
    ("Segmentation fault (core dumped)", "Application"),
    ("INFO: Starting up...\nDEBUG: Loaded config...\nERROR: Null reference encountered in compute_metrics\nINFO: Shutting down...", "Application"),
    ("ERR_APP_500: Failed to process user transaction due to invariant violation in account_balance.", "Application"),

    # -------------------------------------------------------------------------
    # Network (10 variations)
    # -------------------------------------------------------------------------
    ("ECONNREFUSED connecting to 10.0.0.5:8080", "Network"),
    ("getaddrinfo ENOTFOUND my-service.default.svc.cluster.local", "Network"),
    ("tls: failed to verify certificate: x509: certificate has expired or is not yet valid", "Network"),
    ("dial tcp 10.0.0.5:5432: i/o timeout after 30s", "Network"),
    ("context deadline exceeded while awaiting headers from upstream", "Network"),
    ("HTTP/1.1 502 Bad Gateway", "Network"),
    ("read tcp 10.0.0.2:8080->10.0.0.3:5000: read: connection reset by peer", "Network"),
    ("connect: no route to host", "Network"),
    ("DEBUG: resolving hostname\nDEBUG: dial started\nERROR: Failed to connect to redis: connection timeout\nDEBUG: retrying", "Network"),
    ("upstream_reset_before_response_started{connection_failure}", "Network"),

    # -------------------------------------------------------------------------
    # Unknown (10 variations)
    # -------------------------------------------------------------------------
    ("container exited with code 1", "Unknown"),
    ("Liveness probe failed: HTTP probe failed with statuscode: 500", "Unknown"),
    ("WARN: Something went wrong processing request, skipping", "Unknown"),
    ('10.0.0.1 - - [14/May/2026:10:00:00 +0000] "GET /health HTTP/1.1" 200 15 "-" "kube-probe/1.28"', "Unknown"),
    ("Error in processing data: ", "Unknown"),
    ("WARN: CPU usage is high: 95%", "Unknown"),
    ("", "Unknown"),
    ("Timeout occurred", "Unknown"),
    ("Fluent Bit v2.1.0\n* Copyright (C) 2015-2022 The Fluent Bit Authors", "Unknown"),
    ("\x00\x01\x02\x03garbage\x04\x05", "Unknown"),
]


@pytest.mark.parametrize("log_text, expected_domain", TEST_CASES)
def test_router_classification(log_text: str, expected_domain: str) -> None:
    """
    Tests that the router_node correctly classifies various log snippets.
    This is an evaluation test that invokes the actual local LLM.
    """
    state = _build_state(log_text)
    
    # Run the router node
    result_state = router_node(state)
    
    routing = result_state.get("routing")
    if routing is None:
        _results[expected_domain]["fail"] += 1
        pytest.fail("router_node did not return a routing decision")
        
    try:
        assert routing.domain == expected_domain, (
            f"Classification failed!\n"
            f"Input log:\n{log_text}\n\n"
            f"Expected domain: {expected_domain}\n"
            f"Actual domain: {routing.domain}\n"
            f"Confidence: {routing.confidence}\n"
            f"Runners up: {routing.runners_up}"
        )
        _results[expected_domain]["pass"] += 1
    except Exception:
        _results[expected_domain]["fail"] += 1
        raise
