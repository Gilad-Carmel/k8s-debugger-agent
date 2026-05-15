"""
src/agent/api/gui/pods.py

GET /api/pods — List Kubernetes pod statuses for the demo namespace.

Uses `kubectl get pods -n <namespace> -o json` via asyncio subprocess.
Always returns HTTP 200. When kubectl is unavailable or fails, returns
{"pods": [], "warning": "<reason>"} so the GUI can degrade gracefully.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

router = APIRouter()

KUBECTL_TIMEOUT = 5.0


class PodStatus(BaseModel):
    name: str
    namespace: str
    phase: str
    ready: bool
    restart_count: int
    message: Optional[str]
    ts: str


def _parse_pods(pod_list: dict[str, Any], namespace: str) -> list[PodStatus]:
    results: list[PodStatus] = []
    for item in pod_list.get("items", []):
        meta = item.get("metadata", {})
        status = item.get("status", {})

        name = meta.get("name", "unknown")
        phase = status.get("phase", "Unknown")

        # Readiness: all containers Ready
        conditions = status.get("conditions") or []
        ready = any(
            c.get("type") == "Ready" and c.get("status") == "True"
            for c in conditions
        )

        # Restart count: sum across all containers
        container_statuses = status.get("containerStatuses") or []
        restart_count = sum(cs.get("restartCount", 0) for cs in container_statuses)

        # Last failure message
        message: Optional[str] = None
        for cs in container_statuses:
            last_state = cs.get("lastState", {})
            terminated = last_state.get("terminated") or {}
            if terminated:
                message = terminated.get("reason") or terminated.get("message")
                break
            waiting = cs.get("state", {}).get("waiting") or {}
            if waiting:
                message = waiting.get("reason")
                break

        # Timestamp from metadata.creationTimestamp if available
        ts = meta.get("creationTimestamp") or datetime.now(timezone.utc).isoformat()

        results.append(
            PodStatus(
                name=name,
                namespace=namespace,
                phase=phase,
                ready=ready,
                restart_count=restart_count,
                message=message,
                ts=ts,
            )
        )
    return results


@router.get("/api/pods")
async def list_pods(namespace: str = "demo") -> JSONResponse:
    try:
        proc = await asyncio.create_subprocess_exec(
            "kubectl", "get", "pods", "-n", namespace, "-o", "json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=KUBECTL_TIMEOUT
            )
        except asyncio.TimeoutError:
            proc.kill()
            return JSONResponse(content={"pods": [], "warning": f"kubectl timed out after {KUBECTL_TIMEOUT}s — no cluster connected", "fetched_at": datetime.now(timezone.utc).isoformat()})
    except FileNotFoundError:
        return JSONResponse(content={"pods": [], "warning": "kubectl not found — no cluster connected", "fetched_at": datetime.now(timezone.utc).isoformat()})

    if proc.returncode != 0:
        raw = stderr.decode(errors="replace").strip()
        # kubectl spams memcache warnings before the real error — take the last non-empty line
        last_line = next((l for l in reversed(raw.splitlines()) if l.strip()), raw)
        return JSONResponse(content={"pods": [], "warning": last_line or "kubectl error — no cluster connected", "fetched_at": datetime.now(timezone.utc).isoformat()})

    try:
        pod_list = json.loads(stdout)
    except json.JSONDecodeError:
        return JSONResponse(content={"pods": [], "warning": "kubectl output was not valid JSON", "fetched_at": datetime.now(timezone.utc).isoformat()})

    pods = _parse_pods(pod_list, namespace)
    return JSONResponse(
        content={
            "pods": [p.model_dump() for p in pods],
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
    )


@router.get("/api/pods/{namespace}/{name}/logs")
async def get_pod_logs(namespace: str, name: str, tail: int = 100) -> JSONResponse:
    try:
        proc = await asyncio.create_subprocess_exec(
            "kubectl", "logs", name, "-n", namespace, f"--tail={tail}", "--timestamps",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=KUBECTL_TIMEOUT)
        except asyncio.TimeoutError:
            proc.kill()
            return JSONResponse(content={"logs": "", "warning": "kubectl logs timed out"})
    except FileNotFoundError:
        return JSONResponse(content={"logs": "", "warning": "kubectl not found"})

    if proc.returncode != 0:
        err = stderr.decode(errors="replace").strip()
        return JSONResponse(content={"logs": "", "warning": err or "kubectl logs failed"})

    return JSONResponse(content={"logs": stdout.decode(errors="replace"), "warning": None})
