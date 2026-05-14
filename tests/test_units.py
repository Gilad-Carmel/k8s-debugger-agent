"""tests/test_units.py — Unit tests for audit, approval_token, auth, errors."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from src.agent.approval_token import issue_token, verify_token
from src.agent.audit import fetch_chain, log_audit_event
from src.agent.auth import check_approver_role
from src.agent.db import init_db
from src.agent.settings import settings
from src.shared.errors import error_response


# ---------------------------------------------------------------------------
# error_response
# ---------------------------------------------------------------------------
def test_error_response_minimal() -> None:
    out = error_response("foo", "bar")
    assert out == {"error": "foo", "message": "bar"}


def test_error_response_with_correlation_and_detail() -> None:
    out = error_response("foo", "bar", correlation_id="cid-1", detail={"x": 1})
    assert out == {"error": "foo", "message": "bar", "correlation_id": "cid-1", "detail": {"x": 1}}


# ---------------------------------------------------------------------------
# auth.check_approver_role
# ---------------------------------------------------------------------------
def test_role_check_passes_when_role_present() -> None:
    assert check_approver_role([settings.APPROVER_ROLE, "sre"]) is True


def test_role_check_fails_when_role_absent() -> None:
    assert check_approver_role(["just-a-viewer"]) is False
    assert check_approver_role([]) is False


# ---------------------------------------------------------------------------
# approval_token
# ---------------------------------------------------------------------------
def test_token_round_trip() -> None:
    cid = "abc123"
    fp = "fp-deadbeef"
    t = issue_token(cid, fp, ttl_seconds=300)
    assert verify_token(t, cid, fp) is True


def test_token_rejects_wrong_correlation_id() -> None:
    t = issue_token("cid-A", "fp-1", ttl_seconds=300)
    assert verify_token(t, "cid-B", "fp-1") is False


def test_token_rejects_wrong_fingerprint() -> None:
    t = issue_token("cid-A", "fp-1", ttl_seconds=300)
    assert verify_token(t, "cid-A", "fp-2") is False


def test_token_rejects_expired() -> None:
    # Issue with negative TTL → already expired.
    t = issue_token("cid", "fp", ttl_seconds=-1)
    assert verify_token(t, "cid", "fp") is False


def test_token_rejects_tampered_signature() -> None:
    t = issue_token("cid", "fp", ttl_seconds=300)
    exp, _ = t.split(".")
    tampered = f"{exp}.{'0' * 64}"
    assert verify_token(tampered, "cid", "fp") is False


def test_token_rejects_garbage() -> None:
    assert verify_token("not-a-token", "cid", "fp") is False
    assert verify_token("", "cid", "fp") is False


# ---------------------------------------------------------------------------
# audit.log_audit_event — sequence_no monotonicity & concurrency
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_audit_sequence_no_monotonic_per_correlation_id(
    fresh_db: Path,
) -> None:
    await init_db()
    cid = "cid-mono"
    seq1 = await log_audit_event(cid, "stage1")
    seq2 = await log_audit_event(cid, "stage2")
    seq3 = await log_audit_event(cid, "stage3")
    assert (seq1, seq2, seq3) == (1, 2, 3)

    chain = await fetch_chain(cid)
    assert [r["sequence_no"] for r in chain] == [1, 2, 3]
    assert [r["stage"] for r in chain] == ["stage1", "stage2", "stage3"]


@pytest.mark.asyncio
async def test_audit_independent_streams_per_correlation_id(
    fresh_db: Path,
) -> None:
    await init_db()
    a1 = await log_audit_event("cid-A", "x")
    b1 = await log_audit_event("cid-B", "x")
    a2 = await log_audit_event("cid-A", "y")
    b2 = await log_audit_event("cid-B", "y")
    assert a1 == b1 == 1
    assert a2 == b2 == 2


@pytest.mark.asyncio
async def test_audit_concurrent_writes_do_not_collide(fresh_db: Path) -> None:
    """Sanity check: 20 concurrent appends to the same correlation_id end up
    with dense sequence numbers 1..20 and no UNIQUE-constraint failure."""
    await init_db()
    cid = "cid-concurrent"
    await asyncio.gather(*(log_audit_event(cid, f"s{i}") for i in range(20)))
    chain = await fetch_chain(cid)
    seqs = sorted(r["sequence_no"] for r in chain)
    assert seqs == list(range(1, 21))


@pytest.mark.asyncio
async def test_audit_payload_serialisation_roundtrip(fresh_db: Path) -> None:
    await init_db()
    cid = "cid-payload"
    await log_audit_event(
        cid,
        "approval_event",
        outcome="ok",
        payload={"action": "approve", "tokens": [1, 2, 3]},
        actor={"type": "user", "id": "U-1", "roles": ["x"]},
    )
    [row] = await fetch_chain(cid)
    assert row["payload"] == {"action": "approve", "tokens": [1, 2, 3]}
    assert row["actor"] == {"type": "user", "id": "U-1", "roles": ["x"]}
    assert row["outcome"] == "ok"
