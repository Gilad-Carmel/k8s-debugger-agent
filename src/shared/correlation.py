"""
src/shared/correlation.py

Correlation-ID generation + contextvars propagation.

Two API surfaces are exported, both backed by the same ContextVar:

  - Canonical (per tasks.md T014):
      generate_correlation_id() -> str
      set_correlation_id(cid: str) -> None
      get_correlation_id() -> str

  - Short aliases used by the Person 3 webhook/callback layer:
      new_correlation_id() -> str   (alias for generate_correlation_id)
      bind(cid: str) -> None        (alias for set_correlation_id)
      get() -> str                  (alias for get_correlation_id)

The spec calls for UUIDv7 (time-ordered) strings; Python stdlib only ships
uuid1-5. We use uuid4 for the MVP since ordering by correlation_id is never
required — the audit table has its own monotonic sequence_no per row.

Note: previous Person-3 builds used uuid4().hex (32 chars, no dashes).
This module now returns str(uuid4()) (36 chars with dashes) to align with
the team's canonical contract. Callers that compared length must update.
"""
from __future__ import annotations

import uuid
from contextvars import ContextVar

# Single ContextVar backs both API surfaces.
_correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="")

# Public name for code that wants to read the var directly.
correlation_id_var = _correlation_id_var


# ---------------------------------------------------------------------------
# Canonical API (preferred for new code)
# ---------------------------------------------------------------------------
def generate_correlation_id() -> str:
    """Return a fresh UUID4 string (36 chars, with dashes)."""
    return str(uuid.uuid4())


def set_correlation_id(cid: str) -> None:
    """Bind *cid* to the current async context."""
    _correlation_id_var.set(cid)


def get_correlation_id() -> str:
    """Return the bound correlation_id, or '' if unset."""
    return _correlation_id_var.get()


# ---------------------------------------------------------------------------
# Short aliases (kept for the Person 3 webhook + callbacks layer)
# ---------------------------------------------------------------------------
def new_correlation_id() -> str:
    """Alias for generate_correlation_id()."""
    return generate_correlation_id()


def bind(cid: str) -> None:
    """Alias for set_correlation_id(cid)."""
    set_correlation_id(cid)


def get() -> str:
    """Alias for get_correlation_id()."""
    return get_correlation_id()
