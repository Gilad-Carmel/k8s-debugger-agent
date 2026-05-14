"""
tests/conftest.py

Shared fixtures: each test gets a fresh SQLite file so audit_log and
incidents start empty. The app is built per-test under a LifespanManager
so the AsyncSqliteSaver checkpointer + the expiry watcher both run.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Callable

import httpx
import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager

from src.agent.settings import settings


@pytest.fixture
def fresh_db(tmp_path: Path) -> Path:
    """Per-test SQLite path. settings.SQLITE_PATH is shared; patch it."""
    db = tmp_path / "test.sqlite3"
    settings.SQLITE_PATH = str(db)
    return db


@pytest_asyncio.fixture
async def app_and_client(fresh_db: Path) -> AsyncIterator[tuple[Any, httpx.AsyncClient]]:
    """Yield (app, client) so tests that need app.state.graph can grab it."""
    from src.agent.api import create_app

    app = create_app()
    async with LifespanManager(app) as manager:
        transport = httpx.ASGITransport(app=manager.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield app, c


@pytest_asyncio.fixture
async def client(app_and_client) -> AsyncIterator[httpx.AsyncClient]:
    """Most tests only need the client."""
    yield app_and_client[1]


async def graph_state(app, correlation_id: str) -> dict:
    """Return the LangGraph state values for a given correlation_id thread."""
    config = {"configurable": {"thread_id": correlation_id}}
    snapshot = await app.state.graph.aget_state(config)
    return dict(snapshot.values) if snapshot else {}


def _sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture
def sign_alertmanager() -> Callable[[bytes], str]:
    return lambda body: _sign(settings.ALERTMANAGER_HMAC_SECRET, body)


@pytest.fixture
def sign_slack() -> Callable[[bytes], str]:
    return lambda body: _sign(settings.SLACK_MOCK_SECRET, body)


@pytest.fixture
def alertmanager_payload() -> Callable[..., dict[str, Any]]:
    """Builder for a valid Alertmanager v4 payload. Override fields per test."""

    def _build(
        *,
        group_key: str = '{}/{alertname="PodCrashLooping"}:{pod="checkout-7b5d-x29"}',
        namespace: str = "checkout",
        pod: str = "checkout-7b5d-x29",
        status: str = "firing",
        starts_at: datetime | None = None,
    ) -> dict[str, Any]:
        if starts_at is None:
            starts_at = datetime.now(timezone.utc)
        return {
            "version": "4",
            "groupKey": group_key,
            "status": status,
            "groupLabels": {
                "alertname": "PodCrashLooping",
                "namespace": namespace,
                "pod": pod,
            },
            "alerts": [
                {
                    "status": status,
                    "startsAt": starts_at.isoformat().replace("+00:00", "Z"),
                }
            ],
        }

    return _build


@pytest.fixture
def callback_payload() -> Callable[..., dict[str, Any]]:
    """Builder for a Slack-mock callback body."""

    def _build(
        *,
        correlation_id: str,
        roles: list[str] | None = None,
        clicked_at: datetime | None = None,
        action: str = "approve",
    ) -> dict[str, Any]:
        if roles is None:
            roles = [settings.APPROVER_ROLE, "sre"]
        if clicked_at is None:
            clicked_at = datetime.now(timezone.utc)
        return {
            "correlation_id": correlation_id,
            "actor": {"user_id": "U-test", "name": "tester", "roles": roles},
            "action_id": f"{action}_{correlation_id}",
            "reason": "test run",
            "clicked_at": clicked_at.isoformat().replace("+00:00", "Z"),
        }

    return _build


async def fire_webhook(
    client: httpx.AsyncClient,
    payload: dict[str, Any],
    sign: Callable[[bytes], str],
) -> httpx.Response:
    body = json.dumps(payload).encode()
    return await client.post(
        "/webhook/alertmanager",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Alertmanager-Signature": sign(body),
        },
    )


async def fire_callback(
    client: httpx.AsyncClient,
    action: str,
    payload: dict[str, Any],
    sign: Callable[[bytes], str],
    *,
    bad_sig: bool = False,
) -> httpx.Response:
    body = json.dumps(payload).encode()
    sig = "deadbeef" * 8 if bad_sig else sign(body)
    return await client.post(
        f"/callbacks/slack/{action}",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Slack-Mock-Signature": sig,
        },
    )
