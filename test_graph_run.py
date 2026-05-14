#!/usr/bin/env python3
"""
test_graph_run.py

Smoke-test / demo script for the Phase 1 LangGraph scaffold.

Invokes the graph with a fake Alertmanager-style webhook payload,
runs all stub nodes end-to-end, and pretty-prints the final WorkflowState.

Usage::

    python test_graph_run.py

No external services required (no LLM, no MCP, no DB, no Slack).
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
from src.shared.schemas import Incident, Target, TimeWindow


# ---------------------------------------------------------------------------
# Build a fake initial WorkflowState from a synthetic webhook payload
# ---------------------------------------------------------------------------
def make_fake_initial_state() -> WorkflowState:
    """
    Simulate the Ingest node's *pre-state* — i.e. the fields that the webhook
    handler populates before handing off to the LangGraph graph.

    In Phase 1 the Ingest node will fill in filtered_evidence, so we only
    need correlation_id + incident here.
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

    # Budget initialised to generous stubs (no real cost tracking yet)
    return WorkflowState(
        correlation_id=incident.correlation_id,
        incident=incident,
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
    print("  test_graph_run.py — Phase 1 LangGraph scaffold smoke-test")
    print("=" * 64)

    # Build and compile the graph
    print("\n[setup] Compiling LangGraph StateGraph …")
    graph = build_graph()
    print("[setup] Graph compiled successfully.")

    # Prepare fake initial state
    initial_state = make_fake_initial_state()
    print(
        f"\n[setup] Initial state ready — correlation_id={initial_state['correlation_id']}"
    )
    print(f"[setup] Target: {initial_state['incident'].target.namespace}/"
          f"{initial_state['incident'].target.pod}")

    # Invoke the graph
    print("\n[run] Invoking graph …\n")
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

    evidence = final_state.get("filtered_evidence")
    if evidence:
        _print_section(
            "filtered_evidence summary",
            (
                f"  total_bytes={evidence.total_bytes}  "
                f"total_lines={evidence.total_lines}  "
                f"hit_count={evidence.hit_count}  "
                f"truncated={evidence.truncated}"
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

    print("\n" + "=" * 64)
    print("  END OF RUN — all stub nodes executed successfully.")
    print("=" * 64 + "\n")


if __name__ == "__main__":
    main()
