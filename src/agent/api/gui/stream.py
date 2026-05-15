"""
src/agent/api/gui/stream.py

GET /api/events — Server-Sent Events stream for workflow events.

Query params:
  correlation_id (required) — subscribe to events for this run
  last_event_id  (optional) — resume from this sequence number on reconnect

The browser EventSource automatically sends Last-Event-ID on reconnect;
the server replays missed events from the in-memory buffer.

SSE frame format:
  id:    {correlation_id}:{seq}
  event: {WorkflowEventType}
  data:  {JSON-encoded WorkflowEvent}
  (blank line)

The connection closes automatically when a terminal event is emitted
(solver_done, rejected, expired, run_failed).
"""

from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from src.agent.api.gui.event_bus import (
    TERMINAL_EVENTS,
    WorkflowEvent,
    WorkflowEventType,
    cleanup,
    get_missed_events,
    get_or_create_queue,
)
from src.agent.logging_config import get_logger

router = APIRouter()
log = get_logger(__name__)

# How long to wait for the next event before sending a heartbeat comment.
HEARTBEAT_INTERVAL = 15.0
# How long to keep waiting for events total before closing a stale connection.
IDLE_TIMEOUT = 600.0


async def _event_generator(
    correlation_id: str, last_seq: int
) -> AsyncIterator[dict[str, str]]:
    queue = get_or_create_queue(correlation_id)

    # Replay any missed events from the buffer
    missed = get_missed_events(correlation_id, last_seq)
    if missed and last_seq >= 0:
        for event in missed:
            yield _format(event)
    elif not missed and last_seq >= 0:
        # Buffer evicted — tell client to re-fetch state
        yield {
            "id": f"{correlation_id}:-1",
            "event": WorkflowEventType.RECONNECT_MISSED,
            "data": json.dumps({"correlation_id": correlation_id}),
        }
        return

    idle_elapsed = 0.0
    while idle_elapsed < IDLE_TIMEOUT:
        try:
            event: WorkflowEvent = await asyncio.wait_for(
                queue.get(), timeout=HEARTBEAT_INTERVAL
            )
        except asyncio.TimeoutError:
            # Send a comment heartbeat to keep the connection alive.
            yield {"comment": "heartbeat"}
            idle_elapsed += HEARTBEAT_INTERVAL
            continue

        idle_elapsed = 0.0
        yield _format(event)

        if event.type in TERMINAL_EVENTS:
            break

    cleanup(correlation_id)


def _format(event: WorkflowEvent) -> dict[str, str]:
    return {
        "id": f"{event.correlation_id}:{event.seq}",
        "event": event.type.value,
        "data": event.model_dump_json(),
    }


@router.get("/api/events")
async def event_stream(correlation_id: str, last_event_id: str = "") -> EventSourceResponse:
    if not correlation_id:
        return JSONResponse(  # type: ignore[return-value]
            status_code=400,
            content={"error": "missing_correlation_id", "message": "correlation_id query param is required"},
        )

    last_seq = -1
    if last_event_id:
        # Format: "{correlation_id}:{seq}"
        parts = last_event_id.rsplit(":", 1)
        if len(parts) == 2:
            try:
                last_seq = int(parts[1])
            except ValueError:
                pass

    log.info("sse.subscribe", correlation_id=correlation_id, last_seq=last_seq)
    return EventSourceResponse(_event_generator(correlation_id, last_seq))
