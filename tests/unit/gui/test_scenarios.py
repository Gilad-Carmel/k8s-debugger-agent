"""
Unit tests for POST /api/demo/trigger/{scenario}.

Mocks asyncio.create_subprocess_exec and pathlib.Path.exists.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.agent.api.gui.scenarios import router

app = FastAPI()
app.include_router(router)
client = TestClient(app)

SCRIPT_OUTPUT = b"correlation_id: 01JTEST1234\n"


def _make_proc(stdout: bytes = SCRIPT_OUTPUT, returncode: int = 0) -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, b""))
    return proc


@pytest.mark.asyncio
async def test_trigger_unknown_scenario():
    resp = client.post("/api/demo/trigger/foobar")
    assert resp.status_code == 400
    assert resp.json()["error"] == "unknown_scenario"


@pytest.mark.asyncio
async def test_trigger_script_not_found():
    with patch("src.agent.api.gui.scenarios.Path.exists", return_value=False):
        resp = client.post("/api/demo/trigger/crash")
    assert resp.status_code == 503
    assert resp.json()["error"] == "script_not_found"


@pytest.mark.asyncio
async def test_trigger_crash_success():
    proc = _make_proc()
    with (
        patch("src.agent.api.gui.scenarios.Path.exists", return_value=True),
        patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)),
    ):
        resp = client.post("/api/demo/trigger/crash")
    assert resp.status_code == 202
    body = resp.json()
    assert body["scenario"] == "crash"
    assert body["correlation_id"] == "01JTEST1234"


@pytest.mark.asyncio
async def test_trigger_all_valid_scenarios():
    for scenario in ("crash", "bad-deploy", "oom", "scale"):
        proc = _make_proc()
        with (
            patch("src.agent.api.gui.scenarios.Path.exists", return_value=True),
            patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)),
        ):
            resp = client.post(f"/api/demo/trigger/{scenario}")
        assert resp.status_code == 202, f"Scenario {scenario} failed: {resp.json()}"
