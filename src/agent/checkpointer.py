"""
src/agent/checkpointer.py

Factory for LangGraph's AsyncSqliteSaver bound to settings.SQLITE_PATH.

The saver shares the SQLite file with audit_log + incidents — LangGraph
creates its own checkpoint tables (`checkpoints`, `writes`) on first use.

Usage (from FastAPI lifespan):

    async with checkpointer_context() as checkpointer:
        graph = build_graph(checkpointer)
        ...
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from src.agent.settings import settings


@asynccontextmanager
async def checkpointer_context() -> AsyncIterator[AsyncSqliteSaver]:
    """Open the AsyncSqliteSaver for the app's lifetime."""
    async with AsyncSqliteSaver.from_conn_string(settings.SQLITE_PATH) as saver:
        # Ensure the checkpoint tables exist (idempotent).
        await saver.setup()
        yield saver
