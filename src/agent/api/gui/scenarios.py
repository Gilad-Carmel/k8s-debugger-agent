"""
src/agent/api/gui/scenarios.py

POST /api/demo/trigger/{scenario}

Runs one of the four demo trigger scripts from scripts/demo/ to cause a real
cluster failure, then immediately fires a signed Alertmanager webhook so the
triage pipeline starts with a real correlation_id the GUI can subscribe to.

Scenarios:
  crash      → /panic → CrashLoopBackOff        → PodCrashLooping alert
  bad-deploy → v2 image with RUNTIME_ERROR=true → PodCrashLooping alert
  oom        → /stress?mem=50 with 32Mi limit   → PodOOMKilled alert
  scale      → bombardier flood                 → PodHighErrorRate alert
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from src.agent.logging_config import get_logger
from src.agent.settings import settings

router = APIRouter()
log = get_logger(__name__)

VALID_SCENARIOS = {"crash", "bad-deploy", "oom", "scale"}
SCRIPT_MAP = {
    "crash":      "scripts/demo/trigger-crash.sh",
    "bad-deploy": "scripts/demo/trigger-bad-deploy.sh",
    "oom":        "scripts/demo/trigger-oom.sh",
    "scale":      "scripts/demo/trigger-scale.sh",
}
SCRIPT_TIMEOUT = 60.0

# Per-scenario Alertmanager metadata
_SCENARIO_ALERT: dict[str, dict] = {
    "crash": {
        "alertname": "PodCrashLooping",
        "description": "Pod crashed via /panic endpoint — CrashLoopBackOff",
        "post_delay": 1.0,
    },
    "bad-deploy": {
        "alertname": "PodCrashLooping",
        "description": "Pod returning HTTP 500 after bad deployment (RUNTIME_ERROR=true)",
        "post_delay": 4.0,  # give the rollout a moment to create the new pod
    },
    "oom": {
        "alertname": "PodOOMKilled",
        "description": "Pod OOM killed — memory stress exceeded 32Mi container limit",
        "post_delay": 1.0,
    },
    "scale": {
        "alertname": "PodHighErrorRate",
        "description": "High error rate under bombardier load test (50 concurrent)",
        "post_delay": 2.0,
    },
}


async def _run_script(script_path: str) -> None:
    """Run the trigger script; log but don't raise on failure."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "bash", script_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ},
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=SCRIPT_TIMEOUT)
        log.info("scenario.script.done",
                 script=script_path,
                 stdout=stdout.decode(errors="replace")[:500])
    except asyncio.TimeoutError:
        log.warning("scenario.script.timeout", script=script_path)
    except Exception as exc:  # noqa: BLE001
        log.error("scenario.script.error", script=script_path, error=str(exc))


async def _get_pod_name(namespace: str, label: str = "app=podinfo") -> str | None:
    """Return the first Running or Pending pod matching the label selector."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "kubectl", "get", "pod", "-n", namespace, "-l", label,
            "-o", "jsonpath={.items[0].metadata.name}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        name = stdout.decode().strip()
        return name or None
    except Exception:  # noqa: BLE001
        return None


async def _fire_webhook(namespace: str, pod: str, alertname: str,
                        description: str, scenario: str) -> str | None:
    """Build, sign, and POST an Alertmanager webhook; return the correlation_id."""
    group_key = f"k8s-demo|{namespace}|{pod}|{scenario}"
    now_iso = datetime.now(timezone.utc).isoformat()
    payload = {
        "version": "4",
        "groupKey": group_key,
        "status": "firing",
        "groupLabels": {"namespace": namespace, "pod": pod, "alertname": alertname},
        "commonLabels": {"namespace": namespace, "pod": pod},
        "alerts": [{
            "status": "firing",
            "startsAt": now_iso,
            "labels": {
                "alertname": alertname,
                "namespace": namespace,
                "pod": pod,
                "severity": "critical",
                "scenario": scenario,
            },
            "annotations": {
                "description": description,
                "summary": f"K8s demo scenario: {scenario}",
            },
        }],
    }
    body = json.dumps(payload).encode()
    signature = hmac.new(
        settings.alertmanager_hmac_secret.encode(), body, hashlib.sha256
    ).hexdigest()

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
        if resp.status_code in (200, 202):
            return resp.json().get("correlation_id")
        log.warning("scenario.webhook.rejected", status=resp.status_code, body=resp.text[:200])
    except Exception as exc:  # noqa: BLE001
        log.error("scenario.webhook.error", error=str(exc))
    return None


@router.post("/api/demo/trigger/{scenario}")
async def trigger_scenario(scenario: str) -> JSONResponse:
    if scenario not in VALID_SCENARIOS:
        return JSONResponse(
            status_code=400,
            content={
                "error": "unknown_scenario",
                "message": f"scenario must be one of: {', '.join(sorted(VALID_SCENARIOS))}",
            },
        )

    script_rel = SCRIPT_MAP[scenario]
    script_path = Path(script_rel)
    if not script_path.exists():
        return JSONResponse(
            status_code=503,
            content={
                "error": "script_not_found",
                "message": f"{script_rel} not found — run `make demo-deploy` first",
            },
        )

    alert_meta = _SCENARIO_ALERT[scenario]
    namespace = os.getenv("DEMO_NAMESPACE", "demo")
    started_at = datetime.now(timezone.utc).isoformat()

    log.info("scenario.trigger.start", scenario=scenario)

    # 1. Run the script — causes the real cluster failure
    await _run_script(str(script_path))

    # 2. Brief pause so the cluster action has time to land
    await asyncio.sleep(alert_meta["post_delay"])

    # 3. Get the affected pod name from the cluster
    pod = await _get_pod_name(namespace)
    if not pod:
        log.warning("scenario.trigger.no_pod", namespace=namespace)
        pod = f"podinfo-unknown"

    # 4. Fire the Alertmanager webhook → starts the triage pipeline
    cid = await _fire_webhook(
        namespace=namespace,
        pod=pod,
        alertname=alert_meta["alertname"],
        description=alert_meta["description"],
        scenario=scenario,
    )

    if not cid:
        cid = "unknown"
        log.warning("scenario.trigger.no_cid", scenario=scenario)

    log.info("scenario.trigger.done", scenario=scenario, pod=pod, correlation_id=cid)
    return JSONResponse(
        status_code=202,
        content={"correlation_id": cid, "scenario": scenario, "started_at": started_at},
    )
