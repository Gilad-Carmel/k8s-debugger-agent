# Quickstart: Routed Triage and Auto-Remediation Workflow

**Feature**: 002-routed-triage-workflow
**Date**: 2026-05-14
**Audience**: New contributors and reviewers running the workflow end-to-end on a laptop.

The goal of this quickstart is to take a fresh checkout to a successful end-to-end run — webhook → routed triage → chat report → click Approve → Solver acts on a fixture pod → success follow-up in chat — in under five minutes of human time, with no real cluster or real Slack required.

---

## Prerequisites

- Docker + Docker Compose v2.
- `kind` (Kubernetes in Docker) — used for the local fixture cluster.
- An Anthropic API key in your environment as `ANTHROPIC_API_KEY`.
- Python 3.11+ if you plan to run the test suite outside containers.

That's it. No real Slack workspace, no real Kubernetes cluster, no Postgres install — all provided by the compose stack.

---

## One-command setup

```bash
make dev
```

This is shorthand for:

```bash
docker compose -f deploy/docker-compose.yml up --build
```

What comes up:

| Service | Port | Purpose |
|---|---|---|
| `agent` | `8080` | FastAPI: webhook intake + Slack callbacks; runs the LangGraph workflow |
| `mcp-server` | `8081` | MCP server: read tools + scoped-write tools |
| `slack-mock` | `8090` | Tiny FastAPI receiver mimicking Slack Block Kit + interactive callbacks |
| `postgres` | `5432` | LangGraph checkpoints + append-only `audit_record` table |
| `kind-control-plane` | (internal) | Local Kubernetes cluster, pre-seeded with three fixture workloads |

On first start, an init container:

1. Creates a `kind` cluster.
2. Applies `tests/fixtures/cluster/*.yaml` (three deployments simulating Application, Network, and Database failure modes).
3. Initializes the Postgres schema (`agent` and `audit_record` tables; revokes UPDATE/DELETE on `audit_record`).
4. Mints per-tool ServiceAccount tokens for the MCP write tools, mounted into `mcp-server` only.

When `make dev` settles, you should see in the agent logs:

```text
agent.startup ready route=POST /webhook/alertmanager mcp=http://mcp-server:8081
```

---

## End-to-end smoke run

### 1. Fire a synthetic webhook

```bash
make smoke
```

…runs `tests/fixtures/fire_webhook.py`, which signs the Alertmanager payload with the dev HMAC secret and POSTs it to the agent:

```bash
curl -X POST http://localhost:8080/webhook/alertmanager \
  -H 'Content-Type: application/json' \
  -H "X-Alertmanager-Signature: $(scripts/sign.sh "$BODY")" \
  -d "$BODY"
```

Expected response:

```json
{ "correlation_id": "01J...", "deduplicated": false }
```

### 2. See the report appear in slack-mock

Open the slack-mock console:

```bash
open http://localhost:8090
```

You should see one message within ~10 s containing:

- A header naming the routed domain (e.g., "Application").
- A root-cause hypothesis, e.g. *"Repeated `NullPointerException` at `CheckoutService:142` over the last 8 minutes; coincides with deployment of revision 9."*
- Two or three cited log lines.
- A proposed fix block: `rollback-deployment` on `checkout` to revision 8.
- Two buttons: **Approve Remediation** and **Reject**.

### 3. Approve the remediation

Click **Approve Remediation**. The slack-mock translates the click into a signed callback POST to the agent's `/callbacks/slack/approve`. The agent role-checks, transitions the Report from `pending → approved`, and `Command(resume=...)`s the graph into the Solver node.

Within ~30 s, a follow-up message appears in the same thread:

> ✅ **Executed:** `rollback-deployment checkout/checkout → revision 8`.
> Post-state: ready, all replicas healthy.
> **Reversal:** `rollback-deployment` to revision 9 (the version we just left).

### 4. Inspect the audit trail

```bash
make audit CORRELATION=01J...
```

…runs:

```sql
SELECT sequence_no, stage, outcome, at
FROM audit_record
WHERE correlation_id = :corr
ORDER BY sequence_no;
```

You should see a complete chain: `webhook_received → mcp_read → router_decision → expert_diagnosis → report_delivered → approval_event(approve) → solver_preflight → mcp_write(applied) → solver_postcheck(success)`.

### 5. Confirm read-only by default

Try firing a second incident and **rejecting** it:

```bash
make smoke INCIDENT=network
# in slack-mock, click "Reject"
make audit CORRELATION=<the new id>
```

The audit chain MUST end at `approval_event(reject)` with no `mcp_write` row for that correlation_id. This is the Principle I safety gate in action.

---

## Running the test suite

```bash
make test            # full suite — unit + contract + integration + eval + hallucination
make test-unit       # fastest feedback
make eval            # LLM golden-set classification + hallucination only
make perf            # latency benchmark; fails on p50 > 30s or p95 > 60s
make audit-check     # audit-completeness invariants against the last 24h
```

CI runs `make test` plus `make perf`. Coverage gates are 85% pure logic / 95% safety-critical per constitution VII; a coverage drop blocks merge.

---

## Common failure modes

| Symptom | Likely cause | Fix |
|---|---|---|
| Webhook returns `401 signature_invalid` | `ALERTMANAGER_HMAC_SECRET` env not set in your shell when running the helper script | `source deploy/dev.env` |
| No message in slack-mock | Agent → slack-mock URL misconfigured | Check `SLACK_MOCK_URL` in `agent`'s env; default is `http://slack-mock:8090` |
| Approve button does nothing | slack-mock callback signature mismatch | Restart the stack so the shared secret is consistent |
| Solver returns `code: approval_invalid` | The approval was rendered against an older `ProposedFix` and the report has been re-rendered | Click Approve on the newest message; older messages are intentionally inert |
| `make smoke` returns a correlation id but no triage runs | Per-tenant kill switch engaged | `curl -X POST http://localhost:8081/admin/kill-switch?tenant=dev&action=clear` |
| LLM call refused with `budget_exceeded` | Per-incident budget set very low in `deploy/dev.env` | Raise `BUDGET_USD_MICROS_PER_INCIDENT` |

---

## Tearing down

```bash
make clean
```

…brings the stack down and deletes the `kind` cluster. Postgres data is on a named volume and is dropped here; if you want to keep audit history across runs, use `docker compose down` without `-v`.

---

## What this quickstart proves

If `make smoke` followed by Approve succeeds end-to-end, you have exercised:

- Webhook auth + dedup (FR-001..FR-003).
- MCP read tool with redaction at the boundary (R7).
- Router structured-output classification (FR-005..FR-008).
- Expert diagnosis with cited evidence (FR-009..FR-012).
- Slack-shape report rendering (FR-013, FR-014, Principle VIII).
- LangGraph `interrupt` HITL gate (FR-015..FR-019).
- MCP write tool with approval-token + fingerprint match (FR-020, FR-021).
- Reversal recipe capture and post-state verification (FR-022..FR-024).
- End-to-end audit chain joined by correlation_id (FR-028, Principle V).

That is the entire MVP slice for this feature on one laptop.
