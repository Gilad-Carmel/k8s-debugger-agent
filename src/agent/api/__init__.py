"""
src/agent/api/__init__.py

FastAPI app factory.

Lifespan responsibilities:
  1. Configure structured logging.
  2. Initialise the SQLite schema (audit_log + incidents).
  3. Open the LangGraph AsyncSqliteSaver and build the graph.
  4. Allocate the pending-graph-tasks set (asyncio.create_task only holds
     a weak ref; without a strong ref the task can be GC'd mid-run).
  5. Recover orphaned approved incidents (approved in DB but Solver never
     ran because the process died before the background task completed).
  6. Spawn the approval-deadline expiry watcher.
  7. Stash everything on app.state for routers to grab.
  8. On shutdown: cancel expiry, wait briefly for in-flight graph runs.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Coroutine

from fastapi import FastAPI

from src.agent.checkpointer import checkpointer_context
from src.agent.db import get_conn, init_db
from src.agent.graph.builder import build_graph
from src.agent.logging_config import configure_logging, get_logger

log = get_logger(__name__)

# Bounded-time wait for in-flight graph tasks to finish at shutdown.
_SHUTDOWN_DRAIN_SECONDS = 5.0


def spawn_tracked(app: FastAPI, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
    """
    Schedule *coro* on the event loop AND keep a strong reference until done.

    asyncio.create_task() only weak-refs the task, so a fire-and-forget
    `asyncio.create_task(coro)` can be garbage-collected before it runs to
    completion under load. Tracking it in app.state.pending_graph_tasks and
    discarding when done is the canonical fix from the Python asyncio docs.
    """
    pending: set[asyncio.Task[Any]] = app.state.pending_graph_tasks
    task = asyncio.create_task(coro)
    pending.add(task)
    task.add_done_callback(pending.discard)
    return task


async def _recover_approved_incidents(app: FastAPI) -> None:
    """
    Re-queue graph resumes for incidents that were approved but whose Solver
    task never completed — typically because the process was killed between
    the 200 response from POST /callbacks/slack/approve and the background
    task finishing.

    We detect these as rows with status='approved' in the DB. The graph
    checkpoint for each thread_id still exists (it was persisted before we
    died), so aupdate_state + ainvoke(None) will pick up exactly where it
    left off.

    The approval token is recovered from the most recent approval_event audit
    row so the Solver pre-flight receives the same token it would have seen
    had the process not crashed.
    """
    from src.agent.api.callbacks import _resume_graph

    async with get_conn() as conn:
        cur = await conn.execute(
            "SELECT correlation_id FROM incidents WHERE status = 'approved'"
        )
        rows = await cur.fetchall()

    if not rows:
        return

    cids = [r["correlation_id"] for r in rows]
    log.warning(
        "agent.startup",
        phase="recover_approved",
        count=len(cids),
        correlation_ids=cids,
    )

    from src.agent.audit import fetch_chain

    graph = app.state.graph
    for cid in cids:
        # Recover the token from the audit chain so the Solver pre-flight
        # receives the same value it would have gotten on a clean run.
        token = ""
        chain = await fetch_chain(cid)
        for row in reversed(chain):
            if row["stage"] == "approval_event" and row["outcome"] == "ok":
                token = row["payload"].get("approval_token", "")
                break
        spawn_tracked(app, _resume_graph(graph, cid, "APPROVED", token))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    log.info("agent.startup", phase="init_db")
    await init_db()
    async with checkpointer_context() as checkpointer:
        graph = build_graph(checkpointer)
        app.state.graph = graph
        app.state.checkpointer = checkpointer
        # Allocate ONCE in lifespan so concurrent requests can't race on
        # lazy-init and accidentally drop a task.
        app.state.pending_graph_tasks = set()

        # Re-queue any approved incidents whose Solver task never completed
        # because the process was killed after the 200 callback response.
        await _recover_approved_incidents(app)

        # Imported here to avoid circular import (expiry uses graph factory).
        from src.agent.api.expiry import expiry_loop
        from src.agent.graph.nodes.ingest import run_listener

        expiry_task = asyncio.create_task(expiry_loop(graph))
        listener_task = asyncio.create_task(run_listener(graph, app))
        log.info("agent.startup", phase="ready")
        try:
            yield
        finally:
            log.info("agent.shutdown", phase="cancel_expiry")
            expiry_task.cancel()
            try:
                await expiry_task
            except asyncio.CancelledError:
                pass

            # Best-effort drain so in-flight graph runs don't get orphaned
            # at process exit. Strategy: give them a brief grace period to
            # finish naturally, then cancel anything still pending and
            # swallow the cancellation fallout. We do NOT wait for hung
            # LLM calls — at shutdown speed beats grace.
            pending = app.state.pending_graph_tasks
            if pending:
                log.info("agent.shutdown", phase="drain", in_flight=len(pending))
                # Brief grace window for fast-completing tasks.
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*pending, return_exceptions=True),
                        timeout=_SHUTDOWN_DRAIN_SECONDS,
                    )
                except BaseException:  # noqa: BLE001
                    pass
                # Anything still pending: cancel and swallow.
                still_pending = [t for t in pending if not t.done()]
                if still_pending:
                    log.warning(
                        "agent.shutdown",
                        phase="drain_cancel",
                        still_pending=len(still_pending),
                    )
                    for t in still_pending:
                        t.cancel()
                    try:
                        await asyncio.gather(*still_pending, return_exceptions=True)
                    except BaseException:  # noqa: BLE001
                        pass


def create_app() -> FastAPI:
    app = FastAPI(title="k8s-debugger-agent", lifespan=lifespan)

    # Loopback guard for GUI approval endpoints — rejects requests from
    # non-localhost origins so the secret-free approval path can't be
    # called externally.
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.requests import Request as StarletteRequest
    from starlette.responses import JSONResponse as StarletteJSONResponse

    class LoopbackGuardMiddleware(BaseHTTPMiddleware):
        _PROTECTED_PREFIX = "/api/approval/"

        async def dispatch(self, request: StarletteRequest, call_next):  # type: ignore[override]
            if request.url.path.startswith(self._PROTECTED_PREFIX):
                client_host = request.client.host if request.client else ""
                if client_host not in ("127.0.0.1", "::1", "localhost"):
                    return StarletteJSONResponse(
                        status_code=403,
                        content={
                            "error": "forbidden",
                            "message": "GUI approval endpoint is only accessible from localhost.",
                        },
                    )
            return await call_next(request)

    app.add_middleware(LoopbackGuardMiddleware)

    # CORS for Vite dev server (port 5173). Enabled only when GUI_DEV_MODE=true.
    import os
    if os.getenv("GUI_DEV_MODE", "").lower() in ("1", "true", "yes"):
        from fastapi.middleware.cors import CORSMiddleware
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["http://localhost:5173"],
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # Routers — imported lazily to keep create_app() side-effect-free.
    from src.agent.api.callbacks import router as callbacks_router
    from src.agent.api.health import router as health_router
    from src.agent.api.webhook import router as webhook_router

    # GUI routers
    from src.agent.api.gui import (
        approval_router,
        inject_router,
        pods_router,
        scenarios_router,
        stream_router,
    )

    app.include_router(health_router)
    app.include_router(webhook_router)
    app.include_router(callbacks_router)
    app.include_router(pods_router)
    app.include_router(scenarios_router)
    app.include_router(inject_router)
    app.include_router(stream_router)
    app.include_router(approval_router)

    # Optionally serve the built GUI SPA from gui/dist/ when GUI_STATIC_DIR is set.
    gui_static_dir = os.getenv("GUI_STATIC_DIR", "")
    if gui_static_dir:
        import pathlib
        from fastapi.staticfiles import StaticFiles
        static_path = pathlib.Path(gui_static_dir)
        if static_path.is_dir():
            app.mount("/", StaticFiles(directory=str(static_path), html=True), name="gui")

    return app
