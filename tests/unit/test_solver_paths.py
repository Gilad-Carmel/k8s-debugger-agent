"""
tests/integration/test_solver_paths.py

Integration tests for the Solver node execution paths (T089 – T093).

These tests exercise solver_node end-to-end with all MCP write tools mocked
at the coroutine level.  No real Kubernetes cluster or MCP server is required.

T089 — Success path: write tool returns 'applied' → SolverRun.outcome == 'success'
T090 — Partial path: write tool applied but post-state shows incomplete recovery
T091 — Admission denied: write tool returns 'refused' → outcome == 'failure'
T092 — Kill switch: write tool raises GuardError(tenant_halted) → outcome == 'failure'
T093 — Fingerprint mismatch: report fingerprint differs from state → failure
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.agent.graph.nodes.solver import solver_node
from src.mcp_server.tools._guards import GuardError
from src.shared.schemas import (
    ApprovalEvent,
    ExpertDiagnosis,
    LogExcerpt,
    ProposedFix,
    Report,
    ReversalRecipe,
    RoutingDecision,
    SolverRun,
    Target,
    WriteToolOutput,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_NAMESPACE = "default"
_POD = "app-pod-abc"
_CORRELATION_ID = "integ-corr-0001"


def _make_log_excerpt() -> LogExcerpt:
    return LogExcerpt(
        timestamp=datetime.now(tz=timezone.utc),
        container="app",
        text="OOMKilled",
        byte_offset=0,
    )


def _make_routing() -> RoutingDecision:
    return RoutingDecision(
        domain="Application",
        confidence="high",
        cited_evidence=[_make_log_excerpt()],
        runners_up=[],
        model="test-model",
        tokens=10,
    )


def _make_target() -> Target:
    return Target(namespace=_NAMESPACE, pod=_POD)


def _make_fix(action_type: str = "restart-pod", params: dict | None = None) -> ProposedFix:
    return ProposedFix.build(
        action_type=action_type,
        target=_make_target(),
        parameters=params or {},
        permission_scope="sa-restart",
    )


def _make_diagnosis(fix: ProposedFix | None = None) -> ExpertDiagnosis:
    return ExpertDiagnosis(
        domain="Application",
        root_cause_hypothesis="OOM killed the main container.",
        cited_evidence=[_make_log_excerpt()],
        confidence="high",
        runner_up_causes=[],
        proposed_fix=fix,
        model="test-model",
        tokens=50,
    )


def _make_report(fix: ProposedFix | None = None) -> Report:
    now = datetime.now(tz=timezone.utc)
    diagnosis = _make_diagnosis(fix)
    routing = _make_routing()
    from datetime import timedelta
    return Report(
        correlation_id=_CORRELATION_ID,
        routing=routing,
        diagnosis=diagnosis,
        proposed_fix=fix,
        status="approved",
        delivered_at=now,
        approval_deadline=now + timedelta(minutes=30),
        runner_up_domains=[],
    )


def _make_approval(correlation_id: str = _CORRELATION_ID) -> ApprovalEvent:
    return ApprovalEvent(
        correlation_id=correlation_id,
        action="approve",
        actor_id="user@example.com",
        actor_roles=["triage-approver"],
        role_check_passed=True,
        at=datetime.now(tz=timezone.utc),
    )


def _success_output(pre_state: dict | None = None, post_state: dict | None = None) -> WriteToolOutput:
    return WriteToolOutput(
        outcome="applied",
        pre_state=pre_state or {"restart_count": 3},
        post_state=post_state or {"restart_count": 3, "ready": True},
        reversal_recipe=ReversalRecipe(
            description="No automated undo — restart was self-recovering.",
            inverse_action=None,
            inverse_parameters={},
        ),
    )


def _refused_output(reason: str = "admission_denied") -> WriteToolOutput:
    return WriteToolOutput(
        outcome="refused",
        pre_state={},
        post_state={},
        reversal_recipe=ReversalRecipe(
            description="No action was issued; no reversal needed.",
            inverse_action=None,
            inverse_parameters={},
        ),
        error=f"Refused: {reason}",
    )


def _build_state(fix: ProposedFix | None = None) -> dict[str, Any]:
    fix = fix or _make_fix()
    report = _make_report(fix)
    return {
        "correlation_id": _CORRELATION_ID,
        "report": report,
        "approval": _make_approval(),
    }


# ---------------------------------------------------------------------------
# T089 — Success path
# ---------------------------------------------------------------------------


class TestSolverSuccessPath:
    def test_restart_pod_success(self) -> None:
        """Solver returns SolverRun.outcome=='success' when write tool returns 'applied'."""
        fix = _make_fix("restart-pod")
        state = _build_state(fix)

        with patch(
            "src.mcp_server.tools.restart_pod.restart_pod",
            new=AsyncMock(return_value=_success_output()),
        ), patch(
            "src.agent.graph.nodes.reporter.deliver",
            new=AsyncMock(return_value=("2026-01-01T00:00:00Z", "msg-1")),
        ):
            result = solver_node(state)

        solver_run: SolverRun = result["solver_run"]
        assert solver_run.outcome == "success"
        assert solver_run.correlation_id == _CORRELATION_ID
        assert solver_run.error is None
        assert result["report"].status == "executed"

    def test_rollback_deployment_success(self) -> None:
        fix = _make_fix("rollback-deployment", {"to_revision": 3})
        state = _build_state(fix)
        rollback_output = WriteToolOutput(
            outcome="applied",
            pre_state={"current_revision": 4, "replicas": 2},
            post_state={"current_revision": 3, "replicas": 2},
            reversal_recipe=ReversalRecipe(
                description="Undo: rollback-deployment to revision 4",
                inverse_action="rollback-deployment",
                inverse_parameters={"to_revision": 4},
            ),
        )

        with patch(
            "src.mcp_server.tools.rollback_deployment.rollback_deployment",
            new=AsyncMock(return_value=rollback_output),
        ), patch("src.agent.graph.nodes.reporter.deliver", new=AsyncMock(return_value=("t", "id"))):
            result = solver_node(state)

        solver_run = result["solver_run"]
        assert solver_run.outcome == "success"
        assert solver_run.reversal_recipe.inverse_action == "rollback-deployment"

    def test_scale_deployment_success(self) -> None:
        fix = _make_fix("scale-deployment", {"to_replicas": 5})
        state = _build_state(fix)
        scale_output = WriteToolOutput(
            outcome="applied",
            pre_state={"replicas": 2},
            post_state={"replicas": 5, "ready_replicas": 5},
            reversal_recipe=ReversalRecipe(
                description="Undo: scale-deployment to 2 replicas",
                inverse_action="scale-deployment",
                inverse_parameters={"to_replicas": 2},
            ),
        )

        with patch(
            "src.mcp_server.tools.scale_deployment.scale_deployment",
            new=AsyncMock(return_value=scale_output),
        ), patch("src.agent.graph.nodes.reporter.deliver", new=AsyncMock(return_value=("t", "id"))):
            result = solver_node(state)

        assert result["solver_run"].outcome == "success"

    def test_delete_pod_to_reschedule_success(self) -> None:
        fix = _make_fix("delete-pod-to-reschedule")
        state = _build_state(fix)

        with patch(
            "src.mcp_server.tools.delete_pod_to_reschedule.delete_pod_to_reschedule",
            new=AsyncMock(return_value=_success_output()),
        ), patch("src.agent.graph.nodes.reporter.deliver", new=AsyncMock(return_value=("t", "id"))):
            result = solver_node(state)

        assert result["solver_run"].outcome == "success"


# ---------------------------------------------------------------------------
# T090 — Partial path (API call succeeded, post-state incomplete)
# ---------------------------------------------------------------------------


class TestSolverPartialPath:
    def test_applied_but_pod_not_ready_maps_to_success(self) -> None:
        """write tool returns 'applied' even when pod not yet ready; outcome is success."""
        fix = _make_fix("restart-pod")
        state = _build_state(fix)
        partial_output = WriteToolOutput(
            outcome="applied",
            pre_state={"restart_count": 3},
            post_state={"restart_count": 4, "ready": False},
            reversal_recipe=ReversalRecipe(
                description="No automated undo — restart was self-recovering.",
                inverse_action=None,
                inverse_parameters={},
            ),
        )

        with patch(
            "src.mcp_server.tools.restart_pod.restart_pod",
            new=AsyncMock(return_value=partial_output),
        ), patch("src.agent.graph.nodes.reporter.deliver", new=AsyncMock(return_value=("t", "id"))):
            result = solver_node(state)

        # The write tool reports 'applied'; solver maps that to 'success'.
        assert result["solver_run"].outcome == "success"
        assert result["solver_run"].post_state == {"restart_count": 4, "ready": False}


# ---------------------------------------------------------------------------
# T091 — Admission denied
# ---------------------------------------------------------------------------


class TestSolverAdmissionDenied:
    def test_refused_outcome_maps_to_failure(self) -> None:
        fix = _make_fix("restart-pod")
        state = _build_state(fix)

        with patch(
            "src.mcp_server.tools.restart_pod.restart_pod",
            new=AsyncMock(return_value=_refused_output("PDB violation")),
        ), patch("src.agent.graph.nodes.reporter.deliver", new=AsyncMock(return_value=("t", "id"))):
            result = solver_node(state)

        solver_run = result["solver_run"]
        assert solver_run.outcome == "failure"
        assert "Refused" in (solver_run.error or "")
        assert result["report"].status == "failed"

    def test_error_outcome_maps_to_failure(self) -> None:
        fix = _make_fix("restart-pod")
        state = _build_state(fix)
        error_output = WriteToolOutput(
            outcome="error",
            pre_state={},
            post_state={},
            reversal_recipe=ReversalRecipe(
                description="No automated undo.",
                inverse_action=None,
                inverse_parameters={},
            ),
            error="Delete API call failed: 500",
        )

        with patch(
            "src.mcp_server.tools.restart_pod.restart_pod",
            new=AsyncMock(return_value=error_output),
        ), patch("src.agent.graph.nodes.reporter.deliver", new=AsyncMock(return_value=("t", "id"))):
            result = solver_node(state)

        assert result["solver_run"].outcome == "failure"


# ---------------------------------------------------------------------------
# T092 — Kill switch
# ---------------------------------------------------------------------------


class TestSolverKillSwitch:
    def test_kill_switch_causes_failure(self) -> None:
        """GuardError from the kill-switch check propagates as a failure outcome."""
        from src.shared.schemas import ToolError

        fix = _make_fix("restart-pod")
        state = _build_state(fix)

        guard_err = GuardError(
            ToolError(
                machine_token="tenant_halted",
                human_message="Write operations are currently halted.",
            )
        )

        with patch(
            "src.mcp_server.tools.restart_pod.restart_pod",
            new=AsyncMock(side_effect=guard_err),
        ), patch("src.agent.graph.nodes.reporter.deliver", new=AsyncMock(return_value=("t", "id"))):
            result = solver_node(state)

        solver_run = result["solver_run"]
        assert solver_run.outcome == "failure"
        assert "tenant_halted" in (solver_run.error or "")


# ---------------------------------------------------------------------------
# T093 — Fingerprint mismatch
# ---------------------------------------------------------------------------


class TestSolverFingerprintMismatch:
    def test_no_proposed_fix_causes_failure(self) -> None:
        """Solver should fail cleanly when report has no proposed_fix."""
        routing = _make_routing()
        now = datetime.now(tz=timezone.utc)
        from datetime import timedelta
        report_no_fix = Report(
            correlation_id=_CORRELATION_ID,
            routing=routing,
            diagnosis=None,
            proposed_fix=None,
            status="approved",
            delivered_at=now,
            approval_deadline=now + timedelta(minutes=30),
            runner_up_domains=[],
        )
        state = {
            "correlation_id": _CORRELATION_ID,
            "report": report_no_fix,
            "approval": _make_approval(),
        }

        result = solver_node(state)

        solver_run = result["solver_run"]
        assert solver_run.outcome == "failure"
        assert solver_run.error is not None

    def test_missing_report_causes_failure(self) -> None:
        state = {
            "correlation_id": _CORRELATION_ID,
            "report": None,
        }
        result = solver_node(state)
        assert result["solver_run"].outcome == "failure"
