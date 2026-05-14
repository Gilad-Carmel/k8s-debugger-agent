"""tests/test_webhook.py — POST /webhook/alertmanager scenarios."""
from __future__ import annotations

import asyncio
import json

import httpx

from datetime import datetime, timezone

from src.agent.audit import fetch_chain
from src.agent.db import get_conn
from tests.conftest import silence_graph
from tests.conftest import fire_webhook


async def test_health(client: httpx.AsyncClient) -> None:
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


async def test_webhook_happy_path(
    requires_llm, app_and_client, alertmanager_payload, sign_alertmanager
) -> None:
    from tests.conftest import graph_state

    app, client = app_and_client
    r = await fire_webhook(client, alertmanager_payload(), sign_alertmanager)
    assert r.status_code == 202
    body = r.json()
    assert body["deduplicated"] is False
    # uuid4 with dashes — see src/shared/correlation.py canonical API.
    assert len(body["correlation_id"]) == 36

    # Let the graph drain to the interrupt before solver.
    await asyncio.sleep(1.0)

    # Audit row was written by my webhook handler.
    chain = await fetch_chain(body["correlation_id"])
    stages = [r["stage"] for r in chain]
    assert stages[0] == "webhook_received"

    # Graph paused at interrupt_before=['solver'] — Reporter ran (state has
    # `report`), but Solver did NOT (no `solver_run` yet).
    state = await graph_state(app, body["correlation_id"])
    assert "report" in state, f"expected report in state, got keys: {list(state)}"
    assert "solver_run" not in state, "solver MUST NOT run before approval"


async def test_webhook_bad_signature_rejected(
    client: httpx.AsyncClient, alertmanager_payload
) -> None:
    body = json.dumps(alertmanager_payload()).encode()
    r = await client.post(
        "/webhook/alertmanager",
        content=body,
        headers={"X-Alertmanager-Signature": "deadbeef" * 8},
    )
    assert r.status_code == 401
    assert r.json()["error"] == "signature_invalid"


async def test_webhook_missing_signature_header(
    client: httpx.AsyncClient, alertmanager_payload
) -> None:
    body = json.dumps(alertmanager_payload()).encode()
    r = await client.post("/webhook/alertmanager", content=body)
    assert r.status_code == 401


async def test_webhook_malformed_body(
    client: httpx.AsyncClient, sign_alertmanager
) -> None:
    body = b'{"not": "valid alertmanager"}'
    r = await client.post(
        "/webhook/alertmanager",
        content=body,
        headers={"X-Alertmanager-Signature": sign_alertmanager(body)},
    )
    assert r.status_code == 400
    assert r.json()["error"] == "bad_request"


async def test_webhook_missing_target_labels(
    client: httpx.AsyncClient, alertmanager_payload, sign_alertmanager
) -> None:
    payload = alertmanager_payload()
    payload["groupLabels"] = {"alertname": "PodCrashLooping"}  # no namespace/pod
    r = await fire_webhook(client, payload, sign_alertmanager)
    assert r.status_code == 422
    assert r.json()["error"] == "missing_target"


async def test_webhook_dedup_returns_same_correlation_id(
    app_and_client, alertmanager_payload, sign_alertmanager
) -> None:
    app, client = app_and_client
    silence_graph(app)
    payload = alertmanager_payload()
    r1 = await fire_webhook(client, payload, sign_alertmanager)
    assert r1.status_code == 202
    cid1 = r1.json()["correlation_id"]
    assert r1.json()["deduplicated"] is False

    # Identical payload within the 10-min bucket → dedup.
    r2 = await fire_webhook(client, payload, sign_alertmanager)
    assert r2.status_code == 202
    body2 = r2.json()
    assert body2["deduplicated"] is True
    assert body2["correlation_id"] == cid1

    # Only ONE incidents row exists.
    async with get_conn() as conn:
        cur = await conn.execute("SELECT COUNT(*) FROM incidents")
        row = await cur.fetchone()
        assert row[0] == 1

    # An incident_deduped audit row exists for the same correlation_id.
    chain = await fetch_chain(cid1)
    assert any(r["stage"] == "incident_deduped" for r in chain)


