"""
src/shared/correlation.py

correlation_id generation + contextvar propagation for structured logging.

Hackathon simplification: uuid4().hex instead of UUIDv7 to avoid an extra dep.
"""
from __future__ import annotations

import uuid
from contextvars import ContextVar

correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="")


def new_correlation_id() -> str:
    """Generate a fresh correlation_id (hex, no dashes)."""
    return uuid.uuid4().hex


def bind(correlation_id: str) -> None:
    """Bind a correlation_id to the current async context."""
    correlation_id_var.set(correlation_id)


def get() -> str:
    """Return the bound correlation_id, or empty string if none."""
    return correlation_id_var.get()
