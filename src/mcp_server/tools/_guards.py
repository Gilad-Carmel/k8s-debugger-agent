"""
src/mcp_server/tools/_guards.py

Pre-flight guard functions shared across all MCP write tools.

Guards raise GuardError on violation; callers catch it and return the
embedded ToolError as the tool's error response without issuing any
Kubernetes API call.

Corresponds to tasks.md T081.  95% coverage tier (CI-enforced).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import time
from typing import Any, Optional

from src.shared.schemas import ToolError

# ---------------------------------------------------------------------------
# HMAC secret shared between agent (token issuer) and MCP server (verifier).
# Must be the same value in both processes.
# ---------------------------------------------------------------------------
_APPROVAL_TOKEN_SECRET: str = os.environ.get("APPROVAL_TOKEN_SECRET", "dev-approval-secret")


class GuardError(Exception):
    """Raised by a guard function when a pre-flight check fails."""

    def __init__(self, tool_error: ToolError) -> None:
        super().__init__(tool_error.machine_token)
        self.tool_error = tool_error


# ---------------------------------------------------------------------------
# Guard 1 — kill-switch
# ---------------------------------------------------------------------------
async def check_kill_switch(tenant: Optional[str] = None) -> None:
    """
    Raise GuardError(tenant_halted) if the kill switch is active.

    Imports admin lazily to avoid circular imports at module load time.
    """
    from src.mcp_server.admin import is_tenant_halted  # noqa: PLC0415

    if await is_tenant_halted(tenant):
        raise GuardError(
            ToolError(
                machine_token="tenant_halted",
                human_message="Write operations are currently halted for this tenant.",
            )
        )


# ---------------------------------------------------------------------------
# Guard 2 — Pod Disruption Budget
# ---------------------------------------------------------------------------
async def check_pod_disruption_budget(
    api_client: Any,
    namespace: str,
    pod_labels: dict[str, str],
) -> None:
    """
    Raise GuardError(admission_denied) if deleting a pod with *pod_labels*
    would violate any PodDisruptionBudget in *namespace*.

    Uses the PolicyV1 API; wraps the synchronous kubernetes client call in
    asyncio.to_thread().
    """
    from kubernetes import client as k8s_client  # type: ignore[import-untyped]

    policy_v1 = k8s_client.PolicyV1Api(api_client=api_client)

    try:
        pdb_list = await asyncio.to_thread(
            policy_v1.list_namespaced_pod_disruption_budget, namespace
        )
    except Exception:
        # If we cannot read PDBs, fail closed: treat as a refusal.
        raise GuardError(
            ToolError(
                machine_token="admission_denied",
                human_message="Could not verify PodDisruptionBudgets; refusing write.",
            )
        )

    for pdb in pdb_list.items:
        status = pdb.status
        if status is None:
            continue
        if (status.disruptions_allowed or 0) > 0:
            continue
        # disruptionsAllowed == 0 — check if this PDB targets our pod.
        selector = pdb.spec.selector if pdb.spec else None
        if selector is None:
            continue
        if _labels_match(pod_labels, selector.match_labels or {}):
            raise GuardError(
                ToolError(
                    machine_token="admission_denied",
                    human_message=(
                        f"PodDisruptionBudget '{pdb.metadata.name}' in namespace "
                        f"'{namespace}' would be violated (disruptionsAllowed=0)."
                    ),
                )
            )


def _labels_match(pod_labels: dict[str, str], selector_labels: dict[str, str]) -> bool:
    """Return True if all selector_labels entries are present in pod_labels."""
    if not selector_labels:
        return True  # empty selector matches everything
    return all(pod_labels.get(k) == v for k, v in selector_labels.items())


# ---------------------------------------------------------------------------
# Guard 3 — approval token
# ---------------------------------------------------------------------------
def validate_approval_token(
    approval_token: str,
    correlation_id: str,
    proposed_fix_fingerprint: str,
) -> None:
    """
    Validate the HMAC-signed approval token issued by the agent.

    Expected format: ``{exp_unix}.{hmac_hex}``
    HMAC body:       ``{correlation_id}|{fingerprint}|{exp_unix}``

    Raises GuardError(approval_invalid) on:
      - Wrong number of segments
      - Token expired
      - Invalid HMAC signature
    """
    parts = approval_token.split(".", 1)
    if len(parts) != 2:
        raise GuardError(
            ToolError(
                machine_token="approval_invalid",
                human_message="Malformed approval token (expected '{exp_unix}.{hmac}' format).",
            )
        )

    exp_str, token_hmac = parts

    try:
        exp = int(exp_str)
    except ValueError:
        raise GuardError(
            ToolError(
                machine_token="approval_invalid",
                human_message="Approval token expiry is not a valid integer.",
            )
        )

    if time.time() > exp:
        raise GuardError(
            ToolError(
                machine_token="approval_invalid",
                human_message="Approval token has expired.",
            )
        )

    msg = f"{correlation_id}|{proposed_fix_fingerprint}|{exp_str}".encode()
    expected_hmac = hmac.new(
        _APPROVAL_TOKEN_SECRET.encode(), msg, hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(token_hmac, expected_hmac):
        raise GuardError(
            ToolError(
                machine_token="approval_invalid",
                human_message="Approval token HMAC signature is invalid.",
            )
        )