async def test_webhook_resolved_no_prior_firing(
    client: httpx.AsyncClient, alertmanager_payload, sign_alertmanager
) -> None:
    """Resolved alert with no matching firing in the dedup window: record
    the event under a fresh correlation_id, flag the absence in the audit
    payload, and do NOT create an incidents row."""
    payload = alertmanager_payload(status="resolved")
    r = await fire_webhook(client, payload, sign_alertmanager)
    assert r.status_code == 202
    assert r.json()["status"] == "resolved"
    assert r.json()["deduplicated"] is False

    async with get_conn() as conn:
        cur = await conn.execute("SELECT COUNT(*) FROM incidents")
        assert (await cur.fetchone())[0] == 0

    # Audit row carries the diagnostic so an operator can tell this was
    # a stray resolution rather than a normal close-out.
    chain = await fetch_chain(r.json()["correlation_id"])
    assert chain[0]["stage"] == "webhook_received"
    assert chain[0]["payload"]["reason"] == "resolved_no_prior_firing"


async def test_webhook_resolved_links_to_existing_firing(
    app_and_client, alertmanager_payload, sign_alertmanager
) -> None:
    app, client = app_and_client
    silence_graph(app)
    """Fire then resolve the same alert: the resolution MUST be recorded
    under the original correlation_id (so the lifecycle is reconstructable
    from the audit log) and the incident's status MUST flip to 'resolved'."""
    starts_at = datetime.now(timezone.utc)
    firing = alertmanager_payload(status="firing", starts_at=starts_at)
    r1 = await fire_webhook(client, firing, sign_alertmanager)
    assert r1.status_code == 202
    cid = r1.json()["correlation_id"]
    assert r1.json()["deduplicated"] is False

    # Same fingerprint (same groupKey/ns/pod/10-min bucket), status=resolved.
    resolved = alertmanager_payload(status="resolved", starts_at=starts_at)
    r2 = await fire_webhook(client, resolved, sign_alertmanager)
    assert r2.status_code == 202
    body = r2.json()
    assert body["status"] == "resolved"
    # Same correlation_id reused, deduplicated flag set so callers can tell.
    assert body["correlation_id"] == cid
    assert body["deduplicated"] is True

    # incidents.status flipped from pending -> resolved.
    async with get_conn() as conn:
        cur = await conn.execute(
            "SELECT status FROM incidents WHERE correlation_id = ?", (cid,)
        )
        assert (await cur.fetchone())["status"] == "resolved"

    # Audit chain is one stream joined by cid; resolution row carries the link.
    chain = await fetch_chain(cid)
    stages_with_reasons = [
        (r["stage"], r["payload"].get("reason"))
        for r in chain
        if r["stage"] == "webhook_received"
    ]
    # Two webhook_received rows under the SAME correlation_id — the firing
    # (no `reason`) and the resolution (`reason=resolved_for_existing_incident`).
    assert len(stages_with_reasons) == 2
    assert stages_with_reasons[0][1] is None
    assert stages_with_reasons[1][1] == "resolved_for_existing_incident"


async def test_webhook_resolved_preserves_terminal_status(
    app_and_client, alertmanager_payload, sign_alertmanager
) -> None:
    app, client = app_and_client
    silence_graph(app)
    """If an incident is already in a terminal state (failed / rejected /
    expired), a late 'resolved' webhook MUST link to its correlation_id but
    MUST NOT overwrite the terminal status."""
    starts_at = datetime.now(timezone.utc)
    firing = alertmanager_payload(status="firing", starts_at=starts_at)
    r1 = await fire_webhook(client, firing, sign_alertmanager)
    cid = r1.json()["correlation_id"]

    # Simulate the incident having already been rejected by an operator.
    async with get_conn() as conn:
        await conn.execute(
            "UPDATE incidents SET status = 'failed' WHERE correlation_id = ?", (cid,)
        )
        await conn.commit()

    resolved = alertmanager_payload(status="resolved", starts_at=starts_at)
    r2 = await fire_webhook(client, resolved, sign_alertmanager)
    assert r2.status_code == 202
    assert r2.json()["correlation_id"] == cid

    async with get_conn() as conn:
        cur = await conn.execute(
            "SELECT status FROM incidents WHERE correlation_id = ?", (cid,)
        )
        # Terminal status preserved — resolution did not overwrite it.
        assert (await cur.fetchone())["status"] == "failed"

    # The audit row still records the resolution event with prior_status.
    chain = await fetch_chain(cid)
    resolved_audits = [
        r for r in chain
        if r["payload"].get("reason") == "resolved_for_existing_incident"
    ]
    assert len(resolved_audits) == 1
    assert resolved_audits[0]["payload"]["prior_status"] == "failed"
