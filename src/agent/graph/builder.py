"""
src/agent/graph/builder.py

LangGraph wiring. Returns a compiled graph that Person 3 owns end-to-end.
Person 1 swaps in real node bodies in src/agent/graph/nodes/_placeholders.py
without touching this file (signatures + state contract are stable).

Topology:

    START
      ↓
    ingest
      ↓
    router
      ↓ (conditional on classification)
    {application_expert | network_expert | database_expert | <skip>}
      ↓
    reporter
      ↓
    [INTERRUPT BEFORE solver]   ← HITL gate; resumes when callbacks.py
                                  sets approval_status and re-invokes graph
      ↓ (conditional on approval_status)
    {solver | END}
      ↓
    END

Conditional edges use only `classification` (after router) and
`approval_status` (after reporter) — no other branching keys.
"""
from __future__ import annotations

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from src.agent.graph.nodes import _placeholders as nodes
from src.agent.graph.state import WorkflowState


def _route_by_classification(state: WorkflowState) -> str:
    classification = state.get("classification", "UNKNOWN")
    if classification == "APP":
        return "application_expert"
    if classification == "NET":
        return "network_expert"
    if classification == "DB":
        return "database_expert"
    # UNKNOWN short-circuits past Experts straight to Reporter.
    return "reporter"


def _route_by_approval(state: WorkflowState) -> str:
    if state.get("approval_status") == "APPROVED":
        return "solver"
    # REJECTED, EXPIRED, or anything else terminates without mutation.
    return END


def build_graph(checkpointer: BaseCheckpointSaver) -> "CompiledStateGraph":  # type: ignore[name-defined]
    """
    Build and compile the workflow graph with the supplied checkpointer.

    The checkpointer makes the graph resumable across the HITL interrupt:
    callbacks.py uses the same correlation_id as `thread_id` to wake the
    paused run.
    """
    g: StateGraph = StateGraph(WorkflowState)

    g.add_node("ingest", nodes.ingest_node)
    g.add_node("router", nodes.router_node)
    g.add_node("application_expert", nodes.application_expert_node)
    g.add_node("network_expert", nodes.network_expert_node)
    g.add_node("database_expert", nodes.database_expert_node)
    g.add_node("reporter", nodes.reporter_node)
    g.add_node("solver", nodes.solver_node)

    g.add_edge(START, "ingest")
    g.add_edge("ingest", "router")
    g.add_conditional_edges(
        "router",
        _route_by_classification,
        {
            "application_expert": "application_expert",
            "network_expert": "network_expert",
            "database_expert": "database_expert",
            "reporter": "reporter",
        },
    )
    g.add_edge("application_expert", "reporter")
    g.add_edge("network_expert", "reporter")
    g.add_edge("database_expert", "reporter")
    # After Reporter we PAUSE for HITL approval. Resume happens in callbacks.py.
    g.add_conditional_edges(
        "reporter",
        _route_by_approval,
        {
            "solver": "solver",
            END: END,
        },
    )
    g.add_edge("solver", END)

    return g.compile(checkpointer=checkpointer, interrupt_before=["solver"])
