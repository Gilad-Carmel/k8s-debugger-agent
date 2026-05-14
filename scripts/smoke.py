"""
scripts/smoke.py

End-to-end smoke test of the Person 3 plumbing.

Boots the FastAPI app in-process (with full lifespan via asgi_lifespan),
posts a signed Alertmanager payload, waits for the graph to hit its HITL
interrupt, posts a signed approve callback, then prints the audit chain.

Run:
    uv run python scripts/smoke.py
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow running from repo root without an install step.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Use a fresh DB for the smoke run — set BEFORE importing anything that
# reads settings.SQLITE_PATH at import time.
_SMOKE_DB = Path("./data/smoke.sqlite3")
_SMOKE_DB.parent.mkdir(parents=True, exist_ok=True)
if _SMOKE_DB.exists():
    _SMOKE_DB.unlink()
os.environ["SQLITE_PATH"] = str(_SMOKE_DB)

import httpx  # noqa: E402
from asgi_lifespan import LifespanManager  # noqa: E402

from src.agent.api import create_app  # noqa: E402
from src.agent.audit import fetch_chain  # noqa: E402
from src.agent.settings import settings  # noqa: E402


def _sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


async def _run_scenario(client: httpx.AsyncClient) -> str:
    # /health
    r = await client.get("/health")
    assert r.status_code == 200, f"/health failed: {r.text}"
    print(f"[smoke] /health -> {r.json()}")

    # POST /webhook/alertmanager
    starts_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    payload = {
        "version": "4",
        "groupKey": '{}/{alertname="PodCrashLooping"}:{pod="checkout-7b5d-x29"}',
        "status": "firing",
        "groupLabels": {
            "alertname": "PodCrashLooping",
            "namespace": "checkout",
            "pod": "checkout-7b5d-x29",
        },
        "alerts": [{"status": "firing", "startsAt": starts_at}],
    }
    body = json.dumps(payload).encode()
    sig = _sign(settings.ALERTMANAGER_HMAC_SECRET, body)
    r = await client.post(
        "/webhook/alertmanager",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Alertmanager-Signature": sig,
        },
    )
    assert r.status_code == 202, f"webhook failed: {r.status_code} {r.text}"
    webhook_resp = r.json()
    correlation_id: str = webhook_resp["correlation_id"]
    print(f"[smoke] /webhook/alertmanager -> 202 {webhook_resp}")

    # Let the graph run through placeholders and hit interrupt_before=['solver'].
    await asyncio.sleep(1.5)

    # POST /callbacks/slack/approve
    callback_body = {
        "correlation_id": correlation_id,
        "actor": {
            "user_id": "U-smoke",
            "name": "smoke-user",
            "roles": [settings.APPROVER_ROLE, "sre"],
        },
        "action_id": f"approve_{correlation_id}",
        "reason": "smoke run",
        "clicked_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    cb_body = json.dumps(callback_body).encode()
    cb_sig = _sign(settings.SLACK_MOCK_SECRET, cb_body)
    r = await client.post(
        "/callbacks/slack/approve",
        content=cb_body,
        headers={
            "Content-Type": "application/json",
            "X-Slack-Mock-Signature": cb_sig,
        },
    )
    assert r.status_code == 200, f"approve failed: {r.status_code} {r.text}"
    print(f"[smoke] /callbacks/slack/approve -> 200 {r.json()}")

    # Let the resumed graph run through the solver placeholder.
    await asyncio.sleep(1.5)
    return correlation_id


async def main() -> int:
    app = create_app()
    async with LifespanManager(app) as manager:
        transport = httpx.ASGITransport(app=manager.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            correlation_id = await _run_scenario(client)

    chain = await fetch_chain(correlation_id)
    print(f"\n[smoke] audit chain for {correlation_id} ({len(chain)} rows):")
    for row in chain:
        snippet = json.dumps(row["payload"])[:80]
        print(
            f"  #{row['sequence_no']:02d}  {row['stage']:32s}  "
            f"outcome={row['outcome']:8s}  payload={snippet}"
        )

    stages = [r["stage"] for r in chain]
    expected = {
        "webhook_received",
        "ingest_placeholder",
        "router_placeholder",
        "application_expert_placeholder",
        "reporter_placeholder",
        "approval_event",
        "solver_placeholder",
    }
    missing = expected - set(stages)
    if missing:
        print(f"\n[smoke] FAIL -- missing stages: {missing}")
        return 1
    print("\n[smoke] OK -- full pipeline exercised end-to-end.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
