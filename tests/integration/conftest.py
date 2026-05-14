from __future__ import annotations

import httpx
import pytest

from src.agent.settings import settings


def _server_reachable() -> bool:
    """Return True if the configured LLM base URL responds successfully within 3 s."""
    try:
        url = settings.llm_base_url.rstrip("/").removesuffix("/v1") + "/v1/models"
        response = httpx.get(url, timeout=3.0)
        return response.status_code == 200
    except Exception:
        return False


@pytest.fixture(scope="session", autouse=True)
def skip_if_llm_server_unreachable() -> None:
    """Skip integration tests when the configured LLM server is unavailable."""
    if not _server_reachable():
        pytest.skip(
            reason=(
                f"LLM server not reachable at {settings.llm_base_url} — "
                "start your inference server and re-run."
            )
        )
