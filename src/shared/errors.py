"""
src/shared/errors.py

Single user-facing error template (Principle VIII / contracts/slack_mock.md).

machine_token is stable across releases; human-readable message goes alongside.
"""
from __future__ import annotations

from typing import Any, Optional


def error_response(
    machine_token: str,
    message: str,
    correlation_id: Optional[str] = None,
    detail: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Return the canonical error JSON body."""
    body: dict[str, Any] = {"error": machine_token, "message": message}
    if correlation_id is not None:
        body["correlation_id"] = correlation_id
    if detail:
        body["detail"] = detail
    return body
