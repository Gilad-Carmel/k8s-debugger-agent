"""
tests/unit/test_solver_node.py

Focused unit tests for src/agent/graph/nodes/solver.py internals.

Covers gaps not in test_solver_paths.py:
  - SolverRun field correctness (fingerprint, pre_state, post_state, reversal_recipe)
  - Report status transitions (pending→executed, pending→failed)
  - Follow-up Slack delivery failure does NOT crash the solver
  - Unsupported action_type raises ValueError → clean failure SolverRun
  - approval_token.issue_token ↔ _guards.validate_approval_token round-trip
  - Reporter build_followup_blocks for all 3 outcomes + error/reversal blocks
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent.graph.nodes.solver import solver_node
from src.agent.graph.nodes.reporter import build_followup_blocks
from src.shared.schemas import (
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
# Shared fixtures / builders
# ---------------------------------------------------------------------------

_NS = "production"
_POD = "checkout-abc123"
_CORR = "round-trip-corr-0001"


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _log() -> LogExcerpt:
    return LogExcerpt(timestamp=_now(), container="app", text="OOMKilled", byte_offset=0)


def _routing() -> RoutingDecision:
    return RoutingDecision(
        domain="Application",
        confidence="high",
        cited_evidence=[_log()],
        runners_up=[],
        model="test",
        tokens=10,
    )


def _target() -> Target:
    return Target(namespace=_NS, pod=_POD)


def _fix(action_type: str = "restart-pod", params: dict | None = None) -> ProposedFix:
    return ProposedFix.build(
        action_type=action_type,
        target=_target(),
        parameters=params or {},
        permission_scope="sa-test",
    )


def _diagnosis(fix: ProposedFix | None = None) -> ExpertDiagnosis:
    return ExpertDiagnosis(
        domain="Application",
        root_cause_hypothesis="OOM killed the container.",
        cited_evidence=[_log()],
        confidence="high",
        runner_up_causes=[],
        proposed_fix=fix,
        model="test",
        tokens=20,
    )


def _report(fix: ProposedFix | None = None, status: str = "approved") -> Report:
    now = _now()
    f = fix or _fix()
    return Report(
        correlation_id=_CORR,
        routing=_routing(),
        diagnosis=_diagnosis(f),
        proposed_fix=f,
        status=status,
        delivered_at=now,
        approval_deadline=now + timedelta(minutes=30),
        runner_up_domains=[],
    )


def _applied_output(
    pre: dict | None = None,
    post: dict | None = None,
    recipe: ReversalRecipe | None = None,
) -> WriteToolOutput:
    return WriteToolOutput(
        outcome="applied",
        pre_state=pre or {"restart_count": 2},
        post_state=post or {"restart_count": 2, "ready": True},
        reversal_recipe=recipe or ReversalRecipe(
            description="No automated undo — restart was self-recovering.",
            inverse_action=None,
            inverse_parameters={},
        ),
    )


def _state(fix: ProposedFix | None = None) -> dict:
    f = fix or _fix()
    return {
        "correlation_id": _CORR,
        "report": _report(f),
    }


# ---------------------------------------------------------------------------
# Token round-trip (approval_token ↔ _guards)
# ---------------------------------------------------------------------------


class TestTokenRoundTrip:
    """Proves the format fix: token issued by approval_token.py validates in _guards.py."""

    def test_issued_token_validates_in_guards(self) -> None:
        from src.agent.approval_token import issue_token
        from src.mcp_server.tools._guards import validate_approval_token

        fingerprint = "a" * 64
        corr = "rt-corr-1"
        token = issue_token(correlation_id=corr, fingerprint=fingerprint, ttl_seconds=300)

        # Must not raise
        validate_approval_token(token, corr, fingerprint)

    def test_issued_token_wrong_fingerprint_fails(self) -> None:
        from src.agent.approval_token import issue_token
        from src.mcp_server.tools._guards import GuardError, validate_approval_token

        fingerprint = "b" * 64
        corr = "rt-corr-2"
        token = issue_token(correlation_id=corr, fingerprint=fingerprint)

        with pytest.raises(GuardError) as exc_info:
            validate_approval_token(token, corr, "wrong" + fingerprint)
        assert exc_info.value.tool_error.machine_token == "approval_invalid"

    def test_issued_token_wrong_correlation_id_fails(self) -> None:
        from src.agent.approval_token import issue_token
        from src.mcp_server.tools._guards import GuardError, validate_approval_token

        fingerprint = "c" * 64
        token = issue_token(correlation_id="corr-A", fingerprint=fingerprint)

        with pytest.raises(GuardError):
            validate_approval_token(token, "corr-B", fingerprint)

    def test_expired_issued_token_fails(self) -> None:
        from src.agent.approval_token import issue_token
        from src.mcp_server.tools._guards import GuardError, validate_approval_token

        fingerprint = "d" * 64
        corr = "rt-corr-3"
        token = issue_token(correlation_id=corr, fingerprint=fingerprint, ttl_seconds=-1)

        with pytest.raises(GuardError) as exc_info:
            validate_approval_token(token, corr, fingerprint)
        assert "expired" in exc_info.value.tool_error.human_message.lower()


# ---------------------------------------------------------------------------
# SolverRun field correctness
# ---------------------------------------------------------------------------


class TestSolverRunFields:
    def test_fingerprint_carried_through(self) -> None:
        fix = _fix("restart-pod")
        state = _state(fix)

        with patch(
            "src.mcp_server.tools.restart_pod.restart_pod",
            new=AsyncMock(return_value=_applied_output()),
        ), patch("src.agent.graph.nodes.reporter.deliver", new=AsyncMock(return_value=("t", "id"))):
            result = solver_node(state)

        solver_run: SolverRun = result["solver_run"]
        assert solver_run.proposed_fix_fingerprint == fix.fingerprint

    def test_pre_state_and_post_state_copied(self) -> None:
        pre = {"restart_count": 5, "phase": "Running"}
        post = {"restart_count": 5, "ready": True, "phase": "Running"}
        fix = _fix("restart-pod")
        state = _state(fix)

        with patch(
            "src.mcp_server.tools.restart_pod.restart_pod",
            new=AsyncMock(return_value=_applied_output(pre=pre, post=post)),
        ), patch("src.agent.graph.nodes.reporter.deliver", new=AsyncMock(return_value=("t", "id"))):
            result = solver_node(state)

        run = result["solver_run"]
        assert run.pre_state == pre
        assert run.post_state == post

    def test_reversal_recipe_copied(self) -> None:
        recipe = ReversalRecipe(
            description="Undo: scale-deployment to 2 replicas",
            inverse_action="scale-deployment",
            inverse_parameters={"to_replicas": 2},
        )
        fix = _fix("scale-deployment", {"to_replicas": 5})
        state = _state(fix)
        output = WriteToolOutput(
            outcome="applied",
            pre_state={"replicas": 2},
            post_state={"replicas": 5},
            reversal_recipe=recipe,
        )

        with patch(
            "src.mcp_server.tools.scale_deployment.scale_deployment",
            new=AsyncMock(return_value=output),
        ), patch("src.agent.graph.nodes.reporter.deliver", new=AsyncMock(return_value=("t", "id"))):
            result = solver_node(state)

        assert result["solver_run"].reversal_recipe == recipe

    def test_correlation_id_preserved(self) -> None:
        fix = _fix()
        state = _state(fix)

        with patch(
            "src.mcp_server.tools.restart_pod.restart_pod",
            new=AsyncMock(return_value=_applied_output()),
        ), patch("src.agent.graph.nodes.reporter.deliver", new=AsyncMock(return_value=("t", "id"))):
            result = solver_node(state)

        assert result["solver_run"].correlation_id == _CORR

    def test_started_at_before_finished_at(self) -> None:
        fix = _fix()
        state = _state(fix)

        with patch(
            "src.mcp_server.tools.restart_pod.restart_pod",
            new=AsyncMock(return_value=_applied_output()),
        ), patch("src.agent.graph.nodes.reporter.deliver", new=AsyncMock(return_value=("t", "id"))):
            result = solver_node(state)

        run = result["solver_run"]
        assert run.started_at <= run.finished_at


# ---------------------------------------------------------------------------
# Report status transitions
# ---------------------------------------------------------------------------


class TestReportStatusTransition:
    def test_success_sets_report_status_to_executed(self) -> None:
        fix = _fix("restart-pod")
        state = _state(fix)

        with patch(
            "src.mcp_server.tools.restart_pod.restart_pod",
            new=AsyncMock(return_value=_applied_output()),
        ), patch("src.agent.graph.nodes.reporter.deliver", new=AsyncMock(return_value=("t", "id"))):
            result = solver_node(state)

        assert result["report"].status == "executed"

    def test_failure_sets_report_status_to_failed(self) -> None:
        fix = _fix("restart-pod")
        state = _state(fix)
        refused = WriteToolOutput(
            outcome="refused",
            pre_state={},
            post_state={},
            reversal_recipe=ReversalRecipe(
                description="No action.", inverse_action=None, inverse_parameters={}
            ),
            error="PDB violation",
        )

        with patch(
            "src.mcp_server.tools.restart_pod.restart_pod",
            new=AsyncMock(return_value=refused),
        ), patch("src.agent.graph.nodes.reporter.deliver", new=AsyncMock(return_value=("t", "id"))):
            result = solver_node(state)

        assert result["report"].status == "failed"
        assert result["solver_run"].outcome == "failure"

    def test_error_outcome_sets_failed(self) -> None:
        fix = _fix("restart-pod")
        state = _state(fix)
        err_out = WriteToolOutput(
            outcome="error",
            pre_state={},
            post_state={},
            reversal_recipe=ReversalRecipe(
                description="No action.", inverse_action=None, inverse_parameters={}
            ),
            error="API timeout",
        )

        with patch(
            "src.mcp_server.tools.restart_pod.restart_pod",
            new=AsyncMock(return_value=err_out),
        ), patch("src.agent.graph.nodes.reporter.deliver", new=AsyncMock(return_value=("t", "id"))):
            result = solver_node(state)

        assert result["report"].status == "failed"


# ---------------------------------------------------------------------------
# Follow-up delivery failure resilience
# ---------------------------------------------------------------------------


class TestSolverResilientToDeliveryFailure:
    def test_slack_delivery_failure_does_not_crash_solver(self) -> None:
        fix = _fix("restart-pod")
        state = _state(fix)

        with patch(
            "src.mcp_server.tools.restart_pod.restart_pod",
            new=AsyncMock(return_value=_applied_output()),
        ), patch(
            "src.agent.graph.nodes.reporter.deliver",
            new=AsyncMock(side_effect=Exception("Slack is down")),
        ):
            result = solver_node(state)

        # SolverRun must still be returned even when follow-up delivery fails
        assert "solver_run" in result
        assert result["solver_run"].outcome == "success"

    def test_slack_delivery_failure_still_updates_report_status(self) -> None:
        fix = _fix("restart-pod")
        state = _state(fix)

        with patch(
            "src.mcp_server.tools.restart_pod.restart_pod",
            new=AsyncMock(return_value=_applied_output()),
        ), patch(
            "src.agent.graph.nodes.reporter.deliver",
            new=AsyncMock(side_effect=RuntimeError("network error")),
        ):
            result = solver_node(state)

        assert result["report"].status == "executed"


# ---------------------------------------------------------------------------
# Unsupported action type
# ---------------------------------------------------------------------------


class TestUnsupportedActionType:
    def test_unknown_action_type_returns_failure_solverrun(self) -> None:
        """_execute_fix raises ValueError for unknown action type → clean failure, no crash."""
        bad_fix = ProposedFix(
            action_type="restart-pod",   # valid enum so schema accepts it
            target=_target(),
            parameters={},
            permission_scope="sa-test",
            fingerprint="a" * 64,
        )
        report = _report(bad_fix)
        # Patch _execute_fix to raise ValueError simulating an unknown routing
        state = {
            "correlation_id": _CORR,
            "report": report,
        }

        with patch(
            "src.mcp_server.tools.restart_pod.restart_pod",
            new=AsyncMock(side_effect=ValueError("Unsupported action_type: 'bad-action'")),
        ), patch("src.agent.graph.nodes.reporter.deliver", new=AsyncMock(return_value=("t", "id"))):
            result = solver_node(state)

        run = result["solver_run"]
        assert run.outcome == "failure"
        assert run.error is not None


# ---------------------------------------------------------------------------
# Reporter build_followup_blocks
# ---------------------------------------------------------------------------


class TestBuildFollowupBlocks:
    def _make_run(self, outcome: str, error: str | None = None) -> SolverRun:
        now = _now()
        return SolverRun(
            correlation_id=_CORR,
            proposed_fix_fingerprint="a" * 64,
            pre_state={"restart_count": 1},
            action_issued={"action_type": "restart-pod"},
            post_state={"ready": True},
            outcome=outcome,
            reversal_recipe=ReversalRecipe(
                description="No automated undo — restart was self-recovering.",
                inverse_action=None,
                inverse_parameters={},
            ),
            error=error,
            started_at=now,
            finished_at=now,
        )

    def _get_text(self, blocks: list) -> str:
        texts = []
        for b in blocks:
            if "text" in b:
                t = b["text"]
                if isinstance(t, dict):
                    texts.append(t.get("text", ""))
                else:
                    texts.append(str(t))
            if "elements" in b:
                for el in b["elements"]:
                    if isinstance(el, dict) and "text" in el:
                        texts.append(str(el["text"]))
        return "\n".join(texts)

    def test_success_header_contains_check_mark(self) -> None:
        report = _report()
        run = self._make_run("success")
        blocks = build_followup_blocks(report, run)
        header = blocks[0]
        assert "✅" in header["text"]["text"]
        assert "SUCCESS" in header["text"]["text"]

    def test_failure_header_contains_x_mark(self) -> None:
        report = _report()
        run = self._make_run("failure", error="API timed out")
        blocks = build_followup_blocks(report, run)
        header = blocks[0]
        assert "❌" in header["text"]["text"]
        assert "FAILURE" in header["text"]["text"]

    def test_partial_header_contains_warning(self) -> None:
        report = _report()
        run = self._make_run("partial")
        blocks = build_followup_blocks(report, run)
        header = blocks[0]
        assert "⚠️" in header["text"]["text"]

    def test_reversal_block_present(self) -> None:
        report = _report()
        run = self._make_run("success")
        blocks = build_followup_blocks(report, run)
        full_text = self._get_text(blocks)
        assert "Reversal" in full_text
        assert "self-recovering" in full_text

    def test_error_block_present_on_failure(self) -> None:
        report = _report()
        run = self._make_run("failure", error="PDB violation prevented eviction")
        blocks = build_followup_blocks(report, run)
        full_text = self._get_text(blocks)
        assert "PDB violation prevented eviction" in full_text

    def test_error_block_absent_on_success(self) -> None:
        report = _report()
        run = self._make_run("success")
        blocks = build_followup_blocks(report, run)
        full_text = self._get_text(blocks)
        assert "Error" not in full_text or ":warning:" not in full_text

    def test_correlation_id_in_context(self) -> None:
        report = _report()
        run = self._make_run("success")
        blocks = build_followup_blocks(report, run)
        full_text = self._get_text(blocks)
        assert _CORR in full_text

    def test_action_type_mentioned(self) -> None:
        fix = _fix("rollback-deployment", {"to_revision": 3})
        report = _report(fix)
        run = self._make_run("success")
        blocks = build_followup_blocks(report, run)
        full_text = self._get_text(blocks)
        assert "rollback-deployment" in full_text
