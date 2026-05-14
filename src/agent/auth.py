"""
src/agent/auth.py

Approver role-check.

MVP mapping per research.md §R11: a single role, settings.APPROVER_ROLE
(default 'triage-approver'), authorizes every catalog action. Tenants may
later extend this to per-action mappings.
"""
from __future__ import annotations

from typing import Optional

from src.agent.settings import settings


def check_approver_role(actor_roles: list[str], action_type: Optional[str] = None) -> bool:
    """Return True iff the actor holds the configured approver role.

    `action_type` is accepted now so a future per-action mapping is a non-
    breaking change for callers.
    """
    return settings.APPROVER_ROLE in (actor_roles or [])
