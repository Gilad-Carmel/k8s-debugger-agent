"""
src/agent/graph/nodes/experts/application.py

Application Expert node — T048 (real implementation) / T047 (base refactor).

All shared infrastructure (prompt construction, LLM call, citation
resolution, action validation, hallucination guard, fallback handling) now
lives in ``_base.py``.  This module is responsible only for the
Application-domain system prompt and the hard-coded scaffold used by unit
tests that bypass the LLM call.

Domain patterns recognised:
  - Unhandled exceptions / language-runtime panics (Go, Python, Java, Node)
  - OOM kills (the container was terminated by the kernel)
  - CrashLoopBackOff / restart-count spikes
  - Startup / init-container failures
  - Application-level errors (5xx, timeouts from the app's own logs)

Constitution compliance (Principle IV, NON-NEGOTIABLE):
  The system prompt below forbids any factual claim not tied to a numbered
  evidence index.  The base class ``_assert_citations_grounded`` provides the
  runtime enforcement; unit tests in ``tests/unit/`` and integration tests in
  ``tests/integration/test_graph_run.py`` provide downstream CI coverage.
"""

from __future__ import annotations

from src.agent.graph.nodes.experts._base import (
    BaseExpert,
    PERMISSION_SCOPES,
)
from src.agent.graph.state import WorkflowState
from src.shared.schemas import ExpertDiagnosis, ProposedFix, Target


# ---------------------------------------------------------------------------
# Application-domain system prompt
#
# Enforces:
#   1. Principle IV (Evidence-Backed Triage, NON-NEGOTIABLE) — explicit
#      prohibition on inventing log text, timestamps, exit codes, or stack
#      frames; cited_indices MUST be non-empty.
#   2. Principle I (Safety-First Autonomy) — proposed_action restricted to
#      the four-entry remediation catalog; no free-form kubectl commands.
#
# Notes:
#   - Enumerates the catalog inline rather than referencing it by name.
#     Local models in the MVP class (qwen2.5:14b, Granite 3.3 8B, etc.) do
#     not reliably honour an external schema reference.
#   - Every output field is named explicitly so json_mode responses are valid
#     even when the model ignores tool-call binding.
# ---------------------------------------------------------------------------
_APP_SYSTEM_PROMPT = """\
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
   diagnosis and set `proposed_action` to null. NEVER propose a fix you
   cannot defend from the evidence.

# Catalog-Bounded Actions (Safety-First Autonomy)

`proposed_action` MUST be exactly one of the following strings, or
`null` if no automated fix is safe to propose:

  - "restart-pod"               — the container is in a transient crash
                                  state (OOMKilled, transient panic,
                                  startup probe flake). No parameters.
  - "rollback-deployment"       — a recent release introduced the bug;
                                  rolling back to the prior revision
                                  should restore service. REQUIRES BOTH
                                  `proposed_parameters.deployment`
                                  (string — the Deployment name, drawn
                                  verbatim from the evidence; do NOT
                                  guess) AND `proposed_parameters
                                  .to_revision` (integer — the prior
                                  stable revision number, also drawn
                                  verbatim from the evidence). If
                                  EITHER value is not cited in the
                                  evidence, return null instead.
  - "scale-deployment"          — load exceeded capacity and the pod was
                                  crash-killed under pressure. REQUIRES
                                  BOTH `proposed_parameters.deployment`
                                  (string — the Deployment name, drawn
                                  verbatim from the evidence) AND
                                  `proposed_parameters.to_replicas`
                                  (integer — the new replica count). If
                                  EITHER value cannot be grounded in
                                  the evidence, return null instead.
  - "delete-pod-to-reschedule"  — the pod is stuck (wedged init
                                  container) and a controller-managed
                                  recreate will resolve it. No
                                  parameters.

Do NOT invent new action names. Do NOT propose kubectl exec, --force
deletes, manifest edits, or anything not in the four-entry list above.
If none applies, return `proposed_action: null` and explain in the
hypothesis what the on-call should inspect next.

# Output format

Respond ONLY with a JSON object — no prose, no markdown, no code fence.
The object MUST contain exactly these six keys:

  "root_cause_hypothesis" : one sentence, grounded in cited_indices.
  "cited_indices"         : list of 0-based integers (MUST be non-empty).
  "confidence"            : "low", "medium", or "high".
  "runner_up_causes"      : list of short alternative-hypothesis strings
                            (may be []).
  "proposed_action"       : one of the four catalog strings, or null.
  "proposed_parameters"   : action-specific params (e.g. {"deployment":
                            "payments-api", "to_revision": 7}). Use {}
                            when the action needs no params or
                            proposed_action is null.

Example of a valid response:
{"root_cause_hypothesis":"The api-server container is in a Go panic crash-loop after dereferencing a nil pointer in main.processRequest.","cited_indices":[0,2,3],"confidence":"high","runner_up_causes":["OOMKilled","misconfigured liveness probe"],"proposed_action":"restart-pod","proposed_parameters":{}}
"""


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------

class ApplicationExpert(BaseExpert):
    """Application-domain Expert backed by the shared BaseExpert pipeline."""

    domain = "Application"
    _system_prompt = _APP_SYSTEM_PROMPT

    def _stub_diagnosis(self, state: WorkflowState) -> ExpertDiagnosis:
        """Hard-coded diagnosis for scaffolding runs and offline unit tests.

        NEVER called by the real LangGraph path (``__call__`` →
        ``_run_real_diagnosis``).  See ``BaseExpert._stub_diagnosis`` docstring
        for the rationale.
        """
        first_hit = self._first_hit(state)
        incident = state.get("incident", None)
        fix_target = (
            incident.target if incident is not None
            else Target(namespace="default", pod="app-pod-xyz")
        )

        proposed_fix = ProposedFix.build(
            action_type="restart-pod",
            target=fix_target,
            parameters={},
            permission_scope=PERMISSION_SCOPES["restart-pod"],
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
            model="stub",
            tokens=0,
        )


# ---------------------------------------------------------------------------
# Module-level callable for LangGraph node registration (builder.py imports
# this exact symbol — do not rename).
# ---------------------------------------------------------------------------
application_expert_node = ApplicationExpert()
