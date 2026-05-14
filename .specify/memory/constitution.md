<!--
SYNC IMPACT REPORT
==================
Version change: 1.0.0 → 1.1.0
Bump rationale: MINOR — adds four new engineering-discipline principles
(VI Code Quality, VII Testing Standards, VIII User Experience
Consistency, IX Performance Requirements). No existing principle is
removed or redefined; no governance rule is relaxed. Additive change,
backward-compatible with v1.0.0 plans and specs.

Modified principles:
  - I. Safety-First Autonomy — unchanged
  - II. Cost-Conscious by Design — unchanged
  - III. Developer Experience as a Product — unchanged (scoped to
    on-call/incident UX; broader cross-surface UX consistency now
    lives in VIII)
  - IV. Evidence-Backed Triage (NON-NEGOTIABLE) — unchanged
  - V. Observability & Reversibility — unchanged

Added principles:
  - VI. Code Quality
  - VII. Testing Standards (NON-NEGOTIABLE)
  - VIII. User Experience Consistency
  - IX. Performance Requirements (DevOps SLOs)

Added sections: none beyond the four new principles. "Development
Workflow & Quality Gates" expanded to reference VI–IX in CI gates.

Removed sections: none.

Templates requiring updates:
  - ✅ .specify/templates/plan-template.md — "Constitution Check" gate
    is generic and now enumerates Principles I–IX. Existing plans
    against v1.0.0 should be re-checked at next edit; no rewrite
    required.
  - ✅ .specify/templates/spec-template.md — compatible; specs remain
    implementation-agnostic. SLO targets (IX) surface in Success
    Criteria as already practiced.
  - ✅ .specify/templates/tasks-template.md — compatible; testing,
    perf, and code-quality tasks fit existing categories (Setup,
    Foundational, Polish & Cross-Cutting).
  - ✅ .specify/templates/checklist-template.md — compatible.
  - ⚠ specs/001-log-triage-classifier/ — existing spec predates this
    amendment. No content changes required (it already meets VII's
    eval-suite expectations via SC-001 and IX's latency SLOs via
    SC-002), but the plan generated from it MUST enumerate gates
    I–IX in its Constitution Check section.

Follow-up TODOs: none. Coverage floors and SLO numbers in VII/IX are
written as defaults; tighten them per feature in plan.md if needed.
-->

# K8s Debugger Agent Constitution

An Agentic DevOps platform for automated Kubernetes incident triage. This
constitution defines the non-negotiable rules that govern how the agent
designs features, ships code, and operates against customer clusters.

## Core Principles

### I. Safety-First Autonomy

The agent MUST classify every action by blast radius before executing it:

- **Read-only** (logs, events, manifests, metrics) — default; no approval
  required.
- **Mutating, reversible** (scale a deployment, restart a pod, patch a
  ConfigMap value the agent itself just changed) — require an explicit,
  scope-bounded authorization grant and MUST emit a reversal recipe.
- **Mutating, hard-to-reverse** (delete, drain, evict, force-replace,
  IAM/RBAC changes, anything against production data) — require a
  human-in-the-loop confirmation per action, never a blanket grant.

Authorization granted for one action does NOT extend to subsequent
actions. Destructive flags (`--force`, `--cascade`, `--grace-period=0`)
MUST be surfaced verbatim in the confirmation prompt. The agent MUST
prefer dry-runs and diff previews before any mutation, and MUST refuse
to bypass admission controllers, PDBs, or quota guards as a "shortcut."

**Rationale**: The agent operates against live infrastructure where a
single wrong `kubectl delete` can cause an outage. Defaulting to
read-only and gating mutations on explicit, narrow consent is the only
safe posture; user trust collapses irrecoverably the first time the
agent breaks prod unprompted.

### II. Cost-Conscious by Design

Every LLM call, tool invocation, and cluster query has an attributable
cost and MUST be accounted for:

- The agent MUST select the cheapest model that meets the quality bar
  for a given step (cheap classifier → mid-tier reasoner → top-tier only
  when warranted), and MUST justify any escalation in trace metadata.
- Prompt caching, response caching, and conversation summarization MUST
  be applied whenever the same context will be reused; uncached repeated
  context is a defect.
