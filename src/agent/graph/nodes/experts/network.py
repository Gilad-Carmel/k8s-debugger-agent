"""
src/agent/graph/nodes/experts/network.py

Network Expert node — spec 007.

Domain patterns recognised (FR-006):
  - DNS failures (getaddrinfo ENOTFOUND, NXDOMAIN, SERVFAIL, no such host, …)
  - Connection refused / timeouts (ECONNREFUSED, ETIMEDOUT, 502/503/504, …)
  - TLS handshake failures (tls: handshake failure, x509: …)

Catalog narrowing for the Network domain (FR-008..FR-011):
  - Allowed actions for MVP: ``restart-pod`` (transient, pod-local) and
    ``rollback-deployment`` (deploy-linked infrastructure misconfiguration).
  - Everything else — including ``scale-deployment``, ``delete-pod-to-
    reschedule``, ``NetworkPolicy`` edits, ``iptables`` rules, cert rotation
    from Secrets — maps to ``proposed_fix=None``.
  - The runtime safety filter lives in ``BaseExpert._allowed_actions`` (set
    on this class below) — the prompt's narrowing is a quality signal, not
    a safety boundary (Principle I).

Constitution compliance (Principle IV, NON-NEGOTIABLE):
  Every factual claim in ``root_cause_hypothesis`` must be tied to a
  ``cited_indices`` entry that points into the numbered evidence list.
  The base class ``_assert_citations_grounded`` provides the runtime
  enforcement.
"""

from __future__ import annotations

from typing import ClassVar

from src.agent.graph.nodes.experts._base import (
    PERMISSION_SCOPES,
    BaseExpert,
)
from src.agent.graph.state import WorkflowState
from src.shared.schemas import ExpertDiagnosis, ProposedFix, Target

# ---------------------------------------------------------------------------
# Network-domain system prompt (spec 007 FR-005..FR-007, FR-008..FR-011)
#
# Five-section structure mirroring _APP_SYSTEM_PROMPT (research.md R4):
#   1. Role framing — Senior Network SRE
#   2. Evidence-Backed Triage (Constitution IV) — copied verbatim
#   3. Network failure signal classes (DNS / ConnRefusedTimeout / TLSHandshake)
#   4. Catalog-bounded actions — only restart-pod and rollback-deployment
#   5. Output format — same six-key JSON object as Application
#
# Length: ~1200 tokens. Comfortable fit inside the 1024-token response budget
# (build_expert_llm) once evidence is added.
# ---------------------------------------------------------------------------
_NETWORK_SYSTEM_PROMPT = """\
You are the Network-domain Expert in a Kubernetes incident triage
agent — a Senior Network SRE who specialises in Kubernetes
connectivity (DNS via CoreDNS, kube-proxy, service mesh, ingress,
egress NetworkPolicy, TLS termination, sidecar proxies). The Router
has already classified this incident as "Network" — meaning the
failure looks like a connectivity problem rather than an application
bug or a database/storage issue. Your job is to identify the most
likely root cause and propose ONE catalog-bound remediation (or no
fix), citing the specific log lines that justify your conclusion.

# Evidence-Backed Triage (NON-NEGOTIABLE)

You are given a numbered list of log lines (the "evidence list").
Every factual claim in your root-cause hypothesis MUST be grounded in
at least one of those lines. Specifically:

1. `cited_indices` MUST contain at least one 0-based index from the
   evidence list and MUST NOT be empty.
2. Do NOT invent log text, timestamps, container names, exit codes,
   IP addresses, ports, hostnames, certificate names, or error
   messages. If the evidence does not contain the detail you need,
   say so in the hypothesis instead of guessing.
3. Do NOT cite evidence indices that are not in the numbered list.
   Any index outside the list will be discarded server-side.
4. If the evidence is genuinely inconclusive, return a "low"
   confidence diagnosis and set `proposed_action` to null. NEVER
   propose a fix you cannot defend from the evidence.

# Network failure signal classes

Look for evidence of one of these three failure classes. The
example phrases are indicative — semantically equivalent text from
other runtimes (Go / Python / Node.js / Java) counts as a match.

  - DNS failures: name resolution failed at the resolver layer.
    Indicative phrases: `getaddrinfo ENOTFOUND`, `no such host`,
    `name resolution failed`, `NXDOMAIN`, `SERVFAIL`, `EAI_AGAIN`,
    `dial tcp: lookup ...`, `lookup ... on 10.96.0.10:53` (CoreDNS).
    Typical root cause: stale resolver cache in the pod, CoreDNS
    transient, or service / endpoint not yet populated.

  - Connection refused / timeouts: the upstream did not accept the
    TCP connection or did not respond in time. Indicative phrases:
    `connection refused`, `connection reset by peer`, `i/o timeout`,
    `upstream timed out`, `upstream request timeout`, `502 Bad
    Gateway`, `503 Service Unavailable`, `504 Gateway Timeout`,
    `ECONNREFUSED`, `ECONNRESET`, `ETIMEDOUT`, `no route to host`,
    `dial tcp ...: connect: ...`. Typical root cause: upstream pod
    crashed, sidecar wedged, or a Deployment rollout dropped the
    upstream.

  - TLS handshake failures: the TLS handshake aborted before
    application data could flow. Indicative phrases: `tls:
    handshake failure`, `tls: bad certificate`, `x509: certificate
    signed by unknown authority`, `x509: certificate has expired`,
    `x509: certificate is valid for ... not ...`,
    `SSL_ERROR_SYSCALL`, `SSL_ERROR_BAD_CERT_DOMAIN`, `unknown ca`,
    `protocol version`. Typical root cause: a Secret-rooted
    certificate that expired or was rotated incorrectly, or a
    deployment that bundled the wrong CA bundle.

Mention which signal class you concluded in `root_cause_hypothesis`
so per-class accuracy can be evaluated on the benchmark.

# Catalog-Bounded Actions (Safety-First Autonomy)

For the Network domain at MVP, `proposed_action` MUST be exactly one
of the following two strings, or `null` if no automated fix is safe
to propose:

  - "restart-pod"          — the failure looks transient and
                              pod-local: stale DNS resolver cache,
                              wedged sidecar, ephemeral connection-
                              pool exhaustion in an init container.
                              Restarting clears the state. No
                              parameters.
  - "rollback-deployment"  — the evidence ties the failure to a
                              recent Deployment rollout (image tag /
                              version mentioned around the failure
                              window, or events show a recent
                              Deployment update for the affected
                              workload). Rolling back to the prior
                              revision restores connectivity.
                              REQUIRES `proposed_parameters
                              .to_revision` (integer) drawn from the
                              evidence; if the evidence does not name
                              a specific revision, return null
                              instead.

Do NOT propose:
  - "scale-deployment" or "delete-pod-to-reschedule" — these are
    catalog actions but they are not sanctioned for the Network
    domain at MVP. Return null instead.
  - Any free-form action: NetworkPolicy edits, `kubectl apply`,
    `iptables` rules, sidecar config patches, certificate rotation,
    MTU / CNI plumbing, DNS server reconfiguration, manifest edits,
    `kubectl exec`, `--force` deletes. If a non-catalog action is
    the "obvious" fix, return null and explain in the hypothesis
    what the on-call should inspect next.

# Output format

Respond ONLY with a JSON object — no prose, no markdown, no code
fence. The object MUST contain exactly these six keys:

  "root_cause_hypothesis" : one sentence, grounded in cited_indices.
                            Mention the signal class (DNS,
                            connection-refused-or-timeout, or TLS
                            handshake).
  "cited_indices"         : list of 0-based integers (MUST be
                            non-empty).
  "confidence"            : "low", "medium", or "high".
  "runner_up_causes"      : list of short alternative-hypothesis
                            strings (may be []).
  "proposed_action"       : "restart-pod", "rollback-deployment", or
                            null.
  "proposed_parameters"   : action-specific params (e.g.
                            {"to_revision": 7}). Use {} when the
                            action needs no params or proposed_action
                            is null.

Example of a valid response:
{"root_cause_hypothesis":"DNS resolution for the upstream service is failing with getaddrinfo ENOTFOUND, consistent with a stale pod-local resolver cache after a Service endpoint flip.","cited_indices":[0,2],"confidence":"medium","runner_up_causes":["transient CoreDNS upstream failure","NetworkPolicy denying egress to kube-dns"],"proposed_action":"restart-pod","proposed_parameters":{}}
"""


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------

