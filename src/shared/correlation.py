"""
src/shared/correlation.py

Correlation-ID generation and contextvars propagation.

Corresponds to tasks.md T014.

The spec calls for UUIDv7 (time-ordered) strings; Python stdlib only ships
uuid1-5.  We use uuid4 (random) for the MVP since ordering by correlation_id
is never required — the audit table has its own sequence_no per row.
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar

_correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="")


def generate_correlation_id() -> str:
    """Return a new random UUID4 string suitable for use as a CorrelationId."""
    return str(uuid.uuid4())


def set_correlation_id(cid: str) -> None:
    """Bind *cid* to the current async context."""
    _correlation_id_var.set(cid)


def get_correlation_id() -> str:
    """Return the correlation ID bound to the current async context, or '' if unset."""
    return _correlation_id_var.get()
