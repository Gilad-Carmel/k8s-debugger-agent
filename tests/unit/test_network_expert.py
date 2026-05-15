"""
tests/unit/test_network_expert.py

Unit tests for NetworkExpert (spec 007 T009).

Each test mocks ChatOpenAI completely so the network expert can be
exercised without a live inference server. Coverage targets:
  - Happy path with restart-pod
  - Happy path with rollback-deployment
  - Out-of-subset action (scale-deployment) dropped
  - Out-of-catalog action (free-form) dropped
  - Empty cited_indices → demoted + fix dropped
  - Out-of-range cited_indices → first-hit pinned + demoted
  - LLM exception → _fallback_diagnosis path
  - Stub still functional (regression: stub uses an in-subset action)
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock, patch

from src.agent.graph.nodes.experts._base import _ExpertOutput
from src.agent.graph.nodes.experts.network import (
    _NETWORK_SYSTEM_PROMPT,
    NetworkExpert,
    network_expert_node,
)
from src.agent.graph.state import WorkflowState
from src.shared.schemas import (
    FilteredEvidence,
    LogExcerpt,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
_DNS_LINE = "Error: getaddrinfo ENOTFOUND api.svc.cluster.local"
_REFUSED_LINE = "dial tcp 10.0.0.42:5432: connect: connection refused"
_TLS_LINE = "tls: handshake failure: x509: certificate has expired"


def _evidence(lines: list[str]) -> FilteredEvidence:
    """Build a FilteredEvidence with one LogExcerpt per line."""
    hits = [
        LogExcerpt(
            timestamp=datetime(2026, 5, 15, 12, 0, i, tzinfo=UTC),
            container="app",
            text=t,
            byte_offset=i * 100,
        )
        for i, t in enumerate(lines)
    ]
    return FilteredEvidence(
        total_bytes=sum(len(t) for t in lines),
        total_lines=len(lines),
        hit_lines=hits,
        hit_count=len(hits),
        truncated=False,
        containers_sampled=["app"],
    )


def _state_with(lines: list[str]) -> WorkflowState:
    """A WorkflowState carrying one or more lines as filtered evidence."""
    return {"filtered_evidence": _evidence(lines)}  # type: ignore[typeddict-item]


def _mock_llm_returning(parsed: _ExpertOutput | None, parse_error: Any = None) -> MagicMock:
    """Return a mocked ChatOpenAI whose with_structured_output(...).invoke yields parsed."""
    raw = MagicMock()
    raw.usage_metadata = {"total_tokens": 117}
    raw.response_metadata = {"model_name": "mock-network-model"}

    chain = MagicMock()
    chain.invoke.return_value = {
        "raw": raw,
        "parsed": parsed,
        "parsing_error": parse_error,
    }
    llm = MagicMock()
    llm.with_structured_output.return_value = chain
    return llm


def _mock_llm_raising() -> MagicMock:
    """A mocked LLM whose .invoke raises — exercises the fail-closed path."""
    chain = MagicMock()
    chain.invoke.side_effect = RuntimeError("inference server unreachable")
    llm = MagicMock()
    llm.with_structured_output.return_value = chain
    return llm


# ---------------------------------------------------------------------------
# Wiring & metadata
# ---------------------------------------------------------------------------

def test_network_expert_metadata_is_correct() -> None:
    """Class-level invariants: domain, prompt set, subset narrowed."""
    assert NetworkExpert.domain == "Network"
    assert NetworkExpert._system_prompt == _NETWORK_SYSTEM_PROMPT
    assert _NETWORK_SYSTEM_PROMPT.strip()  # non-empty
    assert NetworkExpert._allowed_actions == frozenset(
        {"restart-pod", "rollback-deployment"}
    )


def test_module_level_node_is_an_instance() -> None:
    assert isinstance(network_expert_node, NetworkExpert)


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------

def test_dns_failure_with_restart_pod() -> None:
    """DNS signal → restart-pod, citation grounded."""
    state = _state_with([_DNS_LINE])
    parsed = _ExpertOutput(
        root_cause_hypothesis=(
            "DNS resolution failure (getaddrinfo ENOTFOUND) consistent with "
            "a stale pod-local resolver cache."
        ),
        cited_indices=[0],
        confidence="medium",
        runner_up_causes=["transient CoreDNS upstream"],
        proposed_action="restart-pod",
        proposed_parameters={},
    )

    with patch(
        "src.agent.graph.nodes.experts._base.build_expert_llm",
        return_value=_mock_llm_returning(parsed),
    ):
        diag = network_expert_node._run_real_diagnosis(state)

    assert diag.domain == "Network"
    assert diag.confidence == "medium"
    assert diag.proposed_fix is not None
    assert diag.proposed_fix.action_type == "restart-pod"
    assert diag.proposed_fix.parameters == {}
    assert diag.proposed_fix.permission_scope == "sa-restart-pod"
    # Citation is verbatim from the evidence list.
    assert len(diag.cited_evidence) == 1
    assert diag.cited_evidence[0].text == _DNS_LINE
    assert diag.tokens == 117
    assert diag.model == "mock-network-model"


def test_connection_refused_with_rollback_deployment() -> None:
    """Connection-refused signal + deploy-link → rollback-deployment with revision."""
    state = _state_with([_REFUSED_LINE])
    parsed = _ExpertOutput(
        root_cause_hypothesis=(
            "Upstream backend connection refused after a recent Deployment "
            "rollout; rolling back to revision 7 should restore connectivity."
        ),
        cited_indices=[0],
        confidence="high",
        runner_up_causes=["upstream pod crash"],
        proposed_action="rollback-deployment",
        proposed_parameters={"to_revision": 7},
    )

    with patch(
        "src.agent.graph.nodes.experts._base.build_expert_llm",
        return_value=_mock_llm_returning(parsed),
    ):
        diag = network_expert_node._run_real_diagnosis(state)

    assert diag.proposed_fix is not None
    assert diag.proposed_fix.action_type == "rollback-deployment"
    assert diag.proposed_fix.parameters == {"to_revision": 7}
    assert diag.proposed_fix.permission_scope == "sa-rollback-deployment"
    assert diag.confidence == "high"


def test_tls_handshake_with_null_fix_is_accepted() -> None:
    """TLS-from-Secret is non-automatable in MVP → proposed_action=None is fine."""
    state = _state_with([_TLS_LINE])
    parsed = _ExpertOutput(
        root_cause_hypothesis=(
            "TLS handshake failure with an expired x509 certificate; the "
            "certificate is rooted in a Secret and requires manual rotation."
        ),
        cited_indices=[0],
        confidence="medium",
        runner_up_causes=["wrong CA bundle in image"],
        proposed_action=None,
        proposed_parameters={},
    )

    with patch(
        "src.agent.graph.nodes.experts._base.build_expert_llm",
        return_value=_mock_llm_returning(parsed),
    ):
        diag = network_expert_node._run_real_diagnosis(state)

    assert diag.proposed_fix is None
    assert diag.confidence == "medium"
    # Cited evidence is still mandatory (Principle IV, spec FR-012).
    assert len(diag.cited_evidence) == 1
    assert diag.cited_evidence[0].text == _TLS_LINE


# ---------------------------------------------------------------------------
# Catalog & subset filtering
# ---------------------------------------------------------------------------

def test_out_of_subset_scale_deployment_dropped() -> None:
    """scale-deployment is in the catalog but NOT in the network subset."""
    state = _state_with([_REFUSED_LINE])
    parsed = _ExpertOutput(
        root_cause_hypothesis="load exceeded capacity, scaling up.",
        cited_indices=[0],
        confidence="high",
        runner_up_causes=[],
        proposed_action="scale-deployment",
        proposed_parameters={"to_replicas": 5},
    )

    with patch(
        "src.agent.graph.nodes.experts._base.build_expert_llm",
        return_value=_mock_llm_returning(parsed),
    ):
        diag = network_expert_node._run_real_diagnosis(state)

    assert diag.proposed_fix is None  # subset filter drops it
    # Diagnosis otherwise intact — confidence preserved, citation grounded.
    assert diag.confidence == "high"
    assert diag.cited_evidence[0].text == _REFUSED_LINE


def test_out_of_subset_delete_pod_dropped() -> None:
    """delete-pod-to-reschedule is in the catalog but not in the network subset."""
    state = _state_with([_DNS_LINE])
    parsed = _ExpertOutput(
        root_cause_hypothesis="reschedule the pod onto a fresh node.",
        cited_indices=[0],
        confidence="medium",
        runner_up_causes=[],
        proposed_action="delete-pod-to-reschedule",
        proposed_parameters={},
    )

    with patch(
        "src.agent.graph.nodes.experts._base.build_expert_llm",
        return_value=_mock_llm_returning(parsed),
    ):
        diag = network_expert_node._run_real_diagnosis(state)

    assert diag.proposed_fix is None


def test_out_of_catalog_action_dropped() -> None:
    """Free-form (out-of-catalog) actions are rejected by validate_action."""
    state = _state_with([_REFUSED_LINE])
    parsed = _ExpertOutput(
        root_cause_hypothesis="apply a NetworkPolicy patch.",
        cited_indices=[0],
        confidence="high",
        runner_up_causes=[],
        proposed_action="apply-networkpolicy",  # not in ActionType
        proposed_parameters={"yaml": "..."},
    )

    with patch(
        "src.agent.graph.nodes.experts._base.build_expert_llm",
        return_value=_mock_llm_returning(parsed),
    ):
        diag = network_expert_node._run_real_diagnosis(state)

    assert diag.proposed_fix is None


def test_rollback_without_revision_is_dropped() -> None:
    """rollback-deployment without to_revision int → drop the fix."""
    state = _state_with([_REFUSED_LINE])
    parsed = _ExpertOutput(
        root_cause_hypothesis="recent deploy broke connectivity.",
        cited_indices=[0],
        confidence="medium",
        runner_up_causes=[],
        proposed_action="rollback-deployment",
        proposed_parameters={},  # missing to_revision
    )

    with patch(
        "src.agent.graph.nodes.experts._base.build_expert_llm",
        return_value=_mock_llm_returning(parsed),
    ):
        diag = network_expert_node._run_real_diagnosis(state)

    assert diag.proposed_fix is None


# ---------------------------------------------------------------------------
# Citation safety
# ---------------------------------------------------------------------------

def test_empty_cited_indices_demotes_and_drops_fix() -> None:
    """No citations → low confidence + fix dropped; first hit pinned (Principle IV)."""
    state = _state_with([_DNS_LINE, _REFUSED_LINE])
    parsed = _ExpertOutput(
        root_cause_hypothesis="vague claim with no citation.",
        cited_indices=[],
        confidence="high",
        runner_up_causes=[],
        proposed_action="restart-pod",
        proposed_parameters={},
    )

    with patch(
        "src.agent.graph.nodes.experts._base.build_expert_llm",
        return_value=_mock_llm_returning(parsed),
    ):
        diag = network_expert_node._run_real_diagnosis(state)

    assert diag.confidence == "low"
    assert diag.proposed_fix is None
    # First hit is pinned as the closest grounded citation.
    assert diag.cited_evidence[0].text == _DNS_LINE


def test_out_of_range_cited_indices_filtered_and_demoted() -> None:
    """Hallucinated index (7 against 2 hits) is filtered out → demoted."""
    state = _state_with([_DNS_LINE, _REFUSED_LINE])
    parsed = _ExpertOutput(
        root_cause_hypothesis="claim citing a non-existent line.",
        cited_indices=[7],
        confidence="high",
        runner_up_causes=[],
        proposed_action="restart-pod",
        proposed_parameters={},
    )

    with patch(
        "src.agent.graph.nodes.experts._base.build_expert_llm",
        return_value=_mock_llm_returning(parsed),
    ):
        diag = network_expert_node._run_real_diagnosis(state)

    assert diag.confidence == "low"
    assert diag.proposed_fix is None
    assert diag.cited_evidence[0].text == _DNS_LINE


# ---------------------------------------------------------------------------
# Fail-closed paths
# ---------------------------------------------------------------------------

def test_llm_exception_falls_back_to_low_confidence_no_fix() -> None:
    """LLM call raises → fallback diagnosis, never an exception escape."""
    state = _state_with([_DNS_LINE])

    with patch(
        "src.agent.graph.nodes.experts._base.build_expert_llm",
        return_value=_mock_llm_raising(),
    ):
        diag = network_expert_node._run_real_diagnosis(state)

    assert diag.domain == "Network"
    assert diag.confidence == "low"
    assert diag.proposed_fix is None
    assert len(diag.cited_evidence) >= 1  # spec FR-012


def test_no_evidence_falls_back_cleanly() -> None:
    """Empty filtered_evidence → fallback diagnosis (still cites synthetic placeholder)."""
    state: WorkflowState = {"filtered_evidence": None}  # type: ignore[typeddict-item]

    # No LLM call should happen, but patch defensively so a regression here
    # (calling the LLM with no evidence) produces a clear assertion.
    bad = MagicMock()
    bad.with_structured_output.side_effect = AssertionError(
        "LLM must not be called when there is no evidence"
    )
    with patch(
        "src.agent.graph.nodes.experts._base.build_expert_llm",
        return_value=bad,
    ):
        diag = network_expert_node._run_real_diagnosis(state)

    assert diag.confidence == "low"
    assert diag.proposed_fix is None
    assert len(diag.cited_evidence) == 1  # synthetic placeholder from _first_hit


def test_parse_failure_falls_back() -> None:
    """parsed is None + parsing_error set → fallback path, tokens still recorded."""
    state = _state_with([_DNS_LINE])

    with patch(
        "src.agent.graph.nodes.experts._base.build_expert_llm",
        return_value=_mock_llm_returning(None, parse_error=ValueError("bad JSON")),
    ):
        diag = network_expert_node._run_real_diagnosis(state)

    assert diag.confidence == "low"
    assert diag.proposed_fix is None
    assert diag.tokens == 117  # tokens still captured for budget accounting


# ---------------------------------------------------------------------------
# Stub regression
# ---------------------------------------------------------------------------

def test_stub_returns_in_subset_action() -> None:
    """The stub used to return delete-pod-to-reschedule (out-of-subset).
    Now it must return restart-pod (in subset) so scaffolding tests keep
    receiving a non-None proposed_fix."""
    diag = network_expert_node._stub_diagnosis({"filtered_evidence": None})

    assert diag.domain == "Network"
    assert diag.proposed_fix is not None
    assert diag.proposed_fix.action_type == "restart-pod"
    assert diag.proposed_fix.action_type in NetworkExpert._allowed_actions
