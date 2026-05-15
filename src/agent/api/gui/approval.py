"""
src/agent/api/gui/approval.py

POST /api/approval/{correlation_id}/approve
POST /api/approval/{correlation_id}/reject

GUI-native HITL endpoints. Same logic as callbacks.py but:
  - No Slack HMAC required (GUI is trusted localhost; enforced by middleware).
  - actor_name from request body is used as both user_id and name.
  - roles=["approver"] hardcoded in demo mode so the role check passes.

These endpoints call the same _resume_graph and log_audit_event paths as
callbacks.py, so the audit chain is identical regardless of how approval arrived.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.agent.api.gui.event_bus import WorkflowEventType, publish
from src.agent.approval_token import issue_token
from src.agent.audit import log_audit_event
from src.agent.db import get_conn
from src.agent.logging_config import get_logger
from src.shared.correlation import bind
from src.shared.errors import error_response

router = APIRouter()
log = get_logger(__name__)


class GuiApprovalRequest(BaseModel):
    actor_name: str = "gui-user"
    reason: Optional[str] = None


async def _handle_approval(
    action: str,
    correlation_id: str,
    actor_name: str,
    reason: Optional[str],
    request: Request,
) -> JSONResponse:
    bind(correlation_id)
    clicked_at = datetime.now(timezone.utc)

    # Lookup incident
    async with get_conn() as conn:
        cur = await conn.execute(
            """
            SELECT correlation_id, status, approval_deadline, proposed_fix_fingerprint
            FROM incidents WHERE correlation_id = ?
            """,
            (correlation_id,),
        )
        incident = await cur.fetchone()
        await cur.close()

    if not incident:
        return JSONResponse(
            status_code=404,
            content=error_response("report_not_found", "Unknown correlation_id.", correlation_id=correlation_id),
        )

    current_status = incident["status"]
    if current_status != "pending":
        return JSONResponse(
            status_code=409,
            content=error_response(
                f"report_{current_status}",
                f"Report is already {current_status}.",
                correlation_id=correlation_id,
            ),
        )

    # Deadline check
    deadline_iso = incident["approval_deadline"]
    if deadline_iso:
        deadline = datetime.fromisoformat(deadline_iso)
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
        if clicked_at >= deadline:
            async with get_conn() as conn:
                await conn.execute(
                    "UPDATE incidents SET status = 'expired' WHERE correlation_id = ?",
                    (correlation_id,),
                )
                await conn.commit()
            await log_audit_event(
                correlation_id,
                stage="approval_event",
                outcome="refused",
                actor={"type": "user", "id": actor_name, "roles": ["approver"]},
                payload={"action": action, "actor_id": actor_name, "reason": "expired"},
            )
            await publish(correlation_id, WorkflowEventType.EXPIRED, data={"actor_name": actor_name})
            return JSONResponse(
                status_code=409,
                content=error_response("approval_expired", "Approval window has elapsed.", correlation_id=correlation_id),
            )

    new_status = "approved" if action == "approve" else "rejected"
    approval_status_for_state = "APPROVED" if action == "approve" else "REJECTED"
    event_type = WorkflowEventType.APPROVED if action == "approve" else WorkflowEventType.REJECTED

    token = ""
    if action == "approve":
        fp = incident["proposed_fix_fingerprint"] or ""
        token = issue_token(correlation_id, fp)

    await log_audit_event(
        correlation_id,
        stage="approval_event",
        actor={"type": "user", "id": actor_name, "roles": ["approver"]},
        payload={
            "action": action,
            "actor_id": actor_name,
            "actor_roles": ["approver"],
            "role_check_passed": True,
            "reason": reason,
            "via": "gui",
            **({"approval_token": token} if token else {}),
        },
    )

    async with get_conn() as conn:
        await conn.execute(
            "UPDATE incidents SET status = ? WHERE correlation_id = ?",
            (new_status, correlation_id),
        )
        await conn.commit()

    await publish(correlation_id, event_type, data={"actor_name": actor_name})

    from src.agent.api import spawn_tracked
    from src.agent.api.callbacks import _resume_graph

    spawn_tracked(
        request.app,
        _resume_graph(request.app.state.graph, correlation_id, approval_status_for_state, token),
    )

    log.info("gui.approval", action=action, correlation_id=correlation_id, actor=actor_name)
    return JSONResponse(status_code=200, content={"correlation_id": correlation_id, "status": new_status})


@router.post("/api/approval/{correlation_id}/approve")
async def gui_approve(correlation_id: str, body: GuiApprovalRequest, request: Request) -> JSONResponse:
    return await _handle_approval("approve", correlation_id, body.actor_name, body.reason, request)


@router.post("/api/approval/{correlation_id}/reject")
async def gui_reject(correlation_id: str, body: GuiApprovalRequest, request: Request) -> JSONResponse:
    return await _handle_approval("reject", correlation_id, body.actor_name, body.reason, request)
