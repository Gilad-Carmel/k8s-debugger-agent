<!--
SYNC IMPACT REPORT
==================
Version change: 1.1.0 → 2.0.0
Bump rationale: MAJOR — the project is being reframed as a hackathon
build. Four principles from v1.1.0 are removed, governance rules are
materially relaxed (single-reviewer PRs, no quarterly compliance
review, CI gates trimmed to a smoke set), and several rigid numeric
floors (85/95% coverage, p95 latency budget enforcement in CI) are
dropped. Per the versioning policy, relaxing governance rules and
redefining principles is MAJOR. Plans written against v1.1.0 are NOT
automatically compliant with v2.0.0 — their Constitution Check
sections MUST be re-evaluated against the new six-principle set.

Modified principles:
  - I. Safety-First Autonomy → I. Safety Rails for Live Clusters
    (NON-NEGOTIABLE). Same blast-radius classification, but the
    demo cluster is the only deployment target so multi-tenant
    framing is dropped.
  - II. Cost-Conscious by Design → folded into VI. Cheap, Cached,
    and Replayable. Per-tenant ceilings drop; a single per-session
    ceiling remains.
  - III. Developer Experience as a Product → folded into I. Demo-
    First Delivery. The on-call persona is replaced by the judge/
    teammate persona; latency targets stay but are aspirational,
    not CI-enforced.
  - IV. Evidence-Backed Triage → IV. Evidence-Backed Output
    (NON-NEGOTIABLE). Same rule, smaller scope (the demo path).
  - V. Observability & Reversibility → V. Replayable Traces.
    Kill switch and reversal-recipe requirements survive for
    mutating actions; long-horizon audit retention is dropped.

Removed principles (v1.1.0 → v2.0.0):
  - VI. Code Quality (formal review/complexity caps removed; a
    lighter "Pragmatic Quality" rule lives inside III)
  - VII. Testing Standards (NON-NEGOTIABLE) (coverage floors
    removed; a slim "test the demo path" rule lives inside III)
  - VIII. User Experience Consistency (single-surface project; rule
    no longer load-bearing)
  - IX. Performance Requirements (DevOps SLOs) (CI-enforced
    perf budgets removed; latency targets survive as aspirations
    inside I)

Added principles:
  - I. Demo-First Delivery (NON-NEGOTIABLE)
  - II. Time-Boxed Scope
  - III. Pragmatic Quality
  - VI. Cheap, Cached, and Replayable

Net principle count: 9 → 6.

Added sections: none. "Development Workflow & Quality Gates"
collapsed into "Hackathon Workflow." "Operational Constraints &
Compliance Standards" collapsed into "Demo Cluster Constraints."

Templates requiring updates:
  - ✅ .specify/templates/plan-template.md — "Constitution Check"
    is generic ("Gates determined based on constitution file") and
    auto-adapts; no edit required, but plans authored against
    v1.1.0 MUST re-enumerate Principles I–VI on next edit.
  - ✅ .specify/templates/spec-template.md — compatible; no
    principle-specific section to update.
  - ✅ .specify/templates/tasks-template.md — compatible; the
    "Tests are OPTIONAL" stance already aligns with the new
    Pragmatic Quality principle.
  - ✅ .specify/templates/checklist-template.md — compatible.
  - ⚠ specs/001-log-triage-classifier/ and
    specs/002-routed-triage-workflow/ — both predate this
    amendment. They remain valid as historical records, but any
    NEW plan or task generation against them MUST run the
    Constitution Check against the new six principles. Coverage
    floors and per-tenant ceilings from v1.1.0 are no longer
    binding.
  - ⚠ CLAUDE.md (repo root) — references the v1.1.0 plan and
    constitution. No edit required for this amendment; rule of
    deference (CLAUDE.md → constitution) is preserved.

Follow-up TODOs: none. If the project later returns to a
production posture, version 3.0.0 should reinstate Principles
VI–IX from v1.1.0 rather than re-invent them.
-->

# K8s Debugger Agent Constitution

A hackathon build of an agentic Kubernetes triage helper. This
constitution captures the small set of rules that keep the project
shippable, demoable, and safe enough to run against a live cluster
without becoming a process-heavy enterprise codebase.

The audience is a hackathon team and the judges/teammates who will
watch the demo. Where v1.1.0 optimized for compliance and on-call
SLOs, v2.0.0 optimizes for a working end-to-end story by the deadline.

## Core Principles

### I. Demo-First Delivery (NON-NEGOTIABLE)

The product is the demo. Every increment of work MUST move the
end-to-end demo path forward or be cut:

- The team MUST maintain one named "happy-path script" (a sequence of
  inputs that produces a triage output the judges will see). That
  script MUST run green at the end of every working day. If it goes
  red, fixing it is the next task — feature work pauses.
