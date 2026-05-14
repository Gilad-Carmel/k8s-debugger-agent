"""
src/mcp_server/admin.py

Kill-switch endpoint for the MCP server.

POST /admin/kill-switch?tenant=<name>  — halt all write tool calls for tenant
DELETE /admin/kill-switch?tenant=<name> — lift the halt

Per contracts/mcp_tools.md §Kill switch:
  - IP-restricted (ADMIN_ALLOWED_CIDR env var, default 127.0.0.1/32).
  - Propagation within 5 seconds (synchronous in-memory flag — zero latency).
  - In-flight calls are NOT aborted mid-API-call; subsequent write calls return
    code: "tenant_halted".

Corresponds to tasks.md T032.
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request

# ---------------------------------------------------------------------------
# In-memory kill-switch state
# ---------------------------------------------------------------------------
_lock = asyncio.Lock()
_halted: dict[str, bool] = {}

_ADMIN_CIDR = os.environ.get("ADMIN_ALLOWED_CIDR", "127.0.0.1/32")


def _parse_network(cidr: str) -> ipaddress.IPv4Network | ipaddress.IPv6Network:
    try:
        return ipaddress.ip_network(cidr, strict=False)
    except ValueError:
        return ipaddress.ip_network("127.0.0.1/32")


_ALLOWED_NETWORK = _parse_network(_ADMIN_CIDR)


def _check_ip(request: Request) -> None:
    client_host = request.client.host if request.client else "127.0.0.1"
    try:
        addr = ipaddress.ip_address(client_host)
    except ValueError:
        raise HTTPException(status_code=403, detail="Forbidden")
    if addr not in _ALLOWED_NETWORK:
        raise HTTPException(status_code=403, detail="Forbidden")


# ---------------------------------------------------------------------------
# Public helper — checked before every write tool call
# ---------------------------------------------------------------------------
async def is_tenant_halted(tenant: Optional[str] = None) -> bool:
    """Return True if the kill switch is active for *tenant* (or the global switch)."""
    key = tenant or "__global__"
    async with _lock:
        return _halted.get(key, False) or _halted.get("__global__", False)


# ---------------------------------------------------------------------------
# FastAPI sub-application (mounted at /admin by the MCP HTTP server)
# ---------------------------------------------------------------------------
admin_app = FastAPI(title="MCP Kill-Switch Admin", docs_url=None, redoc_url=None)


@admin_app.post("/kill-switch")
async def halt_tenant(
    request: Request,
    tenant: str = Query(default="__global__", description="Tenant name to halt"),
) -> dict[str, str]:
    _check_ip(request)
    async with _lock:
        _halted[tenant] = True
    return {"status": "halted", "tenant": tenant}


@admin_app.delete("/kill-switch")
async def lift_tenant(
    request: Request,
    tenant: str = Query(default="__global__", description="Tenant name to resume"),
) -> dict[str, str]:
    _check_ip(request)
    async with _lock:
        _halted.pop(tenant, None)
    return {"status": "active", "tenant": tenant}
