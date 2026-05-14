"""
deploy/mocks/agent_mock.py

Mock agent server — simulates the full triage + approval + solver pipeline.

What this replaces:
  The real agent (src/agent/) is a LangGraph workflow with Router, Expert,
  Reporter, and Solver nodes that call Claude and the Kubernetes MCP server.
  This mock fakes all of that with hard-coded fixture data so the Discord/Slack
  UI can be exercised end-to-end without a live cluster or LLM API key.

When to replace:
  This file can be deleted once src/agent/ is fully wired and the real agent
  FastAPI entry-point (src/agent/app.py) is running.  The Discord bot and
  Slack mock use the same POST /messages and callback URLs regardless.

How it works:
  1. POST /webhook/alertmanager — accepts an Alertmanager payload, derives a
     domain from labels, fetches k8s_fixtures, assembles a Report dict, and
     POSTs it to the chat surface (Slack mock OR Discord bot, depending on
     CHAT_SURFACE env var).
  2. POST /callbacks/slack/approve — verifies HMAC, advances status to
     "approved", simulates a 2-second solver run, then updates the chat with
     the final "executed" report.
  3. POST /callbacks/slack/reject — verifies HMAC, advances status to
     "rejected", sends one final update to the chat surface.

Required env vars:
  CHAT_SURFACE   — "discord" or "slack" (default: "discord")
  CHAT_URL       — URL of the chat surface  (default: http://localhost:8091)
  AGENT_SECRET   — HMAC secret shared with chat surface (default: dev-mock-secret)
  AGENT_PORT     — port for this server (default: 8000)

Run:
  python -m deploy.mocks.agent_mock
  # or
  cd deploy/mocks && python agent_mock.py
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import uuid
from collections.abc import Coroutine
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request

# Local fixture library (importable from repo root or deploy/mocks/ directory)
try:
    from deploy.mocks.k8s_fixtures import get_fixture
except ModuleNotFoundError:
    from k8s_fixtures import get_fixture  # type: ignore[no-redef]

logger = logging.getLogger("agent_mock")

CHAT_SURFACE = os.getenv("CHAT_SURFACE", "discord")   # "discord" | "slack"
CHAT_URL = os.getenv("CHAT_URL", "http://localhost:8091")
AGENT_SECRET = os.getenv("AGENT_SECRET", os.getenv("SLACK_MOCK_SECRET", "dev-mock-secret"))
AGENT_PORT = int(os.getenv("AGENT_PORT", "8000"))

app = FastAPI(title="Agent Mock")

# in-memory store: correlation_id -> record dict
_store: dict[str, dict[str, Any]] = {}
_background_tasks: set[asyncio.Task[None]] = set()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _verify_hmac(body: bytes, signature: str) -> bool:
    expected = hmac.new(AGENT_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _sign(body: bytes) -> str:
    return hmac.new(AGENT_SECRET.encode(), body, hashlib.sha256).hexdigest()


def _track_background_task(coro: Coroutine[Any, Any, None], task_name: str) -> None:
    """
    Keep a strong reference to a background task until completion.

    This prevents the task from being garbage-collected mid-flight. A done callback
    removes the task from the tracking set and logs any unhandled failure.
    """
    task = asyncio.create_task(coro, name=task_name)
    _background_tasks.add(task)

    def _cleanup(completed: asyncio.Task[None]) -> None:
        _background_tasks.discard(completed)
        if completed.cancelled():
            return
        exc = completed.exception()
        if exc is not None:
            logger.error("background task failed task=%s", completed.get_name(), exc_info=exc)

    task.add_done_callback(_cleanup)


def _build_record(
    correlation_id: str,
    domain: str,
    fixture: dict[str, Any],
    status: str = "pending",
    solver_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the record dict that the chat surface consumes."""
    now = _now()
    routing = fixture["routing"]
    diagnosis = fixture.get("diagnosis")

    report: dict[str, Any] = {
        "correlation_id": correlation_id,
        "routing": routing,
        "diagnosis": diagnosis,
        "proposed_fix": diagnosis.get("proposed_fix") if diagnosis else None,
        "status": status,
        "delivered_at": now.isoformat(),
        "approval_deadline": (now + timedelta(minutes=30)).isoformat(),
    }

    record: dict[str, Any] = {
        "correlation_id": correlation_id,
        "report": report,
    }
    if solver_result:
        record["solver_result"] = solver_result

    return record


async def _post_to_chat(record: dict[str, Any]) -> None:
    """POST the record to whichever chat surface is configured."""
    body = json.dumps(record, separators=(",", ":")).encode()
    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            resp = await http.post(
                f"{CHAT_URL}/messages",
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Slack-Mock-Signature": _sign(body),
                },
            )
            resp.raise_for_status()
            logger.info(
                "posted to %s surface corr=%s status=%s",
                CHAT_SURFACE,
                record.get("correlation_id"),
                record.get("report", {}).get("status"),
            )
    except Exception as exc:
        logger.warning("failed to post to chat: %s", exc)


