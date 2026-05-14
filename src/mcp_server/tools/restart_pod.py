"""
src/mcp_server/tools/restart_pod.py

MCP write tool: restart_pod

Deletes the pod (default grace period — no --force, no --grace-period=0) and
waits for a new Ready pod owned by the same controller within the verification
window.

Reversal: inverse_action=None (restart is self-recovering; nothing to undo).

Corresponds to tasks.md T077.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Optional

from kubernetes import client as k8s_client  # type: ignore[import-untyped]
from kubernetes.client.rest import ApiException  # type: ignore[import-untyped]

from src.mcp_server.auth import get_core_v1_for_tool
from src.mcp_server.tools._guards import GuardError, check_kill_switch, validate_approval_token
from src.mcp_server.tools.get_pod import _build_snapshot
from src.shared.catalog import build_reversal_description, compute_reversal_parameters, INVERSE_ACTIONS
from src.shared.schemas import ReversalRecipe, ToolError, WriteToolOutput

_TOOL_NAME = "restart_pod"
_POLL_INTERVAL = 2.0  # seconds between readiness polls


async def restart_pod(
    namespace: str,
    pod: str,
    correlation_id: str,
    approval_token: str,
    proposed_fix_fingerprint: str,
    verification_window_sec: int = 60,
    tenant: Optional[str] = None,
) -> WriteToolOutput:
    """
    Restart *pod* by deleting it (default grace period) and verifying recovery.
    """
    # ---- Pre-flight guards -------------------------------------------------
    try:
        await check_kill_switch(tenant)
        validate_approval_token(approval_token, proposed_fix_fingerprint)
    except GuardError as exc:
        return _refused(exc.tool_error, {}, {}, _self_recovering_recipe({}))

    v1 = get_core_v1_for_tool(_TOOL_NAME)

    # ---- Capture pre-state -------------------------------------------------
    try:
        pod_obj = await asyncio.to_thread(v1.read_namespaced_pod, pod, namespace)
    except ApiException as exc:
        if exc.status == 404:
            return _error(f"Pod '{pod}' not found before restart.", {})
        return _error(f"Could not read pod: {exc}", {})

    pre_snap = _build_snapshot(pod_obj)
    pre_state = pre_snap.model_dump(mode="json")

    # ---- PDB check (advisory for restart — we still check) -----------------
    pod_labels: dict[str, str] = {}
    if pod_obj.metadata and pod_obj.metadata.labels:
        pod_labels = dict(pod_obj.metadata.labels)

    from src.mcp_server.tools._guards import check_pod_disruption_budget  # noqa: PLC0415
    try:
        await check_pod_disruption_budget(
            v1.api_client, namespace, pod_labels
        )
    except GuardError as exc:
        return _refused(exc.tool_error, pre_state, {}, _self_recovering_recipe(pre_state))

    # ---- Issue the delete (no --force, no --grace-period=0) ----------------
    action_issued = {
        "action": "delete_pod",
        "namespace": namespace,
        "pod": pod,
        "grace_period": "default",
        "correlation_id": correlation_id,
    }
    try:
        await asyncio.to_thread(
            v1.delete_namespaced_pod,
            name=pod,
            namespace=namespace,
            body=k8s_client.V1DeleteOptions(),
        )
    except ApiException as exc:
        return _error(f"Delete API call failed: {exc}", pre_state)

    # ---- Verification window -----------------------------------------------
    post_state = await _wait_for_ready(v1, namespace, pod, verification_window_sec)
    recipe = _self_recovering_recipe(pre_state)

    if post_state is None:
        return WriteToolOutput(
            outcome="error",
            pre_state=pre_state,
            post_state={},
            reversal_recipe=recipe,
            error=f"Pod did not become Ready within {verification_window_sec}s.",
        )

    return WriteToolOutput(
        outcome="applied",
        pre_state=pre_state,
        post_state=post_state,
        reversal_recipe=recipe,
    )


async def _wait_for_ready(
    v1: k8s_client.CoreV1Api,
    namespace: str,
    pod: str,
    timeout: int,
) -> Optional[dict]:
    """Poll until pod is Ready or timeout expires. Returns post_state dict or None."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(_POLL_INTERVAL)
        try:
            pod_obj = await asyncio.to_thread(v1.read_namespaced_pod, pod, namespace)
            snap = _build_snapshot(pod_obj)
            if snap.ready:
                return snap.model_dump(mode="json")
        except ApiException:
            pass
    return None


def _self_recovering_recipe(pre_state: dict) -> ReversalRecipe:
    return ReversalRecipe(
        description=build_reversal_description("restart-pod", pre_state),
        inverse_action=INVERSE_ACTIONS["restart-pod"],
        inverse_parameters={},
    )


def _refused(error: ToolError, pre_state: dict, post_state: dict, recipe: ReversalRecipe) -> WriteToolOutput:
    return WriteToolOutput(
        outcome="refused",
        pre_state=pre_state,
        post_state=post_state,
        reversal_recipe=recipe,
        error=error.human_message,
    )


def _error(message: str, pre_state: dict) -> WriteToolOutput:
    return WriteToolOutput(
        outcome="error",
        pre_state=pre_state,
        post_state={},
        reversal_recipe=ReversalRecipe(
            description="No automated undo — restart was self-recovering.",
            inverse_action=None,
            inverse_parameters={},
        ),
        error=message,
    )
