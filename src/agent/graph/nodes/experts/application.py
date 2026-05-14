"""
src/agent/graph/nodes/experts/application.py

Application Expert node — T048.

Diagnoses application-domain incidents (code bugs, unhandled exceptions,
crash-loops, OOM kills, restart cascades) using the full-context Expert LLM
sampling profile and emits an ``ExpertDiagnosis`` with cited evidence and a
catalog-bound ``ProposedFix`` (or ``None``).

Design (research.md R2, plan.md §Technical Context, spec FR-009..FR-012):
  - LLM: same OpenAI-compatible local inference server as the Router, but
    using the larger "expert" model (``LLM_EXPERT_MODEL``) and the
    full-context sampling profile (temperature 0.2 for slight reasoning
    breadth; higher ``max_tokens`` for the root-cause prose).
  - Structured output uses ``method="json_mode"`` for Ollama compatibility
    (see ``router.py`` for the full rationale).
  - Evidence binding is **index-based**: the LLM returns 0-based indices
    into ``FilteredEvidence.hit_lines`` rather than reproducing log text.
    This eliminates hallucinated provenance — every cited line is verbatim
    from the input.
  - The proposed action MUST be one of ``ActionType`` (the Literal in
    ``src/shared/labels.py``); any value outside that set yields
    ``proposed_fix=None``.
  - Action parameters are validated against the catalog schemas in
    ``src/shared/catalog.py``; an incomplete or malformed parameter set
    drops the fix rather than synthesising state values (refuses to
    hallucinate revision numbers, replica counts, etc.).
  - Audit fields (``model``, ``tokens``) come from ``usage_metadata`` on
    the raw ``AIMessage``, not LLM-generated text (Principle V, FR-028).

Constitution compliance:
  - **Principle IV — Evidence-Backed Triage (NON-NEGOTIABLE)**: the system
    prompt forbids any factual claim that is not tied to a numbered
    evidence index. At least one citation is required by
    ``ExpertDiagnosis``; if the LLM returns zero valid indices we demote to
    a low-confidence diagnosis with ``proposed_fix=None`` rather than
    synthesise a citation.
  - **Principle I — Safety-First Autonomy**: proposed actions are
    restricted to the four-entry remediation catalog
    (``restart-pod``, ``rollback-deployment``, ``scale-deployment``,
    ``delete-pod-to-reschedule``). An invalid action ⇒ ``proposed_fix=None``
    ⇒ the chat surface omits the Approve button (FR-014).
  - **Principle II — Cost-Conscious**: a single LLM call per incident in
    this node; token usage is recorded; no retry-with-correction loop in
    MVP (the eval suite gates JSON-parse success rate).
  - **Principle V — Observability**: ``model`` and ``tokens`` recorded on
    every diagnosis; correlation context propagates via the logger.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from src.agent.graph.nodes.experts._base import BaseExpert
from src.agent.graph.state import WorkflowState
from src.agent.settings import settings
from src.shared.labels import ACTION_TYPES, ActionType
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
# Permission-scope mapping (ServiceAccount per action_type).
#
# Kept as a constant rather than a settings entry because the values are part
# of the safety contract: changing them silently would broaden write
# authority. Adding a new entry requires the catalog two-reviewer rule
# (constitution §VI + the new-mutating-tool checklist in §Development
# Workflow & Quality Gates).
# ---------------------------------------------------------------------------
_PERMISSION_SCOPES: Dict[str, str] = {
    "restart-pod": "sa-restart-pod",
    "rollback-deployment": "sa-rollback-deployment",
    "scale-deployment": "sa-scale-deployment",
    "delete-pod-to-reschedule": "sa-delete-pod",
}


# ---------------------------------------------------------------------------
# System prompt — enforces Principle IV (Evidence-Backed Triage) and
# Principle I (catalog-bounded actions).
#
# Notes:
#   - The prompt enumerates the catalog inline rather than asking the model
#     to "use the catalog" — local models in the MVP class (qwen2.5:14b,
#     granite, etc.) do not reliably honour an external schema reference.
#   - Every output field is named explicitly so the json_mode response is
#     valid even when the model ignores tool-call binding.
# ---------------------------------------------------------------------------
_SYSTEM_PROMPT = """\
You are the Application-domain Expert in a Kubernetes incident triage
agent. The Router has already classified this incident as "Application"
— meaning the failure looks like a code-level problem (unhandled
exception, panic, OOM kill, crash-loop, startup failure, application
runtime error). Your job is to identify the most likely root cause and
propose ONE catalog-bound remediation, citing the specific log lines
that justify your conclusion.

