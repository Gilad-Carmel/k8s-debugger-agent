# Contract: Pod Status API

**Endpoint**: `GET /api/pods`

**Auth**: None (loopback-only in demo mode)

---

## Query Parameters

| Param | Required | Default | Description |
|-------|----------|---------|-------------|
| `namespace` | no | `demo` | Kubernetes namespace to list pods from |

---

## Response 200

```json
{
  "pods": [
    {
      "name": "podinfo-7d8f9c-xkq2r",
      "namespace": "demo",
      "phase": "Running",
      "ready": true,
      "restart_count": 0,
      "message": null,
      "ts": "2026-05-15T10:00:00Z"
    },
    {
      "name": "podinfo-7d8f9c-abc1d",
      "namespace": "demo",
      "phase": "Failed",
      "ready": false,
      "restart_count": 5,
      "message": "OOMKilled",
      "ts": "2026-05-15T10:01:30Z"
    }
  ],
  "fetched_at": "2026-05-15T10:01:35Z"
}
```

## Response 503

Returned when `kubectl` times out or cannot reach the API server.

```json
{
  "error": "kubectl_timeout",
  "message": "kubectl did not respond within 5s"
}
```

---

## Notes

- `ready` is `true` iff all containers have `Ready=True` in `status.conditions`.
- `restart_count` is the sum of `containerStatuses[*].restartCount`.
- `message` is populated from the last terminated container's `message` or
  `reason` field (e.g. `OOMKilled`, `Error`, `CrashLoopBackOff`).
- The GUI polls this endpoint every 3 seconds.