def _fake_solver(fixture: dict[str, Any], correlation_id: str) -> dict[str, Any]:
    """Return a fake SolverRun-shaped dict."""
    proposed_fix = (fixture.get("diagnosis") or {}).get("proposed_fix") or {}
    return {
        "correlation_id": correlation_id,
        "proposed_fix_fingerprint": "mock-fingerprint-" + correlation_id[:8],
        "pre_state": fixture.get("pod", {}),
        "action_issued": proposed_fix,
        "post_state": {**fixture.get("pod", {}), "restart_count_by_ctr": {}, "ready": True},
        "outcome": "success",
        "reversal_recipe": {
            "description": "No automated undo — restart was self-recovering.",
            "inverse_action": None,
            "inverse_parameters": {},
        },
        "error": None,
        "started_at": _now().isoformat(),
        "finished_at": _now().isoformat(),
    }


# ---------------------------------------------------------------------------
# Triage background task — runs after webhook is accepted
# ---------------------------------------------------------------------------

async def _run_triage(correlation_id: str, domain: str) -> None:
    fixture = get_fixture(domain)

    # Step 1: post pending report immediately
    record = _build_record(correlation_id, domain, fixture, status="pending")
    _store[correlation_id] = {"fixture": fixture, "domain": domain, "record": record}
    await _post_to_chat(record)

    logger.info("triage complete corr=%s domain=%s — waiting for approval", correlation_id, domain)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.post("/webhook/alertmanager")
async def alertmanager_webhook(request: Request) -> dict[str, str]:
    """
    Accepts an Alertmanager-compatible webhook payload.

    Derives the failure domain from alert labels:
      labels.domain  OR  labels.alertname prefix  OR  "unknown"
    """
    body = await request.body()
    payload = json.loads(body)

    alerts = payload.get("alerts", [payload])
    first_alert = alerts[0] if alerts else {}
    labels = first_alert.get("labels", {})

    # Derive domain: explicit label wins, otherwise guess from alertname
    domain = labels.get("domain", "").lower()
    if not domain:
        alertname = labels.get("alertname", "").lower()
        for d in ("application", "network", "database"):
            if d in alertname:
                domain = d
                break
        else:
            domain = "unknown"

    correlation_id = labels.get("correlation_id") or str(uuid.uuid4())

    _track_background_task(_run_triage(correlation_id, domain), f"triage:{correlation_id}:{domain}")

    logger.info("accepted alert corr=%s domain=%s", correlation_id, domain)
    return {"status": "accepted", "correlation_id": correlation_id}


@app.post("/callbacks/slack/approve")
async def approve_callback(request: Request) -> dict[str, str]:
    """
    Handles an approve button click from the chat surface.

    Verifies HMAC, advances status to "approved", waits 2 s to simulate
    the solver running, then posts the final "executed" update.
    """
    body = await request.body()
    sig = request.headers.get("X-Slack-Mock-Signature", "")
    if not _verify_hmac(body, sig):
        raise HTTPException(status_code=403, detail="invalid signature")

    payload = json.loads(body)
    correlation_id: str = payload.get("correlation_id", "")
    entry = _store.get(correlation_id)
    if not entry:
        raise HTTPException(status_code=404, detail="unknown correlation_id")

    fixture = entry["fixture"]
    domain = entry["domain"]

    # Post "approved" immediately
    approved_record = _build_record(correlation_id, domain, fixture, status="approved")
    await _post_to_chat(approved_record)

    # Simulate solver delay then post result
    async def _solver_task() -> None:
        await asyncio.sleep(2)
        solver = _fake_solver(fixture, correlation_id)
        executed_record = _build_record(
            correlation_id, domain, fixture, status="executed", solver_result=solver
        )
        await _post_to_chat(executed_record)
        _store.pop(correlation_id, None)

    _track_background_task(_solver_task(), f"solver:{correlation_id}:{domain}")

    logger.info("approved corr=%s — solver running", correlation_id)
    return {"status": "ok"}


@app.post("/callbacks/slack/reject")
async def reject_callback(request: Request) -> dict[str, str]:
    """
    Handles a reject button click from the chat surface.

    Verifies HMAC, advances status to "rejected", posts one final update.
    """
    body = await request.body()
    sig = request.headers.get("X-Slack-Mock-Signature", "")
    if not _verify_hmac(body, sig):
        raise HTTPException(status_code=403, detail="invalid signature")

    payload = json.loads(body)
    correlation_id: str = payload.get("correlation_id", "")
    entry = _store.get(correlation_id)
    if not entry:
        raise HTTPException(status_code=404, detail="unknown correlation_id")

    fixture = entry["fixture"]
    domain = entry["domain"]

    rejected_record = _build_record(correlation_id, domain, fixture, status="rejected")
    await _post_to_chat(rejected_record)
    _store.pop(correlation_id, None)

    logger.info("rejected corr=%s", correlation_id)
    return {"status": "ok"}


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "surface": CHAT_SURFACE, "chat_url": CHAT_URL}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    )
    logger.info(
        "Starting agent mock on port %d → chat surface: %s @ %s",
        AGENT_PORT, CHAT_SURFACE, CHAT_URL,
    )
    uvicorn.run(app, host="0.0.0.0", port=AGENT_PORT, log_level="warning")
