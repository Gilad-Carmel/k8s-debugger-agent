"""
src/agent/graph/nodes/experts/_base.py

Shared Expert protocol and prompt-builder — T047.

Provides all domain-agnostic infrastructure used by the three Expert nodes
(Application, Network, Database):

  - Module-level utilities: ``format_evidence``, ``format_router_context``,
    ``build_expert_llm``, ``validate_action``, ``resolve_target``.
  - ``_ExpertOutput`` — the Pydantic schema bound to every Expert LLM call.
  - ``PERMISSION_SCOPES`` — ActionType → ServiceAccount mapping (single
    source of truth; changing it requires the catalog two-reviewer rule,
    Principle VI + §Development Workflow & Quality Gates).
  - ``BaseExpert`` — abstract base class that:
      * Provides a concrete ``__call__`` and ``_run_real_diagnosis`` so
        subclasses only need to declare ``domain``, ``_system_prompt``, and a
        ``_stub_diagnosis`` scaffold.
      * Enforces the Constitution IV hallucination guard on every real
        diagnosis path: every cited ``LogExcerpt`` MUST be identity-traceable
        to a row in ``FilteredEvidence.hit_lines``.
      * Provides ``_fallback_diagnosis`` for the three failure modes (no
        evidence, LLM unreachable, parse failure).

Design (research.md R2, plan.md §Technical Context, spec FR-009..FR-012):
  - LLM: same OpenAI-compatible server as the Router, configured via
    ``LLM_EXPERT_MODEL`` (full-context reasoning tier).
  - Structured output: ``method="json_mode"`` for Ollama compatibility (see
    router.py for the full rationale).
  - Citation binding is index-based: the LLM returns 0-based indices into
    ``FilteredEvidence.hit_lines``; mapping to ``LogExcerpt`` objects happens
    server-side.  This is the primary mechanism that prevents hallucinated
    provenance (Principle IV).
  - Audit fields (``model``, ``tokens``) come from ``usage_metadata`` on the
    raw ``AIMessage``, not from LLM-generated text (Principle V, FR-028).

Constitution compliance:
  - **Principle IV (NON-NEGOTIABLE)**: ``_assert_citations_grounded`` raises
    ``AssertionError`` if any cited excerpt is not verbatim from
    ``hit_lines``.  Treated as a Sev-2 defect per constitution §IV.
  - **Principle I**: ``validate_action`` accepts only the four catalog entries
    and enforces per-action parameter shapes; anything outside the catalog ⇒
    ``proposed_fix=None`` (no Approve button).
  - **Principle V**: model + tokens always from ``usage_metadata``.
  - **Principle VI**: two-reviewer rule applies to any PR that changes
    ``PERMISSION_SCOPES``, ``LLM_EXPERT_MODEL``/``LLM_BASE_URL``, or this
    file's core pipeline.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, ClassVar, Dict, List, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from src.agent.graph.state import WorkflowState
from src.agent.settings import settings
from src.shared.labels import ACTION_TYPES, ActionType, Domain
from src.shared.schemas import (
    ExpertDiagnosis,
    FilteredEvidence,
    LogExcerpt,
    ProposedFix,
    RoutingDecision,
    Target,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Permission-scope mapping (ServiceAccount per ActionType).
#
# Single source of truth for all three Expert nodes. Moving a scope here
# means one PR (with the required two reviewers per Principle VI) changes it
# everywhere — not scattered across three files.
#
# Any PR that changes this mapping MUST include the new-mutating-tool
# checklist entry (kill switch wired, reversal recipe, refusal-path test,
# cost / latency budgets declared, eval entry).
# ---------------------------------------------------------------------------
PERMISSION_SCOPES: Dict[str, str] = {
    "restart-pod": "sa-restart-pod",
    "rollback-deployment": "sa-rollback-deployment",
    "scale-deployment": "sa-scale-deployment",
    "delete-pod-to-reschedule": "sa-delete-pod",
}


# ---------------------------------------------------------------------------
# Structured-output schema bound to every Expert LLM call.
#
# Index-based citation (same rationale as _RouterDecision in router.py):
# the LLM cannot reproduce timestamps, byte offsets, or container names
# reliably.  Returning 0-based indices and mapping them server-side is the
# only way to guarantee that provenance metadata is verbatim from the input.
#
# ``model`` and ``tokens`` are absent here — they are extracted from the raw
# AIMessage and added when building ExpertDiagnosis (Principle V).
# ---------------------------------------------------------------------------
class _ExpertOutput(BaseModel):
    """Structured schema for every Expert node's LLM call."""

    root_cause_hypothesis: str = Field(
        description="One sentence, grounded in cited_indices."
    )
    cited_indices: List[int] = Field(
        default_factory=list,
        description=(
            "0-based indices into the numbered evidence list. "
            "MUST be non-empty (Constitution IV)."
        ),
    )
    confidence: str = Field(
        description="'low', 'medium', or 'high'."
    )
    runner_up_causes: List[str] = Field(
        default_factory=list,
        description="Short alternative-hypothesis strings.",
    )
    proposed_action: Optional[str] = Field(
        default=None,
        description=(
            "One of the ActionType literals, or null "
            "if no automated fix is safe."
        ),
    )
    proposed_parameters: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Action-specific parameters (e.g. {'to_revision': 7}). "
            "Use {} when the action needs no params or proposed_action is null."
        ),
    )


