"""
tests/integration/test_graph_run.py

Pytest-compatible version of the repo-root smoke-test (test_graph_run.py).

Invokes the compiled LangGraph with a synthetic Alertmanager-style webhook
payload and fake FilteredEvidence containing Go panic log lines.  The router
node makes a real LLM call (via the configured OAI-compatible server) and
should classify the incident as ``Application`` with at least one
``cited_evidence`` entry.

Run with:
    uv run pytest tests/integration/test_graph_run.py -v -s

Marked ``integration``: skipped automatically when the LLM server is not
reachable (i.e. CI without a running inference server).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.agent.graph.builder import build_graph
from src.agent.graph.state import WorkflowState
from src.shared.schemas import (
    FilteredEvidence,
    Incident,
    LogExcerpt,
    Target,
    TimeWindow,
)


pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def go_panic_evidence() -> FilteredEvidence:
    """
    FilteredEvidence with a realistic Go runtime panic cascade.

    Any classifier should label this as "Application" — code-level crash,
    not network or database.
    """
    base_ts = datetime(2026, 5, 14, 10, 0, 1, tzinfo=timezone.utc)
    panic_lines: list[LogExcerpt] = [
        LogExcerpt(
            timestamp=base_ts,
            container="api-server",
            text="panic: runtime error: index out of range [3] with length 3",
            byte_offset=0,
        ),
        LogExcerpt(
            timestamp=base_ts,
            container="api-server",
            text="goroutine 1 [running]:",
            byte_offset=58,
        ),
        LogExcerpt(
            timestamp=base_ts,
            container="api-server",
            text="main.processRequest(0xc000104000, 0x3)",
            byte_offset=80,
        ),
        LogExcerpt(
            timestamp=base_ts,
            container="api-server",
            text="\t/app/handlers/api.go:42 +0x1a4",
            byte_offset=119,
        ),
        LogExcerpt(
            timestamp=base_ts,
            container="api-server",
            text="net/http.HandlerFunc.ServeHTTP(0xc0001a4000, {0x7f8b, 0x2a})",
            byte_offset=151,
        ),
        LogExcerpt(
            timestamp=base_ts.replace(second=2),
            container="api-server",
            text="exit status 2",
            byte_offset=214,
        ),
    ]
    return FilteredEvidence(
        total_bytes=2048,
        total_lines=420,
        hit_lines=panic_lines,
        hit_count=len(panic_lines),
        truncated=False,
        containers_sampled=["api-server"],
    )


@pytest.fixture()
def initial_state(go_panic_evidence: FilteredEvidence) -> WorkflowState:
    """
    Simulate the Ingest node's output: the fields that the webhook handler
    and Ingest node populate before handing off to the Router.
    ``filtered_evidence`` is pre-populated so the ingest stub is a no-op.
    """
    now = datetime.now(tz=timezone.utc)
    target = Target(namespace="production", pod="api-server-7d9f8b-xk2p4")
    incident = Incident(
        correlation_id="0190abcd-ef01-7abc-8def-012345678901",
        source_alert_id="alertmanager-group-key-abc123",
        dedup_fingerprint="sha256:fake-fingerprint-aabbcc",
        target=target,
        time_window=TimeWindow(
            start=now - timedelta(minutes=10),
            end=now,
        ),
        received_at=now,
        last_seen_at=now,
        status="pending",
    )
    return WorkflowState(
        correlation_id=incident.correlation_id,
        incident=incident,
        filtered_evidence=go_panic_evidence,
        budget_remaining_tokens=50_000,
        budget_remaining_usd_micros=500_000,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_router_classifies_go_panic_as_application(
    initial_state: WorkflowState,
) -> None:
    """
    End-to-end smoke-test: compile the graph, feed it Go panic evidence, and
    assert the router classifies the domain as 'Application' with at least one
    cited evidence line.

    This exercises:
      - graph compilation (builder.py)
      - ingest stub (pass-through when evidence is pre-populated)
      - router_node LLM call (json_mode, local inference server)
      - expert stub for Application domain
      - reporter stub
      - solver stub
    """
    graph = build_graph()
    final_state: WorkflowState = graph.invoke(initial_state)  # type: ignore[assignment]

    routing = final_state.get("routing")

    assert routing is not None, (
        "routing is None — router_node did not set state['routing'].  "
        "Check that the LLM call succeeded and router_node returned a partial state."
    )

    assert routing.domain == "Application", (
        f"Expected domain='Application' for Go panic evidence, got {routing.domain!r}.  "
        f"confidence={routing.confidence!r}  "
        f"cited={len(routing.cited_evidence)}  "
        f"model={routing.model!r}"
    )

    assert routing.cited_evidence, (
        "cited_evidence is empty — the router must cite ≥1 evidence line for a "
        "non-Unknown domain (Principle IV, spec FR-007)."
    )

    # Soft checks printed for debugging; not hard failures.
    print(
        f"\n[test_router_classifies_go_panic_as_application] "
        f"domain={routing.domain!r}  "
        f"confidence={routing.confidence!r}  "
        f"cited={len(routing.cited_evidence)}  "
        f"tokens={routing.tokens}  "
        f"model={routing.model!r}"
    )
    for i, exc in enumerate(routing.cited_evidence):
        print(f"  cited[{i}]: {exc.text!r}")
