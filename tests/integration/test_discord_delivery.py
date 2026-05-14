"""
tests/integration/test_discord_delivery.py

Smoke test for chat_deliver() multi-surface delivery.
Uses respx to mock the HTTP endpoints; no real network calls.
"""
from __future__ import annotations

import pytest
import respx
import httpx
from datetime import datetime, timezone, timedelta

from src.agent.graph.nodes.reporter import chat_deliver
from src.agent.settings import settings
from src.shared.schemas import (
    ExpertDiagnosis,
    LogExcerpt,
    ProposedFix,
    Report,
    RoutingDecision,
    Target,
)


@pytest.fixture(scope="module", autouse=True)
def skip_if_llm_server_unreachable() -> None:  # type: ignore[override]
    """Override: Discord delivery tests use respx mocks and need no LLM server."""


def _make_report() -> Report:
    evidence = LogExcerpt(
        timestamp=datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        container="api",
        text="java.lang.NullPointerException",
        byte_offset=0,
    )
    routing = RoutingDecision(
        domain="Application",
        confidence="high",
        cited_evidence=[evidence],
        runners_up=[],
        model="test-model",
        tokens=100,
    )
    fix = ProposedFix.build(
        action_type="restart-pod",
        target=Target(namespace="default", pod="api-abc", container="api"),
        parameters={},
        permission_scope="k8s-restart",
    )
    diagnosis = ExpertDiagnosis(
        domain="Application",
        root_cause_hypothesis="NPE in request handler",
        confidence="high",
        cited_evidence=[evidence],
        runner_up_causes=[],
        proposed_fix=fix,
        model="test-model",
        tokens=100,
    )
    now = datetime.now(timezone.utc)
    return Report(
        correlation_id="test-corr-001",
        routing=routing,
        diagnosis=diagnosis,
        proposed_fix=fix,
        status="pending",
        delivered_at=now,
        approval_deadline=now + timedelta(minutes=30),
        runner_up_domains=[],
    )


@pytest.mark.asyncio
async def test_discord_delivery_posts_to_discord_only() -> None:
    """chat_deliver() with surface=discord calls the Discord bot and not Slack."""
    report = _make_report()

    original_surface = settings.chat_surface
    original_discord_url = settings.discord_bot_url
    original_slack_url = settings.slack_mock_url

    settings.chat_surface = "discord"
    settings.discord_bot_url = "http://localhost:8091"

    try:
        with respx.mock(assert_all_called=False) as rsps:
            discord_route = rsps.post("http://localhost:8091/messages").mock(
                return_value=httpx.Response(
                    200,
                    json={"delivered_at": "2026-01-01T12:00:00+00:00", "message_id": "abc123"},
                )
            )
            slack_route = rsps.post(f"{original_slack_url}/messages").mock(
                return_value=httpx.Response(200, json={"message_id": "should-not-be-called"})
            )

            _, message_id = await chat_deliver(report)

        assert discord_route.called, "Discord bot endpoint was not called"
        assert not slack_route.called, "Slack endpoint should NOT be called when surface=discord"
        assert message_id == "abc123"

        request_body = discord_route.calls[0].request.content
        assert b"test-corr-001" in request_body
    finally:
        settings.chat_surface = original_surface
        settings.discord_bot_url = original_discord_url


@pytest.mark.asyncio
async def test_slack_delivery_posts_to_slack_only() -> None:
    """chat_deliver() with surface=slack calls Slack mock and not Discord bot."""
    report = _make_report()

    original_surface = settings.chat_surface
    original_slack_url = settings.slack_mock_url

    settings.chat_surface = "slack"
    settings.slack_mock_url = "http://localhost:9000"

    try:
        with respx.mock(assert_all_called=False) as rsps:
            slack_route = rsps.post("http://localhost:9000/messages").mock(
                return_value=httpx.Response(
                    200,
                    json={"delivered_at": "2026-01-01T12:00:00+00:00", "message_id": "slack-001"},
                )
            )
            discord_route = rsps.post("http://localhost:8091/messages").mock(
                return_value=httpx.Response(200, json={"message_id": "should-not-be-called"})
            )

            delivered_at, message_id = await chat_deliver(report)

        assert slack_route.called, "Slack endpoint was not called"
        assert not discord_route.called, "Discord endpoint should NOT be called when surface=slack"
        assert message_id == "slack-001"
    finally:
        settings.chat_surface = original_surface
        settings.slack_mock_url = original_slack_url


@pytest.mark.asyncio
async def test_all_surface_posts_to_both() -> None:
    """chat_deliver() with surface=all calls both Slack and Discord."""
    report = _make_report()

    original_surface = settings.chat_surface
    original_slack_url = settings.slack_mock_url
    original_discord_url = settings.discord_bot_url

    settings.chat_surface = "all"
    settings.slack_mock_url = "http://localhost:9000"
    settings.discord_bot_url = "http://localhost:8091"

    try:
        with respx.mock(assert_all_called=False) as rsps:
            slack_route = rsps.post("http://localhost:9000/messages").mock(
                return_value=httpx.Response(
                    200,
                    json={"delivered_at": "2026-01-01T12:00:00+00:00", "message_id": "slack-001"},
                )
            )
            discord_route = rsps.post("http://localhost:8091/messages").mock(
                return_value=httpx.Response(
                    200,
                    json={"delivered_at": "2026-01-01T12:00:01+00:00", "message_id": "discord-001"},
                )
            )

            delivered_at, message_id = await chat_deliver(report)

        assert slack_route.called, "Slack endpoint was not called"
        assert discord_route.called, "Discord endpoint was not called"
        # message_id is from last successful delivery (discord, since it's second)
        assert message_id == "discord-001"
    finally:
        settings.chat_surface = original_surface
        settings.slack_mock_url = original_slack_url
        settings.discord_bot_url = original_discord_url
