"""
src/shared/labels.py

Single source of truth for every string-valued discriminant used across the
workflow.  All values are Literal types so mypy and pydantic v2 can enforce
exhaustiveness at call-sites.

Corresponds to data-model.md §Common types and tasks.md T012.
"""
from typing import Literal

# ---------------------------------------------------------------------------
# Domain — three-way taxonomy for incident classification (spec FR-005/FR-008)
# ---------------------------------------------------------------------------
Domain = Literal["Application", "Network", "Unknown"]

# Domain values as a frozenset for runtime membership tests
DOMAINS: frozenset[str] = frozenset({"Application", "Network", "Unknown"})

# ---------------------------------------------------------------------------
# Confidence — how certain the Router / Expert is of its conclusion
# ---------------------------------------------------------------------------
Confidence = Literal["low", "medium", "high"]

# ---------------------------------------------------------------------------
# ReportStatus — state machine for the lifecycle of a triage Report
# (see data-model.md §Status state machine)
# ---------------------------------------------------------------------------
ReportStatus = Literal["pending", "approved", "rejected", "expired", "executed", "failed"]

# ---------------------------------------------------------------------------
# SolverOutcome — result of the Solver's deterministic execution
# ---------------------------------------------------------------------------
SolverOutcome = Literal["success", "partial", "failure"]

# ---------------------------------------------------------------------------
# ApprovalAction — what the approver clicked
# ---------------------------------------------------------------------------
ApprovalAction = Literal["approve", "reject"]

# ---------------------------------------------------------------------------
# ActionType — the fixed allowed-remediation catalog (spec FR-011)
# Adding a new entry requires a constitution VII checklist + two-reviewer PR.
# ---------------------------------------------------------------------------
ActionType = Literal[
    "restart-pod",
    "rollback-deployment",
    "scale-deployment",
    "delete-pod-to-reschedule",
]

ACTION_TYPES: frozenset[str] = frozenset(
    {
        "restart-pod",
        "rollback-deployment",
        "scale-deployment",
        "delete-pod-to-reschedule",
    }
)
