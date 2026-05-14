"""
deploy/slack_mock/app.py

Mock Slack receiver. Accepts Block Kit report payloads from the agent,
shows them in a simple HTML list, and forwards Approve/Reject clicks
back to the agent as signed callbacks.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse

logger = logging.getLogger("slack_mock")

app = FastAPI(title="Slack Mock")

AGENT_URL = os.getenv("AGENT_URL", "http://localhost:8000")
SLACK_MOCK_SECRET = os.getenv("SLACK_MOCK_SECRET", "dev-mock-secret")

_messages: dict[str, dict[str, Any]] = {}


def _sign(body: bytes) -> str:
    return hmac.new(SLACK_MOCK_SECRET.encode(), body, hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

_PAGE = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Incident Triage</title>
<style>
  body {{ font-family: system-ui, sans-serif; max-width: 780px; margin: 40px auto; padding: 0 20px; color: #111; }}
  h1 {{ font-size: 1.2rem; font-weight: 600; margin-bottom: 24px; }}
  .incident {{ border: 1px solid #ddd; border-radius: 6px; padding: 20px; margin-bottom: 20px; }}
  .incident-header {{ display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 12px; }}
  .incident-title {{ font-weight: 600; font-size: 1rem; }}
  .incident-meta {{ color: #666; font-size: 0.85rem; }}
  .root-cause {{ margin-bottom: 12px; }}
  .log-block {{ background: #f5f5f5; border-radius: 4px; padding: 10px 12px; font-family: monospace; font-size: 0.8rem; white-space: pre-wrap; color: #333; margin-bottom: 12px; max-height: 160px; overflow-y: auto; }}
  .fix-line {{ margin-bottom: 16px; font-size: 0.9rem; }}
  .fix-line code {{ background: #eee; padding: 1px 5px; border-radius: 3px; }}
  .actions {{ display: flex; gap: 10px; }}
  .btn {{ padding: 7px 18px; border: none; border-radius: 4px; font-size: 0.9rem; cursor: pointer; }}
  .btn-approve {{ background: #2d8a4e; color: #fff; }}
  .btn-reject {{ background: #fff; color: #333; border: 1px solid #bbb; }}
  .btn:disabled {{ opacity: 0.4; cursor: default; }}
  .status-line {{ font-size: 0.85rem; color: #666; margin-top: 10px; }}
  .solver-block {{ margin-top: 12px; padding: 10px 12px; background: #f0f7f0; border-radius: 4px; font-size: 0.9rem; }}
  .no-incidents {{ color: #888; }}
  hr {{ border: none; border-top: 1px solid #eee; margin: 4px 0 16px; }}
</style>
</head>
<body>
<h1>Incident Triage</h1>
<hr>
{body}
<script>
async function act(corrId, action) {{
  const card = document.getElementById('card-' + corrId);
  card.querySelectorAll('.btn').forEach(b => b.disabled = true);
  const r = await fetch('/messages/' + corrId + '/' + action, {{method:'POST'}});
  const d = await r.json();
  if (r.ok) {{
    card.querySelector('.status-line').textContent = action === 'approve' ? 'Approved — solver running...' : 'Rejected.';
    setTimeout(() => location.reload(), 1500);
  }} else {{
    card.querySelectorAll('.btn').forEach(b => b.disabled = false);
    alert(d.detail || 'Error');
  }}
}}
setTimeout(() => location.reload(), 8000);
</script>
</body>
</html>"""


def _fmt_time(iso: str) -> str:
    try:
        return iso[:16].replace("T", " ")
    except Exception:
        return iso


