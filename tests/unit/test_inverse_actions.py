"""
tests/unit/test_inverse_actions.py

Unit tests for the Forward → Inverse Action mapping table and reversal
parameter computation in src/shared/catalog.py.

Corresponds to tasks.md T036 / T087.
"""

import pytest

from src.shared.catalog import (
    INVERSE_ACTIONS,
    build_reversal_description,
    compute_reversal_parameters,
)


class TestInverseActionsTable:
    def test_restart_pod_has_no_inverse(self) -> None:
        assert INVERSE_ACTIONS["restart-pod"] is None

    def test_delete_pod_to_reschedule_has_no_inverse(self) -> None:
        assert INVERSE_ACTIONS["delete-pod-to-reschedule"] is None

    def test_rollback_deployment_inverts_to_itself(self) -> None:
        assert INVERSE_ACTIONS["rollback-deployment"] == "rollback-deployment"

    def test_scale_deployment_inverts_to_itself(self) -> None:
        assert INVERSE_ACTIONS["scale-deployment"] == "scale-deployment"

    def test_all_four_catalog_entries_present(self) -> None:
        expected = {"restart-pod", "rollback-deployment", "scale-deployment", "delete-pod-to-reschedule"}
        assert set(INVERSE_ACTIONS.keys()) == expected


class TestComputeReversalParameters:
    def test_rollback_returns_previous_revision(self) -> None:
        pre_state = {"current_revision": 3}
        params = compute_reversal_parameters("rollback-deployment", pre_state)
        assert params == {"to_revision": 3}

    def test_scale_returns_previous_replica_count(self) -> None:
        pre_state = {"replicas": 2}
        params = compute_reversal_parameters("scale-deployment", pre_state)
        assert params == {"to_replicas": 2}

    def test_restart_pod_returns_empty_dict(self) -> None:
        assert compute_reversal_parameters("restart-pod", {"restart_count": 1}) == {}

    def test_delete_pod_returns_empty_dict(self) -> None:
        assert compute_reversal_parameters("delete-pod-to-reschedule", {}) == {}

    def test_rollback_missing_revision_returns_empty(self) -> None:
        params = compute_reversal_parameters("rollback-deployment", {})
        assert params == {}

    def test_scale_missing_replicas_returns_empty(self) -> None:
        params = compute_reversal_parameters("scale-deployment", {})
        assert params == {}


class TestBuildReversalDescription:
    def test_restart_pod_description(self) -> None:
        desc = build_reversal_description("restart-pod", {})
        assert "self-recovering" in desc.lower()

    def test_delete_pod_description(self) -> None:
        desc = build_reversal_description("delete-pod-to-reschedule", {})
        assert "self-recovering" in desc.lower()

    def test_rollback_description_contains_revision(self) -> None:
        desc = build_reversal_description("rollback-deployment", {"current_revision": 5})
        assert "5" in desc

    def test_rollback_description_unknown_revision(self) -> None:
        desc = build_reversal_description("rollback-deployment", {})
        assert "?" in desc

    def test_scale_description_contains_replicas(self) -> None:
        desc = build_reversal_description("scale-deployment", {"replicas": 3})
        assert "3" in desc
