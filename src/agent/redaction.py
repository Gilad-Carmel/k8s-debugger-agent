"""
src/agent/redaction.py

Double-pass regex redaction applied:
  (a) inside MCP read tools before the payload crosses the MCP boundary, and
  (b) immediately before every LLM call.

Per research.md §R7 and Principle I + V + SC-009:
  - Redaction MUST NOT rely on model behaviour.
  - Two passes are defence-in-depth; the boundary pass ensures audit records
    are clean by construction.

Corresponds to tasks.md T020.  95% coverage tier (CI-enforced).
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Compiled pattern catalogue
# Each tuple is (human label, compiled regex, replacement string).
# ---------------------------------------------------------------------------
_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    # Bearer / token authorization headers
    (
        "bearer_token",
        re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/=]{8,}", re.IGNORECASE),
        "Bearer [REDACTED]",
    ),
    # AWS access key IDs (AKIA…)
    (
        "aws_access_key",
        re.compile(r"\b(AKIA|ASIA|AROA|AIDA)[0-9A-Z]{16}\b"),
        "[REDACTED_AWS_KEY]",
    ),
    # AWS secret access keys (40-char base-64ish strings after keyword)
    (
        "aws_secret_key",
        re.compile(
            r"(?i)(?:aws_secret_access_key|aws_secret)\s*[=:]\s*[A-Za-z0-9/+]{40}"
        ),
        "[REDACTED_AWS_SECRET]",
    ),
    # JWT tokens (three base64url segments separated by dots)
    (
        "jwt",
        re.compile(
            r"\bey[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"
        ),
        "[REDACTED_JWT]",
    ),
    # Authorization header values (generic)
    (
        "authorization_header",
        re.compile(r"(?i)(Authorization\s*:\s*)\S+"),
        r"\1[REDACTED]",
    ),
    # Database connection strings (postgres, mysql, mongodb, redis)
    (
        "db_connection_string",
        re.compile(
            r"(?i)(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://[^\s\"'<>]+"
        ),
        "[REDACTED_DB_URL]",
    ),
    # Generic password= / password: assignments
    (
        "password_kv",
        re.compile(r"(?i)(password\s*[=:]\s*)\S+"),
        r"\1[REDACTED]",
    ),
    # Generic secret= / secret: assignments
    (
        "secret_kv",
        re.compile(r"(?i)(secret\s*[=:]\s*)\S+"),
        r"\1[REDACTED]",
    ),
    # Generic token= / token: assignments (avoid double-redacting Bearer lines)
    (
        "token_kv",
        re.compile(r"(?i)((?<!Bearer )\btoken\s*[=:]\s*)\S+"),
        r"\1[REDACTED]",
    ),
    # Generic api_key / apikey assignments
    (
        "api_key_kv",
        re.compile(r"(?i)(api[_-]?key\s*[=:]\s*)\S+"),
        r"\1[REDACTED]",
    ),
    # GCP / Azure service-account private-key PEM blocks (partial match)
    (
        "private_key_pem",
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[A-Za-z0-9+/=\n\r]+-----END [A-Z ]*PRIVATE KEY-----"),
        "[REDACTED_PRIVATE_KEY]",
    ),
    # Kubernetes ServiceAccount tokens mounted at /var/run/secrets (long JWT-like)
    # Already covered by the jwt pattern above; this catches any remaining
    # "token: <base64>" lines in pod annotations / env.
    (
        "k8s_sa_annotation",
        re.compile(r"(?i)(kubernetes\.io/service-account\.token\s*:\s*)\S+"),
        r"\1[REDACTED]",
    ),
]


def redact(text: str) -> str:
    """
    Apply every compiled pattern in sequence and return the sanitised string.

    This function is deterministic and idempotent: applying it twice yields the
    same result as applying it once.
    """
    for _label, pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text
