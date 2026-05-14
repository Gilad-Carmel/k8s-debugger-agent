"""
demo_slack.py — fire a sample triage Report at the local slack-mock service
so you can see the dashboard UI without needing the full agent stack.

Usage:
    # In one terminal, start the mock:
    uvicorn deploy.slack_mock.app:app --port 8090

    # In another terminal:
    python demo_slack.py [application|network|database|unknown]

Then open http://localhost:8090 to see the rendered incident card.
"""
from __future__ import annotations

import sys
import json
import uuid
from datetime import datetime, timedelta, timezone

import httpx

SLACK_MOCK_URL = "http://localhost:8090"

_NOW = datetime.now(timezone.utc)
_CORR = str(uuid.uuid4())

_SCENARIOS: dict[str, dict] = {
    "application": {
        "domain": "Application",
        "confidence": "high",
        "root_cause": (
            "Repeated NullPointerException at CheckoutService:142 over the last 8 minutes; "
            "coincides with deployment of revision 9 at 09:52 UTC."
        ),
        "cited_evidence": [
            {
                "timestamp": (_NOW - timedelta(minutes=7)).isoformat(),
                "container": "checkout",
                "text": "java.lang.NullPointerException at CheckoutService.processOrder(CheckoutService.java:142)",
                "byte_offset": 104320,
            },
            {
                "timestamp": (_NOW - timedelta(minutes=6)).isoformat(),
                "container": "checkout",
                "text": "\tat OrderProcessor.execute(OrderProcessor.java:89)",
                "byte_offset": 104420,
            },
            {
                "timestamp": (_NOW - timedelta(minutes=5, seconds=30)).isoformat(),
                "container": "checkout",
                "text": "ERROR [checkout] FATAL: unhandled exception in worker thread",
                "byte_offset": 104501,
            },
        ],
        "runner_up_causes": ["Memory leak in connection pool", "Race condition in caching layer"],
        "proposed_fix": {
            "action_type": "rollback-deployment",
            "target": {"namespace": "checkout", "pod": "checkout-deploy-7b5d-x29", "container": None},
            "parameters": {"to_revision": 8},
            "permission_scope": "mcp-write-sa",
            "fingerprint": "abc123def456abc123def456",
        },
        "runners_up": [["Network", "low"]],
    },
    "network": {
        "domain": "Network",
        "confidence": "high",
        "root_cause": (
            "Connection refused on port 5432 from payment-service to postgres-primary; "
            "DNS resolution for 'postgres-primary' returning NXDOMAIN since 10:14 UTC."
        ),
        "cited_evidence": [
            {
                "timestamp": (_NOW - timedelta(minutes=3)).isoformat(),
                "container": "payment",
                "text": "ECONNREFUSED: connect ECONNREFUSED 10.96.0.45:5432",
                "byte_offset": 88100,
            },
            {
                "timestamp": (_NOW - timedelta(minutes=2, seconds=45)).isoformat(),
                "container": "payment",
                "text": "getaddrinfo ENOTFOUND postgres-primary.default.svc.cluster.local",
                "byte_offset": 88210,
            },
        ],
        "runner_up_causes": ["Network policy blocking egress", "Service endpoint missing"],
        "proposed_fix": {
            "action_type": "restart-pod",
            "target": {"namespace": "default", "pod": "payment-service-6c4d-k8p", "container": None},
            "parameters": {},
            "permission_scope": "mcp-write-sa",
            "fingerprint": "net999abc123net999abc123",
        },
        "runners_up": [["Database", "medium"]],
    },
    "database": {
        "domain": "Database",
        "confidence": "medium",
        "root_cause": (
            "Connection pool exhausted on postgres replica; max_connections (100) reached, "
            "causing query timeouts in inventory-service."
        ),
        "cited_evidence": [
            {
                "timestamp": (_NOW - timedelta(minutes=4)).isoformat(),
                "container": "inventory",
                "text": "FATAL: too many connections — max_connections=100 exceeded",
                "byte_offset": 51000,
            },
            {
                "timestamp": (_NOW - timedelta(minutes=3, seconds=20)).isoformat(),
                "container": "inventory",
                "text": "ERROR: query timeout after 30000ms on SELECT * FROM stock WHERE ...",
                "byte_offset": 51120,
            },
        ],
        "runner_up_causes": ["Slow query causing lock contention"],
        "proposed_fix": {
            "action_type": "scale-deployment",
            "target": {"namespace": "data", "pod": "postgres-replica-0", "container": None},
            "parameters": {"to_replicas": 3},
            "permission_scope": "mcp-write-sa",
            "fingerprint": "db456xyz789db456xyz789",
        },
        "runners_up": [["Application", "low"]],
    },
    "unknown": {
        "domain": "Unknown",
        "confidence": "low",
        "root_cause": "",
        "cited_evidence": [],
        "runner_up_causes": [],
        "proposed_fix": None,
        "runners_up": [["Application", "low"], ["Network", "low"]],
    },
}


