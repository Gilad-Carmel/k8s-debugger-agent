"""
src/agent/settings.py

Pydantic Settings: env-driven configuration for the agent service.

Corresponds to tasks.md T017.

All settings can be overridden via environment variables or a ``.env`` file at
the repo root.  Copy ``.env.example`` → ``.env`` and adjust values.

LLM provider (research.md R2, plan.md §Technical Context):
  The agent talks to **any OpenAI-compatible** inference server via
  ``langchain-openai``'s ``ChatOpenAI``.  For local development the default
  target is Ollama at ``http://localhost:11434/v1``.  Override ``LLM_BASE_URL``
  to point at a different server (LM Studio, vLLM, cloud OpenAI, etc.).

Two-reviewer rule (Principle VI / plan.md §Constitution Check): any PR that
changes LLM_ROUTER_MODEL, LLM_EXPERT_MODEL, or LLM_BASE_URL requires two
reviewers and a plan.md citation.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentSettings(BaseSettings):
    """
    Runtime configuration for the agent service.

    Environment variable names are the UPPER_CASE equivalents of the field
    names.  Example: ``llm_base_url`` ↔ ``LLM_BASE_URL``.

    Priority (highest → lowest):
      1. Real environment variables
      2. ``.env`` file at the repo root
      3. Field defaults declared below
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # LLM provider — local OpenAI-compatible inference server
    # (research.md R2; plan.md §Technical Context; Principle VI two-reviewer)
    # ------------------------------------------------------------------
    llm_base_url: str = Field(
        default="http://localhost:11434/v1",
        description=(
            "Base URL of the OpenAI-compatible inference server.  "
            "Default: Ollama at localhost:11434.  "
            "Override for LM Studio (port 1234), vLLM, or cloud OpenAI."
        ),
    )
    llm_api_key: str = Field(
        default="ollama",
        description=(
            "API key sent to the inference server.  "
            "Use 'ollama' for Ollama, 'lm-studio' for LM Studio, "
            "or a real key for cloud endpoints."
        ),
    )
    llm_router_model: str = Field(
        default="qwen2.5:7b",
        description=(
            "Model name for the Router node (small/fast tier, research.md R2).  "
            "Must be available on the server at LLM_BASE_URL."
        ),
    )
    llm_expert_model: str = Field(
        default="qwen2.5:14b",
        description=(
            "Model name for Expert nodes (larger/reasoning tier, research.md R2).  "
            "Must be available on the server at LLM_BASE_URL."
        ),
    )

    # ------------------------------------------------------------------
    # Per-incident budget  (spec FR-029; research.md R8)
    #
    # For local inference there is no USD cost, so budget_usd_micros defaults
    # to -1 (unlimited) while token ceiling remains enforced fail-closed.
    # The USD field is retained for forward-compatibility with cloud providers
    # (plan.md §Constraints).
    # ------------------------------------------------------------------
    budget_tokens_per_incident: int = Field(
        default=50_000,
        description="Per-incident token ceiling.  Fail-closed.",
    )
    budget_usd_micros_per_incident: int = Field(
        default=-1,
        description=(
            "Per-incident USD ceiling in micros.  -1 = unlimited (local inference). "
            "Set to e.g. 500_000 ($0.50) when using a metered cloud provider."
        ),
    )

    # ------------------------------------------------------------------
    # HITL approval window  (spec FR-017)
    # ------------------------------------------------------------------
    approval_window_seconds: int = Field(
        default=1800,
        description="Seconds before a PENDING approval auto-expires (default 30 min).",
    )

    # ------------------------------------------------------------------
    # Webhook dedup window  (research.md R12)
    # ------------------------------------------------------------------
    dedup_window_seconds: int = Field(
        default=600,
        description="Webhook fingerprint dedup window in seconds (default 10-min bucket).",
    )

    # ------------------------------------------------------------------
    # Alertmanager HMAC secret  (spec FR-002)
    # ------------------------------------------------------------------
    alertmanager_hmac_secret: str = Field(
        default="dev-secret-change-me",
        description=(
            "Shared secret for verifying Alertmanager webhook HMAC signatures.  "
            "MUST be overridden in non-dev environments."
        ),
    )

    # ------------------------------------------------------------------
    # Slack mock  (research.md R6)
    # ------------------------------------------------------------------
    slack_mock_secret: str = Field(
        default="dev-slack-secret",
        description="HMAC secret used to sign /callbacks/slack payloads.",
    )
    slack_mock_url: str = Field(
        default="http://localhost:9000",
        description="URL of the mock-Slack receiver service.",
    )


# ---------------------------------------------------------------------------
# Module-level singleton — import ``settings`` everywhere, don't instantiate.
# All fields have defaults, so import succeeds with an empty environment.
# ---------------------------------------------------------------------------
settings = AgentSettings()