# ---------------------------------------------------------------------------
# Module-level shared utilities
# ---------------------------------------------------------------------------

def format_evidence(evidence: Optional[FilteredEvidence]) -> str:
    """Render FilteredEvidence.hit_lines as a 0-indexed numbered list.

    The format ``[idx] ISO-timestamp [container] text`` is intentionally
    identical across all Expert prompts so the LLM sees a consistent
    evidence block regardless of domain.
    """
    if not evidence or not evidence.hit_lines:
        return "(No log evidence available.)"
    lines: list[str] = []
    for i, excerpt in enumerate(evidence.hit_lines):
        ts = excerpt.timestamp.isoformat()
        lines.append(f"[{i}] {ts} [{excerpt.container}] {excerpt.text}")
    return "\n".join(lines)


def format_router_context(routing: Optional[RoutingDecision]) -> str:
    """Render the Router's decision as a short context block for the prompt.

    Giving the Expert the Router's reasoning lets it know why it was
    dispatched and lets it cross-check the classification confidence.
    """
    if routing is None:
        return "(Router context unavailable.)"
    runners = (
        ", ".join(f"{d}({c})" for d, c in routing.runners_up)
        if routing.runners_up
        else "none"
    )
    return (
        f"Router classification : {routing.domain} (confidence={routing.confidence})\n"
        f"Router runners-up     : {runners}\n"
        f"Router-cited indices  : {len(routing.cited_evidence)} evidence line(s)"
    )


def build_expert_llm() -> ChatOpenAI:
    """Instantiate ChatOpenAI with the full-context Expert sampling profile.

    Profile (research.md §R2):
      - Model : ``LLM_EXPERT_MODEL`` (larger reasoning tier)
      - Temperature : 0.2 — slight breadth for quality runner-up hypotheses
      - max_tokens : 1024 — room for prose + JSON; stays inside budget
    """
    return ChatOpenAI(
        model=settings.llm_expert_model,
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,  # type: ignore[arg-type]
        temperature=0.2,
        max_tokens=1024,
    )


def validate_action(
    action: Optional[str],
    parameters: Dict[str, Any],
) -> tuple[Optional[ActionType], Dict[str, Any]]:
    """Validate an LLM-proposed action against the allowed-remediation catalog.

    Returns ``(ActionType, params)`` when the action is safe to accept, or
    ``(None, {})`` when it must be rejected.  The caller MUST treat ``None``
    as ``proposed_fix=None`` (no Approve button surfaced to the on-call).

    Rejection cases (Principle I — refuse rather than guess):
      - Action string not in ActionType Literal.
      - ``rollback-deployment`` without a non-negative integer ``to_revision``.
      - ``scale-deployment`` without a non-negative integer ``to_replicas``.
    """
    if action is None:
        return None, {}
    if not isinstance(action, str) or action not in ACTION_TYPES:
        logger.warning("Expert returned out-of-catalog action %r; dropping", action)
        return None, {}

    if action == "restart-pod":
        return "restart-pod", {}

    if action == "delete-pod-to-reschedule":
        return "delete-pod-to-reschedule", {}

    if action == "rollback-deployment":
        rev = parameters.get("to_revision") if isinstance(parameters, dict) else None
        if not isinstance(rev, int) or rev < 0:
            logger.warning(
                "rollback-deployment missing/invalid to_revision %r; dropping fix",
                rev,
            )
            return None, {}
        return "rollback-deployment", {"to_revision": rev}

    if action == "scale-deployment":
        rep = parameters.get("to_replicas") if isinstance(parameters, dict) else None
        if not isinstance(rep, int) or rep < 0:
            logger.warning(
                "scale-deployment missing/invalid to_replicas %r; dropping fix",
                rep,
            )
            return None, {}
        return "scale-deployment", {"to_replicas": rep}

    # Defensive: new ActionType entry added without updating this validator.
    logger.error("unhandled action_type %r — update validate_action in _base.py", action)
    return None, {}


