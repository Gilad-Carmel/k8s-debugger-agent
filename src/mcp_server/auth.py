"""
src/mcp_server/auth.py

Per-tool Kubernetes ServiceAccount loader.

Each MCP write tool authenticates with its own scoped kube token so the agent
process never holds a write-capable credential.  Read tools share a read-only
SA.

Resolution order (per research.md §R5):
  1. Per-tool token file at /var/run/secrets/{tool_name}/token (in-cluster, prod)
  2. In-cluster config via load_incluster_config()
  3. Local kubeconfig via load_kube_config() (dev / CI)

Corresponds to tasks.md T030.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from kubernetes import client as k8s_client  # type: ignore[import-untyped]
from kubernetes import config as k8s_config  # type: ignore[import-untyped]

# Prefix for per-tool token files; can be overridden for testing.
_SA_TOKEN_DIR = Path(os.environ.get("MCP_SA_TOKEN_DIR", "/var/run/secrets"))


def _load_api_client(tool_name: str) -> Any:
    """
    Return a configured kubernetes ApiClient for *tool_name*.

    Tries per-tool SA token first, falls back to in-cluster, then kubeconfig.
    """
    token_file = _SA_TOKEN_DIR / tool_name / "token"
    if token_file.is_file():
        token = token_file.read_text().strip()
        configuration = k8s_client.Configuration()
        configuration.host = os.environ.get(
            "KUBERNETES_SERVICE_HOST_URL",
            "https://kubernetes.default.svc",
        )
        configuration.api_key = {"authorization": f"Bearer {token}"}
        configuration.api_key_prefix = {"authorization": ""}  # prefix already in value
        # In production the cluster CA bundle is expected at the standard path.
        ca_file = Path("/var/run/secrets/kubernetes.io/serviceaccount/ca.crt")
        if ca_file.is_file():
            configuration.ssl_ca_cert = str(ca_file)
        else:
            configuration.verify_ssl = False
        return k8s_client.ApiClient(configuration)

    # Fall back to standard kubernetes config resolution.
    try:
        k8s_config.load_incluster_config()
    except k8s_config.ConfigException:
        k8s_config.load_kube_config()

    return k8s_client.ApiClient()


def get_core_v1_for_tool(tool_name: str) -> k8s_client.CoreV1Api:
    """Return a CoreV1Api client scoped to *tool_name*'s ServiceAccount."""
    return k8s_client.CoreV1Api(api_client=_load_api_client(tool_name))


def get_apps_v1_for_tool(tool_name: str) -> k8s_client.AppsV1Api:
    """Return an AppsV1Api client scoped to *tool_name*'s ServiceAccount."""
    return k8s_client.AppsV1Api(api_client=_load_api_client(tool_name))


def get_policy_v1_for_tool(tool_name: str) -> k8s_client.PolicyV1Api:
    """Return a PolicyV1Api client scoped to *tool_name*'s ServiceAccount."""
    return k8s_client.PolicyV1Api(api_client=_load_api_client(tool_name))
