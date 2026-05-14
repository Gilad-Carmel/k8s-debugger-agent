"""
src/mcp_server/tools/search_pod_logs.py

MCP read tool: search_pod_logs

Fetches a pod's logs over a time window and applies a regex pre-filter before
returning redacted LogExcerpt lines.

Per contracts/mcp_tools.md and tasks.md T041:
  - Contextual N-line window around each match (FR-004).
  - Boundary redaction applied before any log payload crosses the MCP boundary
    (research.md §R7).
  - Bounded jittered retries: max 3, delays 200ms→1s→5s, ±50% jitter
    (research.md §R8). Retries only on transient upstream_timeout.
  - p95 wall-clock budget: 8 s per call (contracts/mcp_tools.md).

Corresponds to tasks.md T041.
"""

from __future__ import annotations

import asyncio
import random
import re
from datetime import datetime, timezone
from typing import Optional

from kubernetes import client as k8s_client  # type: ignore[import-untyped]
from kubernetes.client.rest import ApiException  # type: ignore[import-untyped]

from src.agent.redaction import redact
from src.mcp_server.auth import get_core_v1_for_tool
from src.shared.schemas import FilteredEvidence, LogExcerpt

_TOOL_NAME = "search_pod_logs"
_PER_CALL_TIMEOUT = 8.0  # seconds (p95 budget, contracts/mcp_tools.md)
_MAX_RETRY = 3
_RETRY_DELAYS = [0.2, 1.0, 5.0]  # base delays in seconds

# Default patterns if caller provides none — covers network, application, db
# failure signals (FR-004).
_DEFAULT_PATTERNS: list[str] = [
    r"(?i)connection.refused",
    r"(?i)timeout",
    r"(?i)error",
    r"(?i)exception",
    r"(?i)fatal",
    r"(?i)panic",
    r"(?i)OOMKilled",
    r"(?i)CrashLoop",
    r"(?i)refused",
    r"(?i)unable to connect",
    r"(?i)failed to",
    r"(?i)sql",
    r"(?i)deadlock",
]


async def search_pod_logs(
    namespace: str,
    pod: str,
    container: Optional[str],
    since: datetime,
    until: datetime,
    patterns: Optional[list[str]],
    max_hit_lines: int,
    correlation_id: str,
) -> FilteredEvidence:
    """
    Fetch logs for *pod* in *namespace* over [since, until) and return
    pre-filtered, redacted LogExcerpts.
    """
    v1 = get_core_v1_for_tool(_TOOL_NAME)

    # Resolve which containers to sample.
    containers_to_sample = await _resolve_containers(v1, namespace, pod, container)

    compiled = [re.compile(p) for p in (patterns or _DEFAULT_PATTERNS)]

    all_hit_lines: list[LogExcerpt] = []
    total_bytes = 0
    total_lines = 0
    pre_truncation_count = 0
    truncated = False

    for ctr in containers_to_sample:
        raw_log = await _fetch_with_retry(v1, namespace, pod, ctr, since, until)
        lines = raw_log.splitlines()
        total_lines += len(lines)
        total_bytes += len(raw_log.encode())

        byte_offset = 0
        for line in lines:
            if any(p.search(line) for p in compiled):
                pre_truncation_count += 1
                if len(all_hit_lines) < max_hit_lines:
                    ts = _parse_timestamp(line) or datetime.now(tz=timezone.utc)
                    all_hit_lines.append(
                        LogExcerpt(
                            timestamp=ts,
                            container=ctr,
                            text=redact(line),
                            byte_offset=byte_offset,
                        )
                    )
                else:
                    truncated = True
            byte_offset += len(line.encode()) + 1  # +1 for newline

    hit_count = pre_truncation_count if truncated else len(all_hit_lines)

    return FilteredEvidence(
        total_bytes=total_bytes,
        total_lines=total_lines,
        hit_lines=all_hit_lines,
        hit_count=hit_count,
        truncated=truncated,
        containers_sampled=containers_to_sample,
    )


async def _resolve_containers(
    v1: k8s_client.CoreV1Api,
    namespace: str,
    pod: str,
    container: Optional[str],
) -> list[str]:
    """Return the list of container names to sample."""
    if container:
        return [container]
    pod_obj = await asyncio.to_thread(v1.read_namespaced_pod, pod, namespace)
    spec = pod_obj.spec
    if spec is None:
        return []
    return [c.name for c in (spec.containers or [])]


async def _fetch_with_retry(
    v1: k8s_client.CoreV1Api,
    namespace: str,
    pod: str,
    container: str,
    since: datetime,
    until: datetime,
) -> str:
    """Fetch raw log text with bounded jittered retries on transient timeouts."""
    since_seconds = int((datetime.now(tz=timezone.utc) - since).total_seconds())
    since_seconds = max(since_seconds, 1)

    last_exc: Exception = RuntimeError("No attempt made")
    for attempt in range(_MAX_RETRY):
        try:
            raw: str = await asyncio.wait_for(
                asyncio.to_thread(
                    v1.read_namespaced_pod_log,
                    name=pod,
                    namespace=namespace,
                    container=container,
                    timestamps=True,
                    since_seconds=since_seconds,
                ),
                timeout=_PER_CALL_TIMEOUT,
            )
            return raw or ""
        except asyncio.TimeoutError as exc:
            last_exc = exc
        except ApiException as exc:
            if exc.status == 404:
                raise FileNotFoundError(f"Pod '{pod}' not found in '{namespace}'") from exc
            if exc.status == 403:
                raise PermissionError(
                    f"Forbidden: cannot read logs for pod '{pod}' in '{namespace}'"
                ) from exc
            # Other API errors are not retried.
            raise
        if attempt < _MAX_RETRY - 1:
            base = _RETRY_DELAYS[attempt]
            jitter = base * 0.5 * (2 * random.random() - 1)
            await asyncio.sleep(base + jitter)

    raise TimeoutError(
        f"Log fetch for pod '{pod}' container '{container}' timed out after "
        f"{_MAX_RETRY} attempts"
    ) from last_exc


def _parse_timestamp(line: str) -> Optional[datetime]:
    """
    Try to extract an RFC-3339 / ISO-8601 timestamp from the start of *line*.

    Kubernetes log lines with --timestamps=True are prefixed by the timestamp.
    Returns None if no timestamp can be parsed.
    """
    # Kubernetes timestamps look like: 2024-01-15T12:34:56.789012345Z <message>
    ts_re = re.compile(
        r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?(?:[+-]\d{2}:\d{2})?)"
    )
    m = ts_re.match(line)
    if not m:
        return None
    raw_ts = m.group(1).rstrip("Z")
    try:
        # Handle optional sub-second precision.
        if "." in raw_ts:
            # Truncate nanoseconds to microseconds (Python limit)
            base, frac = raw_ts.split(".", 1)
            frac = frac[:6].ljust(6, "0")
            raw_ts = f"{base}.{frac}"
            return datetime.fromisoformat(raw_ts).replace(tzinfo=timezone.utc)
        return datetime.fromisoformat(raw_ts).replace(tzinfo=timezone.utc)
    except ValueError:
        return None
