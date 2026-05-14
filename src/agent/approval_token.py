"""
src/agent/approval_token.py

Short-lived signed approval token. Issued by the HITL callback handler on
successful approve, then carried through state to the Solver pre-flight so
no mutating tool can be invoked without an in-flight authorized approval.

Token format: "<exp_unix>.<hex_hmac>"
HMAC body:    "<correlation_id>|<fingerprint>|<exp_unix>"
"""
from __future__ import annotations

import hmac
import time
from hashlib import sha256

from src.agent.settings import settings


def _sign(correlation_id: str, fingerprint: str, exp_unix: int) -> str:
    body = f"{correlation_id}|{fingerprint}|{exp_unix}".encode()
    return hmac.new(settings.APPROVAL_TOKEN_SECRET.encode(), body, sha256).hexdigest()


def issue_token(correlation_id: str, fingerprint: str, ttl_seconds: int = 600) -> str:
    """Issue a token valid for `ttl_seconds` (default 10 min)."""
    exp_unix = int(time.time()) + ttl_seconds
    sig = _sign(correlation_id, fingerprint, exp_unix)
    return f"{exp_unix}.{sig}"


def verify_token(token: str, expected_correlation_id: str, expected_fingerprint: str) -> bool:
    """Constant-time verify. Returns True iff signature + expiry are valid."""
    try:
        exp_str, sig = token.split(".", 1)
        exp_unix = int(exp_str)
    except (ValueError, AttributeError):
        return False
    if exp_unix < int(time.time()):
        return False
    expected = _sign(expected_correlation_id, expected_fingerprint, exp_unix)
    return hmac.compare_digest(expected, sig)
