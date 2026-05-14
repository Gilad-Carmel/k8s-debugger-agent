"""
src/agent/graph/nodes/experts/_base.py

Shared Expert protocol and prompt-builder base — Phase 1 STUB.

Real behaviour (T047):
  - Define abstract Expert interface.
  - Shared prompt construction from FilteredEvidence.
  - Sonnet-tier LLM call with structured ExpertDiagnosis output.
  - Hallucination guard: every claim cites an excerpt present in hit_lines.

This stub provides the base class API that the three Expert stubs extend.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone

from src.agent.graph.state import WorkflowState
from src.shared.labels import Domain
from src.shared.schemas import ExpertDiagnosis, LogExcerpt, ProposedFix


class BaseExpert(ABC):
    """Abstract base for Application / Network / Database Expert nodes."""

    domain: Domain

    def __call__(self, state: WorkflowState) -> WorkflowState:
        """Entry point called by LangGraph."""
        print(f"[{self.domain.lower()}_expert] STUB — returning hardcoded ExpertDiagnosis")
        diagnosis = self._stub_diagnosis(state)
        return {"diagnosis": diagnosis}  # type: ignore[return-value]

    @abstractmethod
    def _stub_diagnosis(self, state: WorkflowState) -> ExpertDiagnosis:
        """Return a hardcoded ExpertDiagnosis for scaffolding runs."""

    @staticmethod
    def _first_hit(state: WorkflowState) -> LogExcerpt:
        """Return the first log hit from state, or a synthetic one."""
        evidence = state.get("filtered_evidence")
        if evidence and evidence.hit_lines:
            return evidence.hit_lines[0]
        return LogExcerpt(
            timestamp=datetime(2026, 5, 14, 10, 0, 0, tzinfo=timezone.utc),
            container="app",
            text="[stub] No evidence available",
            byte_offset=0,
        )
