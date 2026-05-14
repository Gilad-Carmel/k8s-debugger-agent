"""
src/mcp_server/__main__.py

Allows ``python -m src.mcp_server`` to start the MCP server.
"""

import asyncio

from src.mcp_server.server import main

asyncio.run(main())
