"""
src/agent/graph/nodes/solver.py

Solver node — Phase 1 STUB.

Real behaviour (T083):
  - NO LLM call (research.md §R2 — LLM in the write path is a Principle I
    violation).
  - Verify proposed_fix.fingerprint against the frozen Report.
  - Capture pre-state via get_pod / Deployment read.
  - Call the matching MCP write tool with the signed approval token.
  - Verify post-state within the window.
  - Compute Inverse Action from pre_state via shared/catalog.py mapping.
  - Build and return a SolverRun.

This stub skips all external calls and returns a hardcoded SolverRun with
outcome="success" so the graph can be executed end-to-end.
"""

from __future__ import annotations

from datetime import datetime, timezone

from src.agent.graph.state import WorkflowState
from src.shared.schemas import ReversalRecipe, SolverRun


def solver_node(state: WorkflowState) -> WorkflowState:
    """
    STUB: return a hardcoded SolverRun (no MCP, no cluster mutation).

    Returns a partial WorkflowState; LangGraph merges it into the full state.
    """
    print("[solver_node] STUB — returning hardcoded SolverRun (no real execution)")

    correlation_id: str = state.get("correlation_id", "stub-correlation-id")
    report = state.get("report")
    fingerprint = (
        report.proposed_fix.fingerprint
        if (report and report.proposed_fix)
        else "stub-fingerprint"
    )

    now = datetime.now(tz=timezone.utc)

    reversal = ReversalRecipe(
        description="Rollback to revision 2 (pre-migration revision).",
        inverse_action="rollback-deployment",
        inverse_parameters={"to_revision": 2},
    )

    solver_run = SolverRun(
        correlation_id=correlation_id,
        proposed_fix_fingerprint=fingerprint,
        pre_state={"revision": 3, "ready_replicas": 0},
        action_issued={"action_type": "rollback-deployment", "to_revision": 3},
        post_state={"revision": 2, "ready_replicas": 2},
        outcome="success",
        reversal_recipe=reversal,
        error=None,
        started_at=now,
        finished_at=now,
    )

    print(
        f"[solver_node] outcome={solver_run.outcome}  "
        f"reversal={solver_run.reversal_recipe.description}"
    )

    return {"solver_run": solver_run}  # type: ignore[return-value]
