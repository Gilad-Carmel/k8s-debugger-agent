"""
src/agent/api/callbacks.py

POST /callbacks/slack/approve
POST /callbacks/slack/reject

Per contracts/slack_mock.md, in this order:
  1. HMAC verify body w/ SLACK_MOCK_SECRET           → 401 signature_invalid
  2. Lookup incident                                 → 404 report_not_found
  3. Status guard (only `pending` accepts)           → 409 report_<status>
  4. Deadline check                                  → 409 approval_expired (audit)
  5. Role check (approve only)                       → 403 role_check_failed (audit)
  6. Audit approval_event, flip incident status,
     issue approval token, and resume the LangGraph run with
     approval_status = APPROVED|REJECTED.
  7. 200 {correlation_id, status}
"""
from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError

from src.agent.approval_token import issue_token
from src.agent.audit import log_audit_event
from src.agent.auth import check_approver_role
from src.agent.db import get_conn
from src.agent.logging_config import get_logger
from src.agent.settings import settings
from src.shared.correlation import bind
from src.shared.errors import error_response

router = APIRouter()
log = get_logger(__name__)


class _Actor(BaseModel):
    user_id: str
    name: Optional[str] = None
    roles: list[str] = Field(default_factory=list)


class CallbackBody(BaseModel):
    correlation_id: str
    actor: _Actor
    action_id: Optional[str] = None
    reason: Optional[str] = None
    clicked_at: datetime


