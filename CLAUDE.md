<!-- SPECKIT START -->
For additional context about technologies to be used, project structure,
shell commands, and other important information, read the current plan
at `specs/002-routed-triage-workflow/plan.md` and its sibling artifacts:

- `specs/002-routed-triage-workflow/spec.md` — the feature specification.
- `specs/002-routed-triage-workflow/research.md` — Phase 0 stack decisions
  (LangGraph, local OpenAI-compatible LLM via langchain-openai, Postgres/SQLite
  persistence, FastAPI webhook, MCP Python SDK, mock Slack receiver, double-pass
  redaction).
- `specs/002-routed-triage-workflow/data-model.md` — pydantic entities and
  Report status state machine.
- `specs/002-routed-triage-workflow/contracts/` — Alertmanager webhook,
  MCP read+write tools, Slack-mock outbound + callbacks, audit_record
  table schema.
- `specs/002-routed-triage-workflow/quickstart.md` — one-command local
  end-to-end run.

Project-wide rules live in `.specify/memory/constitution.md` (v1.1.0,
Principles I–IX). Plans MUST enumerate the Constitution Check gates
against this file.
<!-- SPECKIT END -->
