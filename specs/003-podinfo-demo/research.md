# Research: Podinfo Demo Integration

**Feature**: 003-podinfo-demo
**Phase**: 0 — Stack decisions

---

## R1 — podinfo Capabilities

**Decision**: Use `ghcr.io/stefanprodan/podinfo:6.7.1`

**Relevant endpoints**:

| Endpoint | Method | Effect |
|---|---|---|
| `/panic` | POST | Immediately calls `panic()`; pod crashes with exit code 2 |
| `/stress` | GET | `?cpu=n&mem=mb&duration=s` — spins CPUs/allocates memory for `duration` seconds |
| `/env` | GET | Returns env vars (useful for verifying rollback) |
| `/readyz` | GET | Returns 200 if ready; becomes 500 after panic until restart |
| `/version` | GET | Returns image version — confirms rollback succeeded |

**Runtime-error mode**: Setting env var `PODINFO_UI_MESSAGE=<msg>` changes the
homepage. More importantly, `PODINFO_RUNTIME_ERROR=true` makes every handler
return `HTTP 500` with a log line containing `runtime error` — ideal for the
bad-deploy scenario.

**Rationale**: podinfo is the de-facto Kubernetes demo workload. It is lightweight
(~10MB image), has controllable failure modes, generates structured log output, and
is maintained by the Flux/Flagger project.

**Alternatives considered**: custom busybox crasher (too minimal, no logs);
httpbin (no controllable crash modes).

---

## R2 — Alertmanager Webhook Format

**Decision**: Reuse the format defined in `contracts/alertmanager_webhook.md`
(feature 002). The fire-webhook script constructs a JSON payload with:

```json
{
  "version": "4",
  "groupKey": "<sha256 of alert labels>",
  "status": "firing",
  "alerts": [{
    "status": "firing",
    "labels": {
      "alertname": "<scenario>",
      "namespace": "demo",
      "pod": "podinfo-<hash>",
      "severity": "warning"
    },
    "annotations": { "description": "<human message>" },
    "startsAt": "<RFC3339>",
    "generatorURL": "http://localhost/graph"
  }]
}
```

The HMAC signature is computed with `ALERTMANAGER_HMAC_SECRET` from `deploy/dev.env`.

**Rationale**: Using the real contract format means the demo exercises the actual
ingestion path including HMAC validation, dedup fingerprinting, and correlation-ID
generation — not a mock bypass.

---

## R3 — Demo Namespace

**Decision**: Use a dedicated `demo` namespace for all podinfo resources.

**Rationale**: Keeps demo state isolated from system workloads; allows
`kubectl delete namespace demo` as a complete teardown; matches production
tenant isolation principle.

**Implementation**: `deploy/demo/00-namespace.yaml` creates the namespace with
label `purpose: demo` so RBAC can be scoped to it.

---

## R4 — Cluster Networking (kind)

**Decision**: Use `kubectl port-forward` in the trigger scripts rather than
NodePort or Ingress.

**Rationale**: The kind cluster used in dev has no external load balancer;
port-forward is the simplest, most portable approach for a local demo. The
trigger script starts port-forward, sends the request, then kills port-forward.

**Alternative considered**: NodePort — works but requires knowing the node IP,
which varies across kind setups.

---

## R5 — Webhook Timing

**Decision**: Trigger scripts wait for the failure condition to be observable
(pod state = `CrashLoopBackOff` or a 500 response) before firing the webhook.
Maximum wait: 30 seconds with a 2-second poll loop.

**Rationale**: Firing too early means the agent reads logs before the crash
has occurred, producing weak evidence. Waiting for the observable state ensures
the agent will find the relevant log lines on the first log fetch.

---

## R6 — Load Generator for S4

**Decision**: Use `kubectl run` with the `alpine/bombardier` image for the
scale-pressure scenario — avoids requiring any local tooling beyond kubectl.

```bash
kubectl run load --image=alpine/bombardier --restart=Never -n demo \
  -- -c 50 -d 60s http://podinfo.demo.svc.cluster.local:9898
```

**Rationale**: Bombardier is a small single-binary HTTP load tester; running
it inside the cluster avoids kind networking complexity.

---

## R7 — Makefile Target Design

**Decision**: Each scenario is a single `make demo-<scenario>` target.
A `make demo-deploy` target is a prerequisite for all scenarios.

```makefile
demo-deploy:      # idempotent: applies all manifests in deploy/demo/
demo-crash:       # S1: panic → webhook
demo-bad-deploy:  # S2: apply v2 → webhook
demo-oom:         # S3: stress mem → webhook
demo-scale:       # S4: bombardier → webhook
demo-teardown:    # delete namespace demo
demo-reset:       # teardown + deploy (between scenarios)
```

**Rationale**: Single-command execution is SC-001 and satisfies Principle III
(developer experience). `demo-reset` between scenarios ensures a clean slate.
