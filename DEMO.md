# K8s Debugger Agent — Demo Guide

An AI agent that watches your Kubernetes cluster, detects failures, diagnoses root causes, and proposes fixes — with a human-in-the-loop approval step before anything is changed.

## Prerequisites

- Docker + Docker Compose v2
- `kubectl` connected to a running cluster (kind is used locally)
- An OpenRouter API key (or any OpenAI-compatible inference server)

## Setup

### 1. Configure secrets

Edit `deploy/dev.env`:

```env
LLM_BASE_URL=https://openrouter.ai/api/v1
LLM_API_KEY=<your-openrouter-key>
LLM_ROUTER_MODEL=google/gemma-4-26b-a4b-it
LLM_EXPERT_MODEL=google/gemma-4-26b-a4b-it
```

### 2. Deploy the demo workload

```bash
bash scripts/demo/deploy.sh
```

This creates the `demo` namespace and deploys a healthy `podinfo` pod.

### 3. Start the agent stack

```bash
docker compose -f deploy/docker-compose.yml up --build -d
```

| Service | URL | Purpose |
|---|---|---|
| Agent | http://localhost:8080 | Webhook intake + HITL callbacks |
| Slack mock | http://localhost:8090 | Incident dashboard + approve/reject UI |

Wait for the agent to be ready:

```bash
curl http://localhost:8080/health   # → {"status":"ok"}
```

## Trigger a failure scenario

### Scenario 1 — CrashLoop (recommended)

```bash
bash scripts/demo/trigger-crash.sh
```

Sends `/panic` to podinfo, crashing the pod. The agent's listener (polling every few seconds) detects the crash logs and k8s events, runs the triage pipeline, and delivers a report to the Slack mock.

### Scenario 2 — Bad deployment

```bash
bash scripts/demo/trigger-bad-deploy.sh
```

Rolls out a new deployment with `PODINFO_RUNTIME_ERROR=true`, causing HTTP 500s.

### Scenario 3 — Scale / load pressure

```bash
bash scripts/demo/trigger-scale.sh
```

Spins up a `bombardier` pod inside the cluster flooding podinfo with 50 concurrent requests for 60 s.

## Watch the flow

Open the Slack mock dashboard:

```
http://localhost:8090
```

Within ~30 seconds of the failure you should see an incident card with:

- Domain classification (Application / Network / Database)
- Root cause hypothesis with cited log evidence
- Proposed fix (e.g. `restart-pod`)
- **Approve Remediation** and **Reject** buttons

Click **Approve Remediation**. The agent will:

1. Verify the approval token and fingerprint
2. Execute the fix against the cluster (e.g. restart the pod)
3. Post a follow-up message with outcome (✅ success / ❌ failure) and reversal recipe

## Teardown

```bash
docker compose -f deploy/docker-compose.yml down -v
bash scripts/demo/teardown.sh
```

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| No incident appears in Slack mock | Pod recovered before listener polled | Lower `POLL_INTERVAL_SECONDS` in `dev.env` and rebuild |
| Approve button returns error | Incident not found in DB (stale message) | Trigger a fresh failure; approve the newest card only |
| Solver fails with PDB violation | PodDisruptionBudget blocks restart | `kubectl delete pdb podinfo-pdb -n demo` |
| Domain classified as Unknown | Insufficient log evidence | Crash the pod first, then wait for the listener to catch the crash logs |
| LLM call fails | Bad API key or wrong base URL | Check `LLM_API_KEY` and `LLM_BASE_URL` in `deploy/dev.env` |
