"""
src/agent/api/__init__.py

FastAPI app factory.

Lifespan responsibilities:
  1. Configure structured logging.
  2. Initialise the SQLite schema (audit_log + incidents).
  3. Open the LangGraph AsyncSqliteSaver and build the graph.
  4. Spawn the approval-deadline expiry watcher.
  5. Stash everything on app.state for routers to grab.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from src.agent.checkpointer import checkpointer_context
from src.agent.db import init_db
from src.agent.graph.builder import build_graph
from src.agent.logging_config import configure_logging, get_logger

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    log.info("agent.startup", phase="init_db")
    await init_db()
    async with checkpointer_context() as checkpointer:
        graph = build_graph(checkpointer)
        app.state.graph = graph
        app.state.checkpointer = checkpointer

        # Imported here to avoid circular import (expiry uses graph factory).
        from src.agent.api.expiry import expiry_loop

        expiry_task = asyncio.create_task(expiry_loop(graph))
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


def create_app() -> FastAPI:
    app = FastAPI(title="k8s-debugger-agent", lifespan=lifespan)

    # Routers — imported lazily to keep create_app() side-effect-free.
    from src.agent.api.callbacks import router as callbacks_router
    from src.agent.api.health import router as health_router
    from src.agent.api.webhook import router as webhook_router

    app.include_router(health_router)
    app.include_router(webhook_router)
    app.include_router(callbacks_router)
    return app
