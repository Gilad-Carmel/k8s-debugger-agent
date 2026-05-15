# Contract: Scenario Trigger API

**Endpoint**: `POST /api/demo/trigger/{scenario}`

**Auth**: None (loopback-only in demo mode)

---

## Path Parameters

| Param | Values | Description |
|-------|--------|-------------|
| `scenario` | `crash`, `bad-deploy`, `oom`, `scale` | Which demo scenario to trigger |

The four scenarios correspond to the scripts in `scripts/demo/`:
- `crash` → `scripts/demo/trigger-crash.sh` (podinfo `/panic` → CrashLoopBackOff)
- `bad-deploy` → `scripts/demo/trigger-bad-deploy.sh` (apply v2 bad image → 500s)
- `oom` → `scripts/demo/trigger-oom.sh` (`/stress?mem=50` → OOMKilled with 32Mi limit)
- `scale` → `scripts/demo/trigger-scale.sh` (bombardier load → error rate)

---

## Request Body

None. The namespace, pod name, agent URL, and HMAC secret are read from the
server's environment (same values used by `make demo-*` targets).

---

## Response 202

Script launched successfully (fire-and-forget; the webhook fires asynchronously).

```json
{
  "correlation_id": "01JVXYZ...",
  "scenario": "crash",
  "started_at": "2026-05-15T10:05:00Z"
}
```

The `correlation_id` is returned immediately so the client can subscribe to
the SSE stream before the first event arrives.

**Note**: The backend starts the trigger script as a background subprocess,
then immediately calls `fire-webhook.sh` to post the Alertmanager payload
and obtain the `correlation_id`. The actual failure may take up to 30s to
manifest on the pod (the script polls until the failure is observable).

---

## Response 400

```json
{
  "error": "unknown_scenario",
  "message": "scenario must be one of: crash, bad-deploy, oom, scale"
}
```

## Response 503

```json
{
  "error": "script_not_found",
  "message": "scripts/demo/trigger-crash.sh not found — run make demo-deploy first"
}
```
