"""
src/mcp_server/tools/scale_deployment.py

MCP write tool: scale_deployment

Scales a Deployment to *to_replicas* replicas. Tenant-configured [min, max]
bounds are enforced server-side; out-of-range requests return code
"out_of_bounds" without issuing a Kubernetes call.

Reversal: inverse_action="scale-deployment" with to_replicas=pre_state.replicas.

Corresponds to tasks.md T079.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Optional

from kubernetes import client as k8s_client  # type: ignore[import-untyped]
from kubernetes.client.rest import ApiException  # type: ignore[import-untyped]

from src.mcp_server.auth import get_apps_v1_for_tool
from src.mcp_server.tools._guards import GuardError, check_kill_switch, validate_approval_token
from src.shared.catalog import INVERSE_ACTIONS, build_reversal_description, compute_reversal_parameters
from src.shared.schemas import ReversalRecipe, ToolError, WriteToolOutput

_TOOL_NAME = "scale_deployment"
_POLL_INTERVAL = 3.0


def _get_bounds() -> tuple[int, int]:
    try:
        min_r = int(os.environ.get("SCALE_REPLICA_MIN", "1"))
        max_r = int(os.environ.get("SCALE_REPLICA_MAX", "20"))
        return min_r, max_r
    except ValueError:
        return 1, 20


async def scale_deployment(
    namespace: str,
    deployment: str,
    to_replicas: int,
    correlation_id: str,
    approval_token: str,
    proposed_fix_fingerprint: str,
    verification_window_sec: int = 60,
    tenant: Optional[str] = None,
) -> WriteToolOutput:
    """Scale *deployment* to *to_replicas* replicas."""

    # ---- Bounds check (server-side, before any API call) -------------------
    min_r, max_r = _get_bounds()
    if not (min_r <= to_replicas <= max_r):
        return WriteToolOutput(
            outcome="refused",
            pre_state={},
            post_state={},
            reversal_recipe=_no_recipe(),
            error=(
                f"to_replicas={to_replicas} is outside the allowed range "
                f"[{min_r}, {max_r}] (out_of_bounds)."
            ),
        )

    # ---- Pre-flight guards -------------------------------------------------
    try:
        await check_kill_switch(tenant)
        validate_approval_token(approval_token, correlation_id, proposed_fix_fingerprint)
    except GuardError as exc:
        return WriteToolOutput(
            outcome="refused",
            pre_state={},
            post_state={},
            reversal_recipe=_no_recipe(),
            error=exc.tool_error.human_message,
        )

    apps_v1 = get_apps_v1_for_tool(_TOOL_NAME)

    # ---- Capture pre-state -------------------------------------------------
    try:
        deploy_obj = await asyncio.to_thread(
            apps_v1.read_namespaced_deployment, deployment, namespace
        )
    except ApiException as exc:
        if exc.status == 404:
            return WriteToolOutput(
                outcome="error",
                pre_state={},
                post_state={},
                reversal_recipe=_no_recipe(),
                error=f"Deployment '{deployment}' not found.",
            )
        return WriteToolOutput(
            outcome="error",
            pre_state={},
            post_state={},
            reversal_recipe=_no_recipe(),
            error=f"Could not read deployment: {exc}",
        )

    pre_replicas = deploy_obj.spec.replicas if deploy_obj.spec else None
    pre_state = {
        "replicas": pre_replicas,
        "observed_at": datetime.now(tz=timezone.utc).isoformat(),
    }

    # ---- Issue the scale ---------------------------------------------------
    patch = {"spec": {"replicas": to_replicas}}
    try:
        await asyncio.to_thread(
            apps_v1.patch_namespaced_deployment,
            name=deployment,
            namespace=namespace,
            body=patch,
        )
    except ApiException as exc:
        if exc.status == 403:
            return WriteToolOutput(
                outcome="refused",
                pre_state=pre_state,
                post_state={},
                reversal_recipe=_make_recipe(pre_state),
                error=f"Admission denied: {exc.reason}",
            )
        return WriteToolOutput(
            outcome="error",
            pre_state=pre_state,
            post_state={},
            reversal_recipe=_make_recipe(pre_state),
            error=f"Scale API call failed: {exc}",
        )

    # ---- Verification window -----------------------------------------------
    post_state = await _wait_for_replicas(
        apps_v1, namespace, deployment, to_replicas, verification_window_sec
    )

    if post_state is None:
        return WriteToolOutput(
            outcome="error",
            pre_state=pre_state,
            post_state={},
            reversal_recipe=_make_recipe(pre_state),
            error=f"Replicas did not reach {to_replicas} within {verification_window_sec}s.",
        )

    return WriteToolOutput(
        outcome="applied",
        pre_state=pre_state,
        post_state=post_state,
        reversal_recipe=_make_recipe(pre_state),
    )


async def _wait_for_replicas(
    apps_v1: k8s_client.AppsV1Api,
    namespace: str,
    deployment: str,
    desired: int,
    timeout: int,
) -> Optional[dict]:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(_POLL_INTERVAL)
        try:
            d = await asyncio.to_thread(apps_v1.read_namespaced_deployment, deployment, namespace)
            ready_replicas = d.status.ready_replicas if d.status else 0
            spec_replicas = d.spec.replicas if d.spec else 0
            if (ready_replicas or 0) >= desired and (spec_replicas or 0) == desired:
                return {
                    "replicas": spec_replicas,
                    "ready_replicas": ready_replicas,
                    "observed_at": datetime.now(tz=timezone.utc).isoformat(),
                }
        except ApiException:
            pass
    return None


def _make_recipe(pre_state: dict) -> ReversalRecipe:
    return ReversalRecipe(
        description=build_reversal_description("scale-deployment", pre_state),
        inverse_action=INVERSE_ACTIONS["scale-deployment"],
        inverse_parameters=compute_reversal_parameters("scale-deployment", pre_state),
    )


def _no_recipe() -> ReversalRecipe:
    return ReversalRecipe(
        description="No action was issued; no reversal needed.",
        inverse_action=None,
        inverse_parameters={},
    )