# Evidence-Backed Triage (NON-NEGOTIABLE)

You are given a numbered list of log lines (the "evidence list"). Every
factual claim in your root-cause hypothesis MUST be grounded in at least
one of those lines. Specifically:

1. `cited_indices` MUST contain at least one 0-based index from the
   evidence list and MUST NOT be empty.
2. Do NOT invent log text, timestamps, container names, exit codes,
   stack frames, or error messages. If the evidence does not contain
   the detail you need, say so in the hypothesis instead of guessing.
3. Do NOT cite evidence indices that are not in the numbered list. Any
   index outside the list will be discarded server-side.
4. If the evidence is genuinely inconclusive, return a "low" confidence
   diagnosis, an empty list of `runner_up_causes` is acceptable, and
   `proposed_action` MUST be null. NEVER propose a fix you cannot defend
   from the evidence.

# Catalog-Bounded Actions (Safety-First Autonomy)

`proposed_action` MUST be exactly one of the following strings, or
`null` if no automated fix is safe to propose:

  - "restart-pod"               — the container is in a transient crash
                                  state (e.g. OOMKilled with normal
                                  memory pressure, transient panic,
                                  startup probe flake). No parameters.
  - "rollback-deployment"       — a recent release introduced the bug;
                                  rolling back to the prior revision
                                  should restore service. REQUIRES
                                  `proposed_parameters.to_revision`
                                  (integer) — use only when the
                                  evidence cites a specific prior
                                  revision number; otherwise return
                                  null.
  - "scale-deployment"          — load exceeded capacity and the pod
                                  was crash-killed under pressure.
                                  REQUIRES
                                  `proposed_parameters.to_replicas`
                                  (integer).
  - "delete-pod-to-reschedule"  — the pod is stuck (e.g. wedged init
                                  container) and a controller-managed
                                  recreate will resolve it without
                                  changing config. No parameters.

Do NOT invent new action names. Do NOT propose `kubectl exec`,
`--force` deletes, manifest edits, or anything not in the four-entry
list above. If none of these applies, return `proposed_action: null`
and explain in the hypothesis what the on-call should inspect next.

# Output format

Respond ONLY with a JSON object — no prose, no markdown, no code fence.
The object MUST contain exactly these six keys:

  "root_cause_hypothesis" : one sentence describing the most likely
                            root cause, grounded in the cited indices.
  "cited_indices"         : list of 0-based integers from the numbered
                            evidence list. MUST be non-empty.
  "confidence"            : one of "low", "medium", "high".
  "runner_up_causes"      : list of short alternative-hypothesis
                            strings (may be []).
  "proposed_action"       : one of "restart-pod",
                            "rollback-deployment", "scale-deployment",
                            "delete-pod-to-reschedule", or null.
  "proposed_parameters"   : object with action-specific parameters
                            (e.g. {"to_revision": 7}). Use {} when the
                            action takes no parameters or when
                            proposed_action is null.

