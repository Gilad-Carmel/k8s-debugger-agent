"""
tests/unit/test_solver_lock.py

Unit tests for src/agent/solver_lock.py (T088).

Verifies:
  - Same lock object returned for the same (namespace, pod) pair.
  - Different lock objects for different targets.
  - Lock is mutually exclusive across threads.
  - Context manager releases lock on normal exit.
  - Context manager releases lock on exception.
"""

from __future__ import annotations

import threading
import time

import pytest

from src.agent.solver_lock import _get_lock, _registry, solver_target_lock


@pytest.fixture(autouse=True)
def _clear_registry():
    """Isolate tests by clearing the global lock registry."""
    _registry.clear()
    yield
    _registry.clear()


class TestGetLock:
    def test_same_target_returns_same_lock(self) -> None:
        lock_a = _get_lock("ns", "pod-a")
        lock_b = _get_lock("ns", "pod-a")
        assert lock_a is lock_b

    def test_different_pod_returns_different_lock(self) -> None:
        lock_a = _get_lock("ns", "pod-a")
        lock_b = _get_lock("ns", "pod-b")
        assert lock_a is not lock_b

    def test_different_namespace_returns_different_lock(self) -> None:
        lock_a = _get_lock("ns-a", "pod")
        lock_b = _get_lock("ns-b", "pod")
        assert lock_a is not lock_b

    def test_lock_registered_after_first_call(self) -> None:
        assert "ns/pod-x" not in _registry
        _get_lock("ns", "pod-x")
        assert "ns/pod-x" in _registry


class TestSolverTargetLock:
    def test_context_manager_acquires_and_releases(self) -> None:
        with solver_target_lock("ns", "pod"):
            pass
        lock = _get_lock("ns", "pod")
        assert not lock.locked()

    def test_context_manager_releases_on_exception(self) -> None:
        with pytest.raises(ValueError):
            with solver_target_lock("ns", "pod"):
                raise ValueError("boom")
        lock = _get_lock("ns", "pod")
        assert not lock.locked()

    def test_mutual_exclusion_across_threads(self) -> None:
        """Second thread must wait until first releases the lock."""
        log: list[str] = []

        def first_thread() -> None:
            with solver_target_lock("ns", "pod"):
                log.append("first:enter")
                time.sleep(0.05)
                log.append("first:exit")

        def second_thread() -> None:
            time.sleep(0.01)  # let first acquire first
            with solver_target_lock("ns", "pod"):
                log.append("second:enter")

        t1 = threading.Thread(target=first_thread)
        t2 = threading.Thread(target=second_thread)
        t1.start()
        t2.start()
        t1.join(timeout=2)
        t2.join(timeout=2)

        assert log == ["first:enter", "first:exit", "second:enter"]

    def test_different_targets_run_concurrently(self) -> None:
        """Two different targets must not block each other."""
        results: list[str] = []
        barrier = threading.Barrier(2)

        def thread_a() -> None:
            with solver_target_lock("ns", "pod-a"):
                barrier.wait(timeout=2)
                results.append("a")

        def thread_b() -> None:
            with solver_target_lock("ns", "pod-b"):
                barrier.wait(timeout=2)
                results.append("b")

        t1 = threading.Thread(target=thread_a)
        t2 = threading.Thread(target=thread_b)
        t1.start()
        t2.start()
        t1.join(timeout=2)
        t2.join(timeout=2)

        assert set(results) == {"a", "b"}
