"""
src/mcp_server/tools/get_pod.py

MCP read tool: get_pod

Returns a PodSnapshot (phase, container states, restart counts, readiness,
resourceVersion) used by the Solver for pre-state / post-state captures.

Corresponds to tasks.md T043.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Optional

from kubernetes import client as k8s_client  # type: ignore[import-untyped]
from kubernetes.client.rest import ApiException  # type: ignore[import-untyped]

from src.mcp_server.auth import get_core_v1_for_tool
from src.shared.schemas import ContainerState, PodSnapshot

_TOOL_NAME = "get_pod"


async def get_pod(
    namespace: str,
    pod: str,
    correlation_id: str,
) -> PodSnapshot:
    """
    Fetch the current state of *pod* in *namespace* and return a PodSnapshot.
    """
    v1 = get_core_v1_for_tool(_TOOL_NAME)

    try:
        pod_obj = await asyncio.to_thread(v1.read_namespaced_pod, pod, namespace)
    except ApiException as exc:
        if exc.status == 404:
            raise FileNotFoundError(
                f"Pod '{pod}' not found in namespace '{namespace}'"
            ) from exc
        if exc.status == 403:
            raise PermissionError(
                f"Forbidden: cannot read pod '{pod}' in namespace '{namespace}'"
            ) from exc
        raise

    return _build_snapshot(pod_obj)


def _build_snapshot(pod_obj: Any) -> PodSnapshot:
    """Build a PodSnapshot from a kubernetes V1Pod object."""
    status = pod_obj.status or k8s_client.V1PodStatus()
    meta = pod_obj.metadata or k8s_client.V1ObjectMeta()

    phase: str = status.phase or "Unknown"
    restart_count_by_ctr: dict[str, int] = {}
    container_states: dict[str, ContainerState] = {}
    ready = False
    all_ready: list[bool] = []

    for cs in status.container_statuses or []:
        name = cs.name or "unknown"
        restart_count_by_ctr[name] = cs.restart_count or 0
        container_states[name] = _parse_container_state(cs.state)
        all_ready.append(bool(cs.ready))

    if all_ready:
        ready = all(all_ready)

    return PodSnapshot(
        phase=phase,
        restart_count_by_ctr=restart_count_by_ctr,
        container_states=container_states,
        ready=ready,
        resource_version=meta.resource_version or "",
        observed_at=datetime.now(tz=timezone.utc),
    )


def _parse_container_state(
    state: Optional[Any],
) -> ContainerState:
    """Convert a V1ContainerState to our ContainerState model."""
    if state is None:
        return ContainerState(state="Waiting")

    if state.running is not None:
        started: Optional[datetime] = None
        if state.running.started_at:
            started = state.running.started_at
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
        return ContainerState(state="Running", started_at=started)

    if state.terminated is not None:
        t = state.terminated
        finished: Optional[datetime] = t.finished_at
        if finished and finished.tzinfo is None:
            finished = finished.replace(tzinfo=timezone.utc)
        started_at: Optional[datetime] = t.started_at
        if started_at and started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        return ContainerState(
            state="Terminated",
            reason=t.reason,
            message=t.message,
            exit_code=t.exit_code,
            started_at=started_at,
            finished_at=finished,
        )

    if state.waiting is not None:
        return ContainerState(
            state="Waiting",
            reason=state.waiting.reason,
            message=state.waiting.message,
        )

    return ContainerState(state="Waiting")