- Vertical slices beat horizontal completeness. A thin path through
  every layer (input → agent → tool call → output) MUST land before
  any single layer gets polished.
- Aspirational latency for the live demo: first token in ≤ 5s, final
  answer in ≤ 60s on the demo cluster. Not CI-enforced; tracked by
  running the happy-path script and noting the time.
- Output MUST lead with a TL;DR (root-cause hypothesis + recommended
  next action). Raw logs and tool dumps are collapsed or linked.
- Error messages MUST tell the demo operator what to do next, not
  just what failed. "TODO: handle this" in a user-facing path is a
  defect during demo week.

**Rationale**: Hackathons are won by teams whose demo runs. A
beautifully architected codebase with a broken demo loses to a
scrappy one whose script works on the first try.

### II. Time-Boxed Scope

Scope is the variable; the deadline is fixed.

- Every task SHOULD carry an explicit time budget (e.g., "≤ 2h").
  If a task blows past 2× its budget, the team MUST stop and choose:
  cut scope, swap to a simpler approach, or escalate.
- "Nice to have" features MUST be tagged as such in tasks.md and
  MUST NOT block the happy-path script.
- Refactors that do not unblock the demo are deferred to after the
  demo. "We'll clean this up post-judging" is a valid answer during
  hackathon week.
- The team maintains a single shared cut-list (in `tasks.md` under a
  "Cut if time runs short" heading). Anything on the cut-list can be
  dropped without discussion in the last 24 hours.

**Rationale**: Without explicit time-boxing, every task expands to
fill the time available and the demo path slips. Naming the
trade-off up front makes the cut decisions cheap when they need to
happen fast.

### III. Pragmatic Quality

Quality serves the demo; it is not an end in itself.

- The project's formatter MUST run before commit. The project's type
  checker MUST pass on `main`. Both are cheap and prevent the worst
  classes of last-minute breakage.
- Tests are written for the happy-path script and for any code that
  touches Principle V (mutating actions, reversal recipes). Coverage
  numbers are not tracked.
- A test that becomes flaky during hackathon week is either fixed
  immediately or deleted; quarantine queues are out of scope.
- Single-reviewer PRs are the norm. Self-merge is allowed for
  hackathon work EXCEPT for changes that touch mutating tools,
  authorization scope, or this constitution — those require one
  teammate's eyes.
- Dead code, commented-out experiments, and `TODO` notes are
  tolerated on branches and DISCOURAGED on `main`. Cleanups happen
  in a single "polish pass" before submission, not continuously.
- No new runtime dependency may be added in the final 24 hours
  before submission. Earlier than that, vetting is informal
  (license check + "is it maintained?").

**Rationale**: Strict quality gates that made sense for a long-lived
production platform become drag in a 48-hour build. Keep the gates
that catch real breakage (format, type-check, tests on the demo
path) and drop the ones that are about long-term maintainability.

### IV. Evidence-Backed Output (NON-NEGOTIABLE)

The agent's outputs MUST be grounded in real cluster observations:

- Every diagnostic claim shown in the demo MUST cite the observation
  supporting it (a log line, an event, a metric, a manifest field).
  No citation, no claim.
- The agent MUST NOT present speculation as fact. Uncertain
  hypotheses MUST be labeled ("likely", "candidate cause") and
  ranked.
- If the agent cannot find evidence, it MUST say so and propose
  what to inspect next. Inventing plausible-sounding details to
  make the demo flow nicer is a Sev-1 defect.
- Hallucinated cluster state caught during dry-runs MUST be fixed
  before the next dry-run, even if it means cutting a flashy
  feature from the demo.

**Rationale**: A judge or teammate who catches the agent inventing a
log line during the demo will not believe anything else the agent
says. Evidence is what makes the demo land. This is the one place
where v1.1.0's rigor stays fully intact.

### V. Replayable Traces

Anything the agent does against the cluster MUST be reconstructable
afterward, both for the post-demo post-mortem and to debug
mid-hackathon failures fast:

- All prompts, tool calls, tool results, and final outputs MUST be
  logged with a single correlation ID per triage run.
- Every mutating action MUST log: the pre-state, the action issued,
  the post-state, and a one-line reversal recipe (the inverse
  command or manifest) for a human to undo it.
- A "kill switch" command MUST exist that halts the agent's in-flight
  actions within ~5 seconds. The team MUST know how to invoke it
  during the demo.
- Long-horizon audit retention, compliance tiers, and per-tenant
  partitioning are out of scope. One log file per run, kept until
  submission, is sufficient.

**Rationale**: When the demo breaks at 2am, the team's only debugger
is the trace. When the demo runs against a real cluster, the team's
only undo button is the reversal recipe. Both are cheap to add up
front and very expensive to retrofit.

