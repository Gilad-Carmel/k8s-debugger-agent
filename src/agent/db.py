"""
src/agent/db.py

aiosqlite connection helpers + schema bootstrap.

Hackathon simplification: SQLite only. The same .sqlite3 file holds:
  - audit_log (this module)
  - incidents (this module)
  - LangGraph checkpointer tables (created lazily by AsyncSqliteSaver)
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

import aiosqlite

from src.agent.settings import settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    correlation_id TEXT NOT NULL,
    sequence_no INTEGER NOT NULL,
    stage TEXT NOT NULL,
    outcome TEXT NOT NULL DEFAULT 'ok',
    actor TEXT,
    payload TEXT NOT NULL DEFAULT '{}',
    at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE(correlation_id, sequence_no)
);
CREATE INDEX IF NOT EXISTS idx_audit_log_correlation_id ON audit_log(correlation_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_at ON audit_log(at);

CREATE TABLE IF NOT EXISTS incidents (
    correlation_id TEXT PRIMARY KEY,
    dedup_fingerprint TEXT NOT NULL UNIQUE,
    source_alert_id TEXT,
    namespace TEXT,
    pod TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    received_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    approval_deadline TEXT,
    proposed_fix_fingerprint TEXT
);
CREATE INDEX IF NOT EXISTS idx_incidents_status_deadline ON incidents(status, approval_deadline);
CREATE INDEX IF NOT EXISTS idx_incidents_dedup ON incidents(dedup_fingerprint);
"""


@asynccontextmanager
async def get_conn() -> AsyncIterator[aiosqlite.Connection]:
    """Open a short-lived aiosqlite connection with WAL + FK pragmas."""
    conn = await aiosqlite.connect(settings.SQLITE_PATH)
    try:
        await conn.execute("PRAGMA journal_mode=WAL;")
        await conn.execute("PRAGMA foreign_keys=ON;")
        conn.row_factory = aiosqlite.Row
        yield conn
    finally:
        await conn.close()


async def set_proposed_fix_fingerprint(correlation_id: str, fingerprint: str) -> None:
    async with get_conn() as conn:
        await conn.execute(
            "UPDATE incidents SET proposed_fix_fingerprint = ? WHERE correlation_id = ?",
            (fingerprint, correlation_id),
        )
        await conn.commit()


async def init_db() -> None:
    """Create the audit_log + incidents tables on startup. Idempotent."""
    db_dir = os.path.dirname(settings.SQLITE_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    async with get_conn() as conn:
        await conn.executescript(_SCHEMA)
        await conn.commit()