- Per-incident, per-tenant, and per-session cost ceilings MUST be
  enforced fail-closed. Exceeding a ceiling halts further spend and
  surfaces a clear message; it MUST NOT silently degrade output.
- `$/incident-resolved` and `tokens/incident` are tracked as primary
  product KPIs, not afterthoughts. Regressions block release.

**Rationale**: Agentic systems can burn unbounded spend if every step
calls the largest model on the full transcript. Treating cost as a
first-class constraint — not a quarterly cleanup project — is what
makes the platform economically viable for customers and for us.

### III. Developer Experience as a Product

The on-call engineer is the customer. Their time and attention budget
are scarce, and the agent MUST respect them:

- Every triage output MUST lead with a TL;DR (root cause hypothesis +
  recommended next action) followed by evidence; raw logs and tool
  output MUST be collapsed/linked, not pasted inline.
- First-token latency for interactive triage MUST be under 3 seconds;
  final-answer latency for a standard triage flow MUST be under 30
  seconds. Both are tracked and regressions block release.
- Setup MUST be a single command against a target cluster, with no
  manual RBAC edits required for the read-only tier.
- Error messages MUST tell the user what to do next, not just what
  failed.

**Rationale**: An "agentic" tool that produces a 4000-line wall of
output during an incident is worse than no tool at all. DX is not
polish — it is the product, because the agent is judged on whether it
shortens time-to-resolution for a human under stress.

### IV. Evidence-Backed Triage (NON-NEGOTIABLE)

Every diagnostic conclusion the agent surfaces MUST cite the specific
observation supporting it — a log line, event, metric sample, manifest
field, or CRD status — with a reference the user can click through to.

- The agent MUST NOT present speculation as fact. Uncertain hypotheses
  MUST be labeled (e.g., "likely", "candidate cause") and ranked.
- If the agent cannot find evidence for a claim, it MUST say so and
  propose what to inspect next, not invent plausible-sounding details.
- Hallucinated facts about cluster state are treated as Sev-2 defects
  and require a regression test before close.

**Rationale**: Triage tooling that confidently lies destroys trust on
first contact and causes engineers to chase phantom causes during real
outages. Citations make the agent auditable and make hallucination
detectable in CI rather than in production.

### V. Observability & Reversibility

Every agent decision and action MUST be reconstructable after the fact:

- All prompts, tool calls, tool results, and final outputs MUST be
  logged with a correlation ID linking them to the triggering incident
  and the operating user/tenant.
- Every mutating action MUST log: the pre-state, the action issued,
  the post-state, and a reversal recipe (the inverse command or
  manifest) sufficient for a human to undo it.
- Audit logs MUST be retained per the tenant's compliance tier and MUST
  NOT be silently truncated; truncation is an incident.
- The platform MUST expose a "kill switch" that halts all in-flight
  agent actions for a tenant within 5 seconds.

**Rationale**: Autonomous systems acting on production require an
audit trail strong enough to answer "what did the agent do, why, and
how do I undo it?" — both for incident review and for regulatory
scrutiny. Reversibility is not optional when the agent has write
access.

### VI. Code Quality

The platform's code is read more often than it is written and is
operated under incident pressure. It MUST stay legible, type-safe,
and free of dead weight:

- Every change MUST pass the project's linter and formatter in CI;
  disabling rules in-line requires a comment naming the reason and
  is reviewed.
- The language's static type checker MUST run in CI at the strictest
  practical setting; `any` / unchecked escape hatches require an
  inline justification.
- Cyclomatic complexity for any single function MUST stay under 15;
  exceeding it requires a refactor or an explicit waiver in PR.
- Dead code, commented-out blocks, and "TODO" markers without a
  linked issue MUST NOT land on `main`.
- Every PR requires at least one human reviewer (two for changes
  touching mutating tools, authorization scope, model dependencies,
  or this constitution). Self-approval is not permitted on `main`.
- New runtime dependencies MUST be vetted for license, maintenance
  signal (recent commits, open security advisories), and supply-chain
  posture; the vetting result is recorded in the PR.
- Public functions and exported types MUST have a one-line purpose
  comment when intent is not obvious from the name. Internal code
  follows the "no comments unless the WHY is non-obvious" rule from
  the project's collaborator guide.

