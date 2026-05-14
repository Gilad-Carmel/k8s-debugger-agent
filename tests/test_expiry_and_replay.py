"""
tests/test_expiry_and_replay.py — slow / cross-cutting flows.

- Expiry sweeper transitions PENDING → EXPIRED and resumes the graph
  with approval_status='EXPIRED' (no Solver invocation).
- Checkpoint replay: a webhook in process A pauses at the interrupt;
  process B starts against the same SQLite file and the approve
  callback successfully resumes the graph.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from asgi_lifespan import LifespanManager

from src.agent.api import create_app
from src.agent.api.expiry import _expire_one
from src.agent.audit import fetch_chain
from src.agent.db import get_conn
from src.agent.settings import settings
from tests.conftest import fire_callback, fire_webhook


async def test_expiry_sweep_transitions_pending_to_expired(
    fresh_db: Path,
    alertmanager_payload,
    sign_alertmanager,
) -> None:
    """We don't wait the full 30s — we backdate the deadline and call the
    sweeper helper directly so the test runs in <2s. Builds its own app
    so it can grab `app.state.graph` directly."""
    app = create_app()
    async with LifespanManager(app) as mgr:
        transport = httpx.ASGITransport(app=mgr.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await fire_webhook(c, alertmanager_payload(), sign_alertmanager)
            assert r.status_code == 202
            cid = r.json()["correlation_id"]
            await asyncio.sleep(1.0)  # let graph hit interrupt

            # Backdate so the sweeper sees this incident as past-deadline.
            async with get_conn() as conn:
                past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
                await conn.execute(
                    "UPDATE incidents SET approval_deadline = ? WHERE correlation_id = ?",
                    (past, cid),
                )
                await conn.commit()

            # Drive the sweeper directly against the running app's graph.
            await _expire_one(app.state.graph, cid)
            await asyncio.sleep(1.0)

    async with get_conn() as conn:
        cur = await conn.execute(
            "SELECT status FROM incidents WHERE correlation_id = ?", (cid,)
        )
        assert (await cur.fetchone())["status"] == "expired"

    chain = await fetch_chain(cid)
    stages = [r["stage"] for r in chain]
    # Solver MUST NOT have run on an expired incident.
    assert "solver_placeholder" not in stages
    # The sweeper recorded the expiry as a refused approval_event.
    expired_audits = [
        r for r in chain
        if r["stage"] == "approval_event"
        and r["outcome"] == "refused"
        and r["payload"].get("reason") == "expired"
    ]
    assert expired_audits


async def test_checkpoint_replay_across_app_restarts(
    fresh_db: Path,
    alertmanager_payload,
    sign_alertmanager,
    sign_slack,
    callback_payload,
) -> None:
    """
    Boot app #1 → fire webhook → graph pauses at interrupt → tear app #1 down.
    Boot app #2 against the SAME sqlite file → fire approve callback →
    graph resumes from the persisted checkpoint and runs the solver.
    This proves AsyncSqliteSaver actually persists, not just in-process state.
    """
    # ---- App #1: open a pending incident, then shut down ----
    app1 = create_app()
    async with LifespanManager(app1) as mgr1:
        transport1 = httpx.ASGITransport(app=mgr1.app)
        async with httpx.AsyncClient(transport=transport1, base_url="http://test") as c1:
            r = await fire_webhook(c1, alertmanager_payload(), sign_alertmanager)
            assert r.status_code == 202
            cid = r.json()["correlation_id"]
            await asyncio.sleep(1.0)

            # Pre-restart sanity: solver hasn't run.
            chain_before = await fetch_chain(cid)
            assert "solver_placeholder" not in [r["stage"] for r in chain_before]

    # App #1 fully torn down here. The sqlite file persists.
    assert Path(settings.SQLITE_PATH).exists()

    # ---- App #2: same DB file, new process-equivalent ----
    app2 = create_app()
    async with LifespanManager(app2) as mgr2:
        transport2 = httpx.ASGITransport(app=mgr2.app)
        async with httpx.AsyncClient(transport=transport2, base_url="http://test") as c2:
            r = await fire_callback(
                c2, "approve", callback_payload(correlation_id=cid), sign_slack
            )
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "approved"
            await asyncio.sleep(1.5)

            chain_after = await fetch_chain(cid)
            stages = [r["stage"] for r in chain_after]
            # The graph resumed from checkpoint and ran solver in app #2.
            assert "solver_placeholder" in stages
            # And we still have all the rows from app #1 — same audit_log.
            assert "webhook_received" in stages
            assert "reporter_placeholder" in stages