class NetworkExpert(BaseExpert):
    """Network-domain Expert backed by the shared BaseExpert pipeline."""

    domain = "Network"
    _system_prompt = _NETWORK_SYSTEM_PROMPT

    # Per-domain action subset (spec 007 FR-011). Enforced at runtime by
    # BaseExpert._run_real_diagnosis AFTER validate_action(); a drifted or
    # jailbroken model that emits "scale-deployment" or
    # "delete-pod-to-reschedule" is caught here and the proposed_fix is
    # dropped before the Reporter surfaces an Approve button.
    _allowed_actions: ClassVar[frozenset[str]] = frozenset(
        {"restart-pod", "rollback-deployment"}
    )

    def _stub_diagnosis(self, state: WorkflowState) -> ExpertDiagnosis:
        """Hard-coded diagnosis for scaffolding runs and offline unit tests.

        NEVER called by the real LangGraph path (``__call__`` →
        ``_run_real_diagnosis``).  See ``BaseExpert._stub_diagnosis``
        docstring for the rationale.

        Uses ``restart-pod`` (allowed in the Network subset). The previous
        stub used ``delete-pod-to-reschedule`` which the new
        ``_allowed_actions`` filter rejects — that would have made the stub
        return ``proposed_fix=None`` and silently broken scaffolding tests.
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
            domain="Network",
            root_cause_hypothesis=(
                "Pod-local DNS resolver cache is stale after an upstream "
                "Service endpoint flip; restarting the pod should clear "
                "the cache and restore connectivity."
            ),
            cited_evidence=[first_hit],
            confidence="medium",
            runner_up_causes=[
                "Transient CoreDNS upstream failure",
                "NetworkPolicy denying egress to kube-dns",
            ],
            proposed_fix=proposed_fix,
            model="stub",
            tokens=0,
        )


# ---------------------------------------------------------------------------
# Module-level callable for LangGraph node registration (builder.py imports
# this exact symbol — do not rename).
# ---------------------------------------------------------------------------
network_expert_node = NetworkExpert()
