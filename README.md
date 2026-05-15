# k8s-debugger-agent

An agentic platform for automated Kubernetes incident triage and remediation. When Alertmanager fires, the agent classifies the incident domain, runs a domain-specific expert diagnosis with cited evidence, delivers a structured report to Slack or Discord, and — after human approval — executes a deterministic fix from a safe remediation catalog.

## Architecture

```
Alertmanager webhook
        │
        ▼
 ┌──────────────┐     ┌─────────────────────────────────────┐
 │  FastAPI     │────▶│  LangGraph Workflow                 │
 │  (port 8080) │     │                                     │
 └──────────────┘     │  ingest ──▶ router ──▶ expert       │
                      │                 │                   │
 ┌──────────────┐     │            (app/net/db)             │
 │  Callbacks   │◀────│                 │                   │
 │  /approve    │     │             reporter ──[HITL]──▶ solver │
 │  /reject     │     └─────────────────────────────────────┘
 └──────────────┘                       │
                                        ▼
 ┌──────────────┐              ┌─────────────────┐
 │  MCP Server  │◀─────────────│  Kubernetes     │
 │  (port 8081) │  read+write  │  read/write ops │
 └──────────────┘              └─────────────────┘
```

Two services:
- **Agent service** — FastAPI + LangGraph workflow on port 8080
- **MCP server** — Model Context Protocol tool server on port 8081 (read-only by default; scoped-write actions require HITL approval)

## Core Principles

| # | Principle | Enforcement |
|---|-----------|-------------|
| I | Safety-First Autonomy | All mutations gate on human approval; read-only by default |
| II | Cost-Conscious Design | Local LLM inference + per-incident token/USD ceilings |
| III | Developer Experience | Single Slack/Discord message with TL;DR + Approve/Reject controls |
| IV | Evidence-Backed Triage | Every claim cited from logs; hallucination suite in CI |
| V | Observability & Reversibility | Append-only audit trail, Inverse Actions, LangGraph checkpoints |
| VI | Code Quality | ruff, black, mypy --strict, cyclomatic complexity ≤ 15 |
| VII | Testing Standards | 85%/95% coverage floors, eval golden-sets |
| VIII | UX Consistency | Single Report schema, shared error templates |
| IX | Performance SLOs | TTFT ≤ 3 s · p50 ≤ 30 s · p95 ≤ 60 s |

## Tech Stack

- **Python 3.11+**
- **LangGraph ≥ 0.2** — state machine, interrupt-resume, SQLite checkpointing
- **FastAPI + uvicorn** — webhook intake, HITL callbacks, health checks
- **langchain-openai** — structured LLM output; points to any OpenAI-compatible server
- **MCP Python SDK** — Kubernetes tool server (read + scoped-write)
- **Pydantic v2** — data models and settings
- **aiosqlite** — async SQLite for audit trail and LangGraph checkpoint store
- **structlog** — correlation-ID-bound structured logging
- **OpenTelemetry** — node entry/exit + MCP call spans
- **uv** — dependency management

## Prerequisites

