"""
src/agent/graph/nodes/ingest.py

Ingest node + proactive log listener (feature 005-router-listener).

This module has two responsibilities:

1.  ``ingest_node`` — LangGraph node (first node in the graph).
    - If ``filtered_evidence`` is already in state (pre-set by the listener
      or by an integration-test fixture), pass through immediately.
    - Otherwise parse target info from ``alert_payload`` / ``incident`` in
      state, call the three MCP read tools concurrently, and populate
      ``filtered_evidence``.

2.  ``run_listener`` — background async coroutine started at app startup.
    Continuously polls ``search_pod_logs`` across every namespace listed in
    ``settings.watch_namespaces``.  When error-pattern hits are found for a
    pod that has not been triaged recently, it creates a fresh
    ``correlation_id`` + ``FilteredEvidence``, inserts a DB incident row for
    dedup tracking, and invokes the compiled LangGraph to run the full
    triage pipeline (router → expert → reporter).

Flow
----
Listener poll                    Graph invocation
─────────────────────────────    ───────────────────────────────────────────
search_pod_logs (per pod)   →    ingest_node (pass-through: evidence set)
  hits found & not deduped  →    router_node (LLM classification)
  create incident row        →    expert_node (diagnosis + proposed fix)
  invoke graph.ainvoke()     →    reporter_node (Slack-mock report)
                                  [HITL interrupt → solver_node on approval]
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from src.agent.graph.state import WorkflowState
from src.agent.settings import settings
from src.mcp_server.tools.get_pod import get_pod
from src.mcp_server.tools.get_pod_events import get_pod_events
from src.mcp_server.tools.search_pod_logs import search_pod_logs
from src.shared.correlation import new_correlation_id
from src.shared.schemas import FilteredEvidence, Incident, Target, TimeWindow

if TYPE_CHECKING:
    from fastapi import FastAPI

log = logging.getLogger(__name__)

_DEFAULT_MAX_HIT_LINES = 100


# ---------------------------------------------------------------------------
# Helpers shared by node + listener
# ---------------------------------------------------------------------------


def _incident_key(namespace: str, pod: str) -> str:
    """Stable key for tracking active incidents: 'namespace/pod'."""
    return f"{namespace}/{pod}"


async def _fetch_evidence(
    namespace: str,
    pod: str,
    container: str | None,
    since: datetime,
    until: datetime,
    correlation_id: str,
) -> FilteredEvidence:
    """
    Call search_pod_logs, get_pod_events, and get_pod concurrently.

    Pod events are converted to synthetic LogExcerpt entries (container
    "k8s-event") and appended to the log hits so every downstream node sees
    them as a single homogeneous evidence list.

    Individual tool failures are logged and absorbed; a partial result is
    always returned so the triage can continue with whatever is available.
    """
    since_minutes = max(1, int((until - since).total_seconds() / 60))

    logs_result, events_result, pod_result = await asyncio.gather(
        search_pod_logs(
            namespace=namespace,
            pod=pod,
            container=container,
            since=since,
            until=until,
            patterns=None,
            max_hit_lines=_DEFAULT_MAX_HIT_LINES,
            correlation_id=correlation_id,
        ),
        get_pod_events(
            namespace=namespace,
            pod=pod,
            since_minutes=since_minutes,
            correlation_id=correlation_id,
        ),
        get_pod(
            namespace=namespace,
            pod=pod,
            correlation_id=correlation_id,
        ),
        return_exceptions=True,
    )

    # --- log evidence ---
    if isinstance(logs_result, FileNotFoundError):
        raise logs_result  # pod gone — propagate so the caller can skip
    if isinstance(logs_result, BaseException):
        log.error("search_pod_logs failed: %s", logs_result, extra={"correlation_id": correlation_id})
        log_evidence = FilteredEvidence(
            total_bytes=0, total_lines=0, hit_lines=[], hit_count=0,
            truncated=False, containers_sampled=[],
        )
    else:
        log_evidence = logs_result

    # --- pod events → synthetic excerpts ---
    from src.shared.schemas import LogExcerpt  # local import avoids circular

    event_excerpts: list[LogExcerpt] = []
    if isinstance(events_result, BaseException):
        log.warning("get_pod_events failed: %s", events_result, extra={"correlation_id": correlation_id})
    else:
        for ev in events_result:
            try:
                ts_raw: str = ev.get("timestamp", "")
                ts = (
                    datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                    if ts_raw
                    else datetime.now(tz=timezone.utc)
                )
                event_type = ev.get("type", "Normal")
                reason = ev.get("reason", "")
                message = ev.get("message", "")
                count = ev.get("count", 1)
                text = f"[k8s-event] {event_type} {reason}: {message}"
                if count > 1:
                    text += f" (x{count})"
                event_excerpts.append(
                    LogExcerpt(timestamp=ts, container="k8s-event", text=text, byte_offset=0)
                )
            except Exception:  # noqa: BLE001
                pass

    # --- pod snapshot — log for context; not stored in WorkflowState yet ---
    if isinstance(pod_result, BaseException):
        log.warning("get_pod failed: %s", pod_result, extra={"correlation_id": correlation_id})
    else:
        log.info(
            "pod snapshot: phase=%s ready=%s",
            pod_result.phase, pod_result.ready,
            extra={"correlation_id": correlation_id},
        )

    # --- merge ---
    merged_hits = list(log_evidence.hit_lines) + event_excerpts
    return FilteredEvidence(
        total_bytes=log_evidence.total_bytes,
        total_lines=log_evidence.total_lines,
        hit_lines=merged_hits,
        hit_count=len(merged_hits) if not log_evidence.truncated else log_evidence.hit_count,
        truncated=log_evidence.truncated,
        containers_sampled=log_evidence.containers_sampled,
    )


# ---------------------------------------------------------------------------
# 1.  LangGraph node
# ---------------------------------------------------------------------------


async def ingest_node(state: WorkflowState) -> WorkflowState:
    """
    First node in the triage graph.

    Pass-through: when the listener (or a test fixture) has already set
    ``filtered_evidence``, return immediately without any I/O.

    Fetch path: read target info from ``incident`` or ``alert_payload`` and
    call the three MCP read tools to populate ``filtered_evidence``.
    """
    # Pass-through guard
    if state.get("filtered_evidence") is not None:
        log.debug("ingest_node: evidence pre-populated; pass-through")
        return {}  # type: ignore[return-value]

    # Resolve target from state
    incident: Incident | None = state.get("incident")  # type: ignore[assignment]
    alert_payload: dict[str, Any] | None = state.get("alert_payload")  # type: ignore[assignment]

    if incident is not None:
        namespace = incident.target.namespace
        pod = incident.target.pod
        container = incident.target.container
        since = incident.time_window.start
        until = incident.time_window.end
        correlation_id = state.get("correlation_id") or incident.correlation_id
    elif alert_payload is not None:
        labels = alert_payload.get("groupLabels", {})
        namespace = labels.get("namespace", "")
        pod = labels.get("pod", "")
        container = None
        now = datetime.now(tz=timezone.utc)
        since = now - timedelta(minutes=settings.listener_lookback_minutes)
        until = now
        correlation_id = state.get("correlation_id") or new_correlation_id()
    else:
        raise ValueError(
            "ingest_node: neither 'incident' nor 'alert_payload' in WorkflowState. "
            "Populate at least one before invoking the graph."
        )

    if not namespace or not pod:
        raise ValueError(
            f"ingest_node: cannot resolve target — namespace={namespace!r} pod={pod!r}"
        )

    log.info(
        "ingest_node: fetching evidence for %s/%s", namespace, pod,
        extra={"correlation_id": correlation_id},
    )

    evidence = await _fetch_evidence(namespace, pod, container, since, until, correlation_id)

    log.info(
        "ingest_node: %d hits (%d log + %d events)",
        len(evidence.hit_lines),
        len(evidence.hit_lines) - sum(1 for h in evidence.hit_lines if h.container == "k8s-event"),
        sum(1 for h in evidence.hit_lines if h.container == "k8s-event"),
        extra={"correlation_id": correlation_id},
    )

    return {"filtered_evidence": evidence}  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# 2.  Proactive listener
# ---------------------------------------------------------------------------


async def _list_pods_in_namespace(namespace: str) -> list[str]:
    """Return Running pod names in *namespace* via the Kubernetes API.

    Excludes Succeeded, Failed, and Unknown-phase pods so throwaway jobs
    (e.g. one-shot curl pods) are never polled for error patterns.
    """
    from src.mcp_server.auth import get_core_v1_for_tool

    v1 = get_core_v1_for_tool("search_pod_logs")
    pod_list = await asyncio.to_thread(v1.list_namespaced_pod, namespace)
    return [
        p.metadata.name
        for p in pod_list.items
        if p.metadata
        and p.metadata.name
        and (p.status and p.status.phase) == "Running"
    ]


async def _poll_pod(
    namespace: str,
    pod: str,
    graph: Any,
    active_incidents: set[str],
) -> None:
    """
    Poll one pod for error logs.

    active_incidents is shared across poll cycles.  A pod is added when a
    graph run fires and removed when the pod goes back to healthy (no hits),
    so it can re-trigger if it breaks again later.
    """
    key = _incident_key(namespace, pod)

    now = datetime.now(tz=timezone.utc)
    since = now - timedelta(minutes=settings.listener_lookback_minutes)
    correlation_id = new_correlation_id()

    try:
        evidence = await _fetch_evidence(namespace, pod, None, since, now, correlation_id)
    except FileNotFoundError:
        log.debug("listener: pod %s/%s not found; skipping", namespace, pod)
        active_incidents.discard(key)
        return
    except Exception as exc:  # noqa: BLE001
        log.warning("listener: evidence fetch failed for %s/%s: %s", namespace, pod, exc)
        return

    if evidence.hit_count == 0:
        # Pod is healthy — clear any active incident so it can re-trigger later.
        if key in active_incidents:
            log.info("listener: %s/%s recovered — clearing active incident", namespace, pod)
            active_incidents.discard(key)
        return

    if key in active_incidents:
        log.debug("listener: %s/%s still unhealthy, already triaged — skipping", namespace, pod)
        return

    active_incidents.add(key)
    log.info(
        "listener: %d error hits in %s/%s — firing triage graph",
        evidence.hit_count, namespace, pod,
        extra={"correlation_id": correlation_id},
    )

    target = Target(namespace=namespace, pod=pod)
    incident = Incident(
        correlation_id=correlation_id,
        source_alert_id=f"listener:{namespace}/{pod}",
        dedup_fingerprint=key,
        target=target,
        time_window=TimeWindow(start=since, end=now),
        received_at=now,
        last_seen_at=now,
        status="pending",
    )

    initial_state: dict[str, Any] = {
        "correlation_id": correlation_id,
        "incident": incident,
        "filtered_evidence": evidence,
        "budget_remaining_tokens": settings.budget_tokens_per_incident,
        "budget_remaining_usd_micros": settings.budget_usd_micros_per_incident,
    }

    try:
        config = {"configurable": {"thread_id": correlation_id}}
        await graph.ainvoke(initial_state, config=config)
        log.info("listener: graph run completed", extra={"correlation_id": correlation_id})
    except Exception as exc:  # noqa: BLE001
        log.error(
            "listener: graph run failed for %s/%s: %s", namespace, pod, exc,
            extra={"correlation_id": correlation_id},
        )


async def run_listener(graph: Any, app: "FastAPI | None" = None) -> None:
    """
    Background coroutine: poll MCP for error logs across all watched namespaces.

    Start this at app startup::

        import asyncio
        from src.agent.graph.nodes.ingest import run_listener

        @app.on_event("startup")
        async def _start_listener():
            asyncio.create_task(run_listener(app.state.graph, app))

    active_incidents tracks which pods currently have an open triage run.
    A pod is added on first detection and removed when it goes healthy again,
    allowing re-triggering on future failures.
    """
    namespaces = settings.watch_namespace_list
    interval = settings.poll_interval_seconds

    log.info(
        "listener: starting — namespaces=%s poll_interval=%ds lookback=%dmin",
        namespaces, interval, settings.listener_lookback_minutes,
    )

    # Persisted across poll cycles: namespace/pod keys that are actively broken.
    active_incidents: set[str] = set()

    while True:
        await asyncio.sleep(interval)

        for namespace in namespaces:
            try:
                pods = await _list_pods_in_namespace(namespace)
            except Exception as exc:  # noqa: BLE001
                log.warning("listener: cannot list pods in %s: %s", namespace, exc)
                continue

            log.debug("listener: polling %d pods in %s", len(pods), namespace)

            await asyncio.gather(
                *(_poll_pod(namespace, pod, graph, active_incidents) for pod in pods),
                return_exceptions=True,
            )
