<!--
SYNC IMPACT REPORT
==================
Version change: (template, unratified) → 1.0.0
Bump rationale: Initial ratification. The prior file contained only
template placeholders; this is the first concrete constitution, so it
establishes the 1.0.0 baseline rather than amending an existing version.

Renamed principles:
  - [PRINCIPLE_1_NAME] → I. Safety-First Autonomy
  - [PRINCIPLE_2_NAME] → II. Cost-Conscious by Design
  - [PRINCIPLE_3_NAME] → III. Developer Experience as a Product
  - [PRINCIPLE_4_NAME] → IV. Evidence-Backed Triage (NON-NEGOTIABLE)
  - [PRINCIPLE_5_NAME] → V. Observability & Reversibility

Added sections:
  - Operational Constraints & Compliance Standards (replaces [SECTION_2_NAME])
  - Development Workflow & Quality Gates (replaces [SECTION_3_NAME])
  - Governance (filled in)

Removed sections: none.

Templates requiring updates:
  - ✅ .specify/templates/plan-template.md — "Constitution Check" gate
    references principles by name; no edits needed (gate text is generic
    and now derives from this file). Plans authored against this
    constitution MUST enumerate gates I–V.
  - ✅ .specify/templates/spec-template.md — compatible; no edits needed.
    Specs continue to be implementation-agnostic; safety/cost/DX appear
    as constraints surfaced in Success Criteria.
  - ✅ .specify/templates/tasks-template.md — compatible; task
    categories (setup, foundational, user-story, polish) accommodate
    safety/cost/observability tasks under "Polish & Cross-Cutting
    Concerns" without structural change.
  - ✅ .specify/templates/checklist-template.md — compatible; checklists
    are generated per-feature.
  - ⚠ .claude/skills/speckit-*/SKILL.md — no references to specific
    principle names found; no edits required at this time.

Follow-up TODOs: none. RATIFICATION_DATE set to today (2026-05-14) as
this is the first ratified version.
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
  Constitution Check section that enumerates Principles I–V and states,
  for each, either "compliant" or "violation + justification." Plans
  with unjustified violations MUST NOT proceed to `/speckit-tasks`.
- **Reviews**: All PRs require at least one human reviewer. PRs that
  introduce a new mutating tool, a new model dependency, or a change to
  authorization scope require a second reviewer from the safety owners.
- **CI gates**: Builds MUST fail on (a) cost regression beyond the
  per-flow budget, (b) latency regression beyond the budgeted p95,
  (c) missing audit log entries in integration tests for any mutating
  action, (d) eval-suite regressions on the hallucination benchmark.
- **New mutating tool checklist**: kill switch wired, reversal recipe
  emitted, integration test covering both happy-path and refusal-path,
  cost and latency budgets declared.
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

**Version**: 1.0.0 | **Ratified**: 2026-05-14 | **Last Amended**: 2026-05-14
