# Feature Specification: Network Expert Agent Node

**Feature Branch**: `007-network-agent`

**Created**: 2026-05-15

**Status**: Draft

**Input**: User description: "Create the file `src/agent/graph/nodes/experts/network.py`. Implement the `network_expert_node` function that takes `WorkflowState` and returns an updated state with an `ExpertDiagnosis`. Use the LLM with structured output (ExpertDiagnosis) to ensure the output is valid Pydantic. Define the System Prompt for this node as a 'Senior Network SRE' specializing in Kubernetes connectivity. Instruct the LLM to analyze `FilteredEvidence` for: DNS failures (e.g., 'getaddrinfo ENOTFOUND'); Connection refused / Timeouts (e.g., 'upstream timed out', '502 Bad Gateway'); TLS Handshake failures. Enforce strict adherence to the Remediation Catalog: For transient network or DNS issues, it may propose 'restart-pod' to clear state. For infrastructure-level misconfigurations detected in logs, it must propose 'rollback-deployment' or return null if it cannot be automated. Validation Rule: The agent MUST cite at least one specific log line in the `cited_evidence` field."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Diagnose a network-routed incident with a cited fix (Priority: P1)

The Router has classified an incident as `Network`. The Network Expert receives the shared workflow state (including `FilteredEvidence` already redacted and pattern-tagged) and produces an `ExpertDiagnosis`: a one-line root-cause hypothesis ("DNS resolution failure on the upstream service"), at least one cited log excerpt that supports the hypothesis, a confidence level, and either a catalog-bound proposed fix or an explicit "no automatic fix available" decision. No mutation is attempted — the diagnosis flows downstream to the Reporter/HITL stage exactly like the Application Expert does.

**Why this priority**: This is the network half of the Routed Triage Workflow's expert tier. Without it, every Router decision of `Network` short-circuits to "no expert diagnosis," and the on-call engineer sees no proposed remediation for the largest class of Kubernetes incidents (connectivity issues). Shipping this slice alone delivers immediate value: assisted triage for network incidents with cited evidence.

**Independent Test**: Construct a `WorkflowState` fixture whose `classification == Network` and whose `evidence.logs` contain a known network signature (e.g., `getaddrinfo ENOTFOUND foo.svc.cluster.local`). Invoke `network_expert_node(state)`. Assert the returned state carries a populated `diagnosis` with: (a) a hypothesis mentioning DNS, (b) `cited_evidence` containing the exact offending log line, (c) a confidence value, and (d) a `proposed_fix` of `restart-pod` or `null` per the catalog rules — and that no other state fields are clobbered.

**Acceptance Scenarios**:

1. **Given** a network-routed incident whose logs contain DNS-failure markers (`getaddrinfo ENOTFOUND`, `name resolution failed`, `no such host`), **When** the Network Expert runs, **Then** the diagnosis names DNS as the root cause, cites the exact log line(s), and proposes `restart-pod` (transient remediation).
2. **Given** logs containing connection-refused or upstream-timeout markers (`connection refused`, `upstream timed out`, `502 Bad Gateway`, `i/o timeout`), **When** the Network Expert runs, **Then** the diagnosis names the connectivity class, cites supporting line(s), and proposes `restart-pod` for transient pod-side state, OR `null` if the evidence points to an external/non-automatable cause.
3. **Given** logs containing TLS handshake errors (`tls: handshake failure`, `x509: certificate signed by unknown authority`, `SSL_ERROR_*`), **When** the Network Expert runs, **Then** the diagnosis names the TLS class, cites supporting line(s), and the proposed fix is `rollback-deployment` if the evidence ties the failure to a recent deployment change, otherwise `null` (manual triage required — certificates are infrastructure-level).
4. **Given** a network-routed incident whose `FilteredEvidence` shows no recognizable network signature (only fallback last-K lines, no triggered patterns), **When** the Network Expert runs, **Then** the diagnosis is `low` confidence with `proposed_fix = null`, and the cited evidence still contains at least one log line (drawn from the fallback excerpt) — never empty.
5. **Given** the LLM returns a structurally invalid response (missing required fields, wrong types, or claims a fix outside the catalog), **When** the node parses the response, **Then** the node MUST fail-closed: emit a low-confidence diagnosis with `proposed_fix = null`, record the validation failure in the audit trail, and never propagate a malformed `ExpertDiagnosis` downstream.

