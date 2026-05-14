"""
deploy/mocks/llm_fixtures.py

Pre-baked LLM Router and Expert responses for each failure domain.

What this replaces:
  The real Router node calls Claude Haiku to classify the domain, and the
  real Expert nodes call Claude Sonnet to diagnose and propose a fix.
  These fixtures let you run unit/integration tests against the graph nodes
  without spending LLM tokens or needing an API key.

When to replace:
  These fixtures are test-only.  When you add real LLM integration tests,
  you can use these as golden-file baselines or snapshot them with pytest.

Usage:
  from deploy.mocks.llm_fixtures import router_response, expert_response

  # Simulate the Router node's structured output for "application"
  routing = router_response("application")

  # Simulate the Expert node's structured output for "application"
  diagnosis = expert_response("application")

  # Use them to build test WorkflowState dicts
  state = {
      "correlation_id": "test-corr-001",
      "routing": routing,
      "diagnosis": diagnosis,
  }
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

_NOW = datetime.now(tz=timezone.utc).isoformat()

# ---------------------------------------------------------------------------
# Router responses  (mimics RoutingDecision schema)
# ---------------------------------------------------------------------------

_ROUTER: dict[str, dict[str, Any]] = {
    "application": {
        "domain": "application",
        "confidence": "high",
        "cited_evidence": [
            {
                "timestamp": _NOW,
                "container": "api-server",
                "text": "java.lang.OutOfMemoryError: Java heap space",
                "byte_offset": 0,
            }
        ],
        "runners_up": [["network", "low"]],
        "model": "claude-haiku-4-5",
        "tokens": 312,
    },
    "network": {
        "domain": "network",
        "confidence": "high",
        "cited_evidence": [
            {
                "timestamp": _NOW,
                "container": "checkout-svc",
                "text": "dial tcp: lookup payment-svc.production.svc.cluster.local: no such host",
                "byte_offset": 0,
            }
        ],
        "runners_up": [["application", "low"]],
        "model": "claude-haiku-4-5",
        "tokens": 288,
    },
    "database": {
        "domain": "database",
        "confidence": "high",
        "cited_evidence": [
            {
                "timestamp": _NOW,
                "container": "orders-api",
                "text": "FATAL: sorry, too many clients already (max_connections=100, current=100)",
                "byte_offset": 118,
            }
        ],
        "runners_up": [["application", "medium"]],
        "model": "claude-haiku-4-5",
        "tokens": 341,
    },
    "unknown": {
        "domain": "Unknown",
        "confidence": "low",
        "cited_evidence": [],
        "runners_up": [],
        "model": "claude-haiku-4-5",
        "tokens": 201,
    },
}

# ---------------------------------------------------------------------------
# Expert responses  (mimics ExpertDiagnosis schema)
# ---------------------------------------------------------------------------

_EXPERT: dict[str, dict[str, Any] | None] = {
    "application": {
        "domain": "application",
        "root_cause_hypothesis": (
            "The api-server container is OOMKilled repeatedly due to unbounded "
            "heap growth in DataProcessor.process() — restart count is 14."
        ),
        "cited_evidence": [
            {
                "timestamp": _NOW,
                "container": "api-server",
                "text": "java.lang.OutOfMemoryError: Java heap space",
                "byte_offset": 0,
            }
        ],
        "confidence": "high",
        "runner_up_causes": ["memory leak in upstream dependency"],
        "proposed_fix": {
            "action_type": "restart-pod",
            "target": {"namespace": "production", "pod": "api-server-5d9f7b6c8-xk2qp"},
            "parameters": {},
            "permission_scope": "triage-bot-sa",
            "fingerprint": "abc123def456",
        },
        "model": "claude-sonnet-4-6",
        "tokens": 891,
    },
    "network": {
        "domain": "network",
        "root_cause_hypothesis": (
            "DNS resolution for payment-svc is failing from checkout-svc — "
            "likely a missing or mis-named Service object in the production namespace."
        ),
        "cited_evidence": [
            {
                "timestamp": _NOW,
                "container": "checkout-svc",
                "text": "dial tcp: lookup payment-svc.production.svc.cluster.local: no such host",
                "byte_offset": 0,
            }
        ],
        "confidence": "high",
        "runner_up_causes": ["NetworkPolicy blocking egress to payment-svc"],
        "proposed_fix": None,
        "model": "claude-sonnet-4-6",
        "tokens": 743,
    },
    "database": {
        "domain": "database",
        "root_cause_hypothesis": (
            "Postgres connection pool is exhausted — orders-api is leaking "
            "connections and hitting the max_connections=100 limit."
        ),
        "cited_evidence": [
            {
                "timestamp": _NOW,
                "container": "orders-api",
                "text": "FATAL: sorry, too many clients already (max_connections=100, current=100)",
                "byte_offset": 118,
            }
        ],
        "confidence": "high",
        "runner_up_causes": ["PgBouncer misconfiguration", "sudden traffic spike"],
        "proposed_fix": {
            "action_type": "restart-pod",
            "target": {"namespace": "production", "pod": "orders-api-7c9d4b8f9-p3rnm"},
            "parameters": {},
            "permission_scope": "triage-bot-sa",
            "fingerprint": "def789abc012",
        },
        "model": "claude-sonnet-4-6",
        "tokens": 812,
    },
    "unknown": None,  # Unknown domain has no expert diagnosis
}

# ---------------------------------------------------------------------------
# Solver responses  (mimics SolverRun schema for "success" and "failure")
# ---------------------------------------------------------------------------

_SOLVER: dict[str, dict[str, Any]] = {
    "success": {
        "outcome": "success",
        "reversal_recipe": {
            "description": "No automated undo — restart was self-recovering.",
            "inverse_action": None,
            "inverse_parameters": {},
        },
        "error": None,
    },
    "failure": {
        "outcome": "failure",
        "reversal_recipe": {
            "description": "No undo available.",
            "inverse_action": "manual",
            "inverse_parameters": {},
        },
        "error": "kubectl patch timed out after 30 s — pod did not reach Running phase",
    },
    "partial": {
        "outcome": "partial",
        "reversal_recipe": {
            "description": "Re-scale to 3 replicas to restore previous state.",
            "inverse_action": "scale-deployment",
            "inverse_parameters": {"replicas": 3},
        },
        "error": None,
    },
}


# ---------------------------------------------------------------------------
# Accessor functions
# ---------------------------------------------------------------------------

def router_response(domain: str) -> dict[str, Any]:
    """Return a RoutingDecision-shaped dict for *domain*."""
    return _ROUTER.get(domain, _ROUTER["unknown"])


def expert_response(domain: str) -> dict[str, Any] | None:
    """Return an ExpertDiagnosis-shaped dict for *domain*, or None for Unknown."""
    return _EXPERT.get(domain)


def solver_response(outcome: str = "success") -> dict[str, Any]:
    """Return a partial SolverRun-shaped dict for the given *outcome*."""
    return _SOLVER.get(outcome, _SOLVER["success"])


def all_domains() -> list[str]:
    return list(_ROUTER.keys())
