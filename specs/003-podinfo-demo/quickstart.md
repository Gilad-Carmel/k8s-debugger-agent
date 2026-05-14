# Quickstart: Podinfo Demo

## Prerequisites

- A running kind cluster (or any kubeconfig-accessible cluster)
- `kubectl` on PATH
- `deploy/dev.env` populated with `ALERTMANAGER_HMAC_SECRET` and `AGENT_WEBHOOK_URL`
- The agent running locally (`make dev` or `uvicorn src.agent.api:app --port 8000`)

## 1. Deploy podinfo

```bash
make demo-deploy
```

Applies all manifests in `deploy/demo/`. Waits for podinfo to be Ready.
Prints the port-forwarded URL (http://localhost:9898).

## 2. Run a demo scenario

### Scenario S1 — CrashLoop → restart-pod

```bash
make demo-crash
```

What happens:
1. Sends `POST /panic` to podinfo → pod crashes
2. Polls until `CrashLoopBackOff` is observed (or 2 restarts)
3. Fires an Alertmanager webhook to the agent
4. Prints the `correlation_id` to track in the agent logs

Expected agent output:
- Domain: **Application**
- Root cause: pod crash loop (exit code 2)
- Proposed fix: `restart-pod`

### Scenario S2 — Bad Deploy → rollback-deployment

```bash
make demo-bad-deploy
```

What happens:
1. Applies `deploy/demo/02-podinfo-v2-bad.yaml` (RUNTIME_ERROR=true)
2. Polls until pod is Degraded / returning 500s
3. Fires webhook
4. After agent proposes + you approve: rollback restores v1

Expected agent output:
- Domain: **Application**
- Root cause: runtime errors on all requests
- Proposed fix: `rollback-deployment`

### Scenario S3 — OOMKill → restart-pod

```bash
make demo-oom
```

What happens:
1. Sends `GET /stress?mem=50&duration=30` (memory limit is 32Mi)
2. Polls until `OOMKilled` event or exit code 137
3. Fires webhook

### Scenario S4 — Scale Pressure → scale-deployment

```bash
make demo-scale
```

What happens:
1. Runs `bombardier` load generator inside cluster (50 concurrent, 60s)
2. Polls until error rate is detectable in logs
3. Fires webhook suggesting scale action

## 3. Approve the fix

Once the agent delivers a triage report with an Approve button (or callback URL),
use the Slack-mock UI at `http://localhost:9000` to approve.

After approval the Solver executes the fix and posts the outcome.

## 4. Reset between scenarios

```bash
make demo-reset
```

Tears down the `demo` namespace and re-deploys a clean podinfo v1.

## 5. Teardown

```bash
make demo-teardown
```

Deletes the `demo` namespace completely.
