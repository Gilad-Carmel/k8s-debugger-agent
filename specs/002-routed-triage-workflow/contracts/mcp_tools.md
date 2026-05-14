# Contract: MCP Tool Catalog

**Feature**: 002-routed-triage-workflow
**Owner**: `src/mcp_server/tools/`
**Spec refs**: FR-004, FR-011, FR-020 – FR-026

Conventions:

- All tool inputs and outputs are pydantic v2 models, serialized as JSON for MCP transport.
- Write tools are physically separate from the agent process (R5). Each write tool authenticates with its own per-action Kubernetes ServiceAccount, loaded at MCP-server startup; the agent process never holds a write-capable token.
- All tool calls receive an opaque `correlation_id` parameter that is echoed into the MCP server's audit trail.
- All tools return a `ToolError` with a stable `code` on failure (Principle VIII — consistent error template). No stack traces in the response body.
- Redaction (R7) is applied **inside** read tools before the response crosses the MCP boundary.

---

## Read tools

### `search_pod_logs`

Fetches a pod's logs and applies a local grep/regex pre-filter before returning.

**Input**

```text
namespace        : str                       # RFC-1123 label
pod              : str                       # RFC-1123 label
container        : str | None                # default: all containers
since            : datetime                  # window start (inclusive)
until            : datetime                  # window end (exclusive)
patterns         : list[str]                 # extended regex; default: built-in network|application|db pattern set
max_hit_lines    : int (1..2000, default 500)
correlation_id   : str
```

**Output**

```text
total_bytes           : int
total_lines           : int
hit_lines             : list[LogExcerpt]     # already-redacted, see R7
hit_count             : int
truncated             : bool                 # True if pre-cap hits exceeded max_hit_lines
containers_sampled    : list[str]
container_instances   : list[ContainerInstance]   # name + UID; tracks restart-rotations
```

**Errors**

| `code` | When |
|---|---|
| `not_found` | Pod doesn't exist or has no readable logs. |
| `forbidden` | The read-tool ServiceAccount can't read logs in the namespace. |
| `window_invalid` | `since >= until` or window exceeds the configured cap. |
| `upstream_timeout` | K8s log API exceeded the per-call deadline. |

**Performance**

- p95 wall-clock budget for this tool: 8 s (the rest of the budget is left for the LLM stages).
- Bounded jittered retries (max 3, 200 ms → 1 s → 5 s, ±50 % jitter) for transient `upstream_timeout`.

---

### `get_pod`

Read-only metadata used by the Solver for pre-state / post-state snapshots.

**Input**

```text
namespace        : str
pod              : str
correlation_id   : str
```

**Output**

```text
phase                  : Literal["Pending", "Running", "Succeeded", "Failed", "Unknown"]
restart_count_by_ctr   : dict[str, int]
container_states       : dict[str, ContainerState]   # Waiting/Running/Terminated + reason
ready                  : bool
resource_version       : str                         # for optimistic-concurrency reads
observed_at            : datetime
```

**Errors**: as `search_pod_logs`, omitting `window_invalid`.

---

## Write tools

All write tools share the following contract:

- **Input** always includes `correlation_id` and an opaque `approval_token` (a short-lived signed claim issued by the agent when an `ApprovalEvent` flips a Report to `approved`).
- **Pre-flight check**: the tool MUST validate `approval_token` signature + expiry + the `proposed_fix_fingerprint` claim. Failure ⇒ `code: "approval_invalid"`, no Kubernetes call issued.
- **Admission, PDB, and quota refusals are NEVER bypassed.** No `--force`, no `--grace-period=0`. Refusal ⇒ `code: "admission_denied"` plus the underlying reason.
- **Output** always includes `outcome ∈ {applied, refused, error}`, `pre_state`, `post_state` (after the verification window), and `reversal_recipe`.

### `restart_pod`

**Input**

```text
namespace                  : str
pod                        : str
correlation_id             : str
approval_token             : str
proposed_fix_fingerprint   : str
verification_window_sec    : int (1..120, default 60)
```

**Output**

```text
outcome              : Literal["applied", "refused", "error"]
pre_state            : PodSnapshot
post_state           : PodSnapshot
reversal_recipe      : ReversalRecipe
error                : str | None
```

**Semantics**: deletes the pod's containers via `kubectl rollout restart`'s equivalent at the API level (i.e., we delete the pod with the default grace period and let the controller reschedule). Verification = wait until a new pod with the same controller owner is `Ready` OR the window elapses.

---

### `rollback_deployment`

**Input**

```text
namespace                  : str
deployment                 : str
to_revision                : int                     # MUST be a real, prior revision; tool fetches the list to verify
correlation_id             : str
approval_token             : str
proposed_fix_fingerprint   : str
verification_window_sec    : int (1..120, default 60)
```

**Output**: as above; `pre_state.current_revision` and `post_state.current_revision` MUST differ on `applied`.

---

### `scale_deployment`

**Input**

```text
namespace                  : str
deployment                 : str
to_replicas                : int                     # tool refuses if outside tenant's [min, max] bound
correlation_id             : str
approval_token             : str
proposed_fix_fingerprint   : str
verification_window_sec    : int (1..120, default 60)
```

**Errors**: additional `code: "out_of_bounds"` if `to_replicas` is outside the tenant configuration.

---

### `delete_pod_to_reschedule`

**Input**

```text
namespace                  : str
pod                        : str
correlation_id             : str
approval_token             : str
proposed_fix_fingerprint   : str
verification_window_sec    : int (1..120, default 60)
```

**Semantics**: deletes the pod, respecting default termination grace period, PDBs, and admission controllers. Used only when the diagnosis is "stuck container that a reschedule will heal." If a PDB or admission controller refuses, the tool returns `outcome: "refused"` with the reason and does NOT retry with destructive flags (Principle I).

---

## Kill switch

The MCP server MUST listen for a kill-switch signal (e.g., a `POST /admin/kill-switch?tenant=...` endpoint, IP-restricted) and, on receipt, refuse all subsequent write tool calls for that tenant within 5 seconds, returning `code: "tenant_halted"`. In-flight calls are not aborted mid-API-call but are NOT followed by verification or any retry; the user receives an outcome on whatever did or didn't happen.
