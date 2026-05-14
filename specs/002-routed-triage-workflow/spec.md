# Feature Specification: Routed Kubernetes Incident Triage and Auto-Remediation Workflow

**Feature Branch**: `002-routed-triage-workflow`

**Created**: 2026-05-14

**Status**: Draft

**Input**: User description: "Routed Kubernetes Incident Triage and Auto-Remediation Workflow — webhook ingestion + grep filter, router agent classifies into Application/Network/Database, expert agents diagnose root cause and propose a fix, Slack-style HITL report with Approve Remediation button, Solver agent executes the approved fix and reports success."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - From alert webhook to a useful triage report in chat (Priority: P1)

An alerting system fires a webhook into the platform. Within a small number of seconds, the on-call engineer sees a single chat message that contains: a one-line root-cause hypothesis, the domain it was routed to (Application / Network / Database), the cited log evidence behind the diagnosis, a confidence indicator, and an "Approve Remediation" button next to the proposed fix. No remediation has happened yet.

**Why this priority**: This is the read-only MVP slice. It exercises ingestion → filter → router → expert → report end-to-end and delivers value (faster human triage) even if the remediation half is never used. It also validates every safety, evidence, and consistency principle before any mutation lands.

**Independent Test**: Fire a synthetic webhook whose payload references a fixture pod with seeded "network/connection-refused" log signal. Verify a chat report arrives that names the Network domain, cites the refused-connection log lines, lists a plausible fix (with no automatic execution), and shows the Approve button in a not-yet-clicked state.

**Acceptance Scenarios**:

1. **Given** a webhook payload referencing a pod with a known application-error stack trace, **When** the workflow processes it, **Then** the report is routed to the Application expert and cites the relevant trace lines in the chat message.
2. **Given** a webhook payload referencing a pod with DNS / connection failures, **When** the workflow processes it, **Then** the report is routed to the Network expert and cites the network-error log lines.
3. **Given** a webhook payload referencing a pod whose logs show DB connection-pool exhaustion or query timeouts, **When** the workflow processes it, **Then** the report is routed to the Database expert and cites the DB-error log lines.
4. **Given** a webhook payload whose logs do not yield enough signal to classify, **When** the workflow processes it, **Then** the report is sent with classification "unknown," no fix proposed, and the Approve button is absent or disabled.
5. **Given** a webhook arrives for a pod the platform cannot read (auth/scope), **When** the workflow runs, **Then** the user sees a clear authorization error in chat and no LLM calls are made.

---

### User Story 2 - Human-in-the-loop approval before any remediation (Priority: P1)

The chat message produced in US1 is also a control surface. The on-call engineer can click "Approve Remediation" to authorize the proposed fix, or "Reject" to dismiss it. Approval is required, scoped to this single proposed fix, and never inferred. Until an approval click occurs, no mutating action is taken against the cluster.

**Why this priority**: This is the safety gate. Without it, the platform mutates production on machine judgement alone, which violates Principle I. P1, not P2, because shipping US1 without this would mean shipping a tool that *could* be wired to mutate prod — too risky to release in halves.

**Independent Test**: Run US1 end-to-end against a fixture so that a proposed fix appears in chat. Verify (a) no cluster mutation occurs in the absence of a click, (b) the Approve click is required per fix and not cached for subsequent incidents, and (c) the audit log records the approver's identity, timestamp, and the exact fix that was approved.

**Acceptance Scenarios**:

1. **Given** a report has been delivered with a proposed fix, **When** no one clicks Approve, **Then** no cluster mutation is issued for the lifetime of the report, and the report shows "pending approval" in its status field.
2. **Given** a report is pending and the configured approval window elapses, **When** the window expires, **Then** the Approve button is disabled, the report shows "expired," and any subsequent attempt to approve is rejected.
3. **Given** a report is pending, **When** a user without the required role clicks Approve, **Then** the click is rejected with a clear message, and the audit log records the rejected attempt.
4. **Given** a report has been delivered for incident A, **When** a similar incident B arrives later, **Then** incident B requires its own approval — prior approval does NOT carry over.
5. **Given** a user clicks Reject, **When** the workflow records the rejection, **Then** the report shows "rejected by <user> at <time>," no remediation occurs, and the audit log captures the rejecter and reason if provided.

---

### User Story 3 - Solver executes the approved fix and reports the outcome (Priority: P2)

Once a fix is approved, the Solver agent executes the exact action that was shown in the report (no late substitution), waits for the action to take effect, verifies the post-state, and posts a follow-up message in the same chat thread: success, partial, or failure, with the reversal recipe in case the user wants to undo.

**Why this priority**: This is the autonomy payoff and is necessary for the platform's promise of "auto-remediation," but US1 + US2 already deliver assisted triage value. US3 is the step that requires the most safety scrutiny, so it ships after US1/US2 are stable.

