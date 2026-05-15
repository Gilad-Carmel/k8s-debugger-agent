"""
src/agent/solver_lock.py

Per-target Solver serialization lock (FR-026).

Ensures at most one Solver execution runs per (namespace, pod) target at a
time.  Uses threading.Lock so it is safe to call from synchronous LangGraph
node functions that may run in a thread executor.

Invariants:
  - A lock is created on first use and never removed (bounded growth: one
    lock per unique namespace/pod pair seen during the process lifetime).
  - Acquiring the lock with `solver_target_lock` is blocking; callers MUST
    release (i.e., always use the context-manager form).
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Generator

_registry: dict[str, threading.Lock] = {}
_registry_lock = threading.Lock()


def _get_lock(namespace: str, pod: str) -> threading.Lock:
    key = f"{namespace}/{pod}"
    with _registry_lock:
        if key not in _registry:
            _registry[key] = threading.Lock()
        return _registry[key]


@contextmanager
def solver_target_lock(namespace: str, pod: str) -> Generator[None, None, None]:
    """Context manager that serializes Solver execution per (namespace, pod)."""
    lock = _get_lock(namespace, pod)
    lock.acquire()
    try:
        yield
    finally:
        lock.release()
