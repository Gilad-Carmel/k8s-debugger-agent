"""
deploy/discord_bot/bot.py

Discord bot — alternative chat surface to the Slack mock.

Receives the same POST /messages payload from the agent Reporter,
posts a Discord embed to a configured channel, and handles
Approve / Reject button clicks by calling back to the agent.

Every update to the same incident edits the original message in place —
no follow-up messages are ever sent.

Required env vars:
  DISCORD_TOKEN        — bot token from the Discord developer portal
  DISCORD_CHANNEL_ID   — channel ID to post incidents into
  AGENT_URL            — e.g. http://localhost:8000
  SLACK_MOCK_SECRET    — shared HMAC secret (same as the rest of the stack)

Optional:
  BOT_PORT             — port for the HTTP receiver (default 8091)
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

import discord
import httpx
import uvicorn
from fastapi import FastAPI, Request

logger = logging.getLogger("discord_bot")

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
DISCORD_CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "0"))
AGENT_URL = os.getenv("AGENT_URL", "http://localhost:8000")
SLACK_MOCK_SECRET = os.getenv("SLACK_MOCK_SECRET", "dev-mock-secret")
BOT_PORT = int(os.getenv("BOT_PORT", "8091"))

# ---------------------------------------------------------------------------
# Discord client
# ---------------------------------------------------------------------------

intents = discord.Intents.default()
client = discord.Client(intents=intents)

# correlation_id -> discord.Message, so every update edits the same message
_posted: dict[str, discord.Message] = {}


def _sign(body: bytes) -> str:
    return hmac.new(SLACK_MOCK_SECRET.encode(), body, hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# Approval view (buttons)
# ---------------------------------------------------------------------------

class ApprovalView(discord.ui.View):
    def __init__(self, correlation_id: str, record: dict[str, Any]) -> None:
        super().__init__(timeout=1800)  # matches 30-min approval window
        self.correlation_id = correlation_id
        self.record = record  # kept so we can rebuild the embed on click

    @discord.ui.button(label="Approve remediation", style=discord.ButtonStyle.green)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._forward(interaction, "approve")

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.secondary)
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._forward(interaction, "reject")

    async def _forward(self, interaction: discord.Interaction, action: str) -> None:
        # Disable all buttons
        for item in self.children:
            item.disabled = True  # type: ignore[union-attr]

        # Optimistically update the embed to show the new status immediately,
        # before waiting for the agent to respond
        new_status = "approved" if action == "approve" else "rejected"
        updated_record: dict[str, Any] = {
            **self.record,
            "report": {**self.record.get("report", {}), "status": new_status},
        }
        embed = _build_embed(updated_record)
        await interaction.response.edit_message(embed=embed, view=self)

        # Forward to the agent
        payload: dict[str, Any] = {
            "correlation_id": self.correlation_id,
            "actor": {
                "user_id": str(interaction.user.id),
                "name": interaction.user.name,
                "roles": ["triage-approver"],
            },
            "action_id": f"{action}_{self.correlation_id}",
            "reason": "",
            "clicked_at": datetime.now(timezone.utc).isoformat(),
        }
        body_bytes = json.dumps(payload, separators=(",", ":")).encode()

        try:
            async with httpx.AsyncClient(timeout=10.0) as http:
                resp = await http.post(
                    f"{AGENT_URL}/callbacks/slack/{action}",
                    content=body_bytes,
                    headers={
                        "Content-Type": "application/json",
                        "X-Slack-Mock-Signature": _sign(body_bytes),
                    },
                )
                resp.raise_for_status()
        except Exception as exc:
            logger.warning("callback failed corr=%s: %s", self.correlation_id, exc)

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Embed builder — single function handles every lifecycle state
# ---------------------------------------------------------------------------

_STATUS_COLOR: dict[str, discord.Color] = {
    "pending":  discord.Color.blurple(),
    "approved": discord.Color.gold(),
    "rejected": discord.Color.red(),
    "expired":  discord.Color.greyple(),
    "executed": discord.Color.green(),
    "failed":   discord.Color.red(),
    "partial":  discord.Color.orange(),
}

_OUTCOME_ICON: dict[str, str] = {
    "success": "✅",
    "partial": "⚠️",
    "failure": "❌",
}

_STATUS_LABEL: dict[str, str] = {
    "approved": "✅ Approved — solver running...",
    "rejected": "❌ Rejected",
    "expired":  "⏰ Approval window expired",
}


def _build_embed(record: dict[str, Any]) -> discord.Embed:
    report = record.get("report", {})
    routing = report.get("routing", {})
    diagnosis = report.get("diagnosis") or {}
    fix = report.get("proposed_fix")
    solver = record.get("solver_result") or {}

    domain = routing.get("domain", "Unknown")
    confidence = routing.get("confidence", "?")
    status = report.get("status", "pending")

    embed = discord.Embed(
        title=f"{domain} incident",
        color=_STATUS_COLOR.get(status, discord.Color.blurple()),
        timestamp=datetime.now(timezone.utc),
    )

    # Root cause
    root_cause = diagnosis.get("root_cause_hypothesis", "")
    if root_cause:
        embed.description = root_cause

    # Log evidence
    evidence: list[dict[str, Any]] = diagnosis.get("cited_evidence", [])
    if evidence:
        lines = "\n".join(
            f"[{e.get('container', '')}] {e.get('text', '')}"
            for e in evidence[:4]
        )
        embed.add_field(name="Evidence", value=f"```{lines[:1000]}```", inline=False)

    # Proposed fix
    if fix:
        action = fix.get("action_type", "")
        t = fix.get("target", {})
        params = fix.get("parameters", {})
        params_str = ", ".join(f"{k}={v}" for k, v in params.items())
        fix_label = f"{action}({params_str})" if params_str else action
        embed.add_field(
            name="Proposed fix",
            value=f"`{fix_label}` on `{t.get('namespace','')}/{t.get('pod','')}`",
            inline=False,
        )

    # Status line (only for non-pending, non-solver states)
    if status in _STATUS_LABEL and not solver:
        embed.add_field(name="Status", value=_STATUS_LABEL[status], inline=False)

    # Solver result — replaces the status line when available
    if solver:
        outcome = solver.get("outcome", "")
        reversal = (solver.get("reversal_recipe") or {}).get("description", "")
        error = solver.get("error", "")
        icon = _OUTCOME_ICON.get(outcome, "❓")
        value = f"{icon} {outcome.upper()}"
        if reversal:
            value += f"\nUndo: {reversal}"
        if error:
            value += f"\nError: {error}"
        embed.add_field(name="Result", value=value, inline=False)

    # Footer
    runner_ups = routing.get("runners_up", [])
    runner_str = ", ".join(
        f"{r[0]} ({r[1]})" if isinstance(r, (list, tuple)) else str(r)
        for r in runner_ups
    )
    footer_parts = [f"Confidence: {confidence}", f"Status: {status}"]
    if runner_str:
        footer_parts.append(f"Runner-ups: {runner_str}")
    embed.set_footer(text="  ·  ".join(footer_parts))

    return embed


# ---------------------------------------------------------------------------
# HTTP receiver
# ---------------------------------------------------------------------------

http_app = FastAPI(title="Discord Bot Receiver")
_pending_posts: asyncio.Queue[dict[str, Any]] = asyncio.Queue()


@http_app.post("/messages")
async def receive_message(request: Request) -> dict[str, str]:
    body = await request.json()
    corr: str = body.get("correlation_id", str(uuid.uuid4()))
    now = datetime.now(timezone.utc).isoformat()
    await _pending_posts.put(body)
    logger.info("queued corr=%s", corr)
    return {"delivered_at": now, "message_id": str(uuid.uuid4())}


@http_app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Discord posting loop
# ---------------------------------------------------------------------------

@client.event
async def on_ready() -> None:
    logger.info("Discord bot ready as %s", client.user)
    asyncio.create_task(_post_loop())


async def _post_loop() -> None:
    while True:
        record = await _pending_posts.get()
        try:
            await _post_or_update(record)
        except Exception as exc:
            logger.warning("failed to post: %s", exc, exc_info=True)


async def _post_or_update(record: dict[str, Any]) -> None:
    corr: str = record.get("correlation_id", "?")
    report = record.get("report", {})
    fix = report.get("proposed_fix")
    status = report.get("status", "pending")

    try:
        channel = await client.fetch_channel(DISCORD_CHANNEL_ID)
    except discord.NotFound:
        logger.warning("channel %s not found", DISCORD_CHANNEL_ID)
        return
    except discord.Forbidden:
        logger.warning("no access to channel %s", DISCORD_CHANNEL_ID)
        return

    if not isinstance(channel, discord.abc.Messageable):
        logger.warning("channel %s (type %s) is not messageable", DISCORD_CHANNEL_ID, type(channel).__name__)
        return

    embed = _build_embed(record)
    view = ApprovalView(corr, record) if fix and status == "pending" else None

    existing = _posted.get(corr)
    if existing:
        await existing.edit(embed=embed, view=view)
        logger.info("updated message id=%s corr=%s status=%s", existing.id, corr, status)
    else:
        msg = await channel.send(embed=embed, view=view)
        _posted[corr] = msg
        logger.info("posted message id=%s corr=%s", msg.id, corr)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def _main() -> None:
    if not DISCORD_TOKEN:
        print(
            "ERROR: DISCORD_TOKEN is not set.\n"
            "  $env:DISCORD_TOKEN = 'your-token-here'   # PowerShell\n"
            "  export DISCORD_TOKEN=your-token-here     # bash\n"
            "Get a token at https://discord.com/developers/applications"
        )
        return

    if not DISCORD_CHANNEL_ID:
        print(
            "ERROR: DISCORD_CHANNEL_ID is not set.\n"
            "Right-click a channel in Discord → Copy Channel ID,\n"
            "then set:  $env:DISCORD_CHANNEL_ID = '123456789'"
        )
        return

    config = uvicorn.Config(http_app, host="0.0.0.0", port=BOT_PORT, log_level="warning")
    server = uvicorn.Server(config)

    discord_task = asyncio.create_task(client.start(DISCORD_TOKEN))
    uvicorn_task = asyncio.create_task(server.serve())

    try:
        await asyncio.gather(discord_task, uvicorn_task)
    except discord.LoginFailure:
        print(
            "ERROR: Discord rejected the token — it may be expired or invalid.\n"
            "Regenerate it at https://discord.com/developers/applications"
        )
        uvicorn_task.cancel()
    except Exception as exc:
        print(f"ERROR: {exc}")
        discord_task.cancel()
        uvicorn_task.cancel()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_main())
