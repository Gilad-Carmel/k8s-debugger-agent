"""
src/shared/errors.py

Re-exports ToolError from src/shared/schemas so import paths such as
``from src.shared.errors import ToolError`` continue to work alongside
the canonical location in schemas.py (T013/T016).
"""

from src.shared.schemas import ToolError

__all__ = ["ToolError"]