**Independent Test**: Stand up a fixture cluster, deliver a report whose proposed fix is "restart pod X" (a reversible, well-understood mutation), approve it as an authorized user, verify the pod is restarted, verify the follow-up message reports success with the reversal recipe, and verify the audit log contains pre-state, action, post-state, and reversal.

**Acceptance Scenarios**:

1. **Given** an approved fix of type "restart pod," **When** the Solver executes, **Then** the pod is restarted via the platform's tool layer, the follow-up message reports the new pod status, and the reversal recipe is included.
2. **Given** an approved fix of type "rollback deployment to revision N," **When** the Solver executes, **Then** the deployment is rolled back, the follow-up message reports the new revision, and the reversal recipe lists the rollback target.
3. **Given** an approved fix whose action type is NOT in the allowed-remediation catalog, **When** the Solver evaluates it, **Then** the Solver refuses to execute and reports the refusal — even if the approval click was successful.
4. **Given** the proposed fix shown to the approver differs from the action the Solver is about to execute (e.g., expert re-ran and produced a new recommendation between approval and execution), **When** the Solver detects the mismatch, **Then** it refuses, reports the mismatch, and requires re-approval.
5. **Given** the Solver action succeeds at the API level but the post-state check fails (e.g., new pod crashlooping), **When** the Solver evaluates the post-state, **Then** the follow-up reports "partial / verification failed" and surfaces the reversal recipe prominently.
6. **Given** the Solver action fails at the API level (permission, conflict, timeout), **When** the failure is observed, **Then** the follow-up reports the failure with the underlying error and proposes a next step (re-route, escalate, manual).

---

### User Story 4 - Full audit trail for routing, diagnosis, approval, and execution (Priority: P2)

Every alert ingestion, classification decision, expert diagnosis, approval click, Solver action, and post-state check is recorded under a single incident correlation ID. An auditor or on-call lead can reconstruct exactly what happened, why, who approved what, and how to undo it.

**Why this priority**: Required by Principle V; necessary for incident review and regulatory scrutiny. P2 because the user-facing flow works without it being polished, but it is a release gate for production rollout.

**Independent Test**: Run a full US1 → US3 scenario, then query the audit trail by incident correlation ID. Verify every stage (ingest, filter, route, expert, report, approval, solver, post-state) is present, ordered, and contains the inputs/outputs and the reversal recipe.

**Acceptance Scenarios**:

1. **Given** any incident handled by the workflow, **When** queried by correlation ID, **Then** the audit trail returns ingestion payload, filtered-log byte counts, router decision and confidence, expert prompt/response, report content, approval (or rejection) event, Solver action, and post-state verification.
2. **Given** sensitive content in any payload (secrets, tokens, customer data), **When** the audit record is stored, **Then** that content has been redacted in both the LLM prompt and the audit copy.

---

### Edge Cases

