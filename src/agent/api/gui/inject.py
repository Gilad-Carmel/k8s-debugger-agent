"""
src/agent/api/gui/inject.py

POST /api/demo/inject/{scenario}

Fires a synthetic Alertmanager webhook directly into the agent pipeline,
bypassing the need for a live Kubernetes cluster.

Scenarios mirror demo_slack.py and DEMO.md:
  application — NullPointerException in CheckoutService after bad deployment
  network     — DNS resolution failure for postgres-primary
  database    — Connection pool exhaustion on postgres replica
  unknown     — No diagnosis available (router returns Unknown)

Each scenario builds an Alertmanager-format payload, signs it with the
configured HMAC secret, and self-POSTs to /webhook/alertmanager.
The agent processes it through the full pipeline:
  ingest → router → expert → reporter → [HITL gate] → solver
and emits SSE events that animate the workflow diagram in the GUI.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from src.agent.logging_config import get_logger
from src.agent.settings import settings

router = APIRouter()
log = get_logger(__name__)

VALID_SCENARIOS = {"application", "network", "database", "unknown"}

# Pre-built synthetic incident data for each scenario (mirrors demo_slack.py)
_SCENARIO_META: dict[str, dict] = {
    "application": {
        "namespace": "checkout",
        "pod": "checkout-deploy-7b5d-x29",
        "alertname": "PodCrashLooping",
        "group_key": "demo|checkout|checkout-deploy-7b5d-x29|application",
        "description": "NullPointerException in CheckoutService after bad deployment",
    },
    "network": {
        "namespace": "default",
        "pod": "payment-service-6c4d-k8p",
        "alertname": "PodNetworkFailure",
        "group_key": "demo|default|payment-service-6c4d-k8p|network",
        "description": "DNS resolution failure for postgres-primary",
    },
    "database": {
        "namespace": "data",
        "pod": "postgres-replica-0",
        "alertname": "PodDatabaseOverload",
        "group_key": "demo|data|postgres-replica-0|database",
        "description": "Connection pool exhaustion on postgres replica",
    },
    "unknown": {
        "namespace": "monitoring",
        "pod": "prometheus-0",
        "alertname": "PodUnknownFailure",
        "group_key": "demo|monitoring|prometheus-0|unknown",
        "description": "Unknown failure — insufficient log evidence",
    },
}


def _build_alertmanager_payload(scenario: str) -> dict:
    meta = _SCENARIO_META[scenario]
    now_iso = datetime.now(timezone.utc).isoformat()
    return {
        "version": "4",
        "groupKey": meta["group_key"],
        "status": "firing",
        "groupLabels": {
            "namespace": meta["namespace"],
            "pod": meta["pod"],
            "alertname": meta["alertname"],
        },
        "commonLabels": {
            "namespace": meta["namespace"],
            "pod": meta["pod"],
        },
        "alerts": [
            {
                "status": "firing",
                "startsAt": now_iso,
                "labels": {
                    "alertname": meta["alertname"],
                    "namespace": meta["namespace"],
                    "pod": meta["pod"],
                    "severity": "critical",
                    "scenario": scenario,
                },
                "annotations": {
                    "description": meta["description"],
                    "summary": f"Demo scenario: {scenario}",
                },
            }
        ],
    }


def _sign(body: bytes) -> str:
    return hmac.new(
        settings.alertmanager_hmac_secret.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()


@router.post("/api/demo/inject/{scenario}")
async def inject_scenario(scenario: str) -> JSONResponse:
    if scenario not in VALID_SCENARIOS:
        return JSONResponse(
            status_code=400,
            content={
                "error": "unknown_scenario",
                "message": f"scenario must be one of: {', '.join(sorted(VALID_SCENARIOS))}",
            },
        )

    payload = _build_alertmanager_payload(scenario)
    body = json.dumps(payload).encode()
    signature = _sign(body)
    started_at = datetime.now(timezone.utc).isoformat()

    log.info("demo.inject.start", scenario=scenario)

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "http://localhost:8000/webhook/alertmanager",
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Alertmanager-Signature": signature,
                },
            )
    except httpx.ConnectError:
        return JSONResponse(
            status_code=503,
            content={
                "error": "agent_unavailable",
                "message": "Cannot reach agent webhook at localhost:8000 — is the agent running?",
            },
        )

    if resp.status_code not in (200, 202):
        return JSONResponse(
            status_code=502,
            content={
                "error": "webhook_rejected",
                "message": f"Agent returned {resp.status_code}: {resp.text[:200]}",
            },
        )

    cid = resp.json().get("correlation_id", "unknown")
    log.info("demo.inject.done", scenario=scenario, correlation_id=cid)

    return JSONResponse(
        status_code=202,
        content={"correlation_id": cid, "scenario": scenario, "started_at": started_at},
    )
