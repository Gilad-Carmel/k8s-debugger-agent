"""
src/agent/graph/nodes/solver.py

Solver node — deterministic execution of the frozen ProposedFix (T083).

Design invariants (spec FR-011, FR-020 — FR-026, Principle I):
  - NO LLM call.  Every decision is deterministic and catalog-bound.
  - Fingerprint verified before any Kubernetes API call (FR-020).
  - Per-target serialization via solver_lock.py (FR-026).
  - Approval token issued inline; MCP write tools validate it server-side.
  - Inverse Action (ReversalRecipe) is computed from pre_state by the write
    tool using the fixed Forward → Inverse mapping in shared/catalog.py
    (FR-022).  The solver never authors an ad-hoc reversal script.
  - Follow-up Slack message delivered after Solver completes (FR-023 / T084).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Coroutine, Optional, TypeVar

_T = TypeVar("_T")

from src.agent.approval_token import verify_token
from src.agent.graph.state import WorkflowState
from src.agent.solver_lock import solver_target_lock
from src.shared.labels import SolverOutcome
from src.shared.schemas import ProposedFix, Report, ReversalRecipe, SolverRun, WriteToolOutput

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _execute_fix(
    fix: ProposedFix,
    approval_token: str,
    correlation_id: str,
    verification_window_sec: int = 60,
) -> WriteToolOutput:
    """Dispatch to the right MCP write tool based on action_type."""
    action_type = fix.action_type
    namespace = fix.target.namespace
    pod = fix.target.pod
    fingerprint = fix.fingerprint

    if action_type == "restart-pod":
        from src.mcp_server.tools.restart_pod import restart_pod  # noqa: PLC0415
        return await restart_pod(
            namespace=namespace,
            pod=pod,
            correlation_id=correlation_id,
            approval_token=approval_token,
            proposed_fix_fingerprint=fingerprint,
            verification_window_sec=verification_window_sec,
        )

    if action_type == "rollback-deployment":
        deployment = fix.parameters.get("deployment")
        if not deployment:
            raise ValueError("rollback-deployment requires parameters['deployment'] (got missing or empty)")
        from src.mcp_server.tools.rollback_deployment import rollback_deployment  # noqa: PLC0415
        return await rollback_deployment(
            namespace=namespace,
            deployment=deployment,
            to_revision=int(fix.parameters["to_revision"]),
            correlation_id=correlation_id,
            approval_token=approval_token,
            proposed_fix_fingerprint=fingerprint,
            verification_window_sec=verification_window_sec,
        )

    if action_type == "scale-deployment":
        deployment = fix.parameters.get("deployment")
        if not deployment:
            raise ValueError("scale-deployment requires parameters['deployment'] (got missing or empty)")
        from src.mcp_server.tools.scale_deployment import scale_deployment  # noqa: PLC0415
        return await scale_deployment(
            namespace=namespace,
            deployment=deployment,
            to_replicas=int(fix.parameters["to_replicas"]),
            correlation_id=correlation_id,
            approval_token=approval_token,
            proposed_fix_fingerprint=fingerprint,
            verification_window_sec=verification_window_sec,
        )

    if action_type == "delete-pod-to-reschedule":
        from src.mcp_server.tools.delete_pod_to_reschedule import delete_pod_to_reschedule  # noqa: PLC0415
        return await delete_pod_to_reschedule(
            namespace=namespace,
            pod=pod,
            correlation_id=correlation_id,
            approval_token=approval_token,
            proposed_fix_fingerprint=fingerprint,
            verification_window_sec=verification_window_sec,
        )

    raise ValueError(f"Unsupported action_type: {action_type!r}")


def _run_async(coro: Coroutine[Any, Any, _T]) -> _T:
    """Run a coroutine, creating a new event loop if none is active."""
    try:
        asyncio.get_running_loop()
        # Inside a running loop (e.g. FastAPI test client).  The sync node
        # was dispatched to a thread executor by LangGraph, so we can safely
        # create a new loop in this thread.
        import concurrent.futures  # noqa: PLC0415
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result()
    except RuntimeError:
        return asyncio.run(coro)


def _build_failure(
    correlation_id: str,
    fingerprint: str,
    pre_state: dict[str, Any],
    error_msg: str,
    started_at: datetime,
) -> WorkflowState:
    """Return a failed SolverRun state update."""
    now = datetime.now(tz=timezone.utc)
    recipe = ReversalRecipe(
        description="No reversal available — action was not issued.",
        inverse_action=None,
        inverse_parameters={},
    )
    solver_run = SolverRun(
        correlation_id=correlation_id,
        proposed_fix_fingerprint=fingerprint,
        pre_state=pre_state,
        action_issued={},
        post_state={},
        outcome="failure",
        reversal_recipe=recipe,
        error=error_msg,
        started_at=started_at,
        finished_at=now,
    )
    logger.error("solver failure corr=%s error=%s", correlation_id, error_msg)
    return {"solver_run": solver_run}


# ---------------------------------------------------------------------------
# Public node
# ---------------------------------------------------------------------------

def solver_node(state: WorkflowState) -> WorkflowState:
    """
    Deterministic Solver node — no LLM, no ad-hoc scripts.

    Steps:
      1. Fingerprint guard (FR-020) — refuse if report has no fix.
      2. Issue a short-lived HMAC approval token.
      3. Acquire the per-target serialization lock (FR-026).
      4. Call the matching MCP write tool.
      5. Compute SolverRun (outcome, pre_state, post_state, reversal_recipe).
      6. Deliver Slack follow-up (FR-023 / T084).
      7. Return updated WorkflowState.
    """
    correlation_id: str = state.get("correlation_id", "unknown")
    report: Optional[Report] = state.get("report")
    started_at = datetime.now(tz=timezone.utc)

    # ---- Guard: report must carry a proposed fix ----------------------------
    if report is None or report.proposed_fix is None:
        return _build_failure(
            correlation_id,
            "no-fingerprint",
            {},
            "Solver invoked but report carries no proposed fix.",
            started_at,
        )

    fix = report.proposed_fix
    fingerprint = fix.fingerprint

    logger.info(
        "solver start corr=%s action=%s target=%s/%s",
        correlation_id,
        fix.action_type,
        fix.target.namespace,
        fix.target.pod,
    )

    # ---- Read the approval token written into state by callbacks.py ----------
    # The token was issued at approve-click time, bound to the fingerprint the
    # user actually saw.  We MUST NOT mint a fresh token here — doing so would
    # let a mutated ProposedFix slip through without the user's knowledge.
    token: str = state.get("approval_token", "")
    if not token:
        return _build_failure(
            correlation_id, fingerprint, {},
            "approval_token missing from state — cannot execute without an "
            "approval-time token (possible replay or state corruption).",
            started_at,
        )

    # Pre-validate before acquiring the lock or touching any Kubernetes API.
    if not verify_token(token, expected_correlation_id=correlation_id, expected_fingerprint=fingerprint):
        return _build_failure(
            correlation_id, fingerprint, {},
            "approval_token is invalid or expired — failing closed (FR-020).",
            started_at,
        )

    # ---- Acquire per-target lock (FR-026) -----------------------------------
    with solver_target_lock(fix.target.namespace, fix.target.pod):
        try:
            result: WriteToolOutput = _run_async(
                _execute_fix(fix, token, correlation_id)
            )
        except Exception as exc:
            logger.exception("solver execution error corr=%s", correlation_id)
            return _build_failure(
                correlation_id, fingerprint, {}, str(exc), started_at
            )

    # ---- Map WriteToolOutput.outcome → SolverOutcome ------------------------
    solver_outcome: SolverOutcome = "success" if result.outcome == "applied" else "failure"

    finished_at = datetime.now(tz=timezone.utc)
    solver_run = SolverRun(
        correlation_id=correlation_id,
        proposed_fix_fingerprint=fingerprint,
        pre_state=result.pre_state,
        action_issued=fix.model_dump(mode="json"),
        post_state=result.post_state,
        outcome=solver_outcome,
        reversal_recipe=result.reversal_recipe,
        error=result.error,
        started_at=started_at,
        finished_at=finished_at,
    )

    logger.info(
        "solver done corr=%s outcome=%s reversal=%s",
        correlation_id,
        solver_outcome,
        solver_run.reversal_recipe.description,
    )

    # ---- Deliver Slack follow-up (T084) -------------------------------------
    new_status = "executed" if solver_outcome == "success" else "failed"
    updated_report = report.model_copy(update={"status": new_status})
    try:
        from src.agent.graph.nodes.reporter import deliver  # noqa: PLC0415
        _run_async(deliver(report=updated_report, solver_run=solver_run))
    except Exception:
        logger.exception("solver follow-up delivery failed corr=%s", correlation_id)

    return {
        "solver_run": solver_run,
        "report": updated_report,
    }
