"""
src/agent/graph/nodes/_placeholders.py

No-op node functions so the graph compiles end-to-end without Person 1's
real implementations. Each writes one audit row so the wiring is visible
during the smoke test.

Person 1 replaces each function with the real LangGraph node body. The
signatures (async, accept WorkflowState, return partial WorkflowState dict)
must match.
"""
from __future__ import annotations

from typing import Any

from src.agent.audit import log_audit_event
from src.agent.graph.state import WorkflowState


async def _audit(state: WorkflowState, stage: str, payload: dict[str, Any] | None = None) -> None:
    cid = state.get("correlation_id", "")
    if cid:
        await log_audit_event(cid, stage=stage, payload=payload or {"placeholder": True})


async def ingest_node(state: WorkflowState) -> dict[str, Any]:
    await _audit(state, "ingest_placeholder")
    return {"evidence": {"logs": "", "events": "", "resource_status": ""}}


async def router_node(state: WorkflowState) -> dict[str, Any]:
    await _audit(state, "router_placeholder")
    # Placeholder: always route to Application so the smoke test exercises one expert.
    return {"classification": "APP"}


async def application_expert_node(state: WorkflowState) -> dict[str, Any]:
    await _audit(state, "application_expert_placeholder")
    return {
        "diagnosis": {"root_cause_hypothesis": "[placeholder] application error"},
        "proposed_fix": {
            "action_type": "restart-pod",
            "target": {"namespace": "default", "pod": "placeholder"},
            "parameters": {},
            "fingerprint": "placeholder-fingerprint",
        },
    }


async def network_expert_node(state: WorkflowState) -> dict[str, Any]:
    await _audit(state, "network_expert_placeholder")
    return {"diagnosis": {"root_cause_hypothesis": "[placeholder] network error"}}


async def database_expert_node(state: WorkflowState) -> dict[str, Any]:
    await _audit(state, "database_expert_placeholder")
    return {"diagnosis": {"root_cause_hypothesis": "[placeholder] database error"}}


async def reporter_node(state: WorkflowState) -> dict[str, Any]:
    await _audit(state, "reporter_placeholder", payload={"would_post_to_slack_mock": True})
    return {"approval_status": "PENDING"}


async def solver_node(state: WorkflowState) -> dict[str, Any]:
    await _audit(
        state,
        "solver_placeholder",
        payload={"approval_status": state.get("approval_status")},
    )
    return {
        "solver_result": {
            "outcome": "success",
            "inverse_action": None,
            "note": "[placeholder] no real mutation executed",
        }
    }
