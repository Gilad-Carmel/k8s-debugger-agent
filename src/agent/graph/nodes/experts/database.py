"""
src/agent/graph/nodes/experts/database.py

Database Expert node — Phase 1 STUB.
"""

from __future__ import annotations

from src.agent.graph.state import WorkflowState
from src.shared.schemas import ExpertDiagnosis, ProposedFix
from src.agent.graph.nodes.experts._base import BaseExpert


class DatabaseExpert(BaseExpert):
    domain = "Database"

    def _stub_diagnosis(self, state: WorkflowState) -> ExpertDiagnosis:
        hit = self._first_hit(state)
        target = state.get("incident", None)
        if target is not None:
            fix_target = target.target
        else:
            from src.shared.schemas import Target
            fix_target = Target(namespace="default", pod="db-pod-xyz")

        proposed_fix = ProposedFix.build(
            action_type="rollback-deployment",
            target=fix_target,
            parameters={"to_revision": 3, "deployment": "db-deployment"},
            permission_scope="sa-rollback-deployment",
        )

        return ExpertDiagnosis(
            domain="Database",
            root_cause_hypothesis=(
                "Database connection pool exhausted after a bad schema migration "
                "introduced a long-running query pattern."
            ),
            cited_evidence=[hit],
            confidence="high",
            runner_up_causes=["misconfigured pool size", "upstream traffic spike"],
            proposed_fix=proposed_fix,
            model="stub-sonnet",
            tokens=0,
        )


database_expert_node = DatabaseExpert()
