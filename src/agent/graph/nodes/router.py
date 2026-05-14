"""
src/agent/graph/nodes/router.py

Router node — Phase 1 STUB.

Real behaviour (T046):
  - Call Haiku-tier LLM with structured Pydantic output.
  - Classify domain with cited_evidence ≥ 1 (FR-005/FR-008).
  - Record model ID + tokens for audit.

This stub hardcodes domain="Database", confidence="high" so the downstream
Database Expert path is exercised.
"""

from __future__ import annotations

from datetime import datetime, timezone

from src.agent.graph.state import WorkflowState
from src.shared.schemas import FilteredEvidence, LogExcerpt, RoutingDecision


def router_node(state: WorkflowState) -> WorkflowState:
    """
    STUB: return a hardcoded RoutingDecision pointing at the Database domain.

    Returns a partial WorkflowState; LangGraph merges it into the full state.
    """
    print("[router_node] STUB — routing to Database domain (hardcoded)")

    # Re-use the first hit line from filtered_evidence as cited evidence, or
    # fabricate one if the state doesn't yet have evidence (defensive).
    evidence: FilteredEvidence | None = state.get("filtered_evidence")
    if evidence and evidence.hit_lines:
        cited = [evidence.hit_lines[0]]
    else:
        cited = [
            LogExcerpt(
                timestamp=datetime(2026, 5, 14, 10, 0, 0, tzinfo=timezone.utc),
                container="app",
                text="Connection refused to db-service:5432 after 3 retries",
                byte_offset=0,
            )
        ]

    routing = RoutingDecision(
        domain="Database",
        confidence="high",
        cited_evidence=cited,
        runners_up=[("Application", "low")],
        model="stub-haiku",
        tokens=0,
    )

    return {"routing": routing}  # type: ignore[return-value]


def route_after_router(state: WorkflowState) -> str:
    """
    Conditional edge function: returns the name of the next node based on the
    Router's classification.

    LangGraph calls this after router_node and uses the returned string to
    select the next edge.
    """
    routing: RoutingDecision | None = state.get("routing")
    if routing is None:
        return "unknown_expert"

    domain_to_node = {
        "Application": "application_expert",
        "Network": "network_expert",
        "Database": "database_expert",
        "Unknown": "reporter",  # Unknown skips Expert entirely
    }
    return domain_to_node.get(routing.domain, "reporter")