def _verify_signature(body: bytes, signature: Optional[str]) -> bool:
    if not signature:
        return False
    expected = hmac.new(
        settings.SLACK_MOCK_SECRET.encode(), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


async def _resume_graph(
    graph: Any, correlation_id: str, approval_status: str, approval_token: str = ""
) -> None:
    """Update the paused checkpoint's state with the approval verdict and continue."""
    bind(correlation_id)
    config = {"configurable": {"thread_id": correlation_id}}
    try:
        await graph.aupdate_state(
            config,
            {"approval_status": approval_status, "approval_token": approval_token},
        )
        # Pass None to continue from where we paused.
        await graph.ainvoke(None, config=config)
        log.info("graph.resume_completed", correlation_id=correlation_id, status=approval_status)
    except Exception as exc:  # noqa: BLE001
        log.error(
            "graph.resume_failed",
            correlation_id=correlation_id,
            status=approval_status,
            error=str(exc),
        )


async def _handle(action: str, request: Request, body_bytes: bytes, signature: Optional[str]) -> JSONResponse:
    # 1. Auth
    if not _verify_signature(body_bytes, signature):
        return JSONResponse(
            status_code=401,
            content=error_response("signature_invalid", "HMAC verification failed."),
        )

    # Parse
    try:
        body = CallbackBody.model_validate_json(body_bytes)
    except ValidationError as exc:
        return JSONResponse(
            status_code=400,
            content=error_response(
                "bad_request",
                "Malformed callback body.",
                detail={"errors": exc.errors()[:5]},
            ),
        )

    cid = body.correlation_id
    bind(cid)
    clicked_at = body.clicked_at
    if clicked_at.tzinfo is None:
        clicked_at = clicked_at.replace(tzinfo=timezone.utc)

    # 2. Lookup incident
    async with get_conn() as conn:
        cur = await conn.execute(
            """
            SELECT correlation_id, status, approval_deadline, proposed_fix_fingerprint
            FROM incidents
            WHERE correlation_id = ?
            """,
            (cid,),
        )
        incident = await cur.fetchone()
        await cur.close()

    if not incident:
        return JSONResponse(
            status_code=404,
            content=error_response("report_not_found", "Unknown correlation_id.", correlation_id=cid),
        )

    # 3. Status guard
    current_status = incident["status"]
    if current_status != "pending":
        return JSONResponse(
            status_code=409,
            content=error_response(
                f"report_{current_status}",
                f"Report is already {current_status}.",
                correlation_id=cid,
            ),
        )

    # 4. Deadline
    deadline_iso = incident["approval_deadline"]
    if deadline_iso:
        deadline = datetime.fromisoformat(deadline_iso)
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
        if clicked_at >= deadline:
            await log_audit_event(
                cid,
                stage="approval_event",
                outcome="refused",
                actor={"type": "user", "id": body.actor.user_id, "roles": body.actor.roles},
                payload={
                    "action": action,
                    "actor_id": body.actor.user_id,
                    "actor_roles": body.actor.roles,
                    "role_check_passed": False,
                    "reason": "expired",
                },
            )
            async with get_conn() as conn:
                await conn.execute(
                    "UPDATE incidents SET status = 'expired' WHERE correlation_id = ?",
                    (cid,),
                )
                await conn.commit()
            return JSONResponse(
                status_code=409,
                content=error_response("approval_expired", "Approval window has elapsed.", correlation_id=cid),
            )

    # 5. Role check (approve only)
    role_ok = True
    if action == "approve":
        role_ok = check_approver_role(body.actor.roles)
        if not role_ok:
            await log_audit_event(
                cid,
                stage="approval_event",
                outcome="refused",
                actor={"type": "user", "id": body.actor.user_id, "roles": body.actor.roles},
                payload={
                    "action": "approve",
                    "actor_id": body.actor.user_id,
                    "actor_roles": body.actor.roles,
                    "role_check_passed": False,
                    "reason": "insufficient_role",
                },
            )
            return JSONResponse(
                status_code=403,
                content=error_response(
                    "role_check_failed",
                    f"Approver must hold the '{settings.APPROVER_ROLE}' role.",
                    correlation_id=cid,
                ),
            )

    # 6. Persist + flip status + issue token + resume graph
    new_status = "approved" if action == "approve" else "rejected"
    approval_status_for_state = "APPROVED" if action == "approve" else "REJECTED"

    audit_payload: dict[str, Any] = {
        "action": action,
        "actor_id": body.actor.user_id,
        "actor_roles": body.actor.roles,
        "role_check_passed": role_ok,
        "reason": body.reason,
    }
    token = ""
    if action == "approve":
        # Issue a token bound to the frozen ProposedFix fingerprint so
        # Person 1's Solver pre-flight can verify nothing changed
        # between approval and execution.
        fp = incident["proposed_fix_fingerprint"] or ""
        token = issue_token(cid, fp)
        audit_payload["approval_token"] = token

    await log_audit_event(
        cid,
        stage="approval_event",
        actor={"type": "user", "id": body.actor.user_id, "roles": body.actor.roles},
        payload=audit_payload,
    )

    async with get_conn() as conn:
        await conn.execute(
            "UPDATE incidents SET status = ? WHERE correlation_id = ?",
            (new_status, cid),
        )
        await conn.commit()

    # Resume the paused graph in a background task so the HTTP response
    # returns immediately (the contract says 200 on accept, the actual
    # solver work is async). spawn_tracked keeps a strong ref so the task
    # can't be GC'd mid-run.
    from src.agent.api import spawn_tracked

    spawn_tracked(
        request.app,
        _resume_graph(request.app.state.graph, cid, approval_status_for_state, token),
    )

    return JSONResponse(
        status_code=200,
        content={"correlation_id": cid, "status": new_status},
    )


@router.post("/callbacks/slack/approve")
async def approve(
    request: Request,
    x_slack_mock_signature: Optional[str] = Header(default=None),
) -> JSONResponse:
    body_bytes = await request.body()
    return await _handle("approve", request, body_bytes, x_slack_mock_signature)


@router.post("/callbacks/slack/reject")
async def reject(
    request: Request,
    x_slack_mock_signature: Optional[str] = Header(default=None),
) -> JSONResponse:
    body_bytes = await request.body()
    return await _handle("reject", request, body_bytes, x_slack_mock_signature)