def _render_incident(rec: dict[str, Any]) -> str:
    corr = rec.get("correlation_id", "?")
    report = rec.get("report", {})
    routing = report.get("routing", {})
    diagnosis = report.get("diagnosis") or {}
    fix = report.get("proposed_fix")
    solver = rec.get("solver_result")

    domain = routing.get("domain", "Unknown")
    confidence = routing.get("confidence", "?")
    status = report.get("status", "pending")
    delivered = _fmt_time(report.get("delivered_at", ""))

    root_cause = diagnosis.get("root_cause_hypothesis", "")
    evidence: list[dict[str, Any]] = diagnosis.get("cited_evidence", [])

    html = f'<div class="incident" id="card-{corr}">\n'

    # header
    html += f'  <div class="incident-header">\n'
    html += f'    <span class="incident-title">{domain} incident</span>\n'
    html += f'    <span class="incident-meta">{delivered} &nbsp;·&nbsp; {confidence} confidence &nbsp;·&nbsp; {status}</span>\n'
    html += f'  </div>\n'

    # root cause
    if root_cause:
        html += f'  <div class="root-cause">{root_cause}</div>\n'

    # log evidence
    if evidence:
        lines = "\n".join(
            f"{e.get('timestamp','')[:19]}  [{e.get('container','')}]  {e.get('text','')}"
            for e in evidence[:5]
        )
        html += f'  <div class="log-block">{lines}</div>\n'

    # proposed fix
    if fix:
        action = fix.get("action_type", "")
        t = fix.get("target", {})
        params = fix.get("parameters", {})
        params_str = ", ".join(f"{k}={v}" for k, v in params.items())
        target_str = f"{t.get('namespace','')}/{t.get('pod','')}"
        fix_desc = f"{action}({params_str})" if params_str else action
        html += f'  <div class="fix-line">Proposed fix: <code>{fix_desc}</code> on <code>{target_str}</code></div>\n'

    # solver follow-up
    if solver:
        outcome = solver.get("outcome", "")
        reversal = (solver.get("reversal_recipe") or {}).get("description", "")
        err = solver.get("error", "")
        icon = {"success": "✓", "partial": "~", "failure": "✗"}.get(outcome, "?")
        html += f'  <div class="solver-block">{icon} Solver: {outcome}'
        if reversal:
            html += f' &nbsp;·&nbsp; Undo: {reversal}'
        if err:
            html += f' &nbsp;·&nbsp; Error: {err}'
        html += '</div>\n'

    # buttons
    if status == "pending" and fix:
        html += f'  <div class="actions">\n'
        html += f'    <button class="btn btn-approve" onclick="act(\'{corr}\',\'approve\')">Approve remediation</button>\n'
        html += f'    <button class="btn btn-reject" onclick="act(\'{corr}\',\'reject\')">Reject</button>\n'
        html += f'  </div>\n'
        html += f'  <div class="status-line">Waiting for approval</div>\n'
    elif status == "pending":
        html += f'  <div class="status-line">No automatic fix — manual triage required</div>\n'
    else:
        html += f'  <div class="status-line">{status}</div>\n'

    html += '</div>\n'
    return html


def _build_page() -> str:
    if not _messages:
        body = '<p class="no-incidents">No incidents yet.</p>'
    else:
        records = sorted(
            _messages.values(),
            key=lambda r: r.get("report", {}).get("delivered_at", ""),
            reverse=True,
        )
        body = "".join(_render_incident(r) for r in records)
    return _PAGE.format(body=body)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def dashboard() -> HTMLResponse:
    return HTMLResponse(_build_page())


@app.post("/messages")
async def receive_message(request: Request) -> dict[str, str]:
    body = await request.json()
    corr: str = body.get("correlation_id", str(uuid.uuid4()))
    now = datetime.now(timezone.utc).isoformat()

    existing = _messages.get(corr, {})
    existing.update(body)
    existing.setdefault("message_id", str(uuid.uuid4()))
    _messages[corr] = existing

    return {"delivered_at": now, "message_id": existing["message_id"]}


@app.post("/messages/{correlation_id}/approve")
async def approve(correlation_id: str) -> dict[str, str]:
    return await _forward(correlation_id, "approve")


@app.post("/messages/{correlation_id}/reject")
async def reject(correlation_id: str) -> dict[str, str]:
    return await _forward(correlation_id, "reject")


async def _forward(correlation_id: str, action: str) -> dict[str, str]:
    rec = _messages.get(correlation_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Incident not found")

    status = rec.get("report", {}).get("status", "pending")
    if status != "pending":
        raise HTTPException(status_code=409, detail=f"report_{status}")

    payload = {
        "correlation_id": correlation_id,
        "actor": {"user_id": "U_MOCK", "name": "mock-user", "roles": ["triage-approver"]},
        "action_id": f"{action}_{correlation_id}",
        "reason": "",
        "clicked_at": datetime.now(timezone.utc).isoformat(),
    }
    body_bytes = json.dumps(payload, separators=(",", ":")).encode()

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{AGENT_URL}/callbacks/slack/{action}",
                content=body_bytes,
                headers={"Content-Type": "application/json", "X-Slack-Mock-Signature": _sign(body_bytes)},
            )
            resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=exc.response.text) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    rec.setdefault("report", {})["status"] = "approved" if action == "approve" else "rejected"
    return {"correlation_id": correlation_id, "status": rec["report"]["status"]}


@app.get("/messages")
async def list_messages() -> list[dict[str, Any]]:
    return list(_messages.values())


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
