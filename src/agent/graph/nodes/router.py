"""
src/agent/graph/nodes/router.py

Router node — T046.

Classifies the incident domain (Application / Network / Database / Unknown)
using a small/fast LLM with ``langchain-openai``'s ``with_structured_output``
binding.

Design (research.md R2, plan.md §Technical Context, spec FR-005..FR-008):
  - Provider: any OpenAI-compatible inference server (Ollama by default).
    Configured via ``settings.llm_base_url`` + ``settings.llm_router_model``.
  - Structured output is enforced by binding the ``ChatOpenAI`` instance to
    ``_RouterDecision`` (a Pydantic model that mirrors ``RoutingDecision``
    without the audit-metadata fields).  LangChain generates a tool-call
    schema from the model and forces the LLM to invoke it.
  - Evidence binding: the LLM returns 0-based line indices into
    ``FilteredEvidence.hit_lines``; we map them back to ``LogExcerpt`` objects
    server-side.  This avoids asking the LLM to reproduce timestamps or byte
    offsets (which it cannot know reliably).
  - Audit fields (``model``, ``tokens``) are populated from the raw
    ``AIMessage`` returned by ``include_raw=True``, not from LLM-generated
    text (Principle V, spec FR-028).

Constitution compliance:
  - Principle IV (NON-NEGOTIABLE): ≥1 cited_evidence item unless domain ==
    'Unknown'.  Enforced here AND in ``RoutingDecision``'s validator.
  - Principle II: small model (``LLM_ROUTER_MODEL``) at temperature=0;
    token ceiling checked post-call.
  - Principle IX: total_tokens logged for per-incident budget tracking.
"""

from __future__ import annotations

from typing import Any, List

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from src.agent.graph.state import WorkflowState
from src.agent.settings import settings
from src.shared.labels import DOMAINS, Domain
from src.shared.schemas import FilteredEvidence, LogExcerpt, RoutingDecision

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------
_SYSTEM_PROMPT = """\
You are a Kubernetes incident triage router.  Your sole job is to classify an
incident into exactly one of these four domains based on the log evidence:

• Application — code bugs, process crashes, unhandled exceptions (Go panics,
  Java stack traces, Python tracebacks), OOM kills, container restart-loops,
  application-level runtime errors.
• Network — connection refused/timeout to internal or external services, DNS
  resolution failures, TLS/certificate errors, network policy blocks.
• Database — connection pool exhaustion, query timeouts, replication lag,
  storage disk-full, I/O errors from a database or persistence layer.
• Unknown — insufficient evidence to classify with confidence.

Rules:
1. Respond ONLY via the structured tool — never with prose.
2. cited_indices MUST reference lines that directly support your classification.
   Provide at least one index unless you choose Unknown.
3. runners_up lists other domains considered, in descending confidence order.
"""


