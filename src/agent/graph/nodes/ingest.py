"""
src/agent/graph/nodes/ingest.py

Ingest node — Phase 1 STUB.

Real behaviour (T045):
  - Emit TTFT acknowledgement to slack-mock (before any LLM call).
  - Call search_pod_logs, get_pod_events, get_pod concurrently via MCP.
  - Populate filtered_evidence on WorkflowState.

This stub hardcodes a minimal FilteredEvidence so the graph can be exercised
end-to-end without any external dependencies.
"""

from __future__ import annotations

from datetime import datetime, timezone

from src.agent.graph.state import WorkflowState
from src.shared.schemas import FilteredEvidence, LogExcerpt


def ingest_node(state: WorkflowState) -> WorkflowState:
    """
    STUB: populate filtered_evidence with a fake log hit.

    Returns a partial WorkflowState containing only the keys this node sets.
    LangGraph merges the return value into the accumulating state.

    Pass-through: if ``filtered_evidence`` is already present in the incoming
    state (e.g. pre-populated by a smoke-test or integration fixture), the
    stub preserves it.  This lets ``test_graph_run.py`` inject custom evidence
    without the ingest stub silently replacing it.
    """
    existing = state.get("filtered_evidence")
    if existing is not None:
        print(
            f"[ingest_node] STUB - evidence already present "
            f"({existing.hit_count} hits); preserving caller evidence"
        )
        return {}  # type: ignore[return-value]

    print("[ingest_node] STUB - populating fake filtered_evidence")

    fake_line = LogExcerpt(
        timestamp=datetime(2026, 5, 14, 10, 0, 0, tzinfo=timezone.utc),
        container="app",
        text="Connection refused to db-service:5432 after 3 retries",
        byte_offset=0,
    )
    evidence = FilteredEvidence(
        total_bytes=4096,
        total_lines=200,
        hit_lines=[fake_line],
        hit_count=1,
        truncated=False,
        containers_sampled=["app"],
    )

    return {"filtered_evidence": evidence}  # type: ignore[return-value]