---

### User Story 2 - Strict adherence to the allowed-remediation catalog (Priority: P1)

The Network Expert is forbidden from inventing remediations. The only fixes it may propose are entries from the project's existing allowed-remediation catalog (defined in spec 002, `Assumptions`): `restart-pod`, `rollback-deployment`, `scale-deployment`, `delete-pod-to-reschedule`. For the network domain specifically, only `restart-pod` (transient) and `rollback-deployment` (when logs implicate a recent deploy) are sanctioned at MVP; anything else MUST resolve to `null` (no automatic fix).

**Why this priority**: Principle I (safety) and FR-011/FR-021 from spec 002. Even a perfect diagnosis becomes dangerous if the proposed action is unbounded — the Solver will refuse a non-catalog action, but the Reporter would still surface a misleading fix in chat. P1 because shipping US1 without this constraint would mean shipping a node that could embarrass the system with un-executable suggestions.

**Independent Test**: Feed the node a synthetic state with logs that would tempt an unconstrained model into proposing `iptables-flush`, `kubectl exec ... restart networking`, `apply networkpolicy`, or any free-form shell. Assert the returned `proposed_fix` is one of: a catalog action with valid parameters, or `null`. Repeat across at least one fixture per non-catalog temptation (DNS resolver tweak, NetworkPolicy edit, MTU change).

**Acceptance Scenarios**:

1. **Given** logs whose obvious "correct" fix is a non-catalog action (e.g., editing a `NetworkPolicy`), **When** the Network Expert runs, **Then** `proposed_fix` is `null` and the diagnosis explicitly says automatic remediation is not available for this cause.
2. **Given** logs whose evidence ties the failure to a recent deployment rollout (image-tag/version mentioned alongside the failure window), **When** the Network Expert runs, **Then** the proposed fix is `rollback-deployment` with the target Deployment named.
3. **Given** logs whose evidence indicates a transient pod-local network condition (DNS cache, connection-pool exhaustion in a sidecar), **When** the Network Expert runs, **Then** the proposed fix is `restart-pod` with the target pod named.
4. **Given** an LLM output that names a remediation outside the catalog, **When** the structured-output validator rejects the response, **Then** the node falls back to `proposed_fix = null` and records the rejection.

---

### User Story 3 - Cited evidence is mandatory (Priority: P1)

Every diagnosis the Network Expert produces MUST include at least one specific log line in `cited_evidence`. A diagnosis without citation is a release-blocking defect.

**Why this priority**: Principle "no uncited claims" (Constitution / SC-005). A network diagnosis without evidence is worse than no diagnosis — it presents machine guesswork as fact, which is exactly what the cited-evidence rule exists to prevent. P1 because this is enforced as a release gate by SC-005 across the platform.

**Independent Test**: Run the Network Expert against every fixture in the project's network-incident benchmark and assert `len(diagnosis.cited_evidence) >= 1` for 100% of outputs, including the "no-signal" fallback path (which cites a fallback line) and the validation-failure path (which cites whatever line is available, or returns the node-level fail-closed diagnosis).

**Acceptance Scenarios**:

