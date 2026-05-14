"""
src/agent/audit.py

Append-only audit writer. One row per stage transition, joined by correlation_id.

Stage names follow contracts/audit_record.md §Stage enum. We allow arbitrary
strings (the hackathon graph also writes <node>_placeholder rows so the wiring
is visible end-to-end), but the canonical set is exposed for autocomplete /
ref docs.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from src.agent.db import get_conn

# Canonical stage names from contracts/audit_record.md (informational only —
# log_audit_event accepts any string so node-placeholder rows can use
# `<node>_placeholder` while teammates are still building.)
CANONICAL_STAGES: frozenset[str] = frozenset(
    {
        "webhook_received",
        "webhook_rejected",
        "incident_deduped",
        "mcp_read",
        "router_decision",
        "expert_diagnosis",
        "report_delivered",
        "report_delivery_failed",
        "approval_event",
        "solver_preflight",
        "mcp_write",
        "solver_postcheck",
        "budget_exceeded",
        "kill_switch_engaged",
    }
)


async def log_audit_event(
    correlation_id: str,
    stage: str,
    outcome: str = "ok",
    payload: Optional[dict[str, Any]] = None,
    actor: Optional[dict[str, Any]] = None,
) -> int:
    """
    Append one audit row.

    Returns the assigned sequence_no (monotonic per correlation_id).
    Safe to call concurrently — SQLite serializes the (SELECT MAX, INSERT)
    pair under the implicit transaction lock.
    """
    payload_json = json.dumps(payload or {}, default=str, separators=(",", ":"))
    actor_json = json.dumps(actor, default=str, separators=(",", ":")) if actor else None

    async with get_conn() as conn:
        # IMMEDIATE transaction so the (SELECT MAX, INSERT) pair is atomic.
        await conn.execute("BEGIN IMMEDIATE;")
        cur = await conn.execute(
            "SELECT COALESCE(MAX(sequence_no), 0) + 1 FROM audit_log WHERE correlation_id = ?",
            (correlation_id,),
        )
        row = await cur.fetchone()
        next_seq = int(row[0]) if row else 1
        await cur.close()
        await conn.execute(
            """
            INSERT INTO audit_log (correlation_id, sequence_no, stage, outcome, actor, payload)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (correlation_id, next_seq, stage, outcome, actor_json, payload_json),
        )
        await conn.commit()
        return next_seq


async def fetch_chain(correlation_id: str) -> list[dict[str, Any]]:
    """Return all audit rows for one incident, ordered by sequence_no."""
    async with get_conn() as conn:
        cur = await conn.execute(
            """
            SELECT sequence_no, stage, outcome, actor, payload, at
            FROM audit_log
            WHERE correlation_id = ?
            ORDER BY sequence_no ASC
            """,
            (correlation_id,),
        )
        rows = await cur.fetchall()
        await cur.close()
    out: list[dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "sequence_no": r["sequence_no"],
                "stage": r["stage"],
                "outcome": r["outcome"],
                "actor": json.loads(r["actor"]) if r["actor"] else None,
                "payload": json.loads(r["payload"]) if r["payload"] else {},
                "at": r["at"],
            }
        )
    return out