**Rationale**: An agentic platform that mutates production needs to
be auditable by humans under stress. Sloppy code that "works" is a
liability — when something goes wrong, the people reading it have
seconds, not afternoons.

### VII. Testing Standards (NON-NEGOTIABLE)

Tests are how we keep the safety, cost, and evidence guarantees
honest. They are not optional:

- Every public function, tool boundary, and decision rule MUST have
  unit tests. Pure-logic modules MUST hit at least 85% line and
  branch coverage; safety-critical modules (authorization, mutation
  gating, secret redaction, cost ceiling enforcement) MUST hit 95%
  and MUST include negative-path tests.
- Every integration with an external system (Kubernetes, MCP, LLM
  provider, observability backend) MUST have contract tests against
  a recorded or sandboxed fixture. The fixture MUST be refreshed on a
  documented cadence.
- LLM-driven behaviors MUST have an eval suite (a golden set of
  labeled inputs with expected outputs and acceptable variance).
  The eval suite runs in CI; regressions block merge.
- Hallucination tests are mandatory for any feature that surfaces a
  classification, recommendation, or summary derived from an LLM.
  A claim emitted without supporting evidence is a test failure.
- New mutating tools MUST ship with at least one refusal-path test
  (asserting the tool refuses without authorization) and at least
  one reversal-recipe test (asserting the recipe undoes the change
  on a fixture).
- Flaky tests are tracked, quarantined within 24 hours of detection,
  and fixed or deleted within one sprint. Quarantined tests MUST NOT
  silently mask real regressions.
- Tests MUST NOT mock the system under test; mock only the
  collaborators across a process or network boundary.

**Rationale**: Without strong testing, the safety, evidence, and
cost principles become aspirational. The cost of a missed test is
borne by customers during outages — far more expensive than the test.

### VIII. User Experience Consistency

The platform speaks to humans across multiple surfaces (chat, CLI,
web, API). Those surfaces MUST present the same product, not three
different products:

- Triage outputs MUST share a single canonical schema (top label,
  confidence, cited evidence, runner-ups, caveats). Channel-specific
  rendering is allowed; the underlying fields are not.
- Labels, severities, and units MUST be drawn from a single shared
  vocabulary. Mixing `error` / `failed` / `failure` for the same
  state is a defect.
- Error messages MUST follow a single template: what failed, why,
  what to try next. Stack traces are not user-facing output.
- The same triage request issued against the same state MUST produce
  the same shape and ordering of fields across surfaces, even if
  prose differs.
- Times, durations, byte sizes, and money values MUST use the
  product-wide formatting standard. ISO-8601 for timestamps, IEC
  binary prefixes for bytes (KiB, MiB), no surface-specific
  variants.
- Net-new user-facing strings MUST go through the shared
  copy/i18n surface; ad-hoc inline strings on individual surfaces
  are a defect.

**Rationale**: When an engineer hits the platform during an incident,
inconsistent labels and shapes across surfaces cost trust and time.
Consistency lets users learn the product once and apply it
everywhere, which is a force-multiplier on Principle III.

### IX. Performance Requirements (DevOps SLOs)

DevOps tooling is judged on latency under pressure. Performance is
a first-class engineering discipline with explicit SLOs and
enforced budgets:

- Every user-facing flow MUST declare SLOs for: (a) time-to-first-
  token (or first-meaningful-byte), (b) end-to-end p50 and p95
  latency, (c) per-flow cost ceiling. Defaults: TTFT ≤ 3s, p50 ≤ 30s,
  p95 ≤ 60s for interactive triage; tighter per-feature targets are
  encouraged in the relevant plan.
- Performance budgets MUST be enforced in CI on a representative
  benchmark fixture. Regressions beyond the budget block merge; an
  override requires a documented justification in the PR and a
  follow-up issue.
- Tool calls to external systems (K8s API, MCP, LLM) MUST honor
  rate-limit backpressure and MUST retry with bounded, jittered
  backoff. Unbounded retries are a defect.
- Memory and concurrency budgets MUST be declared for any service
  exposed to user load; OOMs and unbounded fan-out are defects.
- Hot paths MUST be profilable in production via the shared
  observability stack; "we'll add profiling later" is not acceptable
  for code shipping to production.
