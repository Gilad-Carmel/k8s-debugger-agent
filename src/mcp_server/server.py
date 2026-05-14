"""
src/mcp_server/server.py

MCP server entrypoint.

Registers all tool handlers and starts the server with stdio transport
(dev) or HTTP-SSE transport (prod, when MCP_TRANSPORT=http is set).

Per research.md §R5: physical process separation from the agent process is
the enforcement boundary for Principle I.

Corresponds to tasks.md T029.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from typing import Any

from mcp.server import Server  # type: ignore[import-untyped]
from mcp.server.stdio import stdio_server  # type: ignore[import-untyped]
from mcp.types import TextContent, Tool  # type: ignore[import-untyped]

from src.mcp_server.tools.delete_pod_to_reschedule import delete_pod_to_reschedule
from src.mcp_server.tools.get_pod import get_pod
from src.mcp_server.tools.get_pod_events import get_pod_events
from src.mcp_server.tools.restart_pod import restart_pod
from src.mcp_server.tools.rollback_deployment import rollback_deployment
from src.mcp_server.tools.scale_deployment import scale_deployment
from src.mcp_server.tools.search_pod_logs import search_pod_logs

server = Server("k8s-debugger-mcp")


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

@server.list_tools()
async def _list_tools() -> list[Tool]:
    return [
        Tool(
            name="search_pod_logs",
            description=(
                "Fetch a pod's logs over a time window and return redacted log lines "
                "that match the given patterns. Applies boundary redaction before returning."
            ),
            inputSchema={
                "type": "object",
                "required": ["namespace", "pod", "since", "until", "correlation_id"],
                "properties": {
                    "namespace": {"type": "string"},
                    "pod": {"type": "string"},
                    "container": {"type": "string"},
                    "since": {"type": "string", "format": "date-time"},
                    "until": {"type": "string", "format": "date-time"},
                    "patterns": {"type": "array", "items": {"type": "string"}},
                    "max_hit_lines": {"type": "integer", "minimum": 1, "maximum": 2000, "default": 500},
                    "correlation_id": {"type": "string"},
                },
            },
        ),
        Tool(
            name="get_pod_events",
            description="Return recent Kubernetes events for a target pod.",
            inputSchema={
                "type": "object",
                "required": ["namespace", "pod", "correlation_id"],
                "properties": {
                    "namespace": {"type": "string"},
                    "pod": {"type": "string"},
                    "since_minutes": {"type": "integer", "minimum": 1, "default": 30},
                    "correlation_id": {"type": "string"},
                },
            },
        ),
        Tool(
            name="get_pod",
            description="Return pod metadata: phase, container states, restart counts, readiness.",
            inputSchema={
                "type": "object",
                "required": ["namespace", "pod", "correlation_id"],
                "properties": {
                    "namespace": {"type": "string"},
                    "pod": {"type": "string"},
                    "correlation_id": {"type": "string"},
                },
            },
        ),
        Tool(
            name="restart_pod",
            description=(
                "Restart a pod by deleting it (default grace period) and waiting for "
                "the controller to reschedule it as Ready."
            ),
            inputSchema={
                "type": "object",
                "required": [
                    "namespace", "pod", "correlation_id",
                    "approval_token", "proposed_fix_fingerprint",
                ],
                "properties": {
                    "namespace": {"type": "string"},
                    "pod": {"type": "string"},
                    "correlation_id": {"type": "string"},
                    "approval_token": {"type": "string"},
                    "proposed_fix_fingerprint": {"type": "string"},
                    "verification_window_sec": {"type": "integer", "minimum": 1, "maximum": 120, "default": 60},
                    "tenant": {"type": "string"},
                },
            },
        ),
        Tool(
            name="rollback_deployment",
            description="Roll back a Deployment to a prior revision.",
            inputSchema={
                "type": "object",
                "required": [
                    "namespace", "deployment", "to_revision", "correlation_id",
                    "approval_token", "proposed_fix_fingerprint",
                ],
                "properties": {
                    "namespace": {"type": "string"},
                    "deployment": {"type": "string"},
                    "to_revision": {"type": "integer"},
                    "correlation_id": {"type": "string"},
                    "approval_token": {"type": "string"},
                    "proposed_fix_fingerprint": {"type": "string"},
                    "verification_window_sec": {"type": "integer", "minimum": 1, "maximum": 120, "default": 60},
                    "tenant": {"type": "string"},
                },
            },
        ),
        Tool(
            name="scale_deployment",
            description="Scale a Deployment to a given replica count (within tenant bounds).",
            inputSchema={
                "type": "object",
                "required": [
                    "namespace", "deployment", "to_replicas", "correlation_id",
                    "approval_token", "proposed_fix_fingerprint",
                ],
                "properties": {
                    "namespace": {"type": "string"},
                    "deployment": {"type": "string"},
                    "to_replicas": {"type": "integer"},
                    "correlation_id": {"type": "string"},
                    "approval_token": {"type": "string"},
                    "proposed_fix_fingerprint": {"type": "string"},
                    "verification_window_sec": {"type": "integer", "minimum": 1, "maximum": 120, "default": 60},
                    "tenant": {"type": "string"},
                },
            },
        ),
        Tool(
            name="delete_pod_to_reschedule",
            description=(
                "Delete a pod via the Eviction API (PDB-respecting). Used to trigger "
                "rescheduling; never uses --force or --grace-period=0."
            ),
            inputSchema={
                "type": "object",
                "required": [
                    "namespace", "pod", "correlation_id",
                    "approval_token", "proposed_fix_fingerprint",
                ],
                "properties": {
                    "namespace": {"type": "string"},
                    "pod": {"type": "string"},
                    "correlation_id": {"type": "string"},
                    "approval_token": {"type": "string"},
                    "proposed_fix_fingerprint": {"type": "string"},
                    "verification_window_sec": {"type": "integer", "minimum": 1, "maximum": 120, "default": 60},
                    "tenant": {"type": "string"},
                },
            },
        ),
    ]


# ---------------------------------------------------------------------------
# Tool dispatch
# ---------------------------------------------------------------------------

@server.call_tool()
async def _call_tool(name: str, arguments: dict[str, Any] | None) -> list[TextContent]:
    args = arguments or {}
    try:
        result = await _dispatch(name, args)
        return [TextContent(type="text", text=json.dumps(result, default=str))]
    except FileNotFoundError as exc:
        return [_error_content("not_found", str(exc))]
    except PermissionError as exc:
        return [_error_content("forbidden", str(exc))]
    except TimeoutError as exc:
        return [_error_content("upstream_timeout", str(exc))]
    except ValueError as exc:
        return [_error_content("window_invalid", str(exc))]
    except Exception as exc:  # noqa: BLE001
        return [_error_content("internal_error", str(exc))]


async def _dispatch(name: str, args: dict[str, Any]) -> Any:
    if name == "search_pod_logs":
        since = datetime.fromisoformat(args["since"])
        until = datetime.fromisoformat(args["until"])
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
        result = await search_pod_logs(
            namespace=args["namespace"],
            pod=args["pod"],
            container=args.get("container"),
            since=since,
            until=until,
            patterns=args.get("patterns"),
            max_hit_lines=int(args.get("max_hit_lines", 500)),
            correlation_id=args["correlation_id"],
        )
        return result.model_dump(mode="json")

    if name == "get_pod_events":
        result = await get_pod_events(
            namespace=args["namespace"],
            pod=args["pod"],
            since_minutes=int(args.get("since_minutes", 30)),
            correlation_id=args["correlation_id"],
        )
        return result

    if name == "get_pod":
        result = await get_pod(
            namespace=args["namespace"],
            pod=args["pod"],
            correlation_id=args["correlation_id"],
        )
        return result.model_dump(mode="json")

    if name == "restart_pod":
        result = await restart_pod(
            namespace=args["namespace"],
            pod=args["pod"],
            correlation_id=args["correlation_id"],
            approval_token=args["approval_token"],
            proposed_fix_fingerprint=args["proposed_fix_fingerprint"],
            verification_window_sec=int(args.get("verification_window_sec", 60)),
            tenant=args.get("tenant"),
        )
        return result.model_dump(mode="json")

    if name == "rollback_deployment":
        result = await rollback_deployment(
            namespace=args["namespace"],
            deployment=args["deployment"],
            to_revision=int(args["to_revision"]),
            correlation_id=args["correlation_id"],
            approval_token=args["approval_token"],
            proposed_fix_fingerprint=args["proposed_fix_fingerprint"],
            verification_window_sec=int(args.get("verification_window_sec", 60)),
            tenant=args.get("tenant"),
        )
        return result.model_dump(mode="json")

    if name == "scale_deployment":
        result = await scale_deployment(
            namespace=args["namespace"],
            deployment=args["deployment"],
            to_replicas=int(args["to_replicas"]),
            correlation_id=args["correlation_id"],
            approval_token=args["approval_token"],
            proposed_fix_fingerprint=args["proposed_fix_fingerprint"],
            verification_window_sec=int(args.get("verification_window_sec", 60)),
            tenant=args.get("tenant"),
        )
        return result.model_dump(mode="json")

    if name == "delete_pod_to_reschedule":
        result = await delete_pod_to_reschedule(
            namespace=args["namespace"],
            pod=args["pod"],
            correlation_id=args["correlation_id"],
            approval_token=args["approval_token"],
            proposed_fix_fingerprint=args["proposed_fix_fingerprint"],
            verification_window_sec=int(args.get("verification_window_sec", 60)),
            tenant=args.get("tenant"),
        )
        return result.model_dump(mode="json")

    raise ValueError(f"Unknown tool: {name!r}")


def _error_content(code: str, message: str) -> TextContent:
    return TextContent(
        type="text",
        text=json.dumps({"error": {"code": code, "message": message}}),
    )


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

async def main() -> None:
    transport = os.environ.get("MCP_TRANSPORT", "stdio").lower()
    if transport == "stdio":
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )
    else:
        raise NotImplementedError(
            f"MCP_TRANSPORT={transport!r} is not yet implemented. "
            "Set MCP_TRANSPORT=stdio or leave unset for stdio transport."
        )


if __name__ == "__main__":
    asyncio.run(main())
