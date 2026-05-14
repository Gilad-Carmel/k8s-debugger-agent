"""
src/mcp_server/tools/get_pod_events.py

MCP read tool: get_pod_events

Returns recent Kubernetes events for a target pod, ordered by timestamp.

Timestamp priority (mirrors kubernetes-mcp-server/pkg/kubernetes/events.go):
  1. event_time
  2. series.last_observed_time
  3. last_timestamp
  4. first_timestamp

Corresponds to tasks.md T042.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from kubernetes import client as k8s_client  # type: ignore[import-untyped]
from kubernetes.client.rest import ApiException  # type: ignore[import-untyped]

from src.mcp_server.auth import get_core_v1_for_tool

_TOOL_NAME = "get_pod_events"


async def get_pod_events(
    namespace: str,
    pod: str,
    since_minutes: int,
    correlation_id: str,
) -> list[dict[str, Any]]:
    """
    Return Kubernetes events involving *pod* in *namespace* within the last
    *since_minutes* minutes, ordered ascending by event timestamp.
    """
    v1 = get_core_v1_for_tool(_TOOL_NAME)
    cutoff = datetime.now(tz=timezone.utc) - timedelta(minutes=since_minutes)

    try:
        event_list = await asyncio.to_thread(
            v1.list_namespaced_event,
            namespace,
            field_selector=f"involvedObject.name={pod}",
        )
    except ApiException as exc:
        if exc.status == 404:
            raise FileNotFoundError(f"Namespace '{namespace}' not found") from exc
        if exc.status == 403:
            raise PermissionError(
                f"Forbidden: cannot read events in namespace '{namespace}'"
            ) from exc
        raise

    records: list[dict[str, Any]] = []
    for ev in event_list.items:
        ts = _pick_timestamp(ev)
        if ts is None or ts < cutoff:
            continue
        records.append(
            {
                "timestamp": ts.isoformat(),
                "type": ev.type or "Normal",
                "reason": ev.reason or "",
                "message": ev.message or "",
                "count": ev.count or 1,
                "source_component": (ev.source.component if ev.source else None),
                "name": ev.metadata.name if ev.metadata else None,
            }
        )

    records.sort(key=lambda r: r["timestamp"])
    return records


def _pick_timestamp(ev: Any) -> Optional[datetime]:
    """Return the best available timestamp for an event."""
    candidates: list[Optional[Any]] = [
        ev.event_time,
        ev.series.last_observed_time if ev.series else None,
        ev.last_timestamp,
        ev.first_timestamp,
    ]
    for candidate in candidates:
        if candidate is None:
            continue
        if isinstance(candidate, datetime):
            if candidate.tzinfo is None:
                return candidate.replace(tzinfo=timezone.utc)
            return candidate
    return None
