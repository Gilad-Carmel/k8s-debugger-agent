"""
tests/unit/test_base_expert_allowed_actions.py

Unit tests for the per-domain action subset filter added to BaseExpert
(spec 007 T002 / T003, research.md R1).

The filter runs in BaseExpert._run_real_diagnosis immediately after
validate_action(). When a subclass declares _allowed_actions narrower than
the full catalog, any validated action outside that subset is dropped
(proposed_fix=None). This guarantees the Reporter never surfaces an
Approve button for an out-of-subset action even when the model emits one
(Principle I).

These tests use a synthetic BaseExpert subclass and a hand-rolled mock of
ChatOpenAI.with_structured_output(...).invoke(...) so they exercise the
exact branch we care about, with no dependency on the Network or
Application prompts.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import ClassVar
from unittest.mock import MagicMock, patch

import pytest
from src.agent.graph.nodes.experts._base import BaseExpert, _ExpertOutput
from src.agent.graph.state import WorkflowState
from src.shared.labels import ACTION_TYPES
from src.shared.schemas import (
    ExpertDiagnosis,
    FilteredEvidence,
    LogExcerpt,
)

# ---------------------------------------------------------------------------
# Test fixtures — minimal synthetic state with one verbatim hit line.
# ---------------------------------------------------------------------------
_HIT_TEXT = "test log line: connection refused at upstream backend"


def _build_state() -> WorkflowState:
    """Minimal WorkflowState carrying one LogExcerpt and no incident."""
    hit = LogExcerpt(
        timestamp=datetime(2026, 5, 15, 12, 0, 0, tzinfo=UTC),
        container="app",
        text=_HIT_TEXT,
        byte_offset=0,
    )
    evidence = FilteredEvidence(
        total_bytes=len(_HIT_TEXT),
        total_lines=1,
        hit_lines=[hit],
        hit_count=1,
        truncated=False,
        containers_sampled=["app"],
    )
    return {"filtered_evidence": evidence}  # type: ignore[typeddict-item]


def _mock_llm_returning(parsed: _ExpertOutput) -> MagicMock:
    """Mock build_expert_llm() to return a chain whose .invoke yields parsed."""
    raw_message = MagicMock()
    raw_message.usage_metadata = {"total_tokens": 42}
    raw_message.response_metadata = {"model_name": "mock-model"}

    structured_chain = MagicMock()
    structured_chain.invoke.return_value = {
        "raw": raw_message,
        "parsed": parsed,
        "parsing_error": None,
    }

    llm = MagicMock()
    llm.with_structured_output.return_value = structured_chain
    return llm


# ---------------------------------------------------------------------------
# Synthetic subclasses for isolated testing.
# ---------------------------------------------------------------------------
class _AllCatalogExpert(BaseExpert):
    """Synthetic expert with _allowed_actions left at the default (full catalog)."""

    domain = "Application"
    _system_prompt = "test"  # non-empty so _run_real_diagnosis doesn't short-circuit

    def _stub_diagnosis(self, state: WorkflowState) -> ExpertDiagnosis:
        raise AssertionError("stub must not be called in these tests")


class _NarrowedExpert(BaseExpert):
    """Synthetic expert narrowed to {restart-pod} only."""

    domain = "Application"
    _system_prompt = "test"
    _allowed_actions: ClassVar[frozenset[str]] = frozenset({"restart-pod"})

    def _stub_diagnosis(self, state: WorkflowState) -> ExpertDiagnosis:
        raise AssertionError("stub must not be called in these tests")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_default_allowed_actions_is_full_catalog() -> None:
    """A subclass with no override accepts every catalog action.

    Regression guard: making the filter strict by default would silently
    break the Application and Database experts.
    """
    assert BaseExpert._allowed_actions == ACTION_TYPES
    assert _AllCatalogExpert._allowed_actions == ACTION_TYPES


def test_subclass_can_narrow_to_subset_and_filter_drops_others() -> None:
    """An out-of-subset (but in-catalog) action is dropped to proposed_fix=None."""
    state = _build_state()
    parsed = _ExpertOutput(
        root_cause_hypothesis="some hypothesis",
        cited_indices=[0],
        confidence="medium",
        runner_up_causes=[],
        proposed_action="scale-deployment",
        proposed_parameters={"to_replicas": 5},
    )

    expert = _NarrowedExpert()
    with patch(
        "src.agent.graph.nodes.experts._base.build_expert_llm",
        return_value=_mock_llm_returning(parsed),
    ):
        diag = expert._run_real_diagnosis(state)

    # Action was in-catalog and valid (validate_action accepted it) but
    # outside _allowed_actions ⇒ proposed_fix must be None.
    assert diag.proposed_fix is None
    # The diagnosis itself survives — only the fix is dropped.
    assert diag.confidence == "medium"
    assert len(diag.cited_evidence) == 1
    assert diag.cited_evidence[0].text == _HIT_TEXT
    assert diag.tokens == 42


def test_in_subset_action_passes_through() -> None:
    """An action that IS in the subset is kept and surfaces as a ProposedFix."""
    state = _build_state()
    parsed = _ExpertOutput(
        root_cause_hypothesis="some hypothesis",
        cited_indices=[0],
        confidence="medium",
        runner_up_causes=[],
        proposed_action="restart-pod",
        proposed_parameters={},
    )

    expert = _NarrowedExpert()
    with patch(
        "src.agent.graph.nodes.experts._base.build_expert_llm",
        return_value=_mock_llm_returning(parsed),
    ):
        diag = expert._run_real_diagnosis(state)

    assert diag.proposed_fix is not None
    assert diag.proposed_fix.action_type == "restart-pod"
    assert diag.proposed_fix.parameters == {}


def test_out_of_subset_action_logs_warning(caplog: pytest.LogCaptureFixture) -> None:
    """The drop emits a logger.warning that names the action and the subset."""
    state = _build_state()
    parsed = _ExpertOutput(
        root_cause_hypothesis="some hypothesis",
        cited_indices=[0],
        confidence="medium",
        runner_up_causes=[],
        proposed_action="delete-pod-to-reschedule",
        proposed_parameters={},
    )

    expert = _NarrowedExpert()
    with patch(
        "src.agent.graph.nodes.experts._base.build_expert_llm",
        return_value=_mock_llm_returning(parsed),
    ):
        with caplog.at_level(logging.WARNING, logger="src.agent.graph.nodes.experts._base"):
            diag = expert._run_real_diagnosis(state)

    assert diag.proposed_fix is None
    # Warning text mentions both the rejected action and the allowed subset.
    relevant = [
        r for r in caplog.records
        if "not in the per-domain allowed set" in r.getMessage()
    ]
    assert relevant, "expected a warning about out-of-subset action"
    msg = relevant[0].getMessage()
    assert "delete-pod-to-reschedule" in msg
    assert "restart-pod" in msg  # the allowed set is reported


def test_out_of_catalog_action_filtered_before_subset_check() -> None:
    """Non-catalog actions are dropped by validate_action; the subset filter
    is not reached and does not log a subset-specific warning."""
    state = _build_state()
    parsed = _ExpertOutput(
        root_cause_hypothesis="some hypothesis",
        cited_indices=[0],
        confidence="medium",
        runner_up_causes=[],
        proposed_action="iptables-flush",   # not in ActionType Literal
        proposed_parameters={},
    )

    expert = _NarrowedExpert()
    with patch(
        "src.agent.graph.nodes.experts._base.build_expert_llm",
        return_value=_mock_llm_returning(parsed),
    ):
        diag = expert._run_real_diagnosis(state)

    assert diag.proposed_fix is None
