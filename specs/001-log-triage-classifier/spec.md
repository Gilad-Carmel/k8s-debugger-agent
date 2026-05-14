# Feature Specification: Log-Based Network Issue Triage Classifier

**Feature Branch**: `001-log-triage-classifier`

**Created**: 2026-05-14

**Status**: Draft

**Input**: User description: "we are building an Agentic DevOps platform focused on automated triage and efficiency. The way it works (as a start) is to use K8S MCP to fetch logs (with grep for lightweight) and classify using an LLM the network type"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Classify a network issue from a target pod's logs (Priority: P1)

An on-call engineer suspects a network-related problem in a specific Kubernetes pod. From a single command (chat invocation, CLI call, or web action), they point the triage agent at a pod and time window. The agent fetches recent logs from that pod, narrows them to lines that look network-relevant, asks an LLM to classify the predominant network issue category, and returns a one-line verdict plus the supporting log excerpts. The engineer can decide in seconds whether the issue is a DNS failure, a connection timeout, a TLS problem, a refused/reset connection, an unreachable destination, a rate-limit/throttle, or "no network signal found."

**Why this priority**: This is the MVP. Without it, the platform delivers no triage value. It is also the smallest end-to-end slice that exercises every part of the pipeline (target selection → log fetch → filtering → classification → evidence-cited output), so it validates the architecture for follow-on issue categories.

**Independent Test**: Run the agent against a pod whose logs are known to contain a specific network failure type (seeded test fixture). Verify the returned label matches the seeded category and that the cited evidence lines are present in the fetched logs.

**Acceptance Scenarios**:

1. **Given** a pod whose logs contain repeated "connection refused" errors, **When** the engineer triages that pod, **Then** the agent returns the label "connection-refused" with at least one cited log line containing that error.
2. **Given** a pod whose logs contain DNS resolution failures (e.g., NXDOMAIN, SERVFAIL, "no such host"), **When** the engineer triages that pod, **Then** the agent returns the label "dns-failure" with cited DNS-error log lines.
3. **Given** a pod whose logs contain no network-related signal in the requested window, **When** the engineer triages that pod, **Then** the agent returns "no-network-signal-found" and does NOT fabricate a category.
4. **Given** a pod the requester does not have read access to, **When** the engineer triages that pod, **Then** the agent returns a clear authorization error and performs no LLM call.

---

### User Story 2 - See the evidence and the confidence behind a classification (Priority: P2)

The engineer cannot act on a black-box label during an incident. Every classification the agent returns is accompanied by (a) cited log excerpts that support the label, (b) a confidence indicator, and (c) the runner-up categories the agent considered. When confidence is low or evidence is thin, the agent says so explicitly rather than guessing.

**Why this priority**: This is what makes the output trustworthy and is required by the project's evidence-backed-triage principle. Without it, the MVP is a liability.

**Independent Test**: Triage a pod whose logs contain mixed weak signals across two categories. Verify the response includes a confidence level below "high," lists both candidate labels with their evidence, and uses "uncertain" / "unknown" language rather than a single confident verdict.

**Acceptance Scenarios**:

1. **Given** logs with strong, repeated signal for one category, **When** triage runs, **Then** the response shows confidence "high" with at least 3 cited log lines.
2. **Given** logs with weak or ambiguous signal, **When** triage runs, **Then** the response shows confidence "low" or "medium" and lists alternate candidate labels.
3. **Given** any classification result, **When** the engineer inspects the response, **Then** every claim about the cluster state is backed by a quoted log line — no uncited assertions appear.

---

### User Story 3 - Keep triage cheap and fast on noisy pods (Priority: P3)

Production pods routinely emit tens or hundreds of thousands of log lines per hour. The agent must remain fast and economical by pre-filtering logs locally (grep-style keyword/regex match for known network patterns) before sending anything to the LLM, and by truncating to a bounded sample when filtered output is still large.

**Why this priority**: Performance and cost are platform-survival concerns, but the system is still functional (if slower and pricier) without this. Validating US1/US2 first makes sense; US3 hardens them.

**Independent Test**: Run triage against a pod producing 100k+ log lines in the window. Verify (a) the LLM input size stays below a configured cap, (b) total wall-clock latency stays within the budget, and (c) the classification quality matches a baseline run on the same fixture without the pre-filter.

**Acceptance Scenarios**:

1. **Given** a pod producing 100k log lines in the window, **When** triage runs, **Then** the bytes sent to the LLM are no greater than the configured per-request cap.
2. **Given** the per-triage token budget is exhausted before classification completes, **When** triage runs, **Then** the agent halts, returns a budget-exceeded message, and does NOT silently return a low-quality guess.

---

### Edge Cases

