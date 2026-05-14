# Feature Specification: Podinfo Demo Integration

**Feature ID**: 003-podinfo-demo
**Branch**: `podinfo-setup-and-script`
**Status**: In Planning
**Priority**: P1 (demo / hackathon validation)

---

## Problem Statement

The triage+remediation workflow (feature 002) requires a real Kubernetes workload
to demonstrate against. Running it against arbitrary customer clusters during a demo
is risky and unpredictable. We need a controlled, repeatable workload where failures
can be triggered on demand, logs are meaningful, and recovery is observable.

## Goal

Deploy [podinfo](https://github.com/stefanprodan/podinfo) as a demo workload and
provide a set of one-command scripts that trigger specific failure modes, fire a
matching Alertmanager-format webhook, and drive the end-to-end triage workflow.

## Scope

### In scope

- Kubernetes manifests for podinfo deployment (with limits set to make OOM and
  crash scenarios reproducible)
- Four demo scenarios with matching trigger scripts
- Makefile targets for each scenario (`make demo-crash`, `make demo-bad-deploy`,
  `make demo-oom`, `make demo-scale`)
- A `scripts/fire-webhook.sh` that constructs and POSTs a valid Alertmanager
  v4 webhook payload matching `contracts/alertmanager_webhook.md`
- A `deploy/demo/` directory with all manifests and a `deploy.sh` setup script

### Out of scope

- Real Prometheus/Alertmanager installation (synthetic webhooks are used)
- Multi-tenant demo isolation
- Persistent demo state between cluster restarts

---

## Failure Scenarios

### S1 — CrashLoop (Application domain → `restart-pod`)

**Trigger**: `POST http://<podinfo>/panic` crashes the process. Repeated crashes
produce `CrashLoopBackOff`. The demo script fires a webhook after the second
restart so the agent has real crash logs to cite.

**Expected triage path**:
- Domain: Application
- Evidence: crash log lines with `panic` or `exit code 1`
- Proposed fix: `restart-pod`
- Post-fix: pod returns to Ready

### S2 — Bad Deploy (Application domain → `rollback-deployment`)

**Trigger**: `kubectl apply` of a v2 Deployment spec that sets `RUNTIME_ERROR=true`
(podinfo returns HTTP 500 on every request). Health check fails → pod enters
`CrashLoopBackOff` / NotReady.

**Expected triage path**:
- Domain: Application
- Evidence: `500 Internal Server Error` log lines
- Proposed fix: `rollback-deployment` to revision N-1
- Post-fix: deployment returns to v1 serving 200s

### S3 — OOMKill (Application domain → `restart-pod` or `scale-deployment`)

**Trigger**: `POST http://<podinfo>/stress?mem=64` with a memory limit of 32Mi
on the container causes `OOMKilled`.

**Expected triage path**:
- Domain: Application
- Evidence: OOMKilled event, exit code 137
- Proposed fix: `restart-pod` (or `scale-deployment` if diagnosed as capacity)
- Post-fix: pod restarts within limits

### S4 — Scale Pressure (Network/Application domain → `scale-deployment`)

**Trigger**: `hey` or `wrk` load generator floods podinfo with requests. Combined
with a replica count of 1 and a low CPU limit, this causes request queuing and
timeout errors.

**Expected triage path**:
- Domain: Network or Application (high latency / timeout logs)
- Proposed fix: `scale-deployment` to 3 replicas
- Post-fix: error rate drops

---

## Success Criteria

| Criterion | Acceptance bar |
|---|---|
| SC-001 | `make demo-deploy` deploys podinfo and prints the service URL in <30s |
| SC-002 | Each `make demo-<scenario>` fires the webhook within 5s of the failure |
| SC-003 | The agent produces a triage Report citing at least one real log line or event from the podinfo pod |
| SC-004 | After approval, the fix is applied and the pod returns to Ready |
| SC-005 | `make demo-teardown` removes all demo resources cleanly |

---

## Non-Goals

- This feature does NOT install Prometheus or Alertmanager; all alerts are synthetic.
- This feature does NOT modify the agent or MCP server code.
- This feature is NOT multi-scenario concurrent (one scenario at a time).
