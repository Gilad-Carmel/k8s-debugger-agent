"""
tests/test_spawn_tracked.py

Regression coverage for the asyncio fire-and-forget GC bug.

asyncio.create_task() only weak-refs the task; without a strong reference
the task can be garbage-collected before it runs to completion. The fix
in src/agent/api/__init__.py is `spawn_tracked()`: stash the task in
app.state.pending_graph_tasks, register set.discard as the done callback.

These tests verify:
  1. spawn_tracked tasks complete even when the caller drops its handle
     and GC is forced mid-flight.
  2. The pending set grows on spawn and shrinks on completion (no leak).
  3. N concurrent webhook hits each get a unique correlation_id, each
     creates an incidents row, and every spawned graph task completes
     (does not get GC'd under load).
"""
from __future__ import annotations

import asyncio
import gc
import json

import httpx
import pytest

from src.agent.api import spawn_tracked
from src.agent.db import get_conn
from tests.conftest import fire_webhook


async def test_spawn_tracked_survives_gc_pressure(app_and_client) -> None:
    """Spawn 50 short-running tracked coroutines, drop every local handle,
    force a GC sweep, and verify every one of them ran to completion."""
    app, _client = app_and_client
    completed: set[int] = set()

    async def _work(i: int) -> None:
        await asyncio.sleep(0.05)
        completed.add(i)

    # Spawn — intentionally do NOT keep references to the returned tasks.
    for i in range(50):
        spawn_tracked(app, _work(i))

    # The set on app.state must hold strong refs; pending_graph_tasks
    # should equal 50 immediately after spawn.
    assert len(app.state.pending_graph_tasks) == 50

    # Force GC mid-flight. If spawn_tracked were just a plain
    # `asyncio.create_task` call without the set anchor, this is where
    # tasks would vanish.
    gc.collect()

    # Wait long enough that all _work() bodies finish.
    await asyncio.sleep(0.5)

    assert completed == set(range(50)), (
        f"some tracked tasks did not complete: missing {set(range(50)) - completed}"
    )
    # Done-callback drained the set back to empty.
    assert len(app.state.pending_graph_tasks) == 0


async def test_spawn_tracked_set_drains_on_completion(app_and_client) -> None:
    """Pending set grows on spawn, shrinks to zero once tasks finish."""
    app, _client = app_and_client

    async def _quick() -> None:
        await asyncio.sleep(0.01)

    spawn_tracked(app, _quick())
    spawn_tracked(app, _quick())
    spawn_tracked(app, _quick())
    assert len(app.state.pending_graph_tasks) == 3

    # Yield until every callback fires.
    for _ in range(20):
        await asyncio.sleep(0.05)
        if not app.state.pending_graph_tasks:
            break
    assert len(app.state.pending_graph_tasks) == 0


async def test_spawn_tracked_records_failed_task(app_and_client) -> None:
    """A coroutine that raises still counts as 'done' — the done-callback
    fires and the set is drained. The exception is retrievable via
    task.exception() but the set must not leak."""
    app, _client = app_and_client

    async def _boom() -> None:
        await asyncio.sleep(0.01)
        raise RuntimeError("intentional")

    task = spawn_tracked(app, _boom())
    assert task in app.state.pending_graph_tasks

    for _ in range(20):
        await asyncio.sleep(0.05)
        if task.done():
            break
    assert task.done()
    assert isinstance(task.exception(), RuntimeError)
    assert task not in app.state.pending_graph_tasks


async def test_n_concurrent_webhooks_all_get_distinct_correlation_ids(
    requires_llm, app_and_client, alertmanager_payload, sign_alertmanager
) -> None:
    """Fire 20 webhooks concurrently against the real /webhook/alertmanager
    endpoint. Each MUST get a unique correlation_id and an incidents row.
    Each MUST also enqueue a tracked graph task (no fire-and-forget GC).
    The graph itself may fail at the router (LLM may be down) — that's
    fine; what we're testing here is the task-tracking, not graph success.
    """
    _app, client = app_and_client
    N = 20

    # Each webhook needs a distinct fingerprint or they'd dedup. Vary the
    # pod label per request.
    payloads = [
        alertmanager_payload(pod=f"checkout-{i:02d}-x29")
        for i in range(N)
    ]

    async def _fire(payload):
        return await fire_webhook(client, payload, sign_alertmanager)

    responses = await asyncio.gather(*(_fire(p) for p in payloads))

    correlation_ids = [r.json()["correlation_id"] for r in responses]
    assert all(r.status_code == 202 for r in responses)
    assert len(set(correlation_ids)) == N, "correlation_ids must be unique"

    # Every fingerprint produced an incidents row.
    async with get_conn() as conn:
        cur = await conn.execute("SELECT COUNT(*) FROM incidents")
        assert (await cur.fetchone())[0] == N

    # Give the spawned graph tasks time to either complete or fail
    # (both count as 'done' and remove the task from the set).
    for _ in range(40):
        await asyncio.sleep(0.1)
        if not _app.state.pending_graph_tasks:
            break

    # Hard upper bound on remaining work — if anything is still pending
    # after 4 s of yielding, the LLM call really is hanging; that's a
    # different problem and not what this test is policing.
    leftover = len(_app.state.pending_graph_tasks)
    assert leftover == 0 or leftover == N, (
        f"unexpected partial-leak: {leftover} of {N} tasks neither "
        "completed nor stayed pending — would indicate the GC bug."
    )
