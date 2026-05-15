"""
tests/unit/test_solver_guards.py

Unit tests for src/mcp_server/tools/_guards.py (T086).
95 % coverage tier — tests every code path in each guard function.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.mcp_server.tools._guards import (
    GuardError,
    _labels_match,
    check_kill_switch,
    check_pod_disruption_budget,
    validate_approval_token,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SECRET = "dev-approval-secret"


def _make_token(
    correlation_id: str,
    fingerprint: str,
    ttl: int = 300,
    secret: str = _SECRET,
) -> str:
    """Build a valid approval token matching approval_token.py format."""
    exp_unix = int(time.time()) + ttl
    body = f"{correlation_id}|{fingerprint}|{exp_unix}".encode()
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"{exp_unix}.{sig}"


# ---------------------------------------------------------------------------
# validate_approval_token
# ---------------------------------------------------------------------------


class TestValidateApprovalToken:
    _CID = "corr-1234"
    _FP = "deadbeef" * 8  # 64-char hex

    def test_valid_token_does_not_raise(self) -> None:
        token = _make_token(self._CID, self._FP)
        validate_approval_token(token, self._CID, self._FP)  # must not raise

    def test_missing_dot_raises(self) -> None:
        with pytest.raises(GuardError) as exc_info:
            validate_approval_token("nodothere", self._CID, self._FP)
        assert exc_info.value.tool_error.machine_token == "approval_invalid"

    def test_non_integer_expiry_raises(self) -> None:
        with pytest.raises(GuardError) as exc_info:
            validate_approval_token("notanint.somesig", self._CID, self._FP)
        assert exc_info.value.tool_error.machine_token == "approval_invalid"

    def test_expired_token_raises(self) -> None:
        token = _make_token(self._CID, self._FP, ttl=-1)
        with pytest.raises(GuardError) as exc_info:
            validate_approval_token(token, self._CID, self._FP)
        assert exc_info.value.tool_error.machine_token == "approval_invalid"
        assert "expired" in exc_info.value.tool_error.human_message.lower()

    def test_wrong_fingerprint_raises(self) -> None:
        token = _make_token(self._CID, self._FP)
        with pytest.raises(GuardError) as exc_info:
            validate_approval_token(token, self._CID, "wrong-fingerprint")
        assert exc_info.value.tool_error.machine_token == "approval_invalid"

    def test_wrong_correlation_id_raises(self) -> None:
        token = _make_token(self._CID, self._FP)
        with pytest.raises(GuardError) as exc_info:
            validate_approval_token(token, "other-corr", self._FP)
        assert exc_info.value.tool_error.machine_token == "approval_invalid"

    def test_tampered_signature_raises(self) -> None:
        token = _make_token(self._CID, self._FP)
        # Flip last char of the signature
        parts = token.rsplit(".", 1)
        tampered = parts[0] + "." + parts[1][:-1] + ("a" if parts[1][-1] != "a" else "b")
        with pytest.raises(GuardError) as exc_info:
            validate_approval_token(tampered, self._CID, self._FP)
        assert exc_info.value.tool_error.machine_token == "approval_invalid"

    def test_wrong_secret_raises(self) -> None:
        token = _make_token(self._CID, self._FP, secret="different-secret")
        with pytest.raises(GuardError) as exc_info:
            validate_approval_token(token, self._CID, self._FP)
        assert exc_info.value.tool_error.machine_token == "approval_invalid"


# ---------------------------------------------------------------------------
# check_kill_switch
# ---------------------------------------------------------------------------


class TestCheckKillSwitch:
    @pytest.mark.asyncio
    async def test_halted_tenant_raises(self) -> None:
        with patch("src.mcp_server.admin.is_tenant_halted", new=AsyncMock(return_value=True)):
            with pytest.raises(GuardError) as exc_info:
                await check_kill_switch("my-tenant")
            assert exc_info.value.tool_error.machine_token == "tenant_halted"

    @pytest.mark.asyncio
    async def test_active_tenant_does_not_raise(self) -> None:
        with patch("src.mcp_server.admin.is_tenant_halted", new=AsyncMock(return_value=False)):
            await check_kill_switch("my-tenant")  # must not raise

    @pytest.mark.asyncio
    async def test_none_tenant_checked(self) -> None:
        with patch("src.mcp_server.admin.is_tenant_halted", new=AsyncMock(return_value=False)) as mock_fn:
            await check_kill_switch(None)
            mock_fn.assert_awaited_once_with(None)


# ---------------------------------------------------------------------------
# _labels_match
# ---------------------------------------------------------------------------


class TestLabelsMatch:
    def test_empty_selector_matches_everything(self) -> None:
        assert _labels_match({"app": "foo"}, {}) is True

    def test_subset_selector_matches(self) -> None:
        assert _labels_match({"app": "foo", "env": "prod"}, {"app": "foo"}) is True

    def test_extra_pod_labels_still_match(self) -> None:
        assert _labels_match({"a": "1", "b": "2"}, {"a": "1"}) is True

    def test_mismatched_value_does_not_match(self) -> None:
        assert _labels_match({"app": "bar"}, {"app": "foo"}) is False

    def test_missing_key_does_not_match(self) -> None:
        assert _labels_match({}, {"app": "foo"}) is False


# ---------------------------------------------------------------------------
# check_pod_disruption_budget
# ---------------------------------------------------------------------------


class TestCheckPodDisruptionBudget:
    def _make_pdb(self, name: str, disruptions_allowed: int, match_labels: dict) -> MagicMock:
        pdb = MagicMock()
        pdb.metadata.name = name
        pdb.status.disruptions_allowed = disruptions_allowed
        pdb.spec.selector.match_labels = match_labels
        return pdb

    @pytest.mark.asyncio
    async def test_no_pdbs_does_not_raise(self) -> None:
        api_client = MagicMock()
        with patch("kubernetes.client.PolicyV1Api") as MockPolicy:
            MockPolicy.return_value.list_namespaced_pod_disruption_budget.return_value.items = []
            await check_pod_disruption_budget(api_client, "default", {"app": "foo"})

    @pytest.mark.asyncio
    async def test_pdb_with_allowance_does_not_raise(self) -> None:
        api_client = MagicMock()
        pdb = self._make_pdb("pdb-ok", disruptions_allowed=1, match_labels={"app": "foo"})
        with patch("kubernetes.client.PolicyV1Api") as MockPolicy:
            MockPolicy.return_value.list_namespaced_pod_disruption_budget.return_value.items = [pdb]
            await check_pod_disruption_budget(api_client, "default", {"app": "foo"})

    @pytest.mark.asyncio
    async def test_pdb_at_zero_with_matching_labels_raises(self) -> None:
        api_client = MagicMock()
        pdb = self._make_pdb("pdb-blocked", disruptions_allowed=0, match_labels={"app": "foo"})
        with patch("kubernetes.client.PolicyV1Api") as MockPolicy:
            MockPolicy.return_value.list_namespaced_pod_disruption_budget.return_value.items = [pdb]
            with pytest.raises(GuardError) as exc_info:
                await check_pod_disruption_budget(api_client, "default", {"app": "foo"})
        assert exc_info.value.tool_error.machine_token == "admission_denied"
        assert "pdb-blocked" in exc_info.value.tool_error.human_message

    @pytest.mark.asyncio
    async def test_pdb_at_zero_non_matching_labels_does_not_raise(self) -> None:
        api_client = MagicMock()
        pdb = self._make_pdb("pdb-other", disruptions_allowed=0, match_labels={"app": "other"})
        with patch("kubernetes.client.PolicyV1Api") as MockPolicy:
            MockPolicy.return_value.list_namespaced_pod_disruption_budget.return_value.items = [pdb]
            await check_pod_disruption_budget(api_client, "default", {"app": "foo"})

    @pytest.mark.asyncio
    async def test_k8s_api_error_fails_closed(self) -> None:
        api_client = MagicMock()
        with patch("kubernetes.client.PolicyV1Api") as MockPolicy:
            MockPolicy.return_value.list_namespaced_pod_disruption_budget.side_effect = Exception("k8s down")
            with pytest.raises(GuardError) as exc_info:
                await check_pod_disruption_budget(api_client, "default", {"app": "foo"})
        assert exc_info.value.tool_error.machine_token == "admission_denied"