Example of a valid response:
{"root_cause_hypothesis":"The api-server container is in a Go panic crash-loop after dereferencing a nil pointer in main.processRequest.","cited_indices":[0,2,3],"confidence":"high","runner_up_causes":["OOMKilled","misconfigured liveness probe"],"proposed_action":"restart-pod","proposed_parameters":{}}
"""


# ---------------------------------------------------------------------------
# Structured-output schema bound to the Expert LLM call.
#
# Mirrors the Router's index-based approach (router.py:_RouterDecision) for
# the same reason: the LLM cannot reliably reproduce timestamps or byte
# offsets, so we keep provenance metadata server-side.
#
# `model` and `tokens` are audit metadata filled from the raw AIMessage
# (usage_metadata), so they are absent here.
# ---------------------------------------------------------------------------
class _ExpertOutput(BaseModel):
    """Structured output schema bound to the Application-Expert LLM call."""

    root_cause_hypothesis: str = Field(
        description="One-sentence root cause, grounded in cited_indices."
    )
    cited_indices: List[int] = Field(
        default_factory=list,
        description=(
            "0-based indices into the numbered evidence list that support the "
            "hypothesis. MUST be non-empty (Principle IV)."
        ),
    )
    confidence: str = Field(
        description="Classification confidence: 'low', 'medium', or 'high'."
    )
    runner_up_causes: List[str] = Field(
        default_factory=list,
        description="Alternative hypotheses considered (short strings).",
    )
    proposed_action: Optional[str] = Field(
        default=None,
        description=(
            "One of the ActionType literals "
            "(restart-pod | rollback-deployment | scale-deployment | "
            "delete-pod-to-reschedule), or null if no automated fix is safe."
        ),
    )
    proposed_parameters: Dict[str, Any] = Field(
        default_factory=dict,
        description="Action-specific parameters (e.g. {'to_revision': 7}).",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_evidence(evidence: Optional[FilteredEvidence]) -> str:
    """Render FilteredEvidence.hit_lines as a 0-indexed numbered list."""
    if not evidence or not evidence.hit_lines:
        return "(No log evidence available.)"
    lines: list[str] = []
    for i, excerpt in enumerate(evidence.hit_lines):
        ts = excerpt.timestamp.isoformat()
        lines.append(f"[{i}] {ts} [{excerpt.container}] {excerpt.text}")
    return "\n".join(lines)


def _format_router_context(routing: Optional[RoutingDecision]) -> str:
    """Render the Router's decision as a short context block for the prompt."""
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


def _build_llm() -> ChatOpenAI:
    """Instantiate ChatOpenAI pointed at the configured local inference server.

    Uses the full-context Expert sampling profile from research.md §R2:
      - ``LLM_EXPERT_MODEL`` (larger reasoning-tier model)
      - temperature 0.2 (slight breadth for alternative-hypothesis quality;
        zero would over-collapse on the most-frequent training pattern)
      - max_tokens 1024 (room for the prose hypothesis + runner-ups + JSON
        action object; still tight enough to stay inside the per-incident
        token ceiling — Principle II).
    """
    return ChatOpenAI(
        model=settings.llm_expert_model,
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,  # type: ignore[arg-type]
        temperature=0.2,
        max_tokens=1024,
    )


def _validate_action(
    action: Optional[str],
    parameters: Dict[str, Any],
) -> tuple[Optional[ActionType], Dict[str, Any]]:
    """Reject anything not in the catalog. Validate per-action parameter shape.

    Returns ``(None, {})`` whenever the action cannot be safely accepted
    (Principle I: refuse rather than guess). The caller MUST treat that as
    ``proposed_fix=None`` — i.e. surface the diagnosis without an Approve
    button (FR-014).
    """
    if action is None:
        return None, {}
    if not isinstance(action, str) or action not in ACTION_TYPES:
        logger.warning("Expert returned out-of-catalog action %r; dropping", action)
        return None, {}

    # Per-action parameter validation. Schemas live in data-model.md and
    # src/shared/catalog.py; we duplicate the *shape* check here so an
    # unsafe parameter set never reaches ProposedFix.
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

    # Defensive: ACTION_TYPES exhausted above; falling through means a new
    # entry was added without updating this validator — fail closed.
    logger.error("unhandled action_type %r — update _validate_action", action)
    return None, {}


def _resolve_target(state: WorkflowState) -> Target:
    """Return the Target from state.incident, or a defensive default.

    A missing incident is anomalous (the webhook handler always populates
    it) but we fall back to a placeholder rather than raising — the
    fallback Diagnosis is still useful to the on-call.
    """
    incident = state.get("incident")
    if incident is not None:
        return incident.target
    return Target(namespace="default", pod="unknown-pod")


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------

