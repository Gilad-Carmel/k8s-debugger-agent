#!/usr/bin/env python3
"""
test_graph_run.py

Smoke-test / demo script for the Phase 2 Router LLM call.

Invokes the graph with a synthetic Alertmanager-style webhook payload
**and** a fake ``FilteredEvidence`` containing Go panic log lines.
The router node makes a real Anthropic Haiku LLM call and should classify
the incident as ``Application`` with at least one ``cited_evidence`` entry.

Usage::

    python test_graph_run.py

Requirements:
  - ``ANTHROPIC_API_KEY`` must be set (env var or .env file at repo root).
  - All other nodes (Ingest, Experts, Reporter, Solver) remain stubs.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone

# ---------------------------------------------------------------------------
# Bootstrap: add repo root to sys.path so `src.*` imports resolve when the
# script is run directly from the repo root.
# ---------------------------------------------------------------------------
import os
sys.path.insert(0, os.path.dirname(__file__))

from src.agent.graph.builder import build_graph
from src.agent.graph.state import WorkflowState
from src.shared.schemas import (
    FilteredEvidence,
    Incident,
    LogExcerpt,
    Target,
    TimeWindow,
)


# ---------------------------------------------------------------------------
# Fake FilteredEvidence: Go panic log
#
# A realistic Go runtime panic cascade that any classifier should label
# as "Application" — code-level crash, not network or database.
# ---------------------------------------------------------------------------
def make_fake_filtered_evidence() -> FilteredEvidence:
    """
    Build a ``FilteredEvidence`` with Go panic log lines.

    The router LLM should:
      - Classify domain as ``"Application"``
      - Populate ``cited_evidence`` with at least one of these lines
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


# ---------------------------------------------------------------------------
# Build a fake initial WorkflowState from a synthetic webhook payload
# ---------------------------------------------------------------------------
def make_fake_initial_state() -> WorkflowState:
    """
    Simulate the Ingest node's output — the fields that the webhook handler
    and the Ingest node populate before handing off to the Router.

    ``filtered_evidence`` is pre-populated with Go panic lines so the Router
    LLM has something concrete to classify.
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
        filtered_evidence=make_fake_filtered_evidence(),
        budget_remaining_tokens=50_000,
        budget_remaining_usd_micros=500_000,  # $0.50 in micros
    )


# ---------------------------------------------------------------------------
# Helpers for pretty output
# ---------------------------------------------------------------------------
def _safe_dump(obj: object) -> str:
    """Best-effort JSON serialisation with datetime / pydantic support."""
    try:
        # pydantic models
        if hasattr(obj, "model_dump"):
            return json.dumps(obj.model_dump(mode="json"), indent=2)
        return json.dumps(obj, indent=2, default=str)
    except Exception as exc:  # noqa: BLE001
        return f"<serialisation error: {exc}>"


def _print_section(title: str, content: str) -> None:
    print(f"\n{'─' * 64}")
    print(f"  {title}")
    print(f"{'─' * 64}")
    print(content)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    print("=" * 64)
    print("  test_graph_run.py - Phase 2 Router LLM smoke-test")
    print("  Go panic evidence -> expect domain='Application'")
    print("=" * 64)

    # Build and compile the graph
    print("\n[setup] Compiling LangGraph StateGraph ...")
    graph = build_graph()
    print("[setup] Graph compiled successfully.")

    # Prepare fake initial state (includes Go panic FilteredEvidence)
    initial_state = make_fake_initial_state()
    print(
        f"\n[setup] Initial state ready - correlation_id={initial_state['correlation_id']}"
    )
    print(f"[setup] Target: {initial_state['incident'].target.namespace}/"
          f"{initial_state['incident'].target.pod}")

    evidence = initial_state.get("filtered_evidence")
    if evidence:
        print(
            f"[setup] FilteredEvidence: {evidence.hit_count} hit lines  "
            f"containers={evidence.containers_sampled}"
        )
        print("[setup] First hit line: "
              f"{evidence.hit_lines[0].text!r}")

    # Invoke the graph
    print("\n[run] Invoking graph (Router will call local LLM via Ollama) ...\n")
    final_state: WorkflowState = graph.invoke(initial_state)  # type: ignore[assignment]

    # Pretty-print results
    print("\n" + "=" * 64)
    print("  FINAL STATE")
    print("=" * 64)

    print(f"\n  correlation_id : {final_state.get('correlation_id')}")

    routing = final_state.get("routing")
    if routing:
        _print_section(
            "routing (RoutingDecision)",
            _safe_dump(routing),
        )

    diagnosis = final_state.get("diagnosis")
    if diagnosis:
        _print_section(
            "diagnosis (ExpertDiagnosis)",
            _safe_dump(diagnosis),
        )

    report = final_state.get("report")
    if report:
        _print_section(
            "report (Report)",
            _safe_dump(report),
        )

    solver_run = final_state.get("solver_run")
    if solver_run:
        _print_section(
            "solver_run (SolverRun)",
            _safe_dump(solver_run),
        )

    evidence_out = final_state.get("filtered_evidence")
    if evidence_out:
        _print_section(
            "filtered_evidence summary",
            (
                f"  total_bytes={evidence_out.total_bytes}  "
                f"total_lines={evidence_out.total_lines}  "
                f"hit_count={evidence_out.hit_count}  "
                f"truncated={evidence_out.truncated}"
            ),
        )

    budget_tokens = final_state.get("budget_remaining_tokens", "N/A")
    budget_usd = final_state.get("budget_remaining_usd_micros", "N/A")
    print(f"\n  budget_remaining_tokens     : {budget_tokens}")
    print(f"  budget_remaining_usd_micros : {budget_usd}")

    final_status = report.status if report else "UNKNOWN"
    solver_outcome = solver_run.outcome if solver_run else "NOT RUN"
    print(f"\n  report.status  : {final_status}")
    print(f"  solver outcome : {solver_outcome}")

    # ------------------------------------------------------------------
    # Verification: assert the LLM routed Go panic → Application
    # ------------------------------------------------------------------
    print("\n" + "=" * 64)
    print("  VERIFICATION")
    print("=" * 64)

    errors: list[str] = []

    if routing is None:
        errors.append("FAIL: routing is None — router_node did not set state['routing']")
    else:
        if routing.domain != "Application":
            errors.append(
                f"FAIL: expected domain='Application', got {routing.domain!r}.  "
                "Check the Go panic evidence and router prompt."
            )
        else:
            print(f"  PASS  domain={routing.domain!r}  (expected 'Application')")

        if not routing.cited_evidence:
            errors.append(
                "FAIL: cited_evidence is empty — router must cite ≥1 evidence "
                "line for a non-Unknown domain (Principle IV, spec FR-007)."
            )
        else:
            print(
                f"  PASS  cited_evidence has {len(routing.cited_evidence)} item(s)"
            )
            for i, exc in enumerate(routing.cited_evidence):
                print(f"        [{i}] {exc.text!r}")

        print(
            f"  INFO  confidence={routing.confidence!r}  "
            f"model={routing.model!r}  tokens={routing.tokens}"
        )

    if errors:
        print("\n  --- FAILURES ---")
        for err in errors:
            print(f"  {err}")
        print("\n" + "=" * 64)
        print("  SMOKE TEST FAILED")
        print("=" * 64 + "\n")
        sys.exit(1)
    else:
        print("\n" + "=" * 64)
        print("  SMOKE TEST PASSED — Router correctly classified Go panic as Application")
        print("=" * 64 + "\n")


if __name__ == "__main__":
    main()
