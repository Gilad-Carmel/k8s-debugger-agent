"""
src/agent/graph/builder.py

LangGraph graph builder for the routed triage-and-remediation workflow.

Phase 1 (this file): all nodes are stubs that hardcode fake responses.
No LLM calls, no MCP calls, no DB — purely structural scaffolding.

Full wiring plan (same-file sequencing per tasks.md):
  T026 → T053 → T070 → T085

Current state: T026 — skeleton with stubbed nodes, no interrupt yet.
  ingest → router → {application|network|database}_expert OR reporter
         → reporter → solver

The interrupt between reporter and solver (FR-015, HITL approval gate) will
be added in T070 (Phase 4 / US2). For now the graph runs straight through
to the solver stub so test_graph_run.py can exercise the full path.

Usage::

    from src.agent.graph.builder import build_graph

    graph = build_graph()
    final_state = graph.invoke(initial_state)
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from src.agent.graph.nodes.experts.application import application_expert_node
from src.agent.graph.nodes.experts.database import database_expert_node
from src.agent.graph.nodes.experts.network import network_expert_node
from src.agent.graph.nodes.ingest import ingest_node
from src.agent.graph.nodes.reporter import reporter_node
from src.agent.graph.nodes.router import route_after_router, router_node
from src.agent.graph.nodes.solver import solver_node
from src.agent.graph.state import WorkflowState


def build_graph() -> StateGraph:
    """
    Assemble and compile the triage-and-remediation StateGraph.

    Node layout:
        START
          │
        ingest
          │
        router  ──[conditional]──► application_expert ──┐
                                 ► network_expert      ──┤
                                 ► database_expert     ──┤
                                 ► reporter (Unknown)  ──┘
                                                         │
                                                      reporter
                                                         │
                                                       solver
                                                         │
                                                        END

    The conditional edge after `router` uses route_after_router() which
    returns the literal node name to branch to.
    """
    builder = StateGraph(WorkflowState)

    # ------------------------------------------------------------------
    # Register nodes
    # ------------------------------------------------------------------
    builder.add_node("ingest", ingest_node)
    builder.add_node("router", router_node)
    builder.add_node("application_expert", application_expert_node)
    builder.add_node("network_expert", network_expert_node)
    builder.add_node("database_expert", database_expert_node)
    builder.add_node("reporter", reporter_node)
    builder.add_node("solver", solver_node)

    # ------------------------------------------------------------------
    # Edges: entry → ingest → router
    # ------------------------------------------------------------------
    builder.add_edge(START, "ingest")
    builder.add_edge("ingest", "router")

    # ------------------------------------------------------------------
    # Conditional edge: router → one of the four branches
    # ------------------------------------------------------------------
    builder.add_conditional_edges(
        "router",
        route_after_router,
        {
            "application_expert": "application_expert",
            "network_expert": "network_expert",
            "database_expert": "database_expert",
            "reporter": "reporter",  # Unknown domain short-circuit
        },
    )

    # ------------------------------------------------------------------
    # Expert → reporter (all three experts converge here)
    # ------------------------------------------------------------------
    builder.add_edge("application_expert", "reporter")
    builder.add_edge("network_expert", "reporter")
    builder.add_edge("database_expert", "reporter")

    # ------------------------------------------------------------------
    # reporter → solver → END
    # NOTE: the interrupt() between reporter and solver will be added in
    # T070 (US2 HITL gate).  For Phase 1 scaffolding it runs straight through.
    # ------------------------------------------------------------------
    builder.add_edge("reporter", "solver")
    builder.add_edge("solver", END)

    return builder.compile()