- **No logs in window**: The agent returns "no-logs-available" with the window it queried, not a fabricated classification.
- **Pod was restarted/rotated mid-window**: The agent retrieves logs from previous container(s) where supported and notes which container instances were sampled.
- **Multi-container pod**: The agent either triages a specified container or, by default, samples each container and reports per-container.
- **Logs contain secrets or tokens**: Secret-shaped strings (bearer tokens, AWS-style keys, Authorization headers) MUST be redacted before any LLM call and before any audit log.
- **Logs are in an unsupported language/locale**: The agent still classifies on structural patterns (status codes, error verbs) and lowers confidence accordingly.
- **Cluster credentials are missing/expired**: The agent fails fast with an actionable message before issuing any LLM call.
- **Ambiguous signal across two categories**: The agent reports both candidates with evidence rather than picking arbitrarily.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Users MUST be able to initiate a triage against a target identified by namespace + pod name (and optionally a container name).
- **FR-002**: Users MUST be able to specify a time window for log retrieval; the system MUST apply a sensible default window when none is given.
- **FR-003**: System MUST fetch the target pod's logs via Kubernetes read-only APIs; it MUST NOT issue any mutating cluster operation as part of triage.
- **FR-004**: System MUST apply a lightweight local pre-filter (grep/regex against a maintained set of network-issue patterns) to the fetched logs before passing them to the LLM.
- **FR-005**: System MUST classify the predominant network issue from a defined taxonomy: `dns-failure`, `connection-refused`, `connection-reset`, `timeout`, `tls-error`, `unreachable`, `rate-limited`, `no-network-signal-found`.
- **FR-006**: System MUST return, for every triage: the top label, a confidence indicator (low / medium / high), at least one cited log excerpt per claim, and any runner-up candidate labels considered.
- **FR-007**: System MUST surface `no-network-signal-found` rather than guess when the filtered evidence is below a configured sufficiency threshold.
- **FR-008**: System MUST redact secret-shaped tokens (bearer tokens, common cloud-credential formats, Authorization headers, Kubernetes ServiceAccount tokens) from log content before any LLM call AND before persistence to any audit record.
- **FR-009**: System MUST emit an audit record per triage containing: requester, target, time window, filtered log byte count, prompt and response, model used, token counts, total cost estimate, and a correlation ID linking all of the above.
- **FR-010**: System MUST enforce a configurable per-triage budget (tokens and/or estimated cost); when the budget would be exceeded, the system MUST halt and return a budget-exceeded result rather than silently degrading the answer.
- **FR-011**: System MUST surface a clear, actionable error when the requester lacks read access to the target pod's logs, and MUST NOT invoke the LLM in that case.
- **FR-012**: System MUST complete an interactive triage within the configured latency budget on the 95th percentile, or return a partial result indicating which stage timed out.

### Key Entities *(include if feature involves data)*

- **Triage Request**: A user-initiated request to triage a target. Holds the target identifier (namespace, pod, optional container), the time window, the requester identity, and a correlation ID.
- **Log Sample**: The set of log lines fetched for a Triage Request, plus the subset selected by the network pre-filter. Tracks total bytes, hit count, truncation flag, and container instances sampled.
- **Classification Result**: The structured outcome of a Triage Request. Holds the top label, confidence indicator, cited evidence excerpts, runner-up candidate labels, and any caveats (low confidence, truncated input, mixed signal).
- **Audit Record**: An immutable record of one triage end-to-end. Holds the Triage Request, the Log Sample summary, the prompt and response, model identifier, token and cost metrics, redactions applied, and the final Classification Result, all linked by correlation ID.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: On a labeled benchmark of representative network-failure incidents, the agent's top-1 classification matches the human-labeled category at least 80% of the time, and the correct label appears in the top-2 at least 95% of the time.
- **SC-002**: The median end-to-end triage (from user invocation to final result) completes in under 30 seconds for pods with up to 100,000 log lines in the requested window; the 95th percentile completes in under 60 seconds.
- **SC-003**: Every classification surfaced to the user is accompanied by at least one cited log excerpt; zero uncited claims appear in user-facing output across the benchmark suite.
- **SC-004**: 95% of triages stay below the configured per-triage cost ceiling; triages that would exceed it are halted with a clear message rather than silently truncated.
- **SC-005**: Zero unredacted secrets are present in audit records or LLM prompts on a continuously running redaction-audit job over the benchmark corpus.
- **SC-006**: For low-evidence inputs, the agent returns `no-network-signal-found` or a low-confidence verdict at least 90% of the time, rather than a confident guess (measured against a "no-signal" subset of the benchmark).
- **SC-007**: On-call engineers report (via a short post-incident survey) that the triage output helped them decide a next action in at least 70% of incidents where it was used.

## Assumptions

- "Network type" is interpreted as a category from the taxonomy in FR-005. Expanding the taxonomy (e.g., adding service-mesh-specific labels, ingress-specific labels) is out of scope for the MVP and will be a follow-on feature.
- Triage is initiated by a human operator (chat, CLI, or web action). Automatic triage triggered by alerts is out of scope for the MVP.
- The agent operates with read-only Kubernetes credentials scoped to the target namespace; no write or exec permissions are assumed or required for this feature.
- Log access is via standard Kubernetes log APIs surfaced through an MCP tool. No in-cluster agent, sidecar, or log-shipper is deployed by this feature.
- Logs are reasonably timely (available within the window the user asks about). Long-retention or archived-log retrieval is out of scope for the MVP.
- LLM choice, prompt structure, and model tiering are implementation concerns governed by the project constitution (cost-conscious model selection) and are not pinned by this spec.
- The user has a way to identify the target pod and namespace; pod discovery ("which pod is broken?") is a separate feature.
- The pre-filter pattern set is maintained in-repo and reviewable; updates to it follow the normal PR workflow.