def resolve_target(state: WorkflowState) -> Target:
    """Return the Target from state.incident, or a safe placeholder.

    Missing incident is anomalous but we degrade gracefully rather than
    raising — the on-call still gets a diagnosis, just with a placeholder
    target they can validate.
    """
    incident = state.get("incident")
    if incident is not None:
        return incident.target
    return Target(namespace="default", pod="unknown-pod")


# ---------------------------------------------------------------------------
# BaseExpert
# ---------------------------------------------------------------------------

class BaseExpert(ABC):
    """Abstract base for Application / Network / Database Expert nodes.

    Subclass contract:
      - Set ``domain`` (class variable) to one of the ``Domain`` literals.
      - Set ``_system_prompt`` (class variable) to the domain-specific prompt
        string that enforces Constitution IV and catalog-bounded actions.
      - Implement ``_stub_diagnosis`` as a hard-coded fallback used by
        scaffolding runs and unit tests that bypass the LLM call.

    Everything else — prompt construction, LLM call, citation resolution,
    action validation, ProposedFix building, fallback handling, and the
    hallucination guard — is provided by this base and shared across all
    three Expert nodes.
    """

    domain: ClassVar[Domain]
    _system_prompt: ClassVar[str] = ""  # subclasses MUST override (T049/T050 pending)

    # ------------------------------------------------------------------
    # LangGraph entry point
    # ------------------------------------------------------------------

    def __call__(self, state: WorkflowState) -> WorkflowState:
        """Entry point called by LangGraph.

        Runs the real LLM-backed diagnosis and returns a partial
        ``WorkflowState`` dict that LangGraph merges into the full state.

        If a subclass has not yet set ``_system_prompt`` (i.e. it is still a
        stub awaiting T049/T050 implementation), ``_run_real_diagnosis`` detects
        the empty string and delegates to ``_stub_diagnosis`` so the graph
        remains runnable during incremental delivery.
        """
        diagnosis = self._run_real_diagnosis(state)
        print(
            f"[{self.domain.lower()}_expert] "
            f"confidence={diagnosis.confidence}  "
            f"cited={len(diagnosis.cited_evidence)}  "
            f"fix={diagnosis.proposed_fix.action_type if diagnosis.proposed_fix else None}  "
            f"tokens={diagnosis.tokens}  "
            f"model={diagnosis.model!r}"
        )
        return {"diagnosis": diagnosis}  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Shared execution pipeline (Constitution IV + I)
    # ------------------------------------------------------------------

    def _run_real_diagnosis(self, state: WorkflowState) -> ExpertDiagnosis:
        """Full LLM-backed diagnosis pipeline shared across all three Experts.

        Failure modes — all handled gracefully:
          0. ``_system_prompt`` not set (subclass is still a stub pending
             T049/T050) → delegates to ``_stub_diagnosis`` so the graph
             keeps running during incremental delivery.
          1. No evidence in state → _fallback_diagnosis (no Approve button).
          2. LLM call raises → _fallback_diagnosis.
          3. Structured-output parse fails → _fallback_diagnosis.
          4. Zero valid cited indices → demote to low confidence, first hit
             pinned, proposed_fix dropped.
        """
        # Stub guard: subclass has not yet provided a real system prompt.
        if not self._system_prompt:
            logger.info(
                "%s_expert has no system prompt; running stub diagnosis "
                "(set _system_prompt to activate the real pipeline)",
                self.domain.lower(),
            )
            return self._stub_diagnosis(state)

        evidence: Optional[FilteredEvidence] = state.get("filtered_evidence")
        routing: Optional[RoutingDecision] = state.get("routing")
        target = resolve_target(state)

        # No evidence → cannot satisfy Principle IV; return safe fallback.
        if evidence is None or not evidence.hit_lines:
            logger.warning(
                "%s_expert called without evidence; emitting low-confidence "
                "fallback diagnosis with no proposed fix",
                self.domain.lower(),
            )
            return self._fallback_diagnosis(
                state,
                reason=(
                    f"No log evidence was available to the {self.domain} Expert; "
                    "manual inspection of the pod is required."
                ),
            )

        evidence_text = format_evidence(evidence)
        router_context = format_router_context(routing)
        target_desc = (
            f"{target.namespace}/{target.pod}"
            + (f"/{target.container}" if target.container else "")
        )

        human_message = (
            f"Diagnose the Kubernetes incident for target `{target_desc}`.\n\n"
            "## Router Context\n\n"
            f"{router_context}\n\n"
            f"## Log Evidence (pre-filtered hit lines, 0-indexed)\n\n"
            f"{evidence_text}\n\n"
            "Return your diagnosis as a JSON object with the six keys "
            "described in the system prompt. Remember: every claim must be "
            "tied to a cited_indices entry; out-of-catalog actions are "
            "discarded; missing required parameters drop the fix."
        )

        messages = [
            SystemMessage(content=self._system_prompt),
            HumanMessage(content=human_message),
        ]

        # LLM call — json_mode for Ollama compatibility (see router.py).
        try:
            llm = build_expert_llm()
            structured: Any = llm.with_structured_output(
                _ExpertOutput,
                include_raw=True,
                method="json_mode",
            )
            result: dict[str, Any] = structured.invoke(messages)  # type: ignore[assignment]
        except Exception:
            logger.exception(
                "%s_expert LLM call failed; emitting fallback diagnosis",
                self.domain.lower(),
            )
            return self._fallback_diagnosis(
                state,
                reason=(
                    f"The {self.domain} Expert could not reach the inference "
                    "server; please retry or inspect the pod manually."
                ),
            )

        raw_message = result.get("raw")
        parsed: _ExpertOutput | None = result.get("parsed")
        parse_error = result.get("parsing_error")

        usage: dict[str, Any] = getattr(raw_message, "usage_metadata", None) or {}
        total_tokens: int = int(usage.get("total_tokens", 0))

        if parsed is None or parse_error is not None:
            logger.warning(
                "%s_expert structured-output parse failed (%r); "
                "emitting fallback diagnosis",
                self.domain.lower(),
                parse_error,
            )
            return self._fallback_diagnosis(
                state,
                reason=(
                    f"The {self.domain} Expert returned an unparseable response; "
                    "please retry or inspect the pod manually."
                ),
                tokens=total_tokens,
            )

        # ------------------------------------------------------------------
        # Map cited_indices → LogExcerpt (index-based citation resolution).
        # Out-of-range / non-int entries are silently dropped.
        # ------------------------------------------------------------------
        hit_lines = evidence.hit_lines
        cited: list[LogExcerpt] = [
            hit_lines[idx]
            for idx in (parsed.cited_indices or [])
            if isinstance(idx, int) and 0 <= idx < len(hit_lines)
        ]

        # Zero valid citations → demote; pin first hit as the closest truthful
        # citation we have (it is verbatim input, never synthesised).
        force_low_confidence = False
        if not cited:
            logger.warning(
                "%s_expert returned no valid cited_indices; "
                "demoting to low confidence with proposed_fix=None",
                self.domain.lower(),
            )
            cited = [hit_lines[0]]
            force_low_confidence = True

        # ------------------------------------------------------------------
        # Hallucination guard (Principle IV, NON-NEGOTIABLE).
        # Every cited excerpt must be verbatim from hit_lines.
        # Constitution §IV: "Hallucinated facts … are treated as Sev-2 defects
        # and require a regression test before close."
        # ------------------------------------------------------------------
        self._assert_citations_grounded(cited, evidence)

        # ------------------------------------------------------------------
        # Validate confidence and action.
        # ------------------------------------------------------------------
        valid_confidences = {"low", "medium", "high"}
        confidence = (
            parsed.confidence if parsed.confidence in valid_confidences else "low"
        )
        if force_low_confidence:
            confidence = "low"

        action_str = None if force_low_confidence else parsed.proposed_action
        params_in = {} if force_low_confidence else parsed.proposed_parameters
        validated_action, validated_params = validate_action(action_str, params_in)

        proposed_fix: Optional[ProposedFix] = None
        if validated_action is not None:
            proposed_fix = ProposedFix.build(
                action_type=validated_action,
                target=target,
                parameters=validated_params,
                permission_scope=PERMISSION_SCOPES[validated_action],
            )

        # Sanitise runner-up list to plain non-empty strings.
        runner_ups = [
            s for s in (parsed.runner_up_causes or [])
            if isinstance(s, str) and s
        ]

        # Guard against empty hypothesis (schema requires non-empty string).
        hypothesis = (parsed.root_cause_hypothesis or "").strip()
        if not hypothesis:
            hypothesis = (
                f"The {self.domain} Expert could not articulate a root cause; "
                "see the cited evidence for the raw signal."
            )

        return ExpertDiagnosis(
            domain=self.domain,
            root_cause_hypothesis=hypothesis,
            cited_evidence=cited,
            confidence=confidence,  # type: ignore[arg-type]
            runner_up_causes=runner_ups,
            proposed_fix=proposed_fix,
            model=settings.llm_expert_model,
            tokens=total_tokens,
        )

    # ------------------------------------------------------------------
    # Hallucination guard
    # ------------------------------------------------------------------

    def _assert_citations_grounded(
        self,
        cited: list[LogExcerpt],
        evidence: FilteredEvidence,
    ) -> None:
        """Raise AssertionError if any cited excerpt is not in hit_lines.

        This is the runtime enforcement of Constitution IV (Evidence-Backed
        Triage, NON-NEGOTIABLE).  The eval suite in
        ``tests/eval/hallucination_suite.py`` exercises this via the same
        guard to ensure the check catches regressions in CI.

        Failure is treated as a Sev-2 defect: "Hallucinated facts about
        cluster state require a regression test before close."
        """
        hit_key = frozenset(
            (exc.byte_offset, exc.text) for exc in evidence.hit_lines
        )
        for exc in cited:
            if (exc.byte_offset, exc.text) not in hit_key:
                raise AssertionError(
                    f"[{self.domain}_expert] Constitution IV VIOLATION — "
                    f"cited LogExcerpt is not present in FilteredEvidence.hit_lines.\n"
                    f"  cited text       : {exc.text!r}\n"
                    f"  cited byte_offset: {exc.byte_offset}\n"
                    "This is a Sev-2 hallucination defect. Add a regression test "
                    "before closing."
                )

    # ------------------------------------------------------------------
    # Fallback diagnosis (no LLM / parse failure / no evidence)
    # ------------------------------------------------------------------

    def _fallback_diagnosis(
        self,
        state: WorkflowState,
        *,
        reason: str,
        tokens: int = 0,
    ) -> ExpertDiagnosis:
        """Low-confidence diagnosis with proposed_fix=None for all failure modes.

        Uses the first real hit line if available (verbatim input → grounded),
        or the synthetic placeholder from ``_first_hit`` when evidence is
        absent.  The fallback path intentionally bypasses
        ``_assert_citations_grounded`` because the synthetic placeholder is not
        in hit_lines — the fallback is explicitly a "no data" situation.
        """
        first_hit = self._first_hit(state)
        return ExpertDiagnosis(
            domain=self.domain,
            root_cause_hypothesis=reason,
            cited_evidence=[first_hit],
            confidence="low",
            runner_up_causes=[],
            proposed_fix=None,
            model=settings.llm_expert_model,
            tokens=tokens,
        )

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _first_hit(state: WorkflowState) -> LogExcerpt:
        """Return the first log hit from state, or a synthetic placeholder.

        Used only by ``_fallback_diagnosis`` and ``_stub_diagnosis``.
        The synthetic entry deliberately does NOT carry a ``byte_offset``
        from a real log stream — callers MUST NOT pass it through
        ``_assert_citations_grounded``.
        """
        evidence = state.get("filtered_evidence")
        if evidence and evidence.hit_lines:
            return evidence.hit_lines[0]
        return LogExcerpt(
            timestamp=datetime(2026, 5, 14, 10, 0, 0, tzinfo=timezone.utc),
            container="app",
            text="[no evidence available]",
            byte_offset=0,
        )

    # ------------------------------------------------------------------
    # Abstract scaffold for unit tests and scaffolding runs
    # ------------------------------------------------------------------

    @abstractmethod
    def _stub_diagnosis(self, state: WorkflowState) -> ExpertDiagnosis:
        """Return a hard-coded ExpertDiagnosis for scaffolding / unit tests.

        This method is NEVER called by the real LangGraph execution path
        (``__call__`` → ``_run_real_diagnosis``).  It exists so:
          a) The ABC contract is satisfied even before a full LLM call is
             possible (early dev / offline CI).
          b) Unit tests that need a deterministic diagnosis without a live
             inference server can call it explicitly.
        """