def _build_payload(scenario: str) -> dict:
    s = _SCENARIOS[scenario]
    now_iso = _NOW.isoformat()
    deadline_iso = (_NOW + timedelta(minutes=30)).isoformat()

    report_sidecar = {
        "status": "pending",
        "delivered_at": now_iso,
        "approval_deadline": deadline_iso,
        "routing": {
            "domain": s["domain"],
            "confidence": s["confidence"],
            "cited_evidence": s["cited_evidence"],
            "runners_up": s["runners_up"],
        },
        "diagnosis": {
            "domain": s["domain"],
            "root_cause_hypothesis": s["root_cause"],
            "confidence": s["confidence"],
            "cited_evidence": s["cited_evidence"],
            "runner_up_causes": s["runner_up_causes"],
        } if s["domain"] != "Unknown" else None,
        "proposed_fix": s["proposed_fix"],
    }

    # Build Block Kit blocks
    icon = {"Application": "⚙️", "Network": "🌐", "Database": "🗄️", "Unknown": "❓"}[s["domain"]]
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": f"{icon}  Incident — {s['domain']}", "emoji": True}},
        {"type": "divider"},
    ]

    if s["root_cause"]:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*Root cause:* {s['root_cause']}"}})

    if s["cited_evidence"]:
        lines = "\n".join(f"[{e['container']}] {e['text']}" for e in s["cited_evidence"])
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*Evidence:*\n```{lines}```"}})

    if s["proposed_fix"]:
        fix = s["proposed_fix"]
        t = fix["target"]
        params_str = "  ".join(f"{k}={v}" for k, v in fix["parameters"].items())
        action_disp = f"`{fix['action_type']}({params_str})`" if params_str else f"`{fix['action_type']}`"
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*Proposed fix:* {action_disp} on `{t['namespace']}/{t['pod']}`"}})
        blocks.append({"type": "actions", "elements": [
            {"type": "button", "style": "primary", "text": {"type": "plain_text", "text": "Approve Remediation"}, "value": "approve", "action_id": f"approve_{_CORR}"},
            {"type": "button", "style": "danger", "text": {"type": "plain_text", "text": "Reject"}, "value": "reject", "action_id": f"reject_{_CORR}"},
        ]})
    else:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "*No automatic fix available* — manual triage required."}})

    runners_str = "  •  ".join(f"{d} ({c})" for d, c in s["runners_up"]) or "none"
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"Confidence: *{s['confidence']}*  •  Runner-ups: {runners_str}  •  ID: `{_CORR}`"}]})

    return {
        "correlation_id": _CORR,
        "channel": "#k8s-incidents",
        "report": report_sidecar,
        "blocks": blocks,
    }


def main() -> None:
    scenario = (sys.argv[1] if len(sys.argv) > 1 else "application").lower()
    if scenario not in _SCENARIOS:
        print(f"Unknown scenario {scenario!r}. Choose from: {list(_SCENARIOS)}")
        sys.exit(1)

    payload = _build_payload(scenario)
    print(f"Firing {scenario!r} incident to {SLACK_MOCK_URL}  (corr={_CORR[:20]}…)")

    resp = httpx.post(f"{SLACK_MOCK_URL}/messages", json=payload, timeout=10)
    resp.raise_for_status()
    print(f"Delivered: {resp.json()}")
    print(f"\nOpen http://localhost:8090 to see the incident dashboard.")


if __name__ == "__main__":
    main()
