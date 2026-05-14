"""
src/shared/schemas.py

All Pydantic v2 entity models shared across the agent and MCP server packages.

Corresponds to data-model.md §Entities and tasks.md T016.
Depends on src/shared/labels.py (T012).

Design rules (data-model.md §Implementation note):
  - Field types and validation rules here are normative.
  - Frozen models use model_config = ConfigDict(frozen=True).
  - CorrelationId is str (UUIDv7 string); no UUID type so LangGraph can
    serialise directly to JSON without a custom encoder.
  - datetime fields carry timezone info (UTC); any naive datetime is rejected.

Python 3.9 compatibility note:
  - Use Optional[X] / Union[X, Y] from typing instead of X | None / X | Y
    syntax (the latter requires Python 3.10+ at *runtime* even with
    from __future__ import annotations, because pydantic evaluates annotation
    strings via eval_type_backport which still hits the stdlib restriction).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.shared.labels import (
    ActionType,
    ApprovalAction,
    Confidence,
    Domain,
    ReportStatus,
    SolverOutcome,
)

# ---------------------------------------------------------------------------
# Type alias
# ---------------------------------------------------------------------------
CorrelationId = str  # UUIDv7 string; kept as str for JSON-native serialisation


# ---------------------------------------------------------------------------
# 1. Target  (frozen)
# ---------------------------------------------------------------------------
class Target(BaseModel):
    """Kubernetes target: namespace + pod + optional container."""

    model_config = ConfigDict(frozen=True)

    namespace: str = Field(..., description="Kubernetes namespace (RFC-1123).")
    pod: str = Field(..., description="Pod name (RFC-1123).")
    container: Optional[str] = Field(
        default=None,
        description="Container name. If omitted all containers are sampled.",
    )

    @field_validator("namespace", "pod")
    @classmethod
    def _rfc1123(cls, v: str) -> str:
        """Reject obviously invalid k8s names (empty, leading/trailing hyphens)."""
        if not v or v.startswith("-") or v.endswith("-"):
            raise ValueError(f"Invalid RFC-1123 label: {v!r}")
        return v


# ---------------------------------------------------------------------------
# 2. TimeWindow  (frozen)
# ---------------------------------------------------------------------------
class TimeWindow(BaseModel):
    """Inclusive start / exclusive end for log / event queries."""

    model_config = ConfigDict(frozen=True)

    start: datetime = Field(..., description="Inclusive window start (UTC).")
    end: datetime = Field(..., description="Exclusive window end (UTC).")

    @model_validator(mode="after")
    def _start_before_end(self) -> "TimeWindow":
        if self.start >= self.end:
            raise ValueError("TimeWindow.start must be strictly before end.")
        return self


# ---------------------------------------------------------------------------
# 3. LogExcerpt  (frozen)
# ---------------------------------------------------------------------------
class LogExcerpt(BaseModel):
    """A single redacted log line with provenance metadata."""

    model_config = ConfigDict(frozen=True)

    timestamp: datetime = Field(
        ..., description="Parsed from the log line; falls back to fetch time."
    )
    container: str = Field(..., description="Container the line came from.")
    text: str = Field(..., description="The log line. Redacted at the MCP boundary.")
    byte_offset: int = Field(
        ..., ge=0, description="Byte offset into the original log stream."
    )


# ---------------------------------------------------------------------------
# 4. FilteredEvidence
# ---------------------------------------------------------------------------
class FilteredEvidence(BaseModel):
    """Output of the contextual pre-filter inside search_pod_logs."""

    total_bytes: int = Field(..., ge=0, description="Bytes returned by the K8s log API before filtering.")
    total_lines: int = Field(..., ge=0, description="Lines before filtering.")
    hit_lines: List[LogExcerpt] = Field(
        default_factory=list,
        description="Lines matching the pattern set. Capped at max_hit_lines.",
    )
    hit_count: int = Field(
        ...,
        ge=0,
        description=(
            "len(hit_lines) unless truncated=True, where it reflects the "
            "pre-truncation count."
        ),
    )
    truncated: bool = Field(
        default=False,
        description="True if hits exceeded the cap.",
    )
    containers_sampled: List[str] = Field(
        default_factory=list,
        description="Container instances whose logs were fetched.",
    )


# ---------------------------------------------------------------------------
# 5. RoutingDecision
# ---------------------------------------------------------------------------
class RoutingDecision(BaseModel):
    """Structured output from the Router node (Haiku-tier)."""

    domain: Domain
    confidence: Confidence
    cited_evidence: List[LogExcerpt] = Field(
        default_factory=list,
        description=">=1 unless domain=='Unknown'.",
    )
    runners_up: List[Tuple[Domain, Confidence]] = Field(
        default_factory=list,
        description="Other domains considered, descending confidence.",
    )
    model: str = Field(..., description="Model ID used (audit).")
    tokens: int = Field(..., ge=0, description="Total tokens consumed (audit).")

    @model_validator(mode="after")
    def _cited_evidence_when_known(self) -> "RoutingDecision":
        if self.domain != "Unknown" and not self.cited_evidence:
            raise ValueError(
                "cited_evidence must contain >=1 item when domain != 'Unknown'."
            )
        return self


# ---------------------------------------------------------------------------
# 6. ProposedFix  (frozen once shown in Report — FR-016 / FR-020)
# Declared before ExpertDiagnosis because ExpertDiagnosis references it.
# ---------------------------------------------------------------------------
class ProposedFix(BaseModel):
    """A catalog-bound fix proposed by the Expert and frozen into the Report."""

    model_config = ConfigDict(frozen=True)

    action_type: ActionType
    target: Target
    parameters: Dict[str, Any] = Field(
        default_factory=dict,
        description="Action-specific params; schema validated in shared/catalog.py.",
    )
    permission_scope: str = Field(
        ..., description="ServiceAccount identifier the MCP write tool will use."
    )
    fingerprint: str = Field(
        ...,
        description=(
            "SHA-256 over canonical JSON of (action_type, target, parameters). "
            "Used by Solver to verify nothing changed between approval and execution."
        ),
    )

    @classmethod
    def build(
        cls,
        action_type: ActionType,
        target: Target,
        parameters: Dict[str, Any],
        permission_scope: str,
    ) -> "ProposedFix":
        """Factory that computes the fingerprint deterministically."""
        canonical = json.dumps(
            {
                "action_type": action_type,
                "target": target.model_dump(),
                "parameters": parameters,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        fingerprint = hashlib.sha256(canonical.encode()).hexdigest()
        return cls(
            action_type=action_type,
            target=target,
            parameters=parameters,
            permission_scope=permission_scope,
            fingerprint=fingerprint,
        )


# ---------------------------------------------------------------------------
# 7. ExpertDiagnosis
# ---------------------------------------------------------------------------
class ExpertDiagnosis(BaseModel):
    """Structured output from a domain Expert node (Sonnet-tier)."""

    domain: Domain
    root_cause_hypothesis: str = Field(
        ..., description="One sentence, user-readable root-cause hypothesis."
    )
    cited_evidence: List[LogExcerpt] = Field(
        ...,
        min_length=1,
        description="MUST be >=1 (Principle IV, NON-NEGOTIABLE).",
    )
    confidence: Confidence
    runner_up_causes: List[str] = Field(
        default_factory=list,
        description="Alternative hypotheses considered.",
    )
    proposed_fix: Optional[ProposedFix] = Field(
        default=None,
        description="None means no automatic fix is available.",
    )
    model: str = Field(..., description="Model ID used (audit).")
    tokens: int = Field(..., ge=0)


# ---------------------------------------------------------------------------
# 8. ReversalRecipe  (frozen)
# ---------------------------------------------------------------------------
class ReversalRecipe(BaseModel):
    """
    The Inverse Action computed by the Solver at execution time from pre_state.

    Terminology: spec.md / plan.md use "Inverse Action", constitution.md uses
    "reversal recipe"; ReversalRecipe is the canonical Python class name.
    """

    model_config = ConfigDict(frozen=True)

    description: str = Field(
        ...,
        description=(
            "Human-readable description, e.g. 'Re-scale to 3 replicas' or "
            "'No automated undo — restart was self-recovering.'"
        ),
    )
    inverse_action: Optional[Union[ActionType, str]] = Field(
        ...,
        description=(
            "ActionType that undoes this one; None for transient/self-recovering "
            "actions (restart-pod, delete-pod-to-reschedule); 'manual' if no "
            "clean automated inverse exists."
        ),
    )
    inverse_parameters: Dict[str, Any] = Field(
        default_factory=dict,
        description="Parameters for inverse_action. Empty when inverse_action is None or 'manual'.",
    )


# ---------------------------------------------------------------------------
# 9. Report
# ---------------------------------------------------------------------------
class Report(BaseModel):
    """The single user-facing artifact assembled by the Reporter node."""

    correlation_id: CorrelationId
    routing: RoutingDecision
    diagnosis: Optional[ExpertDiagnosis] = Field(
        default=None,
        description="None iff routing.domain == 'Unknown'.",
    )
    proposed_fix: Optional[ProposedFix] = Field(
        default=None,
        description="Mirrors diagnosis.proposed_fix for rendering convenience.",
    )
    status: ReportStatus = Field(default="pending")
    delivered_at: datetime = Field(
        ..., description="Wall-clock when the chat surface acknowledged receipt."
    )
    approval_deadline: datetime = Field(
        ..., description="delivered_at + 30min by default; tenant-configurable."
    )
    runner_up_domains: List[Tuple[Domain, Confidence]] = Field(
        default_factory=list,
        description="Copied from routing.runners_up for the chat caveat block.",
    )

    @model_validator(mode="after")
    def _consistency(self) -> "Report":
        if self.routing.domain == "Unknown" and self.diagnosis is not None:
            raise ValueError("diagnosis must be None when domain is 'Unknown'.")
        if (
            self.diagnosis is not None
            and self.proposed_fix != self.diagnosis.proposed_fix
        ):
            raise ValueError(
                "proposed_fix must mirror diagnosis.proposed_fix exactly."
            )
        return self


# ---------------------------------------------------------------------------
# 10. ApprovalEvent
# ---------------------------------------------------------------------------
class ApprovalEvent(BaseModel):
    """One approval or rejection click (or an expiration record)."""

    correlation_id: CorrelationId
    action: ApprovalAction
    actor_id: str = Field(..., description="Approver identity from the callback.")
    actor_roles: List[str] = Field(
        default_factory=list,
        description="Roles held at click time (audit-relevant).",
    )
    role_check_passed: bool = Field(
        ...,
        description=(
            "False => no status transition, but the event IS recorded "
            "(audit / abuse signal)."
        ),
    )
    reason: Optional[str] = Field(
        default=None,
        description="Optional free-text supplied by the approver.",
    )
    at: datetime = Field(..., description="ISO-8601 timestamp.")


# ---------------------------------------------------------------------------
# 11. SolverRun
# ---------------------------------------------------------------------------
class SolverRun(BaseModel):
    """Record of one deterministic Solver execution (NO LLM — research.md §R2)."""

    correlation_id: CorrelationId
    proposed_fix_fingerprint: str = Field(
        ...,
        description=(
            "Must equal Report.proposed_fix.fingerprint. "
            "Solver MUST refuse on mismatch (FR-020)."
        ),
    )
    pre_state: Dict[str, Any] = Field(
        ...,
        description="Snapshot from MCP read tools before the action (FR-022).",
    )
    action_issued: Dict[str, Any] = Field(
        ..., description="Canonical JSON of the action sent to MCP."
    )
    post_state: Dict[str, Any] = Field(
        ..., description="Snapshot after the verification window."
    )
    outcome: SolverOutcome
    reversal_recipe: ReversalRecipe = Field(
        ...,
        description=(
            "Inverse Action computed at execution time from pre_state "
            "via the fixed catalog mapping."
        ),
    )
    error: Optional[str] = Field(
        default=None, description="Populated on outcome=='failure'."
    )
    started_at: datetime
    finished_at: datetime


# ---------------------------------------------------------------------------
# 12. Incident
# ---------------------------------------------------------------------------
class Incident(BaseModel):
    """
    Root entity — one per logical alert.

    Lives for the entire triage + remediation + audit-replay lifetime.
    """

    correlation_id: CorrelationId = Field(..., description="Primary key. Stable across all stages.")
    source_alert_id: str = Field(
        ..., description="Upstream alerting system's ID (Alertmanager groupKey)."
    )
    dedup_fingerprint: str = Field(
        ...,
        description=(
            "SHA-256 over (alert_id, namespace, pod, 10-min bucket). "
            "Duplicate webhooks update last_seen_at only (research.md §R12)."
        ),
    )
    target: Target
    time_window: TimeWindow
    received_at: datetime = Field(..., description="Timestamp the webhook arrived.")
    last_seen_at: datetime = Field(
        ..., description="Updated on duplicate webhooks within the dedup window."
    )
    status: ReportStatus = Field(
        default="pending",
        description="Mirrors Report.status for O(1) lookup without a join.",
    )


# ---------------------------------------------------------------------------
# 13. ContainerState  (frozen) — per-container runtime state from get_pod
# ---------------------------------------------------------------------------
class ContainerState(BaseModel):
    """Runtime state of a single container, as returned by the Kubernetes API."""

    model_config = ConfigDict(frozen=True)

    state: str = Field(
        ...,
        description="One of 'Waiting', 'Running', 'Terminated'.",
    )
    reason: Optional[str] = Field(
        default=None,
        description="CrashLoopBackOff, OOMKilled, Completed, etc.",
    )
    message: Optional[str] = Field(default=None)
    exit_code: Optional[int] = Field(default=None)
    started_at: Optional[datetime] = Field(default=None)
    finished_at: Optional[datetime] = Field(default=None)


# ---------------------------------------------------------------------------
# 14. PodSnapshot  — pre/post state captured by MCP write tools (FR-022)
# ---------------------------------------------------------------------------
class PodSnapshot(BaseModel):
    """Immutable point-in-time snapshot of a pod used for pre/post verification."""

    phase: str = Field(
        ...,
        description="Pending | Running | Succeeded | Failed | Unknown",
    )
    restart_count_by_ctr: Dict[str, int] = Field(
        default_factory=dict,
        description="restart_count per container name.",
    )
    container_states: Dict[str, ContainerState] = Field(
        default_factory=dict,
        description="ContainerState per container name.",
    )
    ready: bool = Field(..., description="True if all containers are Ready.")
    resource_version: str = Field(
        ..., description="K8s resourceVersion for optimistic-concurrency reads."
    )
    observed_at: datetime = Field(..., description="Wall-clock when snapshot was taken.")


# ---------------------------------------------------------------------------
# 15. WriteToolOutput  — output shape shared across all MCP write tools
# ---------------------------------------------------------------------------
class WriteToolOutput(BaseModel):
    """Normalised result returned by every MCP write tool."""

    outcome: str = Field(
        ...,
        description="'applied' | 'refused' | 'error'",
    )
    pre_state: Dict[str, Any] = Field(
        ...,
        description="Snapshot before the action (FR-022).",
    )
    post_state: Dict[str, Any] = Field(
        ...,
        description="Snapshot after the verification window.",
    )
    reversal_recipe: ReversalRecipe = Field(
        ...,
        description="Inverse Action computed at execution time from pre_state.",
    )
    error: Optional[str] = Field(
        default=None,
        description="Populated on outcome == 'error'.",
    )


# ---------------------------------------------------------------------------
# 16. ToolError  — machine-readable error returned by any MCP tool call
# ---------------------------------------------------------------------------
class ToolError(BaseModel):
    """
    Single user-facing error template (Principle VIII).

    machine_token is stable across releases and localisation changes.
    human_message is what appears in the chat report or logs.
    """

    machine_token: str = Field(
        ...,
        description="Snake_case stable error identifier, e.g. 'admission_denied'.",
    )
    human_message: str = Field(
        ..., description="One sentence: what failed / why / what to try next."
    )
    detail: Dict[str, Any] = Field(
        default_factory=dict,
        description="Optional structured context for debugging.",
    )
