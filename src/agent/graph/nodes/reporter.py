"""
src/agent/graph/nodes/reporter.py

Reporter node — Phase 1 STUB.

Real behaviour (T051):
  - Assemble Report from routing + diagnosis.
  - Render Block Kit JSON and POST to slack-mock.
  - Set delivered_at + approval_deadline.
  - Persist report_delivered audit row.

This stub assembles a minimal Report with a fake delivered_at / deadline
and prints a banner instead of POSTing to Slack.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.agent.graph.state import WorkflowState
from src.shared.schemas import Report, RoutingDecision


def reporter_node(state: WorkflowState) -> WorkflowState:
    """
    STUB: assemble Report and print a Slack-style summary.

    Returns a partial WorkflowState containing only the keys this node sets.
    """
    print("[reporter_node] STUB — assembling Report (no Slack POST)")

    correlation_id: str = state.get("correlation_id", "stub-correlation-id")
    routing: RoutingDecision = state["routing"]
    diagnosis = state.get("diagnosis")
    proposed_fix = diagnosis.proposed_fix if diagnosis else None

    now = datetime.now(tz=timezone.utc)
    deadline = now + timedelta(minutes=30)

    report = Report(
        correlation_id=correlation_id,
        routing=routing,
        diagnosis=diagnosis,
        proposed_fix=proposed_fix,
        status="pending",
        delivered_at=now,
        approval_deadline=deadline,
        runner_up_domains=routing.runners_up,
    )

    # Mimic a Slack chat message in the terminal
    domain = routing.domain
    hyp = diagnosis.root_cause_hypothesis if diagnosis else "No diagnosis (Unknown domain)."
    fix_summary = (
        f"action={proposed_fix.action_type}"
        if proposed_fix
        else "No automated fix available."
    )
    print(
        f"\n{'=' * 60}\n"
        f"  [STUB Slack Report]  correlation_id={correlation_id}\n"
        f"  Domain      : {domain} ({routing.confidence})\n"
        f"  Root cause  : {hyp}\n"
        f"  Proposed fix: {fix_summary}\n"
        f"  Status      : {report.status}\n"
        f"  Deadline    : {deadline.isoformat()}\n"
        f"{'=' * 60}\n"
    )

    return {"report": report}  # type: ignore[return-value]