### VI. Cheap, Cached, and Replayable

LLM and tool spend is bounded by a single project budget, not by
per-tenant ceilings:

- The team MUST set a single per-session token/$ ceiling (a number
  agreed up front; the project agent halts further spend when
  reached). Exceeding the ceiling halts the run with a clear
  message; it MUST NOT silently degrade output.
- Default to the cheapest model that passes the happy-path script.
  Escalation to a larger model is recorded in the trace and MUST be
  justified ("classifier failed eval N times in a row" is enough).
- Prompt caching and response caching MUST be applied whenever the
  same context is re-used during the demo loop. Uncached repeated
  context is a defect because it makes the demo slow AND expensive.
- Tool calls MUST honor rate-limit backpressure and retry with
  bounded, jittered backoff. Unbounded retries that drain the
  budget mid-demo are a defect.
- Recorded fixtures of the demo cluster MAY be replayed during
  development to avoid spending real tokens; the fixture MUST be
  refreshed before the final dry-run.

**Rationale**: Three things share the same root: keeping spend under
control, keeping demo latency low, and being able to iterate
offline when the cluster or LLM API is flaky. Caching and replay
fixtures buy all three at once.

## Demo Cluster Constraints

- **Default posture**: The demo cluster credentials are read-only.
  Write access is enabled per-action, never as a blanket grant.
- **Secret & PII handling**: Secrets, tokens, and any PII visible in
  cluster state MUST be redacted before any LLM call. Redaction is
  applied at the tool boundary, not relied on as a model behavior.
  Leaking real secrets into a prompt during a public demo is the
  worst possible failure mode.
- **Mutating actions on the demo cluster**: Allowed for the
  "reversible" tier (scale, restart, patch values the agent just
  set). Forbidden for the "hard-to-reverse" tier (delete, drain,
  evict, force-replace, IAM/RBAC) without explicit teammate
  confirmation per action.
- **Supported surface**: Kubernetes API only. No `kubectl exec`
  into workloads without explicit per-action approval.
- **Model selection**: Default to the smallest model that passes
  the happy-path script. Record escalations in the trace.

## Hackathon Workflow

- **Constitution Check (gate)**: Every `/speckit-plan` MUST include
  a Constitution Check section that enumerates Principles I–VI and
  states, for each, "compliant" or "violation + justification." A
  plan with unjustified violations MUST NOT proceed to
  `/speckit-tasks`. Plans MAY explicitly note "N/A for hackathon
  scope" against a principle where the rule is irrelevant to the
  feature (e.g., Principle V on a read-only-only feature).
- **Reviews**: Single-reviewer PRs are the norm. PRs that touch
  mutating tools, authorization scope, or this constitution require
  one additional teammate's review.
- **CI gates** (kept intentionally lean):
  - (a) formatter clean and strict type-check passes (Principle III);
  - (b) the happy-path script runs green (Principles I and IV);
  - (c) any test that covers a mutating tool or its reversal recipe
    runs green (Principles III and V);
  - (d) the per-session cost ceiling is not exceeded by the
    happy-path script's recorded run (Principle VI).
- **New mutating tool checklist**: kill switch wired, reversal
  recipe emitted, happy-path covers both a success and a refusal
  case, cost noted in the trace.
- **Documentation**: The demo script (`quickstart.md` or equivalent)
  is the single source of truth for "how to demo this." It MUST
  stay current; if the demo script is stale, the demo will fail.

## Governance

This constitution supersedes any other practice within the project
during hackathon week. When in conflict, this document wins; the
conflicting practice MUST be updated or removed.

**Amendments**:

- Proposed via PR that edits this file. The PR description MUST
  include the version bump and its rationale (MAJOR/MINOR/PATCH)
  and the list of changed principles.
- Requires approval from one teammate. (Pre-hackathon and
  post-hackathon, return to the v1.1.0 two-reviewer rule.)
- On merge, the Sync Impact Report at the top of this file MUST be
  refreshed.

**Versioning policy** (semantic):

- **MAJOR**: A principle is removed, redefined in a backward-
  incompatible way, or a governance rule is relaxed.
- **MINOR**: A new principle or section is added, or guidance is
  materially expanded.
- **PATCH**: Clarifications, wording fixes, or non-semantic
  refinements.

**Compliance review**: A single end-of-hackathon retrospective MUST
audit the recorded traces of the demo run against Principles I, IV,
and V. Findings go into a "lessons learned" doc; there is no
quarterly cadence during hackathon scope.

**Runtime guidance**: Day-to-day development guidance for AI
collaborators lives in `CLAUDE.md` at the repo root; it MUST defer
to this constitution on any conflict.

**Version**: 2.0.0 | **Ratified**: 2026-05-14 | **Last Amended**: 2026-05-15
