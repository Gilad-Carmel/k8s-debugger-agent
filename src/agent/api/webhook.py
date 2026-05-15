"""
src/agent/api/webhook.py

POST /webhook/alertmanager — Alert Intake.

Per contracts/alertmanager_webhook.md:
  1. HMAC verify raw body (X-Alertmanager-Signature). Fail → 401, audit.
  2. Parse Alertmanager v4 subset. Fail → 400/422.
  3. Sliding-window dedup by (groupKey|namespace|pod):
     Existing incident with last_seen_at inside the configured window
     ⇒ update last_seen_at, return 202 deduped.
  4. New ⇒ insert incidents row, audit webhook_received, kick off graph
     run as a background asyncio task, return 202.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError

from src.agent.audit import log_audit_event
from src.agent.db import get_conn
from src.agent.logging_config import get_logger
from src.agent.settings import settings
from src.shared.correlation import bind, new_correlation_id
from src.shared.errors import error_response

router = APIRouter()
log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Request models — Alertmanager v4 subset
# ---------------------------------------------------------------------------
class _Alert(BaseModel):
    status: str
    startsAt: datetime


class AlertmanagerPayload(BaseModel):
    version: str = "4"
    groupKey: str
    status: str
    groupLabels: dict[str, str] = Field(default_factory=dict)
    alerts: list[_Alert] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _verify_signature(body: bytes, signature: Optional[str]) -> bool:
    if not signature:
        return False
    expected = hmac.new(
        settings.ALERTMANAGER_HMAC_SECRET.encode(), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def _dedup_key(group_key: str, namespace: str, pod: str) -> str:
    raw = f"{group_key}|{namespace}|{pod}".encode()
    return hashlib.sha256(raw).hexdigest()


async def _run_graph(graph: Any, correlation_id: str, alert_payload: dict[str, Any]) -> None:
    """Background task that drives the graph until it interrupts (or ends)."""
    bind(correlation_id)
    config = {"configurable": {"thread_id": correlation_id}}
    initial_state = {
        "correlation_id": correlation_id,
        "alert_payload": alert_payload,
    }
    try:
        await graph.ainvoke(initial_state, config=config)
        log.info("graph.run_completed", correlation_id=correlation_id)
    except Exception as exc:  # noqa: BLE001 — last-line-of-defense logging
        log.error("graph.run_failed", correlation_id=correlation_id, error=str(exc))


async def _run_graph_streaming(graph: Any, correlation_id: str, alert_payload: dict[str, Any]) -> None:
    """
    Streaming variant of _run_graph that publishes WorkflowEvents to the GUI
    event bus for each node boundary. Falls back to ainvoke if streaming fails.

    Node names that emit events match the LangGraph node names in builder.py:
      ingest, router, application_expert, network_expert, database_expert,
      reporter, solver.
    """
    from src.agent.api.gui.event_bus import WorkflowEventType, publish

    bind(correlation_id)
    config = {"configurable": {"thread_id": correlation_id}}
    initial_state = {
        "correlation_id": correlation_id,
        "alert_payload": alert_payload,
    }

    _TRACKED_NODES = {
        "ingest", "router",
        "application_expert", "network_expert", "database_expert",
        "reporter", "solver",
    }

    try:
        async for event in graph.astream_events(initial_state, config=config, version="v2"):
            kind = event.get("event", "")
            name = event.get("name", "")

            if name not in _TRACKED_NODES:
                continue

            if kind == "on_chain_start":
                await publish(correlation_id, WorkflowEventType.NODE_STARTED, node=name)

            elif kind == "on_chain_end":
                output = event.get("data", {}).get("output") or {}
                node_data: dict[str, Any] = {}

                if name == "router" and isinstance(output, dict):
                    routing = output.get("routing") or {}
                    node_data = {
                        "domain": getattr(routing, "domain", routing.get("domain", "")),
                        "confidence": getattr(routing, "confidence", routing.get("confidence", 0)),
                    }
                elif name in {"application_expert", "network_expert", "database_expert"} and isinstance(output, dict):
                    diag = output.get("diagnosis") or {}
                    node_data = {
                        "root_cause_label": getattr(diag, "root_cause_label", diag.get("root_cause_label", "")),
                        "severity": getattr(diag, "severity", diag.get("severity", "")),
                    }
                elif name == "reporter" and isinstance(output, dict):
                    report = output.get("report") or {}
                    pf = getattr(report, "proposed_fix", None) or report.get("proposed_fix") or {}
                    node_data = {
                        "summary": getattr(report, "summary", report.get("summary", "")),
                        "proposed_fix_title": getattr(pf, "title", pf.get("title", "") if isinstance(pf, dict) else ""),
                    }
                    # After reporter completes the graph hits the HITL gate
                    await publish(
                        correlation_id,
                        WorkflowEventType.NODE_COMPLETED,
                        node=name,
                        data=node_data,
                    )
                    await publish(
                        correlation_id,
                        WorkflowEventType.AWAITING_APPROVAL,
                        data={
                            "proposed_fix_title": node_data.get("proposed_fix_title", ""),
                            "proposed_fix_description": "",
                        },
                    )
                    continue

                elif name == "solver" and isinstance(output, dict):
                    solver_run = output.get("solver_run") or {}
                    node_data = {
                        "tool_called": getattr(solver_run, "tool_called", solver_run.get("tool_called", "")),
                        "outcome": getattr(solver_run, "outcome", solver_run.get("outcome", "")),
                    }
                    await publish(
                        correlation_id,
                        WorkflowEventType.NODE_COMPLETED,
                        node=name,
                        data=node_data,
                    )
                    await publish(correlation_id, WorkflowEventType.SOLVER_DONE, data=node_data)
                    continue

                await publish(
                    correlation_id,
                    WorkflowEventType.NODE_COMPLETED,
                    node=name,
                    data=node_data,
                )

            elif kind == "on_chain_error":
                err = str(event.get("data", {}).get("error", "unknown error"))
                await publish(
                    correlation_id,
                    WorkflowEventType.NODE_FAILED,
                    node=name,
                    data={"error": err},
                )

        log.info("graph.streaming_run_completed", correlation_id=correlation_id)

    except Exception as exc:  # noqa: BLE001
        log.error("graph.streaming_run_failed", correlation_id=correlation_id, error=str(exc))
        try:
            from src.agent.api.gui.event_bus import WorkflowEventType as WET, publish as pub
            await pub(correlation_id, WET.RUN_FAILED, data={"error": str(exc)})
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------
@router.post("/webhook/alertmanager")
async def alertmanager_webhook(
    request: Request,
    x_alertmanager_signature: Optional[str] = Header(default=None),
) -> JSONResponse:
    body = await request.body()

    # 1. Auth
    if not _verify_signature(body, x_alertmanager_signature):
        # No correlation_id yet — write the audit row under a synthetic one
        # so the rejection is queryable.
        cid = new_correlation_id()
        await log_audit_event(
            cid,
            stage="webhook_rejected",
            outcome="refused",
            payload={"reason": "signature_invalid", "headers_signed": False},
        )
        return JSONResponse(
            status_code=401,
            content=error_response("signature_invalid", "HMAC verification failed."),
        )

    # 2. Parse
    try:
        payload = AlertmanagerPayload.model_validate_json(body)
    except ValidationError as exc:
        return JSONResponse(
            status_code=400,
            content=error_response(
                "bad_request",
                "Malformed Alertmanager payload.",
                detail={"errors": exc.errors()[:5]},
            ),
        )

    namespace = payload.groupLabels.get("namespace", "")
    pod = payload.groupLabels.get("pod", "")
    if not namespace or not pod:
        return JSONResponse(
            status_code=422,
            content=error_response(
                "missing_target",
                "groupLabels.namespace and groupLabels.pod are required.",
            ),
        )

    if not payload.alerts:
        return JSONResponse(
            status_code=422,
            content=error_response("missing_alerts", "alerts[] must be non-empty."),
        )

    now_iso = datetime.now(timezone.utc).isoformat()

    # Resolved alerts: link to the original firing incident if we have one
    # (so the audit log can be reconstructed by correlation_id), otherwise
    # record the resolution under a fresh id and flag that no prior firing
    # was seen. Either way no triage runs.
    #
    # Look up the most recent incident for this alert without a time window —
    # resolutions can arrive after the dedup window expires.
    if payload.status == "resolved":
        async with get_conn() as conn:
            cur = await conn.execute(
                """
                SELECT correlation_id, status FROM incidents
                WHERE source_alert_id = ? AND namespace = ? AND pod = ?
                ORDER BY received_at DESC LIMIT 1
                """,
                (payload.groupKey, namespace, pod),
            )
            existing = await cur.fetchone()
            await cur.close()

            if existing:
                cid = existing["correlation_id"]
                prior_status = existing["status"]
                # Only flip status when the incident hadn't already reached a
                # terminal/acted state. 'pending' is the common case (never
                # approved); 'approved' / 'executed' are also fine to mark
                # resolved upstream. 'rejected' / 'failed' / 'expired' /
                # already 'resolved' are preserved.
                if prior_status in {"pending", "approved", "executed"}:
                    await conn.execute(
                        "UPDATE incidents SET status = 'resolved', last_seen_at = ?"
                        " WHERE correlation_id = ?",
                        (now_iso, cid),
                    )
                else:
                    await conn.execute(
                        "UPDATE incidents SET last_seen_at = ? WHERE correlation_id = ?",
                        (now_iso, cid),
                    )
                await conn.commit()

                bind(cid)
                await log_audit_event(
                    cid,
                    stage="webhook_received",
                    outcome="ok",
                    payload={
                        "source_alert_id": payload.groupKey,
                        "namespace": namespace,
                        "pod": pod,
                        "headers_signed": True,
                        "reason": "resolved_for_existing_incident",
                        "prior_status": prior_status,
                    },
                )
                return JSONResponse(
                    status_code=202,
                    content={
                        "correlation_id": cid,
                        "deduplicated": True,
                        "status": "resolved",
                    },
                )

        # No prior firing found — record the resolution under a fresh id.
        cid = new_correlation_id()
        bind(cid)
        await log_audit_event(
            cid,
            stage="webhook_received",
            outcome="ok",
            payload={
                "source_alert_id": payload.groupKey,
                "namespace": namespace,
                "pod": pod,
                "headers_signed": True,
                "reason": "resolved_no_prior_firing",
            },
        )
        return JSONResponse(
            status_code=202,
            content={"correlation_id": cid, "deduplicated": False, "status": "resolved"},
        )

    dedup_key = _dedup_key(payload.groupKey, namespace, pod)

    # 3. Dedup check + 4. insert/update  (now_iso already set above)
    dedup_cutoff_iso = (
        datetime.now(timezone.utc) - timedelta(seconds=settings.dedup_window_seconds)
    ).isoformat()
    deadline_iso = (
        datetime.now(timezone.utc) + timedelta(seconds=settings.approval_window_seconds)
    ).isoformat()

    async with get_conn() as conn:
        cur = await conn.execute(
            """
            SELECT correlation_id
            FROM incidents
            WHERE source_alert_id = ?
              AND namespace = ?
              AND pod = ?
              AND last_seen_at > ?
            ORDER BY last_seen_at DESC
            LIMIT 1
            """,
            (payload.groupKey, namespace, pod, dedup_cutoff_iso),
        )
        existing = await cur.fetchone()
        await cur.close()

        if existing:
            cid = existing["correlation_id"]
            await conn.execute(
                "UPDATE incidents SET last_seen_at = ? WHERE correlation_id = ?",
                (now_iso, cid),
            )
            await conn.commit()
            bind(cid)
            await log_audit_event(
                cid,
                stage="incident_deduped",
                payload={
                    "dedup_key": dedup_key,
                    "first_seen_correlation_id": cid,
                    "last_seen_at": now_iso,
                },
            )
            return JSONResponse(
                status_code=202,
                content={"correlation_id": cid, "deduplicated": True},
            )

        cid = new_correlation_id()
        bind(cid)
        fingerprint = hashlib.sha256(f"{dedup_key}|{cid}".encode()).hexdigest()
        await conn.execute(
            """
            INSERT INTO incidents (
                correlation_id, dedup_fingerprint, source_alert_id,
                namespace, pod, status, received_at, last_seen_at, approval_deadline
            ) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?)
            """,
            (
                cid,
                fingerprint,
                payload.groupKey,
                namespace,
                pod,
                now_iso,
                now_iso,
                deadline_iso,
            ),
        )
        await conn.commit()

    await log_audit_event(
        cid,
        stage="webhook_received",
        payload={
            "source_alert_id": payload.groupKey,
            "namespace": namespace,
            "pod": pod,
            "headers_signed": True,
        },
    )

    # Kick off the graph in the background. The streaming variant publishes
    # WorkflowEvents to the GUI event bus; falling back to plain ainvoke if
    # streaming raises. spawn_tracked keeps a strong ref so the task can't
    # be GC'd mid-run.
    from src.agent.api import spawn_tracked

    graph = request.app.state.graph
    spawn_tracked(request.app, _run_graph_streaming(graph, cid, payload.model_dump(mode="json")))

    return JSONResponse(
        status_code=202,
        content={"correlation_id": cid, "deduplicated": False},
    )
