"""
Dev utility: check OpenRouter API key balance and usage.

Usage:
    uv run python scripts/check_openrouter.py
    uv run python scripts/check_openrouter.py --key sk-or-...

Reads GITWHO_LOCAL_API_KEY from .env if --key not provided.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

import httpx


def check_balance(api_key: str) -> None:
    r = httpx.get(
        "https://openrouter.ai/api/v1/key",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=10.0,
    )
    if r.status_code != 200:
        print(f"Error {r.status_code}: {r.text}")
        sys.exit(1)

    data = r.json().get("data", {})
    limit = data.get("limit")
    remaining = data.get("limit_remaining")
    usage = data.get("usage", 0)
    is_free = data.get("is_free_tier", False)

    limit_str = f"${limit:.4f}" if limit is not None else "unlimited"
    remaining_str = f"${remaining:.4f}" if remaining is not None else "N/A"
    used_str = f"${usage:.4f}" if usage is not None else "N/A"
    pct = f" ({100 * usage / limit:.1f}% used)" if limit else ""

    print(f"Limit: {limit_str}")
    print(f"Used: {used_str}{pct}")
    print(f"Remaining: {remaining_str}")
    if is_free:
        print("Tier: free")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--key", default="", help="OpenRouter API key (overrides .env)")
    args = parser.parse_args()

    api_key = args.key or os.getenv("GITWHO_LOCAL_API_KEY")
    if not api_key:
        print("ERROR: no API key. Pass --key or set GITWHO_LOCAL_API_KEY in .env")
        sys.exit(1)

    check_balance(api_key)


if __name__ == "__main__":
    main()