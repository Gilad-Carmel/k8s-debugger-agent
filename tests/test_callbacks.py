"""tests/test_callbacks.py — POST /callbacks/slack/{approve,reject} scenarios."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from src.agent.audit import fetch_chain
from src.agent.db import get_conn
from tests.conftest import fire_callback, fire_webhook, silence_graph


async def _open_pending_incident_via_webhook(
    client, alertmanager_payload, sign_alertmanager
) -> str:
    """Drives the graph past the Router via the real webhook (requires a live LLM)."""
    r = await fire_webhook(client, alertmanager_payload(), sign_alertmanager)
    assert r.status_code == 202
    cid = r.json()["correlation_id"]
    # Let the placeholder graph run to the interrupt.
    await asyncio.sleep(1.0)
    return cid


async def _open_pending_incident(pod: str = "checkout-test-x29") -> str:
    """
    Insert a pending incidents row directly via SQL so callback tests
    can exercise the HTTP-level rejection logic without spawning a
    graph task that hangs on the LLM call.

    Returns the synthetic correlation_id.
    """
    import uuid
    cid = uuid.uuid4().hex
    now = datetime.now(timezone.utc).isoformat()
    deadline = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
    fingerprint = f"test-fp-{cid}"
    async with get_conn() as conn:
        await conn.execute(
            """
            INSERT INTO incidents (
                correlation_id, dedup_fingerprint, source_alert_id,
                namespace, pod, status, received_at, last_seen_at, approval_deadline
            ) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?)
            """,
            (cid, fingerprint, "test-alert", "default", pod, now, now, deadline),
        )
        await conn.commit()
    return cid


async def test_approve_happy_path(
    requires_llm,
    app_and_client,
    alertmanager_payload,
    sign_alertmanager,
    sign_slack,
    callback_payload,
) -> None:
    from tests.conftest import graph_state

    app, client = app_and_client
    cid = await _open_pending_incident_via_webhook(
        client, alertmanager_payload, sign_alertmanager
    )
    r = await fire_callback(client, "approve", callback_payload(correlation_id=cid), sign_slack)
    assert r.status_code == 200
    assert r.json() == {"correlation_id": cid, "status": "approved"}

    # Wait for the resumed graph to run the solver.
    await asyncio.sleep(1.5)
    chain = await fetch_chain(cid)
    assert "approval_event" in [r["stage"] for r in chain]

    # incidents.status flipped to approved.
    async with get_conn() as conn:
        cur = await conn.execute(
            "SELECT status FROM incidents WHERE correlation_id = ?", (cid,)
        )
        row = await cur.fetchone()
        assert row["status"] == "approved"

    # Graph resumed past the interrupt and ran the Solver — `solver_run` is set.
    state = await graph_state(app, cid)
    assert "solver_run" in state, f"solver did not run; state keys: {list(state)}"


async def test_reject_does_not_invoke_solver(
    app_and_client,
    sign_slack,
    callback_payload,
) -> None:
    """Rejection: HTTP-level test of the audit + status-flip path.
    The graph is stubbed so we don't hit the LLM. The 'reject doesn't
    invoke solver' invariant is enforced by the post-interrupt edge in
    builder.py — see test_n_concurrent_webhooks_*  for the live-graph
    coverage of that edge."""
    app, client = app_and_client
    silence_graph(app)
    cid = await _open_pending_incident()
    r = await fire_callback(client, "reject", callback_payload(correlation_id=cid), sign_slack)
    assert r.status_code == 200
    assert r.json() == {"correlation_id": cid, "status": "rejected"}

    chain = await fetch_chain(cid)
    assert "approval_event" in [r["stage"] for r in chain]

    async with get_conn() as conn:
        cur = await conn.execute(
            "SELECT status FROM incidents WHERE correlation_id = ?", (cid,)
        )
        assert (await cur.fetchone())["status"] == "rejected"


async def test_callback_bad_signature_rejected(
    client: httpx.AsyncClient,
    sign_slack,
    callback_payload,
) -> None:
    cid = await _open_pending_incident()
    r = await fire_callback(
        client, "approve", callback_payload(correlation_id=cid), sign_slack, bad_sig=True
    )
    assert r.status_code == 401
    assert r.json()["error"] == "signature_invalid"

    # Status stays pending; no audit approval_event.
    async with get_conn() as conn:
        cur = await conn.execute(
            "SELECT status FROM incidents WHERE correlation_id = ?", (cid,)
        )
        assert (await cur.fetchone())["status"] == "pending"


async def test_callback_unknown_correlation_id_returns_404(
    client: httpx.AsyncClient, sign_slack, callback_payload
) -> None:
    fake_cid = "0" * 32
    r = await fire_callback(
        client, "approve", callback_payload(correlation_id=fake_cid), sign_slack
    )
    assert r.status_code == 404
    assert r.json()["error"] == "report_not_found"


async def test_callback_wrong_role_returns_403_and_audits(
    client: httpx.AsyncClient,
    sign_slack,
    callback_payload,
) -> None:
    cid = await _open_pending_incident()
    body = callback_payload(correlation_id=cid, roles=["just-a-viewer"])
    r = await fire_callback(client, "approve", body, sign_slack)
    assert r.status_code == 403
    assert r.json()["error"] == "role_check_failed"

    # Status is still pending; the failed attempt was audited as refused.
    async with get_conn() as conn:
        cur = await conn.execute(
            "SELECT status FROM incidents WHERE correlation_id = ?", (cid,)
        )
        assert (await cur.fetchone())["status"] == "pending"

    chain = await fetch_chain(cid)
    refused = [r for r in chain if r["stage"] == "approval_event" and r["outcome"] == "refused"]
    assert refused, "wrong-role attempt MUST be audited as refused"
    assert refused[0]["payload"]["role_check_passed"] is False


async def test_callback_double_approve_returns_409(
    app_and_client,
    sign_slack,
    callback_payload,
) -> None:
    app, client = app_and_client
    silence_graph(app)
    cid = await _open_pending_incident()
    r1 = await fire_callback(client, "approve", callback_payload(correlation_id=cid), sign_slack)
    assert r1.status_code == 200

    # Second click while status is no longer pending.
    r2 = await fire_callback(client, "approve", callback_payload(correlation_id=cid), sign_slack)
    assert r2.status_code == 409
    assert r2.json()["error"] == "report_approved"


async def test_startup_recovers_orphaned_approved_incident(fresh_db) -> None:
    """If the process dies after status='approved' is written but before the
    Solver runs, the recovery scan MUST detect the orphaned row and spawn a
    resume task via spawn_tracked.

    Tested in isolation: call _recover_approved_incidents directly with a
    fake app that has a silenced graph, so no LLM call is made.
    """
    import types
    import uuid

    from src.agent.api import _recover_approved_incidents, spawn_tracked
    from src.agent.db import init_db as _init_db
    from src.agent.db import get_conn as _get_conn

    # Schema must exist before we can insert.
    await _init_db()

    cid = uuid.uuid4().hex
    now = datetime.now(timezone.utc).isoformat()
    deadline = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()

    async with _get_conn() as conn:
        await conn.execute(
            """
            INSERT INTO incidents (
                correlation_id, dedup_fingerprint, source_alert_id,
                namespace, pod, status, received_at, last_seen_at, approval_deadline
            ) VALUES (?, ?, ?, ?, ?, 'approved', ?, ?, ?)
            """,
            (cid, f"fp-{cid}", "test-alert", "default", "pod-orphan", now, now, deadline),
        )
        await conn.commit()

    # Build a minimal fake app with a silenced graph.
    async def _noop(*args, **kwargs):
        return None

    fake_graph = types.SimpleNamespace(aupdate_state=_noop, ainvoke=_noop)
    fake_app = types.SimpleNamespace(
        state=types.SimpleNamespace(graph=fake_graph, pending_graph_tasks=set())
    )

    await _recover_approved_incidents(fake_app)  # type: ignore[arg-type]

    # A resume task was spawned into the pending set.
    assert len(fake_app.state.pending_graph_tasks) == 1, (
        "expected exactly 1 recovery task to be enqueued"
    )

    # Let the task complete.
    await asyncio.sleep(0.1)
    assert len(fake_app.state.pending_graph_tasks) == 0, "task should have drained from set"


async def test_startup_no_recovery_when_no_approved_incidents(fresh_db) -> None:
    """When there are no approved incidents, startup completes cleanly with
    an empty pending_graph_tasks set."""
    from asgi_lifespan import LifespanManager

    from src.agent.api import create_app

    app = create_app()
    async with LifespanManager(app):
        # No approved rows → pending set starts empty (nothing was spawned).
        assert len(app.state.pending_graph_tasks) == 0


async def test_callback_after_deadline_returns_approval_expired(
    client: httpx.AsyncClient,
    sign_slack,
    callback_payload,
) -> None:
    cid = await _open_pending_incident()
    # Backdate the incident's approval_deadline so the click is past it.
    async with get_conn() as conn:
        past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        await conn.execute(
            "UPDATE incidents SET approval_deadline = ? WHERE correlation_id = ?",
            (past, cid),
        )
        await conn.commit()

    r = await fire_callback(
        client, "approve", callback_payload(correlation_id=cid), sign_slack
    )
    assert r.status_code == 409
    assert r.json()["error"] == "approval_expired"

    # Status flipped to expired; audit event recorded as refused/expired.
    async with get_conn() as conn:
        cur = await conn.execute(
            "SELECT status FROM incidents WHERE correlation_id = ?", (cid,)
        )
        assert (await cur.fetchone())["status"] == "expired"
    chain = await fetch_chain(cid)
    expired_events = [
        r
        for r in chain
        if r["stage"] == "approval_event" and r["payload"].get("reason") == "expired"
    ]
    assert expired_events
    # The audit row MUST record the actual attempted action, not "reject".
    assert expired_events[0]["payload"]["action"] == "approve", (
        "deadline audit should record the attempted action, not a hardcoded 'reject'"
    )
