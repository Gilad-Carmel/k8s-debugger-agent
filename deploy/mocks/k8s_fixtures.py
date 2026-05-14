"""
deploy/mocks/k8s_fixtures.py

Pre-baked Kubernetes log and pod-state fixtures for each failure domain.

What this replaces:
  The real MCP server (src/mcp_server/) calls the Kubernetes API to fetch pod
  logs and pod state.  These fixtures stand in for that layer so the demo/tests
  can run end-to-end without a live cluster.

When to replace:
  Delete or ignore this file once the MCP server is wired to a real (or
  kind/minikube) cluster.  The agent_mock.py picks these up automatically.

Usage:
  from deploy.mocks.k8s_fixtures import get_fixture
  fixture = get_fixture("application")   # returns {"logs": [...], "pod": {...}}
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

_NOW = datetime.now(tz=timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# One fixture per domain
# ---------------------------------------------------------------------------

_FIXTURES: dict[str, dict[str, Any]] = {

    # ---- Application: CrashLoopBackOff + OOM --------------------------------
    "application": {
        "logs": [
            {
                "timestamp": _NOW,
                "container": "api-server",
                "text": "java.lang.OutOfMemoryError: Java heap space",
                "byte_offset": 0,
            },
            {
                "timestamp": _NOW,
                "container": "api-server",
                "text": "	at com.example.service.DataProcessor.process(DataProcessor.java:142)",
                "byte_offset": 52,
            },
            {
                "timestamp": _NOW,
                "container": "api-server",
                "text": "FATAL Unhandled exception — process exiting",
                "byte_offset": 140,
            },
        ],
        "pod": {
            "phase": "Running",
            "restart_count_by_ctr": {"api-server": 14},
            "container_states": {
                "api-server": {
                    "state": "Waiting",
                    "reason": "CrashLoopBackOff",
                    "exit_code": 137,
                }
            },
            "ready": False,
            "resource_version": "48291",
        },
        "routing": {
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
        "diagnosis": {
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
            },
            "model": "claude-sonnet-4-6",
            "tokens": 891,
        },
    },

    # ---- Network: DNS / connection timeout -----------------------------------
    "network": {
        "logs": [
            {
                "timestamp": _NOW,
                "container": "checkout-svc",
                "text": "dial tcp: lookup payment-svc.production.svc.cluster.local: no such host",
                "byte_offset": 0,
            },
            {
                "timestamp": _NOW,
                "container": "checkout-svc",
                "text": "error: upstream connect error or disconnect/reset before headers. reset reason: connection timeout",
                "byte_offset": 90,
            },
            {
                "timestamp": _NOW,
                "container": "checkout-svc",
                "text": "ERR GET /api/payment: context deadline exceeded (Client.Timeout exceeded while awaiting headers)",
                "byte_offset": 210,
            },
        ],
        "pod": {
            "phase": "Running",
            "restart_count_by_ctr": {"checkout-svc": 2},
            "container_states": {
                "checkout-svc": {"state": "Running", "reason": None, "exit_code": None}
            },
            "ready": True,
            "resource_version": "61037",
        },
        "routing": {
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
        "diagnosis": {
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
            "proposed_fix": None,  # network fixes require manual intervention
            "model": "claude-sonnet-4-6",
            "tokens": 743,
        },
    },

    # ---- Database: connection pool exhaustion --------------------------------
    "database": {
        "logs": [
            {
                "timestamp": _NOW,
                "container": "orders-api",
                "text": "ERROR: remaining connection slots are reserved for non-replication superuser connections",
                "byte_offset": 0,
            },
            {
                "timestamp": _NOW,
                "container": "orders-api",
                "text": "FATAL: sorry, too many clients already (max_connections=100, current=100)",
                "byte_offset": 118,
            },
            {
                "timestamp": _NOW,
                "container": "orders-api",
                "text": "panic: failed to acquire connection from pool after 5000ms timeout",
                "byte_offset": 210,
            },
        ],
        "pod": {
            "phase": "Running",
            "restart_count_by_ctr": {"orders-api": 0},
            "container_states": {
                "orders-api": {"state": "Running", "reason": None, "exit_code": None}
            },
            "ready": True,
            "resource_version": "72841",
        },
        "routing": {
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
        "diagnosis": {
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
            },
            "model": "claude-sonnet-4-6",
            "tokens": 812,
        },
    },

    # ---- Unknown: ambiguous signals, no automated fix -----------------------
    "unknown": {
        "logs": [
            {
                "timestamp": _NOW,
                "container": "worker",
                "text": "WARNING: unexpected shutdown signal received",
                "byte_offset": 0,
            },
            {
                "timestamp": _NOW,
                "container": "worker",
                "text": "INFO: graceful shutdown in progress",
                "byte_offset": 55,
            },
        ],
        "pod": {
            "phase": "Unknown",
            "restart_count_by_ctr": {"worker": 1},
            "container_states": {
                "worker": {"state": "Terminated", "reason": "Completed", "exit_code": 0}
            },
            "ready": False,
            "resource_version": "53009",
        },
        "routing": {
            "domain": "Unknown",
            "confidence": "low",
            "cited_evidence": [],
            "runners_up": [],
            "model": "claude-haiku-4-5",
            "tokens": 201,
        },
        "diagnosis": None,
    },
}


def get_fixture(domain: str) -> dict[str, Any]:
    """Return fixture data for *domain* (application | network | database | unknown)."""
    return _FIXTURES.get(domain, _FIXTURES["unknown"])


def list_domains() -> list[str]:
    return list(_FIXTURES.keys())
