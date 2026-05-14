"""
src/shared/errors.py

Two unrelated exports kept together because both have the historical name
``src.shared.errors``:

  1. ``ToolError`` — re-exported from src/shared/schemas (T013/T016 / R7).
     The structured error shape returned by MCP tool calls.

  2. ``error_response()`` — single user-facing JSON error template per
     Principle VIII / contracts/slack_mock.md, used by the FastAPI
     webhook + callbacks endpoints. machine_token is stable across
     releases; human-readable message goes alongside.
"""
from __future__ import annotations

from typing import Any, Optional

from src.shared.schemas import ToolError

__all__ = ["ToolError", "error_response"]


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
