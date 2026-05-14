"""
deploy/mocks/fire_alert.py

CLI helper — fires a signed Alertmanager-style webhook at the agent mock
(or at the real agent when it exists).

What this replaces:
  In production, Alertmanager itself fires webhook alerts when a Prometheus
  rule fires.  This script lets you trigger one manually for demo / testing.

When to replace:
  Keep this script; it remains useful as a manual test trigger even when the
  real agent is deployed.

Usage:
  # Fire an application-domain incident at the agent mock
  python deploy/mocks/fire_alert.py application

  # Fire at a custom URL
  python deploy/mocks/fire_alert.py network --url http://localhost:8000

  # Reuse a specific correlation_id (for testing dedup)
  python deploy/mocks/fire_alert.py database --corr my-corr-123

  # List available domains
  python deploy/mocks/fire_alert.py --list

Domains: application | network | database | unknown
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
import uuid

import httpx

AGENT_URL = os.getenv("AGENT_URL", "http://localhost:8000")
AGENT_SECRET = os.getenv("AGENT_SECRET", os.getenv("SLACK_MOCK_SECRET", "dev-mock-secret"))

_DOMAIN_ALERTNAMES = {
    "application": "ApplicationCrashLoopBackOff",
    "network":     "NetworkConnectivityFailure",
    "database":    "DatabaseConnectionPoolExhausted",
    "unknown":     "UnknownPodAnomaly",
}

_NAMESPACES = {
    "application": ("production", "api-server-5d9f7b6c8-xk2qp"),
    "network":     ("production", "checkout-svc-6b7f4d9c7-r8tlk"),
    "database":    ("production", "orders-api-7c9d4b8f9-p3rnm"),
    "unknown":     ("default",    "worker-6c8b5f7d4-z9vmx"),
}


def _build_payload(domain: str, correlation_id: str) -> dict:
    alertname = _DOMAIN_ALERTNAMES.get(domain, "UnknownAlert")
    namespace, pod = _NAMESPACES.get(domain, ("default", "unknown-pod"))

    return {
        "version": "4",
        "groupKey": f'{{}}:{{alertname="{alertname}"}}',
        "status": "firing",
        "receiver": "k8s-debugger",
        "groupLabels": {"alertname": alertname},
        "commonLabels": {
            "alertname": alertname,
            "namespace": namespace,
            "pod": pod,
            "domain": domain,
            "correlation_id": correlation_id,
        },
        "commonAnnotations": {
            "summary": f"Simulated {domain} incident for testing",
        },
        "alerts": [
            {
                "status": "firing",
                "labels": {
                    "alertname": alertname,
                    "namespace": namespace,
                    "pod": pod,
                    "domain": domain,
                    "correlation_id": correlation_id,
                },
                "annotations": {
                    "summary": f"Simulated {domain} incident for testing",
                    "description": (
                        f"Pod {pod} in namespace {namespace} is exhibiting "
                        f"{domain}-domain failure symptoms."
                    ),
                },
                "startsAt": "2026-01-01T00:00:00Z",
                "endsAt": "0001-01-01T00:00:00Z",
                "generatorURL": "http://prometheus.local/graph",
            }
        ],
    }


def _sign(body: bytes) -> str:
    return hmac.new(AGENT_SECRET.encode(), body, hashlib.sha256).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description="Fire a mock Alertmanager webhook at the agent.")
    parser.add_argument(
        "domain",
        nargs="?",
        default="application",
        choices=list(_DOMAIN_ALERTNAMES.keys()),
        help="Failure domain to simulate (default: application)",
    )
    parser.add_argument("--url", default=AGENT_URL, help="Agent URL (default: $AGENT_URL or http://localhost:8000)")
    parser.add_argument("--corr", default=None, help="Override correlation_id (default: random UUID)")
    parser.add_argument("--list", action="store_true", help="List available domains and exit")
    args = parser.parse_args()

    if args.list:
        print("Available domains:")
        for d in _DOMAIN_ALERTNAMES:
            print(f"  {d}")
        return

    correlation_id = args.corr or str(uuid.uuid4())
    payload = _build_payload(args.domain, correlation_id)
    body = json.dumps(payload, separators=(",", ":")).encode()

    url = f"{args.url}/webhook/alertmanager"
    headers = {
        "Content-Type": "application/json",
        "X-Alertmanager-Signature": _sign(body),
    }

    print(f"Firing {args.domain!r} alert → {url}")
    print(f"  correlation_id : {correlation_id}")

    try:
        resp = httpx.post(url, content=body, headers=headers, timeout=10.0)
        resp.raise_for_status()
        data = resp.json()
        print(f"  response       : {data}")
    except httpx.ConnectError:
        print(f"ERROR: could not connect to {url}")
        print("       Is the agent mock running?  python -m deploy.mocks.agent_mock")
        sys.exit(1)
    except httpx.HTTPStatusError as e:
        print(f"ERROR: {e.response.status_code} {e.response.text}")
        sys.exit(1)


if __name__ == "__main__":
    main()
