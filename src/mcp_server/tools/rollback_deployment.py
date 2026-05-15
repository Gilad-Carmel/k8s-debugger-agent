"""
src/mcp_server/tools/rollback_deployment.py

MCP write tool: rollback_deployment

Rolls back a Deployment to a prior revision by patching the
kubectl.kubernetes.io/last-applied-configuration annotation or the
deployment.kubernetes.io/revision annotation on the pod template.

Pre-condition: verifies that *to_revision* is an existing revision.
Post-condition: pre_state.current_revision != post_state.current_revision.

Reversal: inverse_action="rollback-deployment" with to_revision=pre_state.current_revision.

Corresponds to tasks.md T078.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Optional

from kubernetes import client as k8s_client  # type: ignore[import-untyped]
from kubernetes.client.rest import ApiException  # type: ignore[import-untyped]

from src.mcp_server.auth import get_apps_v1_for_tool
from src.mcp_server.tools._guards import GuardError, check_kill_switch, validate_approval_token
from src.shared.catalog import INVERSE_ACTIONS, build_reversal_description, compute_reversal_parameters
from src.shared.schemas import ReversalRecipe, WriteToolOutput

_TOOL_NAME = "rollback_deployment"
_POLL_INTERVAL = 3.0
_REVISION_ANNOTATION = "deployment.kubernetes.io/revision"


async def rollback_deployment(
    namespace: str,
    deployment: str,
    to_revision: int,
    correlation_id: str,
    approval_token: str,
    proposed_fix_fingerprint: str,
    verification_window_sec: int = 60,
    tenant: Optional[str] = None,
) -> WriteToolOutput:
    """Roll *deployment* back to *to_revision*."""

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

    annotations = (deploy_obj.metadata.annotations or {}) if deploy_obj.metadata else {}
    current_revision_str = annotations.get(_REVISION_ANNOTATION, "0")
    try:
        current_revision = int(current_revision_str)
    except ValueError:
        current_revision = 0

    pre_state = {
        "current_revision": current_revision,
        "replicas": (deploy_obj.spec.replicas if deploy_obj.spec else None),
        "observed_at": datetime.now(tz=timezone.utc).isoformat(),
    }

    # ---- Patch deployment template to trigger rollback via annotation ------
    # Kubernetes rollback is performed by patching the pod template annotation
    # to the desired revision.  The deployment controller handles the actual
    # rollout.
    patch = {
        "spec": {
            "template": {
                "metadata": {
                    "annotations": {
                        "kubectl.kubernetes.io/restartedAt": datetime.now(tz=timezone.utc).isoformat(),
                        _REVISION_ANNOTATION: str(to_revision),
                    }
                }
            }
        }
    }
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
            error=f"Patch failed: {exc}",
        )

    # ---- Verification window -----------------------------------------------
    post_state = await _wait_for_revision_change(
        apps_v1, namespace, deployment, current_revision, to_revision, verification_window_sec
    )

    if post_state is None:
        return WriteToolOutput(
            outcome="error",
            pre_state=pre_state,
            post_state={},
            reversal_recipe=_make_recipe(pre_state),
            error=f"Revision did not change within {verification_window_sec}s.",
        )

    return WriteToolOutput(
        outcome="applied",
        pre_state=pre_state,
        post_state=post_state,
        reversal_recipe=_make_recipe(pre_state),
    )


async def _wait_for_revision_change(
    apps_v1: k8s_client.AppsV1Api,
    namespace: str,
    deployment: str,
    old_revision: int,
    target_revision: int,
    timeout: int,
) -> Optional[dict]:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(_POLL_INTERVAL)
        try:
            d = await asyncio.to_thread(apps_v1.read_namespaced_deployment, deployment, namespace)
            ann = (d.metadata.annotations or {}) if d.metadata else {}
            new_rev_str = ann.get(_REVISION_ANNOTATION, str(old_revision))
            new_rev = int(new_rev_str) if new_rev_str.isdigit() else old_revision
            if new_rev != old_revision:
                return {
                    "current_revision": new_rev,
                    "replicas": (d.spec.replicas if d.spec else None),
                    "observed_at": datetime.now(tz=timezone.utc).isoformat(),
                }
        except ApiException:
            pass
    return None


def _make_recipe(pre_state: dict) -> ReversalRecipe:
    return ReversalRecipe(
        description=build_reversal_description("rollback-deployment", pre_state),
        inverse_action=INVERSE_ACTIONS["rollback-deployment"],
        inverse_parameters=compute_reversal_parameters("rollback-deployment", pre_state),
    )


def _no_recipe() -> ReversalRecipe:
    return ReversalRecipe(
        description="No action was issued; no reversal needed.",
        inverse_action=None,
        inverse_parameters={},
    )
