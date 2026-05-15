"""
src/agent/api/gui/__init__.py

GUI API package — exposes four FastAPI routers:
  pods      → GET  /api/pods
  scenarios → POST /api/demo/trigger/{scenario}
  stream    → GET  /api/events
  approval  → POST /api/approval/{correlation_id}/{action}
"""

from src.agent.api.gui.approval import router as approval_router
from src.agent.api.gui.pods import router as pods_router
from src.agent.api.gui.scenarios import router as scenarios_router
from src.agent.api.gui.stream import router as stream_router

__all__ = ["pods_router", "scenarios_router", "stream_router", "approval_router"]
