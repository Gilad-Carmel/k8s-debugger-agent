"""
src/agent/api/gui/scenarios.py

POST /api/demo/trigger/{scenario}

Runs one of the four demo trigger scripts from scripts/demo/ as a background
subprocess, then fires the Alertmanager webhook to start the triage workflow.

Returns the correlation_id immediately so the client can subscribe to the
SSE stream before the first event arrives.

Scenarios (from 003-podinfo-demo plan.md):
  crash      → scripts/demo/trigger-crash.sh   (podinfo /panic → CrashLoopBackOff)
  bad-deploy → scripts/demo/trigger-bad-deploy.sh (v2 image with RUNTIME_ERROR=true)
  oom        → scripts/demo/trigger-oom.sh      (/stress?mem=50, 32Mi limit)
  scale      → scripts/demo/trigger-scale.sh    (bombardier load → error rate)
"""

from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from src.agent.logging_config import get_logger

router = APIRouter()
log = get_logger(__name__)

VALID_SCENARIOS = {"crash", "bad-deploy", "oom", "scale"}
SCRIPT_MAP = {
    "crash": "scripts/demo/trigger-crash.sh",
    "bad-deploy": "scripts/demo/trigger-bad-deploy.sh",
    "oom": "scripts/demo/trigger-oom.sh",
    "scale": "scripts/demo/trigger-scale.sh",
}
SCRIPT_TIMEOUT = 60.0  # max seconds to wait for the script to fire the webhook


async def _run_trigger(script_path: str) -> str | None:
    """Run trigger script and extract correlation_id from stdout."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "bash", script_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ},
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=SCRIPT_TIMEOUT)
        output = stdout.decode(errors="replace")
        # Scripts print "correlation_id: <cid>" or just the raw cid
        match = re.search(r"correlation_id[:\s]+([0-9A-Za-z_-]+)", output)
        if match:
            return match.group(1)
        # Fallback: last non-empty line
        lines = [l.strip() for l in output.splitlines() if l.strip()]
        return lines[-1] if lines else None
    except asyncio.TimeoutError:
        log.warning("scenario.trigger.timeout", script=script_path)
        return None
    except Exception as exc:  # noqa: BLE001
        log.error("scenario.trigger.error", script=script_path, error=str(exc))
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

    started_at = datetime.now(timezone.utc).isoformat()
    log.info("scenario.trigger.start", scenario=scenario, script=script_rel)

    cid = await _run_trigger(str(script_path))
    if not cid:
        cid = "unknown"

    log.info("scenario.trigger.done", scenario=scenario, correlation_id=cid)
    return JSONResponse(
        status_code=202,
        content={"correlation_id": cid, "scenario": scenario, "started_at": started_at},
    )
