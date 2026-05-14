"""tests/test_callbacks.py — POST /callbacks/slack/{approve,reject} scenarios."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from src.agent.audit import fetch_chain
from src.agent.db import get_conn
from tests.conftest import fire_callback, fire_webhook


async def _open_pending_incident(
    client, alertmanager_payload, sign_alertmanager
) -> str:
    r = await fire_webhook(client, alertmanager_payload(), sign_alertmanager)
    assert r.status_code == 202
    cid = r.json()["correlation_id"]
    # Let the placeholder graph run to the interrupt.
    await asyncio.sleep(1.0)
    return cid


async def test_approve_happy_path(
    client: httpx.AsyncClient,
    alertmanager_payload,
    sign_alertmanager,
    sign_slack,
    callback_payload,
) -> None:
    cid = await _open_pending_incident(client, alertmanager_payload, sign_alertmanager)
    r = await fire_callback(client, "approve", callback_payload(correlation_id=cid), sign_slack)
    assert r.status_code == 200
    assert r.json() == {"correlation_id": cid, "status": "approved"}

    # Wait for the resumed graph to run the solver placeholder.
    await asyncio.sleep(1.5)
    chain = await fetch_chain(cid)
    stages = [r["stage"] for r in chain]
    assert "approval_event" in stages
    assert "solver_placeholder" in stages

    # incidents.status flipped to approved.
    async with get_conn() as conn:
        cur = await conn.execute(
            "SELECT status FROM incidents WHERE correlation_id = ?", (cid,)
        )
        row = await cur.fetchone()
        assert row["status"] == "approved"


async def test_reject_does_not_invoke_solver(
    client: httpx.AsyncClient,
    alertmanager_payload,
    sign_alertmanager,
    sign_slack,
    callback_payload,
) -> None:
    cid = await _open_pending_incident(client, alertmanager_payload, sign_alertmanager)
    r = await fire_callback(client, "reject", callback_payload(correlation_id=cid), sign_slack)
    assert r.status_code == 200
    assert r.json() == {"correlation_id": cid, "status": "rejected"}

    await asyncio.sleep(1.5)
    chain = await fetch_chain(cid)
    stages = [r["stage"] for r in chain]
    assert "approval_event" in stages
    # Critical safety invariant: rejection MUST NOT trigger the solver.
    assert "solver_placeholder" not in stages

    async with get_conn() as conn:
        cur = await conn.execute(
            "SELECT status FROM incidents WHERE correlation_id = ?", (cid,)
        )
        assert (await cur.fetchone())["status"] == "rejected"


async def test_callback_bad_signature_rejected(
    client: httpx.AsyncClient,
    alertmanager_payload,
    sign_alertmanager,
    sign_slack,
    callback_payload,
) -> None:
    cid = await _open_pending_incident(client, alertmanager_payload, sign_alertmanager)
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
    alertmanager_payload,
    sign_alertmanager,
    sign_slack,
    callback_payload,
) -> None:
    cid = await _open_pending_incident(client, alertmanager_payload, sign_alertmanager)
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
    client: httpx.AsyncClient,
    alertmanager_payload,
    sign_alertmanager,
    sign_slack,
    callback_payload,
) -> None:
    cid = await _open_pending_incident(client, alertmanager_payload, sign_alertmanager)
    r1 = await fire_callback(client, "approve", callback_payload(correlation_id=cid), sign_slack)
    assert r1.status_code == 200

    # Second click while status is no longer pending.
    r2 = await fire_callback(client, "approve", callback_payload(correlation_id=cid), sign_slack)
    assert r2.status_code == 409
    assert r2.json()["error"] == "report_approved"


async def test_callback_after_deadline_returns_approval_expired(
    client: httpx.AsyncClient,
    alertmanager_payload,
    sign_alertmanager,
    sign_slack,
    callback_payload,
) -> None:
    cid = await _open_pending_incident(client, alertmanager_payload, sign_alertmanager)
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
