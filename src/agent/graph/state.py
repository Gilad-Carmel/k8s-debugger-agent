"""
src/agent/graph/state.py

WorkflowState — the LangGraph shared-state contract.

Per spec.md §"Shared Workflow State" and data-model.md §WorkflowState.
LangGraph merges per-node updates shallowly by key, so every entry must be
a top-level key.

Person 1 may add additional bookkeeping keys (budget counters, retry attempts);
they MUST NOT remove or repurpose any field below. Other people read these
fields and downstream consumers depend on them.
"""
from __future__ import annotations

from typing import Any, Optional, TypedDict


class WorkflowState(TypedDict, total=False):
    correlation_id: str
    # Raw alert payload preserved verbatim (after HMAC + dedup) for replay.
    alert_payload: dict[str, Any]
    # Pre-filtered cluster signal — set by Ingest from MCP read tools.
    evidence: dict[str, Any]
    # Router output. One of: "APP" / "NET" / "DB" / "UNKNOWN".
    classification: str
    # Expert root-cause hypothesis + cited evidence list.
    diagnosis: dict[str, Any]
    # Frozen catalog-bound fix proposed by the Expert.
    proposed_fix: Optional[dict[str, Any]]
    # Set by the HITL callback handler before the graph resumes.
    # Values: "PENDING" / "APPROVED" / "REJECTED" / "EXPIRED".
    approval_status: str
    # Result of the deterministic Solver execution.
    solver_result: dict[str, Any]
