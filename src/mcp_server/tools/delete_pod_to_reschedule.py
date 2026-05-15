"""
src/mcp_server/tools/delete_pod_to_reschedule.py

MCP write tool: delete_pod_to_reschedule

Deletes a pod to trigger rescheduling by its parent controller. Uses the
Kubernetes Eviction API to respect PodDisruptionBudgets and admission
controllers.  NEVER uses --force or --grace-period=0.

Reversal: inverse_action=None (the parent controller recreates the pod;
nothing to undo).

Corresponds to tasks.md T080.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Optional

from kubernetes import client as k8s_client  # type: ignore[import-untyped]
from kubernetes.client.rest import ApiException  # type: ignore[import-untyped]

from src.mcp_server.auth import get_core_v1_for_tool
from src.mcp_server.tools._guards import (
    GuardError,
    check_kill_switch,
    check_pod_disruption_budget,
    validate_approval_token,
)
from src.mcp_server.tools.get_pod import _build_snapshot
from src.shared.catalog import INVERSE_ACTIONS, build_reversal_description
from src.shared.schemas import ReversalRecipe, WriteToolOutput

_TOOL_NAME = "delete_pod_to_reschedule"
_POLL_INTERVAL = 2.0


async def delete_pod_to_reschedule(
    namespace: str,
    pod: str,
    correlation_id: str,
    approval_token: str,
    proposed_fix_fingerprint: str,
    verification_window_sec: int = 60,
    tenant: Optional[str] = None,
) -> WriteToolOutput:
    """
    Delete *pod* to trigger rescheduling, respecting PDBs and admission
    controllers.  Uses eviction API; NEVER --force.
    """

    # ---- Pre-flight guards -------------------------------------------------
    try:
        await check_kill_switch(tenant)
        validate_approval_token(approval_token, correlation_id, proposed_fix_fingerprint)
    except GuardError as exc:
        return WriteToolOutput(
            outcome="refused",
            pre_state={},
            post_state={},
            reversal_recipe=_self_recovering_recipe(),
            error=exc.tool_error.human_message,
        )

    v1 = get_core_v1_for_tool(_TOOL_NAME)

    # ---- Capture pre-state -------------------------------------------------
    try:
        pod_obj = await asyncio.to_thread(v1.read_namespaced_pod, pod, namespace)
    except ApiException as exc:
        if exc.status == 404:
            return WriteToolOutput(
                outcome="error",
                pre_state={},
                post_state={},
                reversal_recipe=_self_recovering_recipe(),
                error=f"Pod '{pod}' not found.",
            )
        return WriteToolOutput(
            outcome="error",
            pre_state={},
            post_state={},
            reversal_recipe=_self_recovering_recipe(),
            error=f"Could not read pod: {exc}",
        )

    pre_snap = _build_snapshot(pod_obj)
    pre_state = pre_snap.model_dump(mode="json")

    # ---- PDB pre-flight check ----------------------------------------------
    pod_labels: dict[str, str] = {}
    if pod_obj.metadata and pod_obj.metadata.labels:
        pod_labels = dict(pod_obj.metadata.labels)

    try:
        await check_pod_disruption_budget(v1.api_client, namespace, pod_labels)
    except GuardError as exc:
        return WriteToolOutput(
            outcome="refused",
            pre_state=pre_state,
            post_state={},
            reversal_recipe=_self_recovering_recipe(),
            error=exc.tool_error.human_message,
        )

    # ---- Issue the eviction (PDB-aware delete) ------------------------------
    eviction = k8s_client.V1Eviction(
        metadata=k8s_client.V1ObjectMeta(name=pod, namespace=namespace),
        delete_options=k8s_client.V1DeleteOptions(),
    )
    try:
        await asyncio.to_thread(
            v1.create_namespaced_pod_eviction,
            name=pod,
            namespace=namespace,
            body=eviction,
        )
    except ApiException as exc:
        if exc.status == 429:
            # PDB violation: "Cannot evict pod as it would violate the pod's disruption budget."
            return WriteToolOutput(
                outcome="refused",
                pre_state=pre_state,
                post_state={},
                reversal_recipe=_self_recovering_recipe(),
                error=f"Eviction refused by PDB: {exc.reason}",
            )
        if exc.status == 403:
            return WriteToolOutput(
                outcome="refused",
                pre_state=pre_state,
                post_state={},
                reversal_recipe=_self_recovering_recipe(),
                error=f"Admission denied: {exc.reason}",
            )
        return WriteToolOutput(
            outcome="error",
            pre_state=pre_state,
            post_state={},
            reversal_recipe=_self_recovering_recipe(),
            error=f"Eviction API call failed: {exc}",
        )

    # ---- Verification window -----------------------------------------------
    post_state = await _wait_for_new_pod(v1, namespace, pod, verification_window_sec)

    if post_state is None:
        return WriteToolOutput(
            outcome="error",
            pre_state=pre_state,
            post_state={},
            reversal_recipe=_self_recovering_recipe(),
            error=f"New pod did not become Ready within {verification_window_sec}s.",
        )

    return WriteToolOutput(
        outcome="applied",
        pre_state=pre_state,
        post_state=post_state,
        reversal_recipe=_self_recovering_recipe(),
    )


async def _wait_for_new_pod(
    v1: k8s_client.CoreV1Api,
    namespace: str,
    pod_name: str,
    timeout: int,
) -> Optional[dict]:
    """
    Poll until a pod matching *pod_name* is Ready or until timeout.

    After eviction the controller will create a new pod, often with a
    generated name suffix; we poll for any Ready pod with the base name
    prefix.
    """
    deadline = asyncio.get_event_loop().time() + timeout
    pod_prefix = pod_name.rsplit("-", 2)[0]  # strip last two generated segments

    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(_POLL_INTERVAL)
        try:
            pod_list = await asyncio.to_thread(
                v1.list_namespaced_pod,
                namespace,
                field_selector=f"status.phase=Running",
            )
            for p in pod_list.items:
                name = p.metadata.name if p.metadata else ""
                if name.startswith(pod_prefix):
                    snap = _build_snapshot(p)
                    if snap.ready:
                        return snap.model_dump(mode="json")
        except ApiException:
            pass
    return None


def _self_recovering_recipe() -> ReversalRecipe:
    return ReversalRecipe(
        description=build_reversal_description("delete-pod-to-reschedule", {}),
        inverse_action=INVERSE_ACTIONS["delete-pod-to-reschedule"],
        inverse_parameters={},
    )
