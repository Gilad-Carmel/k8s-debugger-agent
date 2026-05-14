"""
src/shared/catalog.py

Allowed-remediation catalog: parameter schemas and the fixed Forward → Inverse
Action mapping per spec.md §Assumptions and data-model.md §Allowed-remediation
catalog.

Corresponds to tasks.md T015.

Adding a new entry requires:
  - A constitution VII checklist (refusal-path + reversal-recipe tests +
    budget/latency declaration).
  - A two-reviewer PR per Principle VI.
"""

from __future__ import annotations

from typing import Any, Optional

# ---------------------------------------------------------------------------
# Forward → Inverse Action mapping
#
# None  ⇒ action is transient/self-recovering; nothing needs to be undone.
# ---------------------------------------------------------------------------
INVERSE_ACTIONS: dict[str, Optional[str]] = {
    "restart-pod": None,
    "rollback-deployment": "rollback-deployment",
    "scale-deployment": "scale-deployment",
    "delete-pod-to-reschedule": None,
}

# Human-readable description templates for the Reporter chat caveat block.
# Concrete values (e.g. revision numbers) are formatted at execution time.
INVERSE_DESCRIPTIONS: dict[str, str] = {
    "restart-pod": "No automated undo — restart was self-recovering.",
    "rollback-deployment": "Undo: rollback-deployment to revision {to_revision}",
    "scale-deployment": "Undo: scale-deployment to {to_replicas} replicas",
    "delete-pod-to-reschedule": "No automated undo — delete-to-reschedule is self-recovering.",
}


def compute_reversal_parameters(
    action_type: str,
    pre_state: dict[str, Any],
) -> dict[str, Any]:
    """
    Return the *parameters* dict for the inverse action.

    Empty dict when the action has no inverse (None) or when no concrete
    pre-state values are available.
    """
    if action_type == "rollback-deployment":
        current = pre_state.get("current_revision")
        return {"to_revision": current} if current is not None else {}
    if action_type == "scale-deployment":
        replicas = pre_state.get("replicas")
        return {"to_replicas": replicas} if replicas is not None else {}
    return {}


def build_reversal_description(
    action_type: str,
    pre_state: dict[str, Any],
) -> str:
    """Return the human-readable reversal description with concrete values filled in."""
    template = INVERSE_DESCRIPTIONS.get(action_type, "Unknown action type.")
    if action_type == "rollback-deployment":
        return template.format(to_revision=pre_state.get("current_revision", "?"))
    if action_type == "scale-deployment":
        return template.format(to_replicas=pre_state.get("replicas", "?"))
    return template
