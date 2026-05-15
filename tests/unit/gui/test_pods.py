"""
Unit tests for GET /api/pods.

Mocks asyncio.create_subprocess_exec so no real kubectl is required.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.agent.api.gui.pods import router
from fastapi import FastAPI

app = FastAPI()
app.include_router(router)
client = TestClient(app)


def _make_proc(stdout: bytes, returncode: int = 0, stderr: bytes = b"") -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.kill = MagicMock()
    return proc


POD_LIST = {
    "items": [
        {
            "metadata": {"name": "podinfo-abc", "creationTimestamp": "2026-05-15T10:00:00Z"},
            "status": {
                "phase": "Running",
                "conditions": [{"type": "Ready", "status": "True"}],
                "containerStatuses": [{"restartCount": 0}],
            },
        },
        {
            "metadata": {"name": "podinfo-xyz"},
            "status": {
                "phase": "Failed",
                "conditions": [],
                "containerStatuses": [
                    {
                        "restartCount": 3,
                        "lastState": {"terminated": {"reason": "OOMKilled"}},
                        "state": {},
                    }
                ],
            },
        },
    ]
}


@pytest.mark.asyncio
async def test_list_pods_success():
    proc = _make_proc(stdout=json.dumps(POD_LIST).encode())
    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
        resp = client.get("/api/pods")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["pods"]) == 2
    running = next(p for p in body["pods"] if p["name"] == "podinfo-abc")
    assert running["ready"] is True
    assert running["phase"] == "Running"
    failed = next(p for p in body["pods"] if p["name"] == "podinfo-xyz")
    assert failed["ready"] is False
    assert failed["message"] == "OOMKilled"
    assert failed["restart_count"] == 3


@pytest.mark.asyncio
async def test_list_pods_kubectl_not_found():
    with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError):
        resp = client.get("/api/pods")
    assert resp.status_code == 503
    assert resp.json()["error"] == "kubectl_not_found"


@pytest.mark.asyncio
async def test_list_pods_kubectl_error():
    proc = _make_proc(stdout=b"", returncode=1, stderr=b"connection refused")
    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
        resp = client.get("/api/pods")
    assert resp.status_code == 503
    assert "kubectl_error" in resp.json()["error"]
