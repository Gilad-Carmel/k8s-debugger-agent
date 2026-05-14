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

import asyncio
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
        # When ainvoke returns, the graph either ended or hit the
        # interrupt_before=["solver"] gate. Either way, no more work here.
        log.info("graph.run_completed", correlation_id=correlation_id)
    except Exception as exc:  # noqa: BLE001 — last-line-of-defense logging
        log.error("graph.run_failed", correlation_id=correlation_id, error=str(exc))


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

    # Resolved alerts are recorded but short-circuited (no triage).
    if payload.status == "resolved":
        cid = new_correlation_id()
        await log_audit_event(
            cid,
            stage="webhook_received",
            outcome="ok",
            payload={
                "source_alert_id": payload.groupKey,
                "namespace": namespace,
                "pod": pod,
                "headers_signed": True,
                "reason": "resolved_short_circuit",
            },
        )
        return JSONResponse(
            status_code=202,
            content={"correlation_id": cid, "deduplicated": False, "status": "resolved"},
        )

    dedup_key = _dedup_key(payload.groupKey, namespace, pod)

    # 3. Dedup check + 4. insert/update
    now_iso = datetime.now(timezone.utc).isoformat()
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

    # Kick off the graph in the background. ainvoke runs until the
    # interrupt_before=["solver"] gate; the HITL callback resumes it.
    graph = request.app.state.graph
    asyncio.create_task(_run_graph(graph, cid, payload.model_dump(mode="json")))

    return JSONResponse(
        status_code=202,
        content={"correlation_id": cid, "deduplicated": False},
    )