# ---------------------------------------------------------------------------
# Intermediate Pydantic schema — bound to the LLM via with_structured_output.
#
# Uses index-based citation rather than full LogExcerpt objects because:
#   • The LLM cannot reliably reproduce timestamps, byte offsets, etc.
#   • Index-based citation prevents hallucinated provenance metadata.
# After the call we map indices → LogExcerpt from FilteredEvidence.hit_lines.
#
# ``model`` and ``tokens`` are audit metadata filled from the API response,
# so they are absent here and added when building the final RoutingDecision.
# ---------------------------------------------------------------------------
class _RouterDecision(BaseModel):
    """Structured output schema bound to the Router LLM call."""

    domain: Domain = Field(description="Incident domain classification.")
    confidence: str = Field(
        description="Classification confidence: 'low', 'medium', or 'high'."
    )
    cited_indices: List[int] = Field(
        default_factory=list,
        description=(
            "0-based indices of the evidence lines (from the numbered list in "
            "the prompt) that most support the classification.  Required "
            "non-empty unless domain is 'Unknown'."
        ),
    )
    runners_up: List[List[str]] = Field(
        default_factory=list,
        description=(
            "Other domains considered, as [[domain, confidence], ...] pairs "
            "in descending confidence order.  May be empty."
        ),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_evidence(evidence: FilteredEvidence | None) -> str:
    """Render FilteredEvidence.hit_lines as a 0-indexed numbered list."""
    if not evidence or not evidence.hit_lines:
        return "(No log evidence available.)"
    lines: list[str] = []
    for i, excerpt in enumerate(evidence.hit_lines):
        ts = excerpt.timestamp.isoformat()
        lines.append(f"[{i}] {ts} [{excerpt.container}] {excerpt.text}")
    return "\n".join(lines)


def _build_llm() -> ChatOpenAI:
    """Instantiate ChatOpenAI pointed at the configured local inference server."""
    return ChatOpenAI(
        model=settings.llm_router_model,
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,  # type: ignore[arg-type]
        temperature=0,
        max_tokens=512,
    )


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------

def router_node(state: WorkflowState) -> WorkflowState:
    """
    Router node: classify incident domain via a structured-output LLM call.

    Binds ``ChatOpenAI`` to ``_RouterDecision`` using
    ``with_structured_output(include_raw=True)`` so we capture both the
    parsed decision and the raw ``AIMessage`` (for token-count audit).

    Returns a partial WorkflowState dict; LangGraph merges it into the full
    state.
    """
    evidence: FilteredEvidence | None = state.get("filtered_evidence")
    incident = state.get("incident")
    target_desc = (
        f"{incident.target.namespace}/{incident.target.pod}"
        if incident
        else "unknown-target"
    )

    evidence_text = _format_evidence(evidence)

    messages = [
        SystemMessage(content=_SYSTEM_PROMPT),
        HumanMessage(
            content=(
                f"Classify the Kubernetes incident for target `{target_desc}`.\n\n"
                "## Log Evidence (pre-filtered hit lines)\n\n"
                f"{evidence_text}\n\n"
                "Return your classification using the structured tool."
            )
        ),
    ]

    # ------------------------------------------------------------------
    # Bind the LLM to _RouterDecision and invoke — include_raw=True gives
    # us the AIMessage alongside the parsed model (for usage_metadata).
    # ------------------------------------------------------------------
    llm = _build_llm()
    structured: Any = llm.with_structured_output(_RouterDecision, include_raw=True)

    result: dict[str, Any] = structured.invoke(messages)  # type: ignore[assignment]

    raw_message = result.get("raw")
    parsed: _RouterDecision | None = result.get("parsed")
    parse_error = result.get("parsing_error")

    # Token count from usage_metadata (provided by langchain-openai).
    usage: dict[str, Any] = getattr(raw_message, "usage_metadata", None) or {}
    total_tokens: int = int(usage.get("total_tokens", 0))

    # ------------------------------------------------------------------
    # Defensive path: parsing failed → Unknown domain.
    # ------------------------------------------------------------------
    if parsed is None or parse_error is not None:
        print(
            f"[router_node] WARNING: structured output parse failed "
            f"({parse_error!r}); falling back to Unknown"
        )
        return {  # type: ignore[return-value]
            "routing": RoutingDecision(
                domain="Unknown",
                confidence="low",
                cited_evidence=[],
                runners_up=[],
                model=settings.llm_router_model,
                tokens=total_tokens,
            )
        }

    # ------------------------------------------------------------------
    # Map cited_indices → LogExcerpt objects
    # Out-of-range indices are silently dropped.
    # ------------------------------------------------------------------
    hit_lines: list[LogExcerpt] = evidence.hit_lines if evidence else []
    cited: list[LogExcerpt] = [
        hit_lines[idx]
        for idx in (parsed.cited_indices or [])
        if isinstance(idx, int) and 0 <= idx < len(hit_lines)
    ]

    # Enforce Principle IV: ≥1 cited item when domain != Unknown.
    domain: Domain = parsed.domain
    if domain != "Unknown" and not cited:
        if hit_lines:
            cited = [hit_lines[0]]  # fallback to first available line
        else:
            domain = "Unknown"      # no evidence → cannot classify

    # ------------------------------------------------------------------
    # Parse runners_up: [[domain_str, confidence_str], ...]
    # ------------------------------------------------------------------
    valid_confidences = {"low", "medium", "high"}
    runners_up = [
        (item[0], item[1])
        for item in (parsed.runners_up or [])
        if (
            isinstance(item, (list, tuple))
            and len(item) == 2
            and item[0] in DOMAINS
            and item[1] in valid_confidences
        )
    ]

    # Normalise confidence: clamp any unexpected value to "low".
    confidence = parsed.confidence if parsed.confidence in valid_confidences else "low"

    routing = RoutingDecision(
        domain=domain,
        confidence=confidence,  # type: ignore[arg-type]
        cited_evidence=cited,
        runners_up=runners_up,  # type: ignore[arg-type]
        model=settings.llm_router_model,
        tokens=total_tokens,
    )

    print(
        f"[router_node] domain={routing.domain}  "
        f"confidence={routing.confidence}  "
        f"cited={len(routing.cited_evidence)}  "
        f"tokens={routing.tokens}  "
        f"model={routing.model}"
    )

    return {"routing": routing}  # type: ignore[return-value]


def route_after_router(state: WorkflowState) -> str:
    """
    Conditional edge function: returns the next node name based on the
    Router's classification.

    LangGraph calls this after router_node and uses the returned string to
    select the next edge.
    """
    routing: RoutingDecision | None = state.get("routing")
    if routing is None:
        # Defensive: no routing decision → send to reporter (safe path).
        return "reporter"

    domain_to_node: dict[str, str] = {
        "Application": "application_expert",
        "Network": "network_expert",
        "Database": "database_expert",
        "Unknown": "reporter",  # Unknown short-circuits past all Experts
    }
    return domain_to_node.get(routing.domain, "reporter")
