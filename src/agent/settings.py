"""
src/agent/settings.py

Pydantic Settings: env-driven configuration for the agent service.

Corresponds to tasks.md T017.

All settings can be overridden via environment variables or a ``.env`` file at
the repo root.  Copy ``.env.example`` -> ``.env`` and adjust values.

LLM provider (research.md R2, plan.md S Technical Context):
  The agent talks to **any OpenAI-compatible** inference server via
  ``langchain-openai``'s ``ChatOpenAI``.  For local development the default
  target is Ollama at ``http://localhost:11434/v1``.  Override ``LLM_BASE_URL``
  to point at a different server (LM Studio, vLLM, cloud OpenAI, etc.).

Two-reviewer rule (Principle VI / plan.md S Constitution Check): any PR that
changes LLM_ROUTER_MODEL, LLM_EXPERT_MODEL, or LLM_BASE_URL requires two
reviewers and a plan.md citation.

Naming convention:
  Canonical Python attribute names are ``lower_case`` (PEP 8). Pydantic-
  settings is case-insensitive when matching env vars, so the ``.env`` /
  shell still uses UPPER_CASE keys (e.g. ``LLM_BASE_URL`` -> ``llm_base_url``).

  UPPER_CASE Python aliases (``ALERTMANAGER_HMAC_SECRET``, ``SQLITE_PATH``,
  ``APPROVAL_WINDOW_MINUTES``, ...) are exposed as ``@property``s for the
  webhook / HITL / persistence call sites that pre-date the rename. New code
  should use the lower_case names.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentSettings(BaseSettings):
    """Runtime configuration for the agent service."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # LLM provider — OpenAI-compatible inference server
    # ------------------------------------------------------------------
    llm_base_url: str = Field(
        default="http://localhost:11434/v1",
        description="Base URL of the OpenAI-compatible inference server.",
    )
    llm_api_key: str = Field(
        default="ollama",
        description="API key sent to the inference server.",
    )
    llm_router_model: str = Field(
        default="qwen2.5:7b",
        description="Model name for the Router node (small/fast tier).",
    )
    llm_expert_model: str = Field(
        default="qwen2.5:14b",
        description="Model name for Expert nodes (larger/reasoning tier).",
    )

    # ------------------------------------------------------------------
    # Per-incident budget (spec FR-029)
    # ------------------------------------------------------------------
    budget_tokens_per_incident: int = Field(
        default=50_000,
        description="Per-incident token ceiling. Fail-closed.",
    )
    budget_usd_micros_per_incident: int = Field(
        default=-1,
        description="Per-incident USD ceiling in micros. -1 = unlimited (local inference).",
    )

    # ------------------------------------------------------------------
    # HITL approval window (spec FR-017)
    # ------------------------------------------------------------------
    approval_window_seconds: int = Field(
        default=1800,
        description="Seconds before a PENDING approval auto-expires (default 30 min).",
    )

    # ------------------------------------------------------------------
    # Webhook dedup window (research.md R12)
    # ------------------------------------------------------------------
    dedup_window_seconds: int = Field(
        default=600,
        description="Webhook fingerprint dedup window in seconds (default 10-min bucket).",
    )

    # ------------------------------------------------------------------
    # Alertmanager + Slack-mock secrets
    # ------------------------------------------------------------------
    alertmanager_hmac_secret: str = Field(
        default="dev-secret-change-me",
        description="Shared secret for verifying Alertmanager webhook HMAC signatures.",
    )
    slack_mock_secret: str = Field(
        default="dev-mock-secret",
        description="HMAC secret used to sign /callbacks/slack payloads. Must match SLACK_MOCK_SECRET in the Discord bot.",
    )
    slack_mock_url: str = Field(
        default="http://localhost:9000",
        description="URL of the mock-Slack receiver service.",
    )
    discord_bot_url: str = Field(
        default="http://localhost:8091",
        description="URL of the Discord bot HTTP receiver (POST /messages).",
    )
    chat_surface: str = Field(
        default="slack",
        description=(
            "Which chat surface(s) to deliver reports to. "
            "Values: 'slack' | 'discord' | 'all'."
        ),
    )

    # ------------------------------------------------------------------
    # HITL resume + persistence (Person 3 webhook layer)
    # ------------------------------------------------------------------
    approval_token_secret: str = Field(
        default="dev-approval-secret",
        description="HMAC secret for short-lived approval tokens issued by the callback handler.",
    )
    sqlite_path: str = Field(
        default="./data/agent.sqlite3",
        description="Path to the SQLite file holding audit_log + incidents + LangGraph checkpoints.",
    )
    expiry_sweep_seconds: int = Field(
        default=30,
        description="Interval between approval-deadline expiry sweeps.",
    )
    approver_role: str = Field(
        default="triage-approver",
        description="Role required to approve any catalog action (research.md R11 default mapping).",
    )

    # ------------------------------------------------------------------
    # Proactive log listener (feature 005-router-listener)
    # Instead of waiting for Alertmanager webhooks, the listener polls
    # MCP search_pod_logs continuously and fires the triage graph whenever
    # error patterns are detected.
    # ------------------------------------------------------------------
    watch_namespaces: str = Field(
        default="default",
        description=(
            "Comma-separated Kubernetes namespaces to poll for error logs. "
            "Example: 'default,production,staging'."
        ),
    )
    poll_interval_seconds: int = Field(
        default=30,
        description="Seconds between listener poll cycles.",
    )
    listener_lookback_minutes: int = Field(
        default=5,
        description="How many minutes back each poll looks for error log matches.",
    )

    @property
    def watch_namespace_list(self) -> list[str]:
        """Parsed list of namespaces to watch."""
        return [ns.strip() for ns in self.watch_namespaces.split(",") if ns.strip()]

    # ------------------------------------------------------------------
    # Backward-compatibility properties for the webhook / HITL / persistence
    # call sites that pre-date the rename. Read-only EXCEPT SQLITE_PATH
    # (writable so per-test fixtures can point at a tmp file).
    # New code should use the lower_case names directly.
    # ------------------------------------------------------------------
    @property
    def DISCORD_BOT_URL(self) -> str:  # noqa: N802
        return self.discord_bot_url

    @property
    def CHAT_SURFACE(self) -> str:  # noqa: N802
        return self.chat_surface

    @property
    def ALERTMANAGER_HMAC_SECRET(self) -> str:  # noqa: N802
        return self.alertmanager_hmac_secret

    @property
    def SLACK_MOCK_SECRET(self) -> str:  # noqa: N802
        return self.slack_mock_secret

    @property
    def APPROVAL_TOKEN_SECRET(self) -> str:  # noqa: N802
        return self.approval_token_secret

    @property
    def APPROVER_ROLE(self) -> str:  # noqa: N802
        return self.approver_role

    @property
    def EXPIRY_SWEEP_SECONDS(self) -> int:  # noqa: N802
        return self.expiry_sweep_seconds

    @property
    def APPROVAL_WINDOW_MINUTES(self) -> int:  # noqa: N802
        return self.approval_window_seconds // 60

    @property
    def DEDUP_WINDOW_MINUTES(self) -> int:  # noqa: N802
        return self.dedup_window_seconds // 60

    @property
    def SQLITE_PATH(self) -> str:  # noqa: N802
        return self.sqlite_path

    @SQLITE_PATH.setter
    def SQLITE_PATH(self, value: str) -> None:  # noqa: N802
        # Per-test fixtures override the DB path with this name.
        self.sqlite_path = value


# Module-level singleton — import ``settings`` everywhere, don't instantiate.
settings = AgentSettings()
