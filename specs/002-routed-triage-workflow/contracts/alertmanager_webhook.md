# Contract: Alertmanager Webhook Intake

**Feature**: 002-routed-triage-workflow
**Owner**: `src/agent/api/webhook.py`
**Spec refs**: FR-001, FR-002, FR-003

## Endpoint

```text
POST /webhook/alertmanager
Content-Type: application/json
X-Alertmanager-Signature: <hex-encoded HMAC-SHA256 of body, using tenant shared secret>
```

## Request body (Alertmanager v4 group payload, subset we use)

```json
{
  "version": "4",
  "groupKey": "{}/{alertname=\"PodCrashLooping\"}:{pod=\"checkout-7b5d-x29\"}",
  "status": "firing",
  "receiver": "k8s-debugger",
  "groupLabels": {
    "alertname": "PodCrashLooping",
    "namespace": "checkout",
    "pod": "checkout-7b5d-x29"
  },
  "commonLabels": { "...": "..." },
  "commonAnnotations": { "summary": "..." },
  "externalURL": "...",
  "alerts": [
    {
      "status": "firing",
      "labels": { "...": "..." },
      "annotations": { "...": "..." },
      "startsAt": "2026-05-14T10:00:00Z",
      "endsAt": "0001-01-01T00:00:00Z",
      "generatorURL": "..."
    }
  ]
}
```

### Required fields (we MUST read)

- `groupKey` — used as the upstream `source_alert_id`.
- `groupLabels.namespace` and `groupLabels.pod` — used to populate `Target`.
- `alerts[*].startsAt` — used to derive the default `TimeWindow.start` (we look back `lookback_seconds` from the earliest `startsAt`; default 900s).
- `status` — we only act on `firing`. `resolved` payloads are recorded and short-circuited.

### Optional fields (we MAY read)

- `groupLabels.container` — narrows `Target.container`.
- `commonLabels.environment`, `commonLabels.severity` — recorded in audit; not used for routing in MVP.

## Authentication

- `X-Alertmanager-Signature` MUST be the HMAC-SHA256 of the raw request body using the tenant's shared secret (env: `ALERTMANAGER_HMAC_SECRET`).
- Constant-time comparison (`hmac.compare_digest`).
- Verification failure: HTTP **401**, no body parsing, no LLM call. Recorded in audit as a rejection event.

## Responses

| Status | Meaning |
|---|---|
| **202 Accepted** | Payload verified, deduplicated, and either (a) launched a new graph run or (b) updated `last_seen_at` of an existing Incident. Body: `{"correlation_id": "...", "deduplicated": <bool>}`. |
| **400 Bad Request** | Malformed JSON, missing required fields, unsupported `version`. Body: `{"error": "...", "field": "..."}`. |
| **401 Unauthorized** | HMAC verification failed. Body: `{"error": "signature_invalid"}`. |
| **422 Unprocessable Entity** | Target namespace/pod don't pass RFC-1123 validation. Body: `{"error": "...", "field": "..."}`. |
| **503 Service Unavailable** | Per-tenant ingestion rate limit exceeded, or kill-switch engaged. `Retry-After` header set. |

## Deduplication

Per R12: `dedup_fingerprint = sha256(group_key || namespace || pod || floor(startsAt / 600))`. Duplicates within the 10-minute bucket update `Incident.last_seen_at` only. The HTTP response distinguishes via the `deduplicated` flag.

## Idempotency

The endpoint is idempotent under (`groupKey`, time bucket, target). Same payload re-delivered ⇒ same `correlation_id` returned, no new graph run.

## Error contract

All errors return a JSON body with at least `{"error": "<machine_token>"}`. Stack traces MUST NOT appear in the body (Principle VIII — consistent error template).

## Performance

- Verification + dedup-check + state insert: target p95 < 250 ms.
- The actual triage runs asynchronously on a background task; the webhook returns 202 the moment the Incident is persisted.