1. **Given** any network-routed incident where the Network Expert produces a diagnosis, **When** the diagnosis is inspected, **Then** `cited_evidence` is non-empty and every cited line text appears verbatim somewhere in `evidence.logs` or `evidence.events`.
2. **Given** an LLM response that omits `cited_evidence` or returns an empty list, **When** the structured-output validator runs, **Then** the response is rejected, the node fails closed (per US1 #5), and the failure is recorded.

---

### Edge Cases

- **Evidence is empty or only fallback lines**: The node still emits a diagnosis (low confidence, `null` fix), cites at least one fallback line, and does not crash.
- **Mixed signals (network + application)**: The Router has already chosen `Network`; the Network Expert MUST stay in-domain. If the strongest evidence is application-flavored (e.g., a Python traceback) the expert reports low confidence and `null` fix rather than re-routing.
- **Logs reference a multi-pod target (Deployment with N replicas)**: The proposed `restart-pod` MUST name a specific pod; if no single pod is identifiable, the fix degrades to `null` (the Solver would refuse a wildcard).
- **Evidence cites a Service or Ingress, not a Pod**: `restart-pod` is not applicable. The expert proposes `rollback-deployment` only if a Deployment ownership chain is evident in `evidence.resource_status`/`events`; otherwise `null`.
- **TLS error rooted in expired cert from a Secret**: Not automatable from the catalog. Diagnosis names the cause; `proposed_fix = null`.
- **LLM returns a confident hypothesis with no supporting log line**: Rejected by the structured-output validator (US3 #2).
- **LLM exceeds the per-incident token / cost ceiling (spec 002 FR-029)**: The node halts, emits a "budget exceeded — partial result" diagnosis with `null` fix, and surfaces the partial-result notice upstream.
- **Redaction stripped the failing line**: If the cited line text was redacted (secrets/PII), the expert cites the redacted form (the redaction marker tokens are stable text) — never the unredacted original.

## Requirements *(mandatory)*

### Functional Requirements

**Node contract**

- **FR-001**: The system MUST provide a `network_expert_node` callable that accepts the shared `WorkflowState` and returns a state update containing a populated `diagnosis` field of type `ExpertDiagnosis`. No other state fields may be overwritten by this node.
- **FR-002**: The node MUST be wired as the destination of the Router's `Network` conditional edge in the LangGraph workflow defined by spec 002. The Router and downstream Reporter contracts are unchanged.
- **FR-003**: The node MUST be the sole entry point for network-domain diagnosis; no other graph node may produce a `diagnosis` when `classification == Network`.

**LLM invocation & output validation**

- **FR-004**: The node MUST invoke the configured LLM with structured output bound to the `ExpertDiagnosis` pydantic schema, so any response that fails schema validation is rejected before reaching downstream nodes.
- **FR-005**: The LLM MUST be prompted as a **Senior Network SRE** with deep Kubernetes connectivity expertise (DNS, service mesh, ingress, kube-proxy, CoreDNS, TLS, NetworkPolicy). The exact system-prompt text is an implementation detail captured in the plan; the role and domain framing are fixed by this spec.
- **FR-006**: The prompt MUST instruct the model to scan `FilteredEvidence` for at least the following network-failure classes:
    - **DNS failures**: e.g., `getaddrinfo ENOTFOUND`, `name resolution failed`, `no such host`, `SERVFAIL`, `NXDOMAIN`.
    - **Connection refused / timeouts**: e.g., `connection refused`, `upstream timed out`, `502 Bad Gateway`, `504 Gateway Timeout`, `i/o timeout`, `ECONNREFUSED`, `ETIMEDOUT`, `connection reset`.
    - **TLS handshake failures**: e.g., `tls: handshake failure`, `x509: certificate signed by unknown authority`, `certificate has expired`, `SSL_ERROR_*`, `bad certificate`.
- **FR-007**: The prompt MUST forbid free-form remediations; the model MUST choose `proposed_fix` from a closed enumeration corresponding to the allowed-remediation catalog (spec 002 Assumptions), or `null`. If the model returns a fix outside the enumeration, the structured-output binding rejects it (FR-004).

**Remediation rules (network-specific subset of the catalog)**

- **FR-008**: For evidence consistent with a **transient** network or DNS condition local to a pod (DNS-cache poisoning, ephemeral connection-pool exhaustion in a sidecar, transient TLS handshake races), the expert MAY propose `restart-pod` targeting the specific pod identified in the evidence.
- **FR-009**: For evidence consistent with an **infrastructure-level misconfiguration** that aligns in time with a recent Deployment rollout (image tag / version mentioned around the failure window, or `events` show a recent `Deployment` update), the expert MUST propose `rollback-deployment` targeting the implicated Deployment.
- **FR-010**: For evidence that does not match FR-008 or FR-009 — including non-automatable causes such as expired certificates from a Secret, NetworkPolicy misconfiguration, external DNS provider outages, MTU/CNI plumbing issues, or ambiguous multi-pod signals — the expert MUST return `proposed_fix = null` and state in the hypothesis that automatic remediation is not available.
- **FR-011**: The expert MUST NOT propose any catalog action other than `restart-pod`, `rollback-deployment`, or `null` for the MVP. (Other catalog entries — `scale-deployment`, `delete-pod-to-reschedule` — are out of scope for the network domain in MVP.)

**Cited-evidence validation**

- **FR-012**: Every `ExpertDiagnosis` produced by this node MUST contain at least one item in `cited_evidence`. An empty `cited_evidence` list is a validation error.
- **FR-013**: Each cited evidence item MUST be a verbatim substring of `evidence.logs` or `evidence.events` carried in the input `WorkflowState`. The node MUST verify this post-hoc (after LLM response, before returning the state update) and reject responses that fabricate citations.
- **FR-014**: When the verification in FR-013 fails, the node MUST fail closed: emit a low-confidence diagnosis with `proposed_fix = null`, cite the most relevant available evidence line as a best-effort fallback, and record the fabrication in the audit trail.

**Safety, audit, and cross-cutting**

- **FR-015**: The node MUST NOT issue any mutating cluster call directly. It only produces `ExpertDiagnosis`. All execution remains the Solver's responsibility (spec 002 FR-020).
- **FR-016**: The node MUST honor the per-incident token / cost ceiling (spec 002 FR-029). On exceeding the ceiling, it emits a "budget exceeded — partial result" diagnosis with `proposed_fix = null` and a fallback cited line.
- **FR-017**: The node MUST emit audit records (prompts sent, structured response received, validation outcome, fabrication-rejection events) tagged with the shared `incident_id`, joined to the rest of the workflow's audit trail (spec 002 FR-028).
- **FR-018**: The node MUST refuse to propose a fix that would require permissions the platform does not hold for the target (spec 002 FR-012). If the model proposes such a fix, the node downgrades to `proposed_fix = null`.

### Key Entities *(include if feature involves data)*

- **NetworkExpertNode**: A LangGraph node bound to the `Network` branch of the Router. Reads `WorkflowState` (in particular `evidence`, `classification`, and `alert_payload`); writes `diagnosis` and (via the Expert contract) `proposed_fix` onto the state. Stateless across incidents — every invocation is independent.
- **ExpertDiagnosis (Network variant)**: The shared `ExpertDiagnosis` pydantic model (defined in spec 002 `data-model.md`), populated with: `hypothesis` (root cause in plain English), `cited_evidence` (≥1 verbatim log/event line), `confidence` (`low` / `medium` / `high`), `proposed_fix` (a catalog action with target+parameters, or `null`), and any runner-up causes considered. No new entity is introduced by this feature; the schema is inherited.
- **NetworkFailureSignal**: A taxonomy used inside the system prompt only (not a persisted entity). Three classes: `DNS`, `ConnectionRefusedOrTimeout`, `TLSHandshake`. The model is asked to tag the dominant signal class in its hypothesis to make routing-tier diagnostics easier to evaluate against the benchmark.

### Shared Workflow State Interaction (delta only)

This feature does NOT change the workflow state schema defined in spec 002. It only writes to fields that were already declared there. For convenience:

| Field | Read or Write | Notes |
|---|---|---|
| `incident_id` | Read | Carried through to audit records and structured-output traces. |
| `evidence` | Read | The sole input to the LLM prompt body, after the system prompt. The node MUST NOT re-fetch logs — it consumes exactly what Ingest produced. |
| `classification` | Read | Pre-condition guard. The node MAY assert `classification == Network` and fail closed otherwise (defense in depth against mis-routing). |
| `diagnosis` | Write | Set to the validated `ExpertDiagnosis`. |
| `proposed_fix` | Write (via diagnosis) | Carried inside `ExpertDiagnosis`; downstream Reporter/HITL freezes it (spec 002 FR-016). |
| `alert_payload` | Read (optional) | The node MAY use the alert's target reference to disambiguate multi-pod evidence; it MUST NOT use it to invent claims beyond what `evidence` supports. |

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of `ExpertDiagnosis` objects emitted by this node carry at least one item in `cited_evidence`, and every cited item appears verbatim in the input `evidence` (no fabricated citations). Verified by an audit-completeness job on every release.
- **SC-002**: 100% of proposed fixes emitted by this node belong to the allowed-remediation catalog (or are `null`); zero free-form / non-catalog proposals are surfaced to users across the network-incident benchmark and adversarial test suite.
- **SC-003**: On a labeled network-incident benchmark, the expert's proposed fix matches the human-chosen remediation at least 70% of the time (consistent with spec 002 SC-002 reported per-domain). Performance is reported separately for the DNS, ConnectionRefusedOrTimeout, and TLSHandshake signal classes so weaknesses are visible.
- **SC-004**: Median wall-clock time for `network_expert_node` execution (LLM call included) is under 8 seconds; p95 is under 20 seconds. This budget keeps the end-to-end report under spec 002 SC-003 (30s median / 60s p95).
- **SC-005**: On the adversarial "tempt the model into non-catalog fixes" suite (NetworkPolicy edits, `iptables`, sidecar config patches, etc.), 100% of outputs resolve to a catalog action or `null` — zero leak of out-of-catalog suggestions.
- **SC-006**: 100% of fail-closed paths (LLM validation failure, fabricated citation, budget exceeded, permission scope missing) produce a low-confidence diagnosis with `proposed_fix = null` and a non-empty `cited_evidence` — never a hard crash that breaks the graph run.
- **SC-007**: Zero unredacted secrets appear in audited prompts/responses from this node on the continuously running redaction audit (consistent with spec 002 SC-009).

## Assumptions

- **Catalog inheritance**: The allowed-remediation catalog and its Forward → Inverse mappings are defined in spec 002 (`Assumptions`). This feature does NOT add new catalog entries; it only declares which entries the network domain may propose at MVP (`restart-pod`, `rollback-deployment`, or `null`).
- **Expert contract reuse**: The `ExpertDiagnosis` pydantic schema, the `WorkflowState` shape, the structured-output binding pattern, and the audit-record envelope are reused exactly as defined by spec 002 and as implemented by the Application Expert (spec 004). This feature MUST NOT diverge from those contracts.
- **FilteredEvidence inputs**: The node consumes `evidence` exactly as produced by the Ingest + pre-filter stage of spec 002 — specifically, the `logs`, `events`, and `resource_status` fields, all already redacted at the MCP boundary. The node MUST NOT call MCP read tools directly.
- **Pattern lists are guidance, not gates**: The example patterns in FR-006 are illustrative of the network signals the model should recognize; the model is not restricted to literal regex matching. The pre-filter (spec 002 FR-004) already runs domain regexes upstream — the expert is free to reason about evidence the pre-filter surfaced even if the exact strings differ.
- **Domain boundary**: The Network Expert stays in the network domain. Even if the strongest evidence in the input is application- or DB-flavored (a mis-route from the Router), the expert returns low confidence and `null` fix rather than poaching another expert's classification. Re-routing is out of scope for the expert tier in MVP.
- **TLS-from-Secret is non-automatable in MVP**: Expired or wrong certificates rooted in Kubernetes Secrets are diagnosable but not auto-remediable from the MVP catalog. The expert names the cause and returns `proposed_fix = null`; a future catalog entry could change this.
- **Model and prompt details**: The specific LLM tier, system-prompt phrasing, and structured-output adapter are implementation concerns governed by the constitution and the project's existing LLM-tiering policy. They are pinned in the plan, not in this spec.
- **Test fixtures**: Network-incident fixtures (DNS, connection-refused, TLS) live alongside the existing benchmark fixtures used by the Router and Application Expert. This feature MUST extend that fixture set with at least one fixture per FR-006 signal class and at least one adversarial fixture per FR-011 temptation.
