# Data Model: Podinfo Demo Integration

**Feature**: 003-podinfo-demo

This feature introduces no new Pydantic entities or database tables. All entities
(Target, TimeWindow, FilteredEvidence, Report, etc.) are defined in feature 002
and remain unchanged.

---

## Kubernetes Resources

### Namespace

```yaml
# deploy/demo/00-namespace.yaml
apiVersion: v1
kind: Namespace
metadata:
  name: demo
  labels:
    purpose: demo
```

### podinfo Deployment — v1 (good)

```yaml
# deploy/demo/01-podinfo-v1.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: podinfo
  namespace: demo
  annotations:
    demo.k8s-debugger/scenario: base
spec:
  replicas: 1
  selector:
    matchLabels:
      app: podinfo
  template:
    metadata:
      labels:
        app: podinfo
    spec:
      containers:
      - name: podinfo
        image: ghcr.io/stefanprodan/podinfo:6.7.1
        ports:
        - containerPort: 9898
        resources:
          requests:
            memory: "16Mi"
            cpu: "50m"
          limits:
            memory: "32Mi"   # intentionally low for OOM demo
            cpu: "100m"
        livenessProbe:
          httpGet:
            path: /healthz
            port: 9898
          initialDelaySeconds: 5
          periodSeconds: 5
        readinessProbe:
          httpGet:
            path: /readyz
            port: 9898
          initialDelaySeconds: 5
          periodSeconds: 5
```

### podinfo Deployment — v2 (bad, for rollback demo)

```yaml
# deploy/demo/02-podinfo-v2-bad.yaml
# Same as v1 but adds PODINFO_RUNTIME_ERROR=true
# Applied with: kubectl apply -f deploy/demo/02-podinfo-v2-bad.yaml
spec:
  template:
    spec:
      containers:
      - name: podinfo
        env:
        - name: PODINFO_RUNTIME_ERROR
          value: "true"
        - name: PODINFO_UI_MESSAGE
          value: "ERROR: database connection refused"
```

### Service

```yaml
# deploy/demo/03-service.yaml
apiVersion: v1
kind: Service
metadata:
  name: podinfo
  namespace: demo
spec:
  selector:
    app: podinfo
  ports:
  - port: 9898
    targetPort: 9898
```

### PodDisruptionBudget (for PDB demo guard)

```yaml
# deploy/demo/04-pdb.yaml
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: podinfo-pdb
  namespace: demo
spec:
  minAvailable: 1
  selector:
    matchLabels:
      app: podinfo
```

---

## Script Inputs / Outputs

### fire-webhook.sh inputs

| Parameter | Source | Example |
|---|---|---|
| `--scenario` | CLI arg | `crash-loop`, `bad-deploy`, `oom-kill`, `scale-pressure` |
| `--namespace` | CLI arg, default `demo` | `demo` |
| `--pod` | Auto-detected from cluster | `podinfo-abc123` |
| `--agent-url` | Env `AGENT_WEBHOOK_URL` | `http://localhost:8000/webhook/alertmanager` |
| `--hmac-secret` | Env `ALERTMANAGER_HMAC_SECRET` | from `deploy/dev.env` |

### fire-webhook.sh output

A `202 Accepted` response containing `{"correlation_id": "<uuid>"}` which the
trigger script prints and can be used to poll for the triage report.

---

## Alertname → Domain mapping (expected by triage agent)

| Scenario | alertname | Expected domain |
|---|---|---|
| S1 crash-loop | `KubePodCrashLooping` | Application |
| S2 bad-deploy | `KubePodNotReady` | Application |
| S3 oom-kill | `KubePodOOMKilled` | Application |
| S4 scale-pressure | `KubeContainerWaiting` | Network or Application |
