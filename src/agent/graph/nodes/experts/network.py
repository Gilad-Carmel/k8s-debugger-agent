"""
src/agent/graph/nodes/experts/network.py

Network Expert node — Phase 1 STUB.
"""

from __future__ import annotations

from src.agent.graph.state import WorkflowState
from src.shared.schemas import ExpertDiagnosis, ProposedFix
from src.agent.graph.nodes.experts._base import BaseExpert


class NetworkExpert(BaseExpert):
    domain = "Network"

    def _stub_diagnosis(self, state: WorkflowState) -> ExpertDiagnosis:
        hit = self._first_hit(state)
        target = state.get("incident", None)
        if target is not None:
            fix_target = target.target
        else:
            from src.shared.schemas import Target
            fix_target = Target(namespace="default", pod="app-pod-xyz")

        proposed_fix = ProposedFix.build(
            action_type="delete-pod-to-reschedule",
            target=fix_target,
            parameters={},
            permission_scope="sa-delete-pod",
        )

        return ExpertDiagnosis(
            domain="Network",
            root_cause_hypothesis=(
                "Pod is stuck on a dead node due to a network partition; "
                "rescheduling should restore connectivity."
            ),
            cited_evidence=[hit],
            confidence="high",
            runner_up_causes=["DNS misconfiguration", "NetworkPolicy blocking egress"],
            proposed_fix=proposed_fix,
            model="stub-sonnet",
            tokens=0,
        )


network_expert_node = NetworkExpert()
