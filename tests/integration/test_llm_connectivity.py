"""
tests/integration/test_llm_connectivity.py

Checks that the configured OpenAI-compatible inference server is reachable
and that both call patterns used by the agent work end-to-end:

  1. Raw chat completion  — mirrors `curl .../v1/chat/completions`
  2. Structured output    — the actual path used by router_node
                           (ChatOpenAI.with_structured_output, json_mode)

Run with:
    uv run pytest tests/integration/test_llm_connectivity.py -v

The tests are marked `integration` and will be skipped automatically when the
server is not reachable (i.e. in CI without a running inference server).

Notes on method="json_mode":
  The default `with_structured_output` strategy (`function_calling`) requires the
  model to honour OpenAI tool-call schema binding — not all Ollama models do.
  `json_mode` sets `response_format={"type": "json_object"}`, which every
  OpenAI-compatible server (Ollama, LM Studio, vLLM) supports.  We therefore use
  `json_mode` for the connectivity smoke-test.  The router node itself also uses
  json_mode (see src/agent/graph/nodes/router.py) for the same reason.
"""

from __future__ import annotations

import pytest
import httpx

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from src.agent.settings import settings


# ---------------------------------------------------------------------------
# Skip the whole module if the inference server is not reachable.
# ---------------------------------------------------------------------------

def _server_reachable() -> bool:
    """Return True if the configured LLM base URL responds successfully within 3 s."""
    try:
        # Hit the /models endpoint — present on every OpenAI-compatible server.
        url = settings.llm_base_url.rstrip("/").removesuffix("/v1") + "/v1/models"
        response = httpx.get(url, timeout=3.0)
        return response.status_code == 200
    except Exception:
        return False


pytestmark = pytest.mark.integration

# Applied at collection time so the skip message is clear.
if not _server_reachable():
    pytestmark = [
        pytest.mark.integration,
        pytest.mark.skip(
            reason=(
                f"LLM server not reachable at {settings.llm_base_url} — "
                "start your inference server and re-run."
            )
        ),
    ]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_llm() -> ChatOpenAI:
    """Mirror the factory in router.py so we test the identical configuration."""
    return ChatOpenAI(
        model=settings.llm_router_model,
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,  # type: ignore[arg-type]
        temperature=0,
        max_tokens=256,
    )


# ---------------------------------------------------------------------------
# Test 1 — raw chat completion (mirrors the curl request)
# ---------------------------------------------------------------------------

def test_llm_raw_chat_completion() -> None:
    """
    Sends a trivial chat completion request and asserts that:
    - The call completes without raising.
    - The response content is a non-empty string.
    - usage_metadata is populated (token counts available for budget tracking).
    """
    llm = _build_llm()
    messages = [
        SystemMessage(content="You are a helpful assistant."),
        HumanMessage(content="Reply with exactly one word: OK"),
    ]

    response = llm.invoke(messages)

    assert response.content, "LLM returned an empty response"
    assert isinstance(response.content, str), (
        f"Expected str content, got {type(response.content)}"
    )
    # Usage metadata should be present (needed for per-incident token budget).
    assert response.usage_metadata is not None, (
        "usage_metadata is None — token budget tracking will not work"
    )
    total = response.usage_metadata.get("total_tokens", 0)
    assert total > 0, f"total_tokens={total}; expected a positive value"

    print(
        f"\n[test_llm_raw_chat_completion] "
        f"model={settings.llm_router_model}  "
        f"base_url={settings.llm_base_url}  "
        f"total_tokens={total}  "
        f"reply={response.content!r}"
    )


# ---------------------------------------------------------------------------
# Test 2 — structured output (the path used by router_node)
# ---------------------------------------------------------------------------

class _SimpleDecision(BaseModel):
    """Minimal structured schema — avoids depending on internal router schema."""
    answer: str = Field(description="Your one-word answer.")
    confidence: str = Field(description="Your confidence: low, medium, or high.")


def test_llm_structured_output() -> None:
    """
    Exercises ChatOpenAI.with_structured_output(method="json_mode") — the same
    binding used by router_node to get _RouterDecision back.

    We use method="json_mode" (response_format={"type":"json_object"}) rather
    than the default "function_calling" because Ollama models (including
    qwen2.5:7b) do not reliably honour tool-call schema binding via the
    OpenAI-compatible API; they return plain text instead of a structured tool
    response.  json_mode is universally supported and matches the strategy used
    by router.py.

    Asserts that:
    - The call completes without raising.
    - `parsed` is a valid _SimpleDecision (not None).
    - `raw` AIMessage is returned (needed for usage_metadata extraction).
    - No parsing_error is present.
    - confidence is one of the three expected literals.
    """
    llm = _build_llm()
    # json_mode: model must emit a JSON object; Pydantic validates the shape.
    structured = llm.with_structured_output(
        _SimpleDecision,
        include_raw=True,
        method="json_mode",
    )

    messages = [
        SystemMessage(
            content=(
                "You answer questions with JSON only. "
                "Respond with a single JSON object that has exactly two keys:\n"
                '  "answer"     — your one-word answer (string)\n'
                '  "confidence" — your confidence level: one of "low", "medium", or "high"\n'
                "Do not include any text outside the JSON object."
            )
        ),
        HumanMessage(content="What is the capital of France? Answer in one word."),
    ]

    result: dict = structured.invoke(messages)  # type: ignore[assignment]

    raw = result.get("raw")
    parsed: _SimpleDecision | None = result.get("parsed")
    parse_error = result.get("parsing_error")

    assert parse_error is None, (
        f"Structured output parsing failed: {parse_error}\n"
        "The model may not support json_mode / JSON-object response format.\n"
        f"Raw response: {getattr(raw, 'content', raw)!r}"
    )
    assert parsed is not None, (
        f"parsed is None — structured output returned nothing.\n"
        f"Raw response: {getattr(raw, 'content', raw)!r}"
    )
    assert parsed.answer, "parsed.answer is empty"
    assert parsed.confidence in {"low", "medium", "high"}, (
        f"Unexpected confidence value: {parsed.confidence!r}\n"
        "Expected one of: low, medium, high"
    )

    # Verify we can extract token counts the same way router_node does.
    usage = getattr(raw, "usage_metadata", None) or {}
    total = int(usage.get("total_tokens", 0))
    assert total > 0, f"total_tokens={total}; expected a positive value"

    print(
        f"\n[test_llm_structured_output] "
        f"method=json_mode  "
        f"answer={parsed.answer!r}  "
        f"confidence={parsed.confidence!r}  "
        f"total_tokens={total}"
    )
