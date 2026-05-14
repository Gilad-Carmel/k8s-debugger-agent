"""
src/agent/graph/nodes/experts/application.py

Application Expert node — Phase 1 STUB.

Real behaviour (T048):
  - Sonnet-tier LLM call with structured ExpertDiagnosis output.
  - Cited evidence must be a subset of FilteredEvidence.hit_lines.
  - ProposedFix from the allowed-remediation catalog.

This stub returns a hardcoded ExpertDiagnosis without calling an LLM.
"""

from __future__ import annotations

from src.agent.graph.state import WorkflowState
from src.shared.schemas import ExpertDiagnosis, ProposedFix
from src.agent.graph.nodes.experts._base import BaseExpert


class ApplicationExpert(BaseExpert):
    domain = "Application"

    def _stub_diagnosis(self, state: WorkflowState) -> ExpertDiagnosis:
        hit = self._first_hit(state)
        target = state.get("incident", None)
        if target is not None:
            fix_target = target.target
        else:
            from src.shared.schemas import Target
            fix_target = Target(namespace="default", pod="app-pod-xyz")

        proposed_fix = ProposedFix.build(
            action_type="restart-pod",
            target=fix_target,
            parameters={},
            permission_scope="sa-restart-pod",
        )

        return ExpertDiagnosis(
            domain="Application",
            root_cause_hypothesis=(
                "Application container is crash-looping due to an "
                "unhandled exception on startup."
            ),
            cited_evidence=[hit],
            confidence="medium",
            runner_up_causes=["OOMKilled", "readiness probe misconfiguration"],
            proposed_fix=proposed_fix,
            model="stub-sonnet",
            tokens=0,
        )


# Module-level callable for LangGraph node registration
application_expert_node = ApplicationExpert()