- Data freshness SLOs MUST be declared for any cached or summarized
  cluster state surfaced to users. Stale data MUST be labeled with
  its age.

**Rationale**: An on-call engineer waiting 90 seconds for triage is
no longer triaging — they are debugging the tool. Treating
performance as an SLO with CI enforcement, not as a polish item,
is what keeps the platform usable when it matters most.

## Operational Constraints & Compliance Standards

- **Default posture**: Production cluster credentials are read-only.
  Write access is per-tenant opt-in and per-action scoped.
- **Secret & PII handling**: Secrets, tokens, and customer PII MUST be
  redacted before any LLM call. Redaction MUST be applied at the tool
  boundary, not relied upon as a model behavior. Unredacted data in a
  prompt is a Sev-1 defect.
- **Tenant isolation**: Prompts, caches, embeddings, and traces MUST be
  partitioned per tenant. Cross-tenant data leakage is a Sev-1 defect.
- **Cost ceilings**: Per-session and per-tenant token/$ ceilings are
  enforced fail-closed (see Principle II).
- **Supported surfaces**: Kubernetes 1.27+ via the Kubernetes API; no
  direct `kubectl exec` into customer workloads without explicit per-
  action approval.
- **Model selection**: Default to the smallest model that passes the
  task's eval bar; escalation MUST be recorded in the trace.

## Development Workflow & Quality Gates

- **Constitution Check (gate)**: Every `/speckit-plan` MUST include a
  Constitution Check section that enumerates Principles I–IX and states,
  for each, either "compliant" or "violation + justification." Plans
  with unjustified violations MUST NOT proceed to `/speckit-tasks`.
- **Reviews**: All PRs require at least one human reviewer (Principle
  VI). PRs that introduce a new mutating tool, a new model dependency,
  or a change to authorization scope require a second reviewer from the
  safety owners.
- **CI gates**: Builds MUST fail on:
  - (a) cost regression beyond the per-flow budget (Principle II);
  - (b) latency regression beyond the budgeted p95 (Principle IX);
  - (c) missing audit log entries in integration tests for any mutating
    action (Principle V);
  - (d) eval-suite or hallucination-benchmark regressions
    (Principles IV and VII);
  - (e) coverage drop below the per-module floor — 85% pure logic, 95%
    safety-critical (Principle VII);
  - (f) linter, formatter, or strict-type-check failures (Principle VI);
  - (g) introduction of a user-facing string outside the shared
    copy/i18n surface or use of a label outside the shared vocabulary
    (Principle VIII).
- **New mutating tool checklist**: kill switch wired, reversal recipe
  emitted, integration test covering both happy-path and refusal-path,
  cost and latency budgets declared, eval entry added if LLM-backed.
- **Documentation**: Public behavior changes MUST update the relevant
  spec under `specs/` and the user-facing changelog in the same PR.

## Governance

This constitution supersedes any other practice, style guide, or
informal convention within the project. When in conflict, this
document wins; the conflicting practice MUST be updated or removed.

**Amendments**:

- Proposed via PR that edits this file. The PR description MUST
  include: the version bump and its rationale (MAJOR/MINOR/PATCH),
  the list of changed principles, and a migration note for any
  in-flight features affected.
- Requires approval from two maintainers, at least one of whom is a
  designated safety owner.
- On merge, the Sync Impact Report at the top of this file MUST be
  refreshed and dependent templates under `.specify/templates/`
  reviewed in the same PR.

**Versioning policy** (semantic):

- **MAJOR**: A principle is removed, redefined in a backward-
  incompatible way, or a governance rule is relaxed.
- **MINOR**: A new principle or section is added, or guidance is
  materially expanded.
- **PATCH**: Clarifications, wording fixes, or non-semantic
  refinements.

**Compliance review**: A quarterly review MUST audit a sample of
recent incidents, PRs, and audit traces against Principles I–V.
Findings are tracked as issues and triaged within one sprint.

**Runtime guidance**: Day-to-day development guidance for AI
collaborators lives in `CLAUDE.md` at the repo root; it MUST defer
to this constitution on any conflict.

**Version**: 1.1.0 | **Ratified**: 2026-05-14 | **Last Amended**: 2026-05-14