- **Duplicate webhooks**: The same alert fires multiple times in a short window. The workflow MUST deduplicate so only one report is delivered per logical incident, not one per webhook.
- **Webhook for a resource that no longer exists**: The platform reports "target not found" rather than fabricating context.
- **Router low-confidence**: When no domain scores above the configured threshold, the report is sent with classification "unknown," no fix is proposed, and the engineer is invited to triage manually.
- **Router ambiguity (two domains close)**: The report names the primary domain, lists the runner-up, and the proposed fix (if any) is the one corresponding to the primary; if both are close, no fix is proposed.
- **Approval click after a long delay**: If the approval window has expired, the click is rejected (US2 #2).
- **Approver and reporter are the same person**: Allowed (no four-eyes requirement in v1) but flagged in the audit record; an org-level setting may require a second approver in the future.
- **Slack/chat surface is unavailable**: The report falls back to the platform's persisted incident view; an alert is raised that chat delivery failed but triage still proceeded.
- **Webhook source is unauthenticated or unverified**: Rejected at ingestion; no LLM call, no report, no audit aside from the rejection.
- **Concurrent incidents on the same target**: Each gets its own correlation ID and its own approval; remediations on the same target serialize.
- **Solver attempts to act and a cluster admission controller / PDB refuses**: Solver reports the refusal as a failure and proposes a manual next step. It MUST NOT retry with a destructive flag (e.g., `--force`).

## Requirements *(mandatory)*

### Functional Requirements

**Ingestion & Filtering**

- **FR-001**: The system MUST expose an authenticated webhook endpoint that accepts alert payloads from configured upstream alerting systems.
- **FR-002**: The system MUST reject webhook payloads that fail signature/authentication verification, log the rejection, and proceed no further.
- **FR-003**: The system MUST deduplicate incoming webhooks into a single "incident" using a configurable fingerprint (e.g., source alert id + target resource + time bucket).
- **FR-004**: On accepting a webhook, the system MUST fetch logs from the referenced target(s) via the platform's Kubernetes tool layer (read-only) and apply a lightweight local pre-filter for known issue patterns before any LLM call.

**Routing**

- **FR-005**: A Router agent MUST classify the filtered evidence into exactly one of: `Application`, `Network`, `Database`, or `Unknown`, and MUST emit a confidence indicator.
- **FR-006**: When confidence falls below a configured threshold, the Router MUST emit `Unknown` rather than guess.
- **FR-007**: The Router MUST cite at least one log excerpt supporting the chosen domain.
- **FR-008**: The Router MUST record alternate candidate domains it considered.

**Expert Agents**

- **FR-009**: For each domain in the routing taxonomy, the system MUST have a dedicated Expert agent that receives the filtered logs and any structured context the Router gathered.
- **FR-010**: Each Expert MUST produce: a root-cause hypothesis, cited log evidence supporting it, a confidence indicator, and either a single proposed fix or an explicit "no automatic fix available" decision.
- **FR-011**: A proposed fix MUST belong to a documented, finite allowed-remediation catalog (see Operational Constraints below) and MUST include the target resource, the exact action, and the pre-computed reversal recipe.
- **FR-012**: An Expert MUST refuse to propose a fix that requires permissions the platform does not hold for the target.

**Reporting & Human-in-the-Loop (HITL)**

- **FR-013**: The system MUST deliver the Router + Expert output as a single chat message to the configured channel, formatted to the platform's shared report schema (top label, confidence, cited evidence, proposed fix, reversal recipe, runner-ups).
- **FR-014**: The chat message MUST include interactive controls: `Approve Remediation` (visible only if a fix is proposed) and `Reject`.
- **FR-015**: The system MUST NOT execute any mutation against the cluster before an `Approve` click from an authorized user.
- **FR-016**: Approval MUST be scoped to a single proposed fix and a single incident correlation ID; it MUST NOT be reused across incidents or substituted late.
- **FR-017**: Approval MUST expire after a configurable window (default 30 minutes); expired approvals are rejected with a clear message.
- **FR-018**: The system MUST verify the clicker has the role required to approve the specific action type before accepting the approval.
- **FR-019**: The system MUST record approval events (approve / reject / expire) in the audit trail with the user identity, timestamp, action, and any reason supplied.

**Solver & Remediation**

- **FR-020**: On an authorized approval, the Solver agent MUST execute exactly the action shown in the report — same resource, same action, same parameters. If anything differs, the Solver MUST refuse and require re-approval.
- **FR-021**: The Solver MUST refuse any action not in the allowed-remediation catalog, regardless of approval state.
- **FR-022**: For every executed action, the Solver MUST capture the pre-state snapshot, the action issued, the post-state observed after a verification window, and the reversal recipe.
- **FR-023**: After execution, the Solver MUST post a follow-up message in the same chat thread reporting outcome (`success` / `partial` / `failure`), the observed post-state summary, and the reversal recipe.
- **FR-024**: On `partial` (action succeeded but verification failed) or `failure`, the Solver MUST surface the reversal recipe prominently and propose a next step (re-route, escalate, manual).
- **FR-025**: The Solver MUST honor cluster admission controllers, Pod Disruption Budgets, and quota guards. It MUST NOT bypass them via destructive flags (e.g., `--force`, `--grace-period=0`).
- **FR-026**: Two remediations against the same target MUST serialize; no concurrent mutations on the same resource.

**Cross-cutting**

- **FR-027**: Secrets and credential-shaped strings MUST be redacted from all log content before any LLM call and before persistence to any audit record.
- **FR-028**: Every stage (ingest, filter, route, expert, report, approval, solver, post-state) MUST emit audit records linked by a single incident correlation ID.
- **FR-029**: The workflow MUST enforce a configurable per-incident token / cost ceiling; if a stage would exceed the ceiling, the stage halts and the report is delivered with a clear "budget exceeded — partial result" notice.
- **FR-030**: The workflow MUST emit a kill switch capable of halting all in-flight Solver actions for a tenant within 5 seconds.

### Key Entities *(include if feature involves data)*

- **Incident**: A logical alert event with a stable correlation ID. Holds the source webhook payload, dedup fingerprint, target resources, time window, and references to every artifact produced (filter result, routing decision, expert diagnosis, report, approval events, solver run, post-state).
- **Filtered Evidence**: The output of the lightweight pre-filter on the fetched logs — total bytes, hit count, truncation flag, container instances sampled.
- **Routing Decision**: Domain label (`Application` / `Network` / `Database` / `Unknown`), confidence, cited evidence, runner-up candidates.
- **Expert Diagnosis**: Root-cause hypothesis, cited evidence, confidence, proposed fix (or `no-fix`), and runner-up causes considered.
- **Proposed Fix**: A reference to one entry in the allowed-remediation catalog, parameterized with the target resource and the reversal recipe. Immutable once shown in the report.
- **Report**: The chat-channel message holding the Routing Decision + Expert Diagnosis + Proposed Fix, plus interactive controls and a status field (`pending` / `approved` / `rejected` / `expired` / `executed` / `failed`).
- **Approval Event**: Approve / Reject / Expire, with the approver identity, timestamp, role check result, and any supplied reason.
- **Solver Run**: Pre-state, action issued, post-state, outcome (`success` / `partial` / `failure`), reversal recipe, and any error.
- **Audit Record**: Immutable record per stage, joined by correlation ID, including prompts, responses, model used, token counts, cost estimate, and redactions applied.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: On a labeled benchmark of representative incidents, the Router's domain classification matches the human label at least 85% of the time top-1, and the correct label is in the top-2 at least 97% of the time.
- **SC-002**: For incidents in scope of the allowed-remediation catalog, the Expert's proposed fix matches the human-chosen remediation at least 70% of the time on a labeled benchmark, and the proposed-fix accuracy is reported per domain (so weaknesses are visible).
- **SC-003**: Median wall-clock time from webhook receipt to delivered report is under 30 seconds; p95 is under 60 seconds.
- **SC-004**: The platform issues zero cluster mutations in the absence of an authorized, in-window approval click, measured across the full benchmark and adversarial test suite.
- **SC-005**: 100% of reports surfaced to users contain at least one cited log excerpt; zero uncited claims appear in user-facing output.
- **SC-006**: 100% of executed remediations have a stored pre-state, action, post-state, and reversal recipe, verified by an audit-completeness job on every release.
- **SC-007**: 95% of incidents stay below the configured per-incident cost ceiling; incidents that would exceed it are halted with a clear message rather than silently truncated.
- **SC-008**: For incidents where the Solver runs, the success-or-partial rate (i.e., the proposed fix at least executes cleanly at the API level) is at least 95% on the benchmark; net "the fix resolved the incident" verification rate is reported separately and tracked over time.
- **SC-009**: Zero unredacted secrets appear in any audit record or LLM prompt on a continuously running redaction audit.
- **SC-010**: 100% of approval clicks pass role-check before any mutation; any role-check bypass on the benchmark is a release-blocking defect.

## Assumptions

- **Allowed-remediation catalog (MVP)**: `restart-pod`, `rollback-deployment-to-previous-revision`, `scale-deployment` (within a configured min/max), and `delete-pod-to-trigger-reschedule`. Each entry has a pre-defined reversal recipe and a permission scope. Adding entries is a follow-on feature governed by the constitution's "new mutating tool" checklist (kill switch, reversal, tests, budgets).
- **Domain taxonomy**: `Application` / `Network` / `Database` / `Unknown`. Expanding (e.g., `Infra`, `Configuration`, `Storage`) is out of scope for the MVP.
- **Chat surface**: Slack is the primary delivery target. The shared report schema and interactive controls (Approve / Reject) are designed surface-agnostic so a web or CLI surface can render the same artifact in the future.
- **Approval expiry default**: 30 minutes. Tenants may configure a tighter window. Beyond that window, approvals are rejected.
- **Authorization model**: Approvers MUST hold a role that is mapped to the proposed-fix's action type. The mapping is tenant-configurable; in the MVP, the default mapping requires the `triage-approver` role to approve any catalog action.
- **Webhook source**: Alertmanager / Prometheus-style webhooks signed with a shared secret. Other sources (PagerDuty, OpsGenie, custom) are configurable adapters but not all enabled in MVP.
- **Cluster access**: The platform holds a read-only ServiceAccount per tenant by default. Mutating ServiceAccount(s) used by the Solver are explicitly scoped per allowed-remediation entry and per namespace; broad cluster-admin credentials are NOT used.
- **LLM tiering and model choice**: Implementation concerns governed by the constitution (cost-conscious model selection) — not pinned by this spec. The Router may run on a cheaper/faster model than the Experts; this is permitted, not required.
- **Approval scope**: One approval authorizes exactly one Solver run on exactly one Proposed Fix for one Incident. Repeating the same remediation later requires a new approval. No "approve all similar incidents" toggle in MVP.
- **Same-person approval**: Not blocked in MVP (i.e., the reporter and approver may be the same human) but is flagged in the audit record. Org-level four-eyes can be enforced later.
- **Verification window**: After a Solver action, the post-state verification runs for a bounded time (default 60s). If the system has not stabilized in that window, the outcome is reported as `partial`.
- **Dedup window**: Webhook fingerprint dedup runs in a configurable rolling window (default 10 minutes) to absorb duplicate fires from upstream alerting.
