"""
src/agent/settings.py

Env-driven configuration via pydantic-settings.

For the hackathon all secrets default to dev values so the smoke script runs
without the operator setting anything. In any non-local deploy the env vars
MUST be overridden.
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # HMAC secrets (dev defaults — override in any deployed env)
    ALERTMANAGER_HMAC_SECRET: str = "dev-am-secret"
    SLACK_MOCK_SECRET: str = "dev-slack-secret"
    APPROVAL_TOKEN_SECRET: str = "dev-approval-secret"

    # SQLite path — holds audit_log, incidents, and the LangGraph checkpointer tables.
    SQLITE_PATH: str = "./data/agent.sqlite3"

    # Windows
    APPROVAL_WINDOW_MINUTES: int = 30
    DEDUP_WINDOW_MINUTES: int = 10
    EXPIRY_SWEEP_SECONDS: int = 30

    # Authorization
    APPROVER_ROLE: str = "triage-approver"


settings = Settings()
