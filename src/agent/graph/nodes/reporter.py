"""
src/agent/graph/nodes/reporter.py

Reporter node — takes the Router + Expert outputs from WorkflowState, assembles
the immutable Report entity, builds Slack Block Kit JSON, and POSTs it to the
configured Slack surface (mock in dev, real Slack adapter in prod).

Spec refs: FR-013, FR-014 (Block Kit shape + actions block rules)
Contract:  specs/002-routed-triage-workflow/contracts/slack_mock.md
Task:      T051 (initial), T084 (solver follow-up extension)
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from src.agent.graph.state import WorkflowState
from src.shared.schemas import (
    ExpertDiagnosis,
    LogExcerpt,
    ProposedFix,
    Report,
    RoutingDecision,
    SolverRun,
    Target,
)

logger = logging.getLogger(__name__)

SLACK_MOCK_URL = os.getenv("SLACK_MOCK_URL", "http://localhost:8090")
SLACK_CHANNEL = os.getenv("SLACK_CHANNEL", "#k8s-incidents")
TENANT_ID = os.getenv("TENANT_ID", "dev")
APPROVAL_WINDOW_MINUTES = int(os.getenv("APPROVAL_WINDOW_MINUTES", "30"))

_DOMAIN_ICON: dict[str, str] = {
    "Application": "⚙️",
    "Network": "🌐",
    "Database": "🗄️",
    "Unknown": "❓",
}


# ---------------------------------------------------------------------------
# Block Kit builders
# ---------------------------------------------------------------------------

def _header_block(report: Report) -> dict[str, Any]:
    domain = report.routing.domain
    icon = _DOMAIN_ICON.get(domain, "🚨")
    return {
        "type": "header",
        "text": {
            "type": "plain_text",
            "text": f"{icon}  Incident — {domain}",
            "emoji": True,
        },
    }


def _root_cause_block(diagnosis: ExpertDiagnosis) -> dict[str, Any]:
    return {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": f"*Root cause:* {diagnosis.root_cause_hypothesis}",
        },
    }


def _evidence_block(evidence: list[LogExcerpt]) -> dict[str, Any]:
    lines = []
    for ev in evidence[:5]:
        ts = ev.timestamp.strftime("%H:%M:%S") if ev.timestamp else ""
        prefix = f"[{ev.container}]" if ev.container else ""
        lines.append(f"{ts}  {prefix} {ev.text}")
    snippet = "\n".join(lines)
    return {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": f"*Evidence ({len(evidence)} match(es)):*\n```{snippet}```",
        },
    }


def _fix_block(fix: ProposedFix) -> dict[str, Any]:
    target: Target = fix.target
    params_str = ""
    if fix.parameters:
        params_str = "  " + "  ".join(f"{k}={v}" for k, v in fix.parameters.items())
    action_display = f"`{fix.action_type}{params_str}`"
    return {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": (
                f"*Proposed fix:* {action_display} on "
                f"`{target.namespace}/{target.pod}`"
            ),
        },
    }


def _actions_block(correlation_id: str) -> dict[str, Any]:
    """Approve / Reject buttons. Omitted when no fix is proposed (FR-014)."""
    return {
        "type": "actions",
        "elements": [
            {
                "type": "button",
                "style": "primary",
                "text": {"type": "plain_text", "text": "Approve Remediation", "emoji": True},
                "value": "approve",
                "action_id": f"approve_{correlation_id}",
            },
            {
                "type": "button",
                "style": "danger",
                "text": {"type": "plain_text", "text": "Reject", "emoji": True},
                "value": "reject",
                "action_id": f"reject_{correlation_id}",
            },
        ],
    }


def _context_block(report: Report) -> dict[str, Any]:
    parts = [f"Confidence: *{report.routing.confidence}*"]
    if report.runner_up_domains:
        runner_up_str = ", ".join(f"{d} ({c})" for d, c in report.runner_up_domains)
        parts.append(f"Runner-ups: {runner_up_str}")
    parts.append(f"ID: `{report.correlation_id}`")
    return {
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": "  •  ".join(parts)}],
    }


def _divider() -> dict[str, Any]:
    return {"type": "divider"}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_initial_blocks(report: Report) -> list[dict[str, Any]]:
    """
    Assemble the Block Kit blocks for the initial triage report message.

    Rules (contract slack_mock.md §Outbound):
    - actions block MUST be omitted when proposed_fix is None (FR-014)
    - actions block MUST be omitted when status != "pending"
    - header, root-cause section, evidence section, context MUST always be present
    """
    blocks: list[dict[str, Any]] = [_header_block(report), _divider()]

    diagnosis = report.diagnosis
    if diagnosis:
        blocks.append(_root_cause_block(diagnosis))
        if diagnosis.cited_evidence:
            blocks.append(_evidence_block(diagnosis.cited_evidence))
        if diagnosis.proposed_fix:
            blocks.append(_fix_block(diagnosis.proposed_fix))
    else:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    "*No actionable signal* — domain could not be determined with "
                    "sufficient confidence. Manual triage required."
                ),
            },
        })

    # Approve / Reject only when a fix exists and the report is still pending
    if report.proposed_fix and report.status == "pending":
        blocks.append(_actions_block(report.correlation_id))

    blocks.append(_context_block(report))
    return blocks


def build_followup_blocks(report: Report, solver_run: SolverRun) -> list[dict[str, Any]]:
    """
    Block Kit for the Solver follow-up message posted to the same thread.

    Spec FR-023: outcome (success/partial/failure) + post-state + Inverse Action.
    """
    _OUTCOME_ICON = {"success": "✅", "partial": "⚠️", "failure": "❌"}
    icon = _OUTCOME_ICON.get(solver_run.outcome, "❓")

    fix = report.proposed_fix
    action_str = (
        f"`{fix.action_type}` on `{fix.target.namespace}/{fix.target.pod}`"
        if fix
        else "action"
    )

    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"{icon}  Solver Result — {solver_run.outcome.upper()}",
                "emoji": True,
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Executed:* {action_str}",
            },
        },
    ]

    reversal = solver_run.reversal_recipe
    if reversal:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Reversal:* {reversal.description}",
            },
        })

    if solver_run.error:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f":warning: *Error:* {solver_run.error}",
            },
        })

    blocks.append({
        "type": "context",
        "elements": [
            {
                "type": "mrkdwn",
                "text": f"ID: `{report.correlation_id}`",
            }
        ],
    })
    return blocks


def build_message_body(
    report: Report,
    blocks: list[dict[str, Any]],
    solver_run: SolverRun | None = None,
) -> dict[str, Any]:
    """
    Full POST body sent to /messages (contract slack_mock.md §Outbound).

    The `report` sidecar preserves structured fields so the mock (or a future
    real Slack adapter) can choose what to render.
    """
    routing = report.routing
    diagnosis = report.diagnosis
    proposed_fix = report.proposed_fix

    report_sidecar: dict[str, Any] = {
        "status": report.status,
        "delivered_at": report.delivered_at.isoformat(),
        "approval_deadline": report.approval_deadline.isoformat(),
        "routing": {
            "domain": routing.domain,
            "confidence": routing.confidence,
            "cited_evidence": [
                {
                    "timestamp": e.timestamp.isoformat(),
                    "container": e.container,
                    "text": e.text,
                }
                for e in routing.cited_evidence
            ],
            "runners_up": [
                [d, c] for d, c in routing.runners_up
            ],
        },
        "diagnosis": {
            "domain": diagnosis.domain,
            "root_cause_hypothesis": diagnosis.root_cause_hypothesis,
            "confidence": diagnosis.confidence,
            "cited_evidence": [
                {
                    "timestamp": e.timestamp.isoformat(),
                    "container": e.container,
                    "text": e.text,
                }
                for e in diagnosis.cited_evidence
            ],
            "runner_up_causes": diagnosis.runner_up_causes,
        }
        if diagnosis
        else None,
        "proposed_fix": {
            "action_type": proposed_fix.action_type,
            "target": {
                "namespace": proposed_fix.target.namespace,
                "pod": proposed_fix.target.pod,
                "container": proposed_fix.target.container,
            },
            "parameters": proposed_fix.parameters,
            "fingerprint": proposed_fix.fingerprint,
        }
        if proposed_fix
        else None,
    }

    body: dict[str, Any] = {
        "correlation_id": report.correlation_id,
        "channel": SLACK_CHANNEL,
        "report": report_sidecar,
        "blocks": blocks,
    }

    if solver_run:
        body["solver_result"] = {
            "outcome": solver_run.outcome,
            "reversal_recipe": {
                "description": solver_run.reversal_recipe.description,
                "inverse_action": solver_run.reversal_recipe.inverse_action,
                "inverse_parameters": solver_run.reversal_recipe.inverse_parameters,
            }
            if solver_run.reversal_recipe
            else None,
            "error": solver_run.error,
        }

    return body


async def chat_deliver(
    report: Report,
    solver_run: SolverRun | None = None,
) -> tuple[str, str]:
    """
    POST the report to each configured chat surface.

    Returns the most recently observed (delivered_at_iso, message_id) from successful deliveries.
    Raises when chat surface configuration is invalid or when all deliveries fail.
    """
    from src.agent.settings import settings

    surface = settings.chat_surface
    valid_surfaces = {"slack", "discord", "all"}
    if surface not in valid_surfaces:
        raise ValueError(f"Invalid chat_surface={surface!r}. Expected one of: slack, discord, all.")

    targets: list[tuple[str, str]] = []
    if surface in ("slack", "all"):
        slack_url = (settings.slack_mock_url or "").strip()
        if slack_url:
            targets.append(("slack", slack_url))
    if surface in ("discord", "all"):
        discord_url = (settings.discord_bot_url or "").strip()
        if discord_url:
            targets.append(("discord", discord_url))
    if not targets:
        raise RuntimeError("No chat delivery targets configured.")

    if solver_run:
        blocks = build_followup_blocks(report, solver_run)
    else:
        blocks = build_initial_blocks(report)

    body = build_message_body(report, blocks, solver_run)
    body_bytes = json.dumps(body, default=str).encode()

    delivered_at = datetime.now(timezone.utc).isoformat()
    message_id = "unknown"
    last_exc: Exception | None = None

    delivered = False
    for target_name, url in targets:
        try:
            headers: dict[str, str] = {"Content-Type": "application/json"}
            if target_name == "slack":
                headers["X-Tenant-Id"] = TENANT_ID
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    f"{url}/messages",
                    content=body_bytes,
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()
                delivered_at = data.get("delivered_at", delivered_at)
                message_id = data.get("message_id", message_id)
                delivered = True
                logger.info(
                    "report delivered corr=%s surface=%s solver=%s",
                    report.correlation_id, url, solver_run is not None,
                )
        except Exception as exc:
            logger.warning("delivery failed url=%s corr=%s: %s", url, report.correlation_id, exc)
            last_exc = exc

    if not delivered:
        if last_exc is None:
            logger.error("chat delivery ended without success or captured exception corr=%s", report.correlation_id)
            raise RuntimeError(f"No successful chat deliveries to {len(targets)} target(s).")
        raise last_exc

    return delivered_at, message_id


def make_report(
    correlation_id: str,
    routing: RoutingDecision,
    diagnosis: ExpertDiagnosis | None,
    delivered_at: datetime,
    approval_window_minutes: int = APPROVAL_WINDOW_MINUTES,
) -> Report:
    """
    Factory: assemble a Report from the graph state fields produced by the
    Router and Expert nodes.  Sets status='pending' and computes deadline.
    """
    approval_deadline = delivered_at + timedelta(minutes=approval_window_minutes)
    return Report(
        correlation_id=correlation_id,
        routing=routing,
        diagnosis=diagnosis,
        proposed_fix=diagnosis.proposed_fix if diagnosis else None,
        status="pending",
        delivered_at=delivered_at,
        approval_deadline=approval_deadline,
        runner_up_domains=list(routing.runners_up),
    )

async def reporter_node(state: WorkflowState) -> WorkflowState:
    """Assemble and deliver the report, returning the report field in state."""
    from src.agent.db import set_proposed_fix_fingerprint

    correlation_id = state["correlation_id"]
    routing = state["routing"]
    diagnosis = state.get("diagnosis")

    tentative_delivered_at = datetime.now(tz=timezone.utc)
    report = make_report(
        correlation_id=correlation_id,
        routing=routing,
        diagnosis=diagnosis,
        delivered_at=tentative_delivered_at,
    )

    if report.proposed_fix:
        await set_proposed_fix_fingerprint(correlation_id, report.proposed_fix.fingerprint)

    try:
        delivered_at_iso, _ = await chat_deliver(report)
        delivered_at = datetime.fromisoformat(delivered_at_iso.replace("Z", "+00:00"))
        report = report.model_copy(
            update={
                "delivered_at": delivered_at,
                "approval_deadline": delivered_at + timedelta(minutes=APPROVAL_WINDOW_MINUTES),
            }
        )
    except Exception:
        logger.exception("report delivery failed corr=%s", correlation_id)
        report = report.model_copy(update={"status": "failed"})

    return {"report": report}  # type: ignore[return-value]


async def reporter_followup_node(state: WorkflowState) -> WorkflowState:
    """Deliver the solver result back to the chat surface after solver runs."""
    report = state["report"]
    solver_run = state.get("solver_run")
    if solver_run is None:
        return {}  # type: ignore[return-value]
    try:
        await chat_deliver(report, solver_run)
        logger.info("solver follow-up delivered corr=%s", report.correlation_id)
    except Exception:
        logger.exception("solver follow-up delivery failed corr=%s", report.correlation_id)
    return {}  # type: ignore[return-value]
