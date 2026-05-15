"""
src/agent/api/gui/event_bus.py

In-memory asyncio pub/sub bus for workflow events.

Design:
  - One asyncio.Queue[WorkflowEvent] per correlation_id.
  - The graph runner puts events; the SSE handler drains them.
  - Queues are capped at MAX_QUEUE_SIZE to bound memory usage.
  - Queues are cleaned up when the SSE consumer signals done OR when
    a terminal event (SOLVER_DONE, REJECTED, EXPIRED, RUN_FAILED) is put.
  - Thread-safety: asyncio event loop is single-threaded; dict access is safe.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel

MAX_QUEUE_SIZE = 200


class WorkflowEventType(str, Enum):
    NODE_STARTED = "node_started"
    NODE_COMPLETED = "node_completed"
    NODE_FAILED = "node_failed"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    SOLVER_DONE = "solver_done"
    RUN_FAILED = "run_failed"
    RECONNECT_MISSED = "reconnect_missed"


TERMINAL_EVENTS = {
    WorkflowEventType.SOLVER_DONE,
    WorkflowEventType.REJECTED,
    WorkflowEventType.EXPIRED,
    WorkflowEventType.RUN_FAILED,
}


class WorkflowEvent(BaseModel):
    seq: int
    correlation_id: str
    type: WorkflowEventType
    node: str | None = None
    ts: datetime
    data: dict[str, Any] = {}


# Global registry: correlation_id → (queue, replay_buffer, seq_counter)
_queues: dict[str, asyncio.Queue[WorkflowEvent]] = {}
_buffers: dict[str, list[WorkflowEvent]] = {}
_seqs: dict[str, int] = {}


def _next_seq(correlation_id: str) -> int:
    _seqs[correlation_id] = _seqs.get(correlation_id, -1) + 1
    return _seqs[correlation_id]


def get_or_create_queue(correlation_id: str) -> asyncio.Queue[WorkflowEvent]:
    if correlation_id not in _queues:
        _queues[correlation_id] = asyncio.Queue(maxsize=MAX_QUEUE_SIZE)
        _buffers[correlation_id] = []
        _seqs[correlation_id] = -1
    return _queues[correlation_id]


async def publish(
    correlation_id: str,
    event_type: WorkflowEventType,
    node: str | None = None,
    data: dict[str, Any] | None = None,
) -> None:
    queue = get_or_create_queue(correlation_id)
    event = WorkflowEvent(
        seq=_next_seq(correlation_id),
        correlation_id=correlation_id,
        type=event_type,
        node=node,
        ts=datetime.now(timezone.utc),
        data=data or {},
    )
    _buffers[correlation_id].append(event)
    # Drop oldest buffer entries beyond MAX_QUEUE_SIZE
    if len(_buffers[correlation_id]) > MAX_QUEUE_SIZE:
        _buffers[correlation_id] = _buffers[correlation_id][-MAX_QUEUE_SIZE:]
    try:
        queue.put_nowait(event)
    except asyncio.QueueFull:
        # Best-effort: if the SSE consumer is too slow, skip rather than block the graph.
        pass


def get_missed_events(correlation_id: str, last_seq: int) -> list[WorkflowEvent]:
    buf = _buffers.get(correlation_id, [])
    return [e for e in buf if e.seq > last_seq]


def cleanup(correlation_id: str) -> None:
    _queues.pop(correlation_id, None)
    _buffers.pop(correlation_id, None)
    _seqs.pop(correlation_id, None)
