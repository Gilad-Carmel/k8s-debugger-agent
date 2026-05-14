"""
src/agent/api/expiry.py

Background sweeper. Every settings.EXPIRY_SWEEP_SECONDS:
  - Find incidents WHERE status='pending' AND approval_deadline < now()
  - Flip them to 'expired'
  - Audit `approval_event(action=reject, reason=expired)`
  - Resume the paused graph thread with approval_status='EXPIRED' so it
    terminates cleanly without invoking the Solver.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from src.agent.audit import log_audit_event
from src.agent.db import get_conn
from src.agent.logging_config import get_logger
from src.agent.settings import settings
from src.shared.correlation import bind

log = get_logger(__name__)


async def _expire_one(graph: Any, correlation_id: str) -> None:
    bind(correlation_id)
    async with get_conn() as conn:
        await conn.execute(
            "UPDATE incidents SET status = 'expired' WHERE correlation_id = ? AND status = 'pending'",
            (correlation_id,),
        )
        await conn.commit()
    await log_audit_event(
        correlation_id,
        stage="approval_event",
        outcome="refused",
        actor={"type": "system", "id": "expiry_watcher"},
        payload={
            "action": "reject",
            "role_check_passed": False,
            "reason": "expired",
        },
    )
    config = {"configurable": {"thread_id": correlation_id}}
    try:
        await graph.aupdate_state(config, {"approval_status": "EXPIRED", "approval_token": ""})
        await graph.ainvoke(None, config=config)
    except Exception as exc:  # noqa: BLE001
        log.warning("expiry.resume_failed", correlation_id=correlation_id, error=str(exc))


async def expiry_loop(graph: Any) -> None:
    """Run forever. Cancel via task.cancel() in lifespan shutdown."""
    log.info("expiry.start", interval=settings.EXPIRY_SWEEP_SECONDS)
    try:
        while True:
            await asyncio.sleep(settings.EXPIRY_SWEEP_SECONDS)
            now_iso = datetime.now(timezone.utc).isoformat()
            try:
                async with get_conn() as conn:
                    cur = await conn.execute(
                        """
                        SELECT correlation_id FROM incidents
                        WHERE status = 'pending' AND approval_deadline IS NOT NULL
                          AND approval_deadline < ?
                        """,
                        (now_iso,),
                    )
                    rows = await cur.fetchall()
                    await cur.close()
                for row in rows:
                    await _expire_one(graph, row["correlation_id"])
            except Exception as exc:  # noqa: BLE001 — keep the loop alive
                log.error("expiry.sweep_failed", error=str(exc))
    except asyncio.CancelledError:
        log.info("expiry.stop")
        raise