class ApplicationExpert(BaseExpert):
    """Real Application-domain Expert backed by the local Expert LLM."""

    domain = "Application"

    def __call__(self, state: WorkflowState) -> WorkflowState:  # type: ignore[override]
        diagnosis = self._real_diagnosis(state)
        print(
            f"[application_expert] confidence={diagnosis.confidence}  "
            f"cited={len(diagnosis.cited_evidence)}  "
            f"fix={diagnosis.proposed_fix.action_type if diagnosis.proposed_fix else None}  "
            f"tokens={diagnosis.tokens}  "
            f"model={diagnosis.model!r}"
        )
        return {"diagnosis": diagnosis}  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Real LLM-backed diagnosis
    # ------------------------------------------------------------------
    def _real_diagnosis(self, state: WorkflowState) -> ExpertDiagnosis:
        evidence: Optional[FilteredEvidence] = state.get("filtered_evidence")
        routing: Optional[RoutingDecision] = state.get("routing")
        target = _resolve_target(state)

        # ------------------------------------------------------------------
        # Hard precondition: we need at least one log line. Without evidence
        # there is no way to honour Principle IV; return a low-confidence
        # stub with no proposed fix and let the Reporter surface it as a
        # "needs human inspection" message (FR-014).
        # ------------------------------------------------------------------
        if evidence is None or not evidence.hit_lines:
            logger.warning(
                "application_expert called without evidence; emitting "
                "low-confidence stub diagnosis with no proposed fix"
            )
            return self._fallback_diagnosis(
                state,
                reason=(
                    "No log evidence was available to the Application Expert; "
                    "manual inspection of the pod is required."
                ),
            )

        evidence_text = _format_evidence(evidence)
        router_context = _format_router_context(routing)
        target_desc = (
            f"{target.namespace}/{target.pod}"
            + (f"/{target.container}" if target.container else "")
        )

        human_message = (
            f"Diagnose the Kubernetes incident for target `{target_desc}`.\n\n"
            "## Router Context\n\n"
            f"{router_context}\n\n"
            "## Log Evidence (pre-filtered hit lines, 0-indexed)\n\n"
            f"{evidence_text}\n\n"
            "Return your diagnosis as a JSON object with the six keys "
            "described in the system prompt. Remember: every claim must be "
            "tied to a cited_indices entry; out-of-catalog actions are "
            "discarded; missing required parameters drop the fix."
        )

        messages = [
            SystemMessage(content=_SYSTEM_PROMPT),
            HumanMessage(content=human_message),
        ]

        # ------------------------------------------------------------------
        # Bind the LLM to _ExpertOutput using json_mode (Ollama-compatible;
        # see router.py for the full rationale on json_mode vs.
        # function_calling). include_raw=True gives us the AIMessage for
        # usage_metadata extraction.
        # ------------------------------------------------------------------
        try:
            llm = _build_llm()
            structured: Any = llm.with_structured_output(
                _ExpertOutput,
                include_raw=True,
                method="json_mode",
            )
            result: dict[str, Any] = structured.invoke(messages)  # type: ignore[assignment]
        except Exception:
            logger.exception(
                "application_expert LLM call failed; emitting fallback diagnosis"
            )
            return self._fallback_diagnosis(
                state,
                reason=(
                    "The Application Expert could not reach the inference "
                    "server; please retry or inspect the pod manually."
                ),
            )

        raw_message = result.get("raw")
        parsed: _ExpertOutput | None = result.get("parsed")
        parse_error = result.get("parsing_error")

        usage: dict[str, Any] = getattr(raw_message, "usage_metadata", None) or {}
        total_tokens: int = int(usage.get("total_tokens", 0))

        # ------------------------------------------------------------------
        # Parse failure → fallback diagnosis (still cites real evidence).
        # ------------------------------------------------------------------
        if parsed is None or parse_error is not None:
            logger.warning(
                "application_expert structured-output parse failed (%r); "
                "emitting fallback diagnosis",
                parse_error,
            )
            return self._fallback_diagnosis(
                state,
                reason=(
                    "The Application Expert returned an unparseable response; "
                    "please retry or inspect the pod manually."
                ),
                tokens=total_tokens,
            )

        # ------------------------------------------------------------------
        # Map cited_indices → LogExcerpt. Out-of-range / non-int entries
        # are silently dropped (Principle IV: never synthesise provenance).
        # ------------------------------------------------------------------
        hit_lines = evidence.hit_lines
        cited: list[LogExcerpt] = [
            hit_lines[idx]
            for idx in (parsed.cited_indices or [])
            if isinstance(idx, int) and 0 <= idx < len(hit_lines)
        ]

        # If the LLM cited nothing valid, demote to low-confidence and pin
        # to the first hit. ExpertDiagnosis requires ≥1 cited entry; we
        # MUST NOT fabricate one, so the first hit is the most-faithful
        # fallback (it is verbatim input, not synthesised).
        force_low_confidence = False
        if not cited:
            logger.warning(
                "application_expert returned no valid cited_indices; "
                "demoting to low confidence with proposed_fix=None"
            )
            cited = [hit_lines[0]]
            force_low_confidence = True

        # ------------------------------------------------------------------
        # Validate confidence and action against the schema literals.
        # ------------------------------------------------------------------
        valid_confidences = {"low", "medium", "high"}
        confidence = (
            parsed.confidence if parsed.confidence in valid_confidences else "low"
        )
        if force_low_confidence:
            confidence = "low"

        # If we lost the citations, also drop any proposed action — we no
        # longer have evidence to justify it.
        action_str = None if force_low_confidence else parsed.proposed_action
        params_in = {} if force_low_confidence else parsed.proposed_parameters
        validated_action, validated_params = _validate_action(action_str, params_in)

        proposed_fix: Optional[ProposedFix] = None
        if validated_action is not None:
            proposed_fix = ProposedFix.build(
                action_type=validated_action,
                target=target,
                parameters=validated_params,
                permission_scope=_PERMISSION_SCOPES[validated_action],
            )

        # ------------------------------------------------------------------
        # Sanitise the runner-up list to plain strings.
        # ------------------------------------------------------------------
        runner_ups = [s for s in (parsed.runner_up_causes or []) if isinstance(s, str) and s]

        # Guard against pathological empty hypothesis text — the schema
        # requires a non-empty string but pydantic won't enforce that
        # alone, so we substitute a faithful placeholder when needed.
        hypothesis = (parsed.root_cause_hypothesis or "").strip()
        if not hypothesis:
            hypothesis = (
                "The Application Expert could not articulate a root cause; "
                "see the cited evidence for the raw signal."
            )

        return ExpertDiagnosis(
            domain="Application",
            root_cause_hypothesis=hypothesis,
            cited_evidence=cited,
            confidence=confidence,  # type: ignore[arg-type]
            runner_up_causes=runner_ups,
            proposed_fix=proposed_fix,
            model=settings.llm_expert_model,
            tokens=total_tokens,
        )

    # ------------------------------------------------------------------
    # Fallback path used when (a) evidence is missing, (b) the LLM is
    # unreachable, or (c) the structured-output response cannot be parsed.
    #
    # Returns a low-confidence diagnosis with proposed_fix=None so the
    # Reporter renders an evidence-only chat message and the chat surface
    # omits the Approve button (FR-014).
    # ------------------------------------------------------------------
    def _fallback_diagnosis(
        self,
        state: WorkflowState,
        *,
        reason: str,
        tokens: int = 0,
    ) -> ExpertDiagnosis:
        first_hit = self._first_hit(state)
        return ExpertDiagnosis(
            domain="Application",
            root_cause_hypothesis=reason,
            cited_evidence=[first_hit],
            confidence="low",
            runner_up_causes=[],
            proposed_fix=None,
            model=settings.llm_expert_model,
            tokens=tokens,
        )

    # ------------------------------------------------------------------
    # BaseExpert abstract-method satisfier. Retained so callers that
    # bypass __call__ (e.g. unit tests of the base protocol) still get a
    # syntactically valid ExpertDiagnosis. The real path uses
    # _real_diagnosis(); _stub_diagnosis() is only reached by tests that
    # explicitly call it.
    # ------------------------------------------------------------------
    def _stub_diagnosis(self, state: WorkflowState) -> ExpertDiagnosis:
        first_hit = self._first_hit(state)
        target = _resolve_target(state)
        proposed_fix = ProposedFix.build(
            action_type="restart-pod",
            target=target,
            parameters={},
            permission_scope=_PERMISSION_SCOPES["restart-pod"],
        )
        return ExpertDiagnosis(
            domain="Application",
            root_cause_hypothesis=(
                "Application container is crash-looping due to an unhandled "
                "exception on startup."
            ),
            cited_evidence=[first_hit],
            confidence="medium",
            runner_up_causes=["OOMKilled", "readiness probe misconfiguration"],
            proposed_fix=proposed_fix,
            model="stub-sonnet",
            tokens=0,
        )


# ---------------------------------------------------------------------------
# Module-level callable for LangGraph node registration (builder.py imports
# this exact symbol).
# ---------------------------------------------------------------------------
application_expert_node = ApplicationExpert()
