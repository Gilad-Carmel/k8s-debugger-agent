"""
src/agent/graph/builder.py

LangGraph wiring for the routed triage-and-remediation workflow.

Topology:

    START
      |
    ingest
      |
    router
      | (conditional on routing.domain via route_after_router)
    {application_expert | network_expert | database_expert | reporter}
      |
    reporter
      |
    [INTERRUPT BEFORE solver]   <-- HITL gate; resumes when callbacks.py
                                    sets approval_status and re-invokes graph
      | (conditional on approval_status)
    {solver | END}
      |
    END

Conditional edges use exactly two discriminants — `routing.domain` after
the router, and `approval_status` after the reporter. Anything other than
'APPROVED' on the post-interrupt edge terminates the run without invoking
the Solver (FR-015 safety invariant).
"""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from src.agent.graph.nodes.experts.application import application_expert_node
from src.agent.graph.nodes.experts.database import database_expert_node
from src.agent.graph.nodes.experts.network import network_expert_node
from src.agent.graph.nodes.ingest import ingest_node
from src.agent.graph.nodes.reporter import reporter_followup_node, reporter_node
from src.agent.graph.nodes.router import route_after_router, router_node
from src.agent.graph.nodes.solver import solver_node
from src.agent.graph.state import WorkflowState


def _route_after_reporter(state: WorkflowState) -> str:
    """Post-interrupt edge: only APPROVED proceeds to the Solver."""
    if state.get("approval_status") == "APPROVED":
        return "solver"
    # REJECTED, EXPIRED, missing — terminate without mutation.
    return END


def build_graph(checkpointer: BaseCheckpointSaver | None = None) -> Any:
    """
    Assemble and compile the StateGraph.

    The optional `checkpointer` enables interrupt + resume across the
    HITL gate; when omitted the graph still compiles for unit-style
    end-to-end runs but cannot be resumed across process restarts.
    """
    builder = StateGraph(WorkflowState)

    # Nodes
    builder.add_node("ingest", ingest_node)
    builder.add_node("router", router_node)
    builder.add_node("application_expert", application_expert_node)
    builder.add_node("network_expert", network_expert_node)
    builder.add_node("database_expert", database_expert_node)
    builder.add_node("reporter", reporter_node)
    builder.add_node("solver", solver_node)
    builder.add_node("reporter_followup", reporter_followup_node)

    # Linear entry
    builder.add_edge(START, "ingest")
    builder.add_edge("ingest", "router")

    # Router -> one of four (Unknown short-circuits past Experts to Reporter).
    builder.add_conditional_edges(
        "router",
        route_after_router,
        {
            "application_expert": "application_expert",
            "network_expert": "network_expert",
            "database_expert": "database_expert",
            "reporter": "reporter",
        },
    )

    # Experts converge on Reporter
    builder.add_edge("application_expert", "reporter")
    builder.add_edge("network_expert", "reporter")
    builder.add_edge("database_expert", "reporter")

    # HITL gate. The graph PAUSES at interrupt_before=['solver'] until
    # callbacks.py re-invokes it after the human approves/rejects.
    builder.add_conditional_edges(
        "reporter",
        _route_after_reporter,
        {
            "solver": "solver",
            END: END,
        },
    )
    builder.add_edge("solver", "reporter_followup")
    builder.add_edge("reporter_followup", END)

    compile_kwargs: dict[str, Any] = {"interrupt_before": ["solver"]}
    if checkpointer is not None:
        compile_kwargs["checkpointer"] = checkpointer
    return builder.compile(**compile_kwargs)
