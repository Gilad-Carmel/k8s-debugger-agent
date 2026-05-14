"""tests/test_webhook.py — POST /webhook/alertmanager scenarios."""
from __future__ import annotations

import asyncio
import json

import httpx

from src.agent.audit import fetch_chain
from src.agent.db import get_conn
from tests.conftest import fire_webhook


async def test_health(client: httpx.AsyncClient) -> None:
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


async def test_webhook_happy_path(
    client: httpx.AsyncClient, alertmanager_payload, sign_alertmanager
) -> None:
    r = await fire_webhook(client, alertmanager_payload(), sign_alertmanager)
    assert r.status_code == 202
    body = r.json()
    assert body["deduplicated"] is False
    assert len(body["correlation_id"]) == 32  # uuid4 hex

    # Let the placeholder graph drain to the interrupt before solver.
    await asyncio.sleep(1.0)

    # Audit row was written under that correlation_id.
    chain = await fetch_chain(body["correlation_id"])
    stages = [r["stage"] for r in chain]
    assert stages[0] == "webhook_received"
    assert "ingest_placeholder" in stages
    assert "reporter_placeholder" in stages
    # Solver MUST NOT run before approval — interrupt holds.
    assert "solver_placeholder" not in stages


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
    client: httpx.AsyncClient, alertmanager_payload, sign_alertmanager
) -> None:
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


async def test_webhook_resolved_short_circuits(
    client: httpx.AsyncClient, alertmanager_payload, sign_alertmanager
) -> None:
    payload = alertmanager_payload(status="resolved")
    r = await fire_webhook(client, payload, sign_alertmanager)
    assert r.status_code == 202
    assert r.json()["status"] == "resolved"

    # No incidents row created (short-circuit before insert).
    async with get_conn() as conn:
        cur = await conn.execute("SELECT COUNT(*) FROM incidents")
        row = await cur.fetchone()
        assert row[0] == 0