- Docker + Docker Compose v2
- [`kind`](https://kind.sigs.k8s.io/) — Kubernetes in Docker (local cluster fixture)
- An OpenAI-compatible local inference server (Ollama, LM Studio, vLLM)

## Quick Start

### 1. Configure environment

```bash
cp .env.example .env
# Edit .env — set LLM_BASE_URL, LLM_API_KEY, and model names
```

Key variables:

```bash
LLM_BASE_URL=http://localhost:52984/v1
LLM_API_KEY=lm-studio
LLM_ROUTER_MODEL=/models/hf.ibm-granite.granite-3.3-8b-instruct-GGUF
LLM_EXPERT_MODEL=/models/hf.ibm-granite.granite-3.3-8b-instruct-GGUF

BUDGET_TOKENS_PER_INCIDENT=50000
ALERTMANAGER_HMAC_SECRET=dev-secret-change-me

CHAT_SURFACE=discord           # slack | discord | all
DISCORD_BOT_URL=http://localhost:8091
APPROVAL_WINDOW_SECONDS=1800
```

### 2. Start the stack

```bash
make dev
```

Brings up: agent (8080), MCP server (8081), slack-mock UI (8090), and a `kind` cluster pre-seeded with failure fixtures.

### 3. Fire a test incident

```bash
make smoke
```

Then open [http://localhost:8090](http://localhost:8090) to see the triage report, click **Approve Remediation**, and watch the Solver act.

### 4. Query the audit trail

```bash
make audit CORRELATION=<uuid>
```

### 5. Tear down

```bash
make clean
```

## Workflow Nodes

| Node | Role |
|------|------|
| `ingest` | Dedup webhook, fetch logs, send TTFT acknowledgement |
| `router` | Structured LLM classification → App / Network / DB / Unknown |
| `expert` | Domain-specific diagnosis with evidence citations and proposed fix |
| `reporter` | Assemble Report, deliver to chat surface, open HITL gate |
| `solver` | (Post-approval) Execute fix deterministically, capture pre-state, verify post-state, record Inverse Action |

## MCP Tools

| Tool | Type | Description |
|------|------|-------------|
| `search_pod_logs` | READ | Context-window grep with additive match expansion |
| `get_pod_events` | READ | Kubernetes events for a target pod |
| `get_pod` | READ | Pod phase, restart count, parent controller |
| `restart_pod` | WRITE | Graceful restart via delete |
| `rollback_deployment` | WRITE | `kubectl rollout undo` |
| `scale_deployment` | WRITE | Bounded scale (server-side min/max) |
| `delete_pod_to_reschedule` | WRITE | Delete without `--force` (reschedule only) |

All WRITE tools require an approved `ApprovalToken` carrying the fix fingerprint and correlation ID.

## Testing

```bash
make test          # Full suite (unit + contract + integration + eval)
make test-unit     # Fast feedback — no live services or LLM
make eval          # LLM quality + hallucination checks
make perf          # Latency benchmark (p50 ≤ 30 s, p95 ≤ 60 s)
make audit-check   # Audit-completeness invariants
```

Coverage floors are enforced per-module in [pyproject.toml](pyproject.toml).

## Failure Scenarios (demo fixtures)

The `specs/003-podinfo-demo` workload includes trigger scripts for four failure modes:

1. **CrashLoopBackOff** — repeated container crashes
2. **Bad deployment** — image tag rollout failure
3. **OOM kill** — memory limit breach
4. **Scale pressure** — HPA saturation

## Project Structure

```
src/
├── agent/
│   ├── api/           # webhook, callbacks, approval expiry
│   ├── graph/         # LangGraph builder, state, nodes
│   │   └── nodes/
│   │       ├── ingest.py
│   │       ├── router.py
│   │       ├── reporter.py
│   │       ├── solver.py
│   │       └── experts/
│   ├── audit.py       # append-only audit writer
│   ├── budget.py      # per-incident token + USD ceiling
│   ├── kill_switch.py # halt in-flight solver within 5 s
│   ├── redaction.py   # double-pass secret scrubbing
│   └── settings.py    # pydantic-settings
├── mcp_server/
│   ├── tools/         # kubernetes read + write tools
│   ├── auth.py        # per-tool ServiceAccount + scope check
│   └── server.py
└── shared/
    ├── schemas.py     # Report, RoutingDecision, ExpertDiagnosis, …
    ├── catalog.py     # allowed remediations + Inverse Action map
    └── correlation.py # UUIDv7 correlation ID generation
```

## Specifications

Detailed design artifacts live under [specs/002-routed-triage-workflow/](specs/002-routed-triage-workflow/):

- [spec.md](specs/002-routed-triage-workflow/spec.md) — functional requirements (FR-001 – FR-030)
- [plan.md](specs/002-routed-triage-workflow/plan.md) — implementation plan + Constitution Check
- [quickstart.md](specs/002-routed-triage-workflow/quickstart.md) — one-command local run guide
- [data-model.md](specs/002-routed-triage-workflow/data-model.md) — Pydantic entity definitions
- [contracts/](specs/002-routed-triage-workflow/contracts/) — webhook, MCP, chat, and audit schemas
