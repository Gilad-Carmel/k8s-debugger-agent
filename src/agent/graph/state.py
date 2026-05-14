"""
src/agent/graph/state.py

WorkflowState TypedDict — the shared state object passed between every
LangGraph node in the triage-and-remediation graph.

Corresponds to data-model.md §WorkflowState and tasks.md T025.
Depends on src/shared/schemas.py (T016) and src/shared/labels.py (T012).

Design notes:
  - TypedDict (not BaseModel) because LangGraph's StateGraph expects a plain
    dict-compatible type for its internal checkpointer serialisation.
  - total=False so every field is optional — LangGraph nodes only set the
    keys they are responsible for; prior keys remain untouched.
  - Invariants (enforced in unit tests, per Principle VII):
      * Once report is set, report.proposed_fix.fingerprint is immutable.
      * approval.correlation_id MUST equal correlation_id.
      * solver_run.proposed_fix_fingerprint MUST equal
        report.proposed_fix.fingerprint.
      * budget_remaining_tokens and budget_remaining_usd_micros are
        monotonically non-increasing.
"""

from __future__ import annotations

from typing import TypedDict

from src.shared.schemas import (
    ApprovalEvent,
    CorrelationId,
    FilteredEvidence,
    Incident,
    Report,
    RoutingDecision,
    ExpertDiagnosis,
    SolverRun,
)


class WorkflowState(TypedDict, total=False):
    """
    Mutable state threaded through every node of the LangGraph workflow.

    Key lifecycle:
      Ingest  → sets: correlation_id, incident, filtered_evidence
      Router  → sets: routing
      Expert  → sets: diagnosis
      Reporter→ sets: report
      (interrupt — awaits approval callback)
      Solver  → sets: solver_run
      budget fields decremented at every LLM call site
    """

    # ------------------------------------------------------------------
    # Identity — set once in Ingest, never overwritten (data-model §WorkflowState)
    # ------------------------------------------------------------------
    correlation_id: CorrelationId

    # ------------------------------------------------------------------
    # Ingest outputs
    # ------------------------------------------------------------------
    incident: Incident
    filtered_evidence: FilteredEvidence

    # ------------------------------------------------------------------
    # Router output
    # ------------------------------------------------------------------
    routing: RoutingDecision

    # ------------------------------------------------------------------
    # Expert output  (None when routing.domain == "Unknown")
    # ------------------------------------------------------------------
    diagnosis: ExpertDiagnosis

    # ------------------------------------------------------------------
    # Reporter output  (mutated for status transitions by the callback handler)
    # ------------------------------------------------------------------
    report: Report

    # ------------------------------------------------------------------
    # Approval — hydrated by the /callbacks/slack/approve|reject handler
    # after the LangGraph interrupt resumes
    # ------------------------------------------------------------------
    approval: ApprovalEvent

    # ------------------------------------------------------------------
    # Solver output  (set only when approval.role_check_passed == True and
    # approval.action == "approve")
    # ------------------------------------------------------------------
    solver_run: SolverRun

    # ------------------------------------------------------------------
    # Budget tracking — decremented by src/agent/budget.py at every LLM call
    # (spec FR-029; fail-closed when either reaches 0)
    # ------------------------------------------------------------------
    budget_remaining_tokens: int
    budget_remaining_usd_micros: int

    # ------------------------------------------------------------------
    # HITL routing discriminant set by the /callbacks/slack/* handler
    # (or by the expiry watcher) immediately before the graph resumes
    # from interrupt_before=['solver']. Values: 'APPROVED' | 'REJECTED'
    # | 'EXPIRED'. The post-interrupt conditional edge keys on this and
    # this only — when REJECTED or EXPIRED the graph terminates without
    # invoking the Solver. See builder.py and api/callbacks.py.
    # ------------------------------------------------------------------
    approval_status: str

    # HMAC approval token issued at approve-time and written into state so
    # the Solver pre-flight can verify that no ProposedFix mutation happened
    # between approval and execution. Only present on APPROVED resumes;
    # absent (empty string) on REJECTED / EXPIRED paths.
    approval_token: str

    # Raw Alertmanager body, preserved verbatim after HMAC verify + dedup
    # so the full triage can be replayed from audit. Set by Ingest (or
    # by the webhook handler when it kicks the graph off).
    alert_payload: dict
