#!/usr/bin/env python3
"""Credential validator, ported in spirit from record-scanner/scripts/check_env.py.
Run this first when anything downstream is failing -- a missing or
unreachable credential and a real bug look identical from inside scan.py.
"""

import os
import sys

from .envload import load_dotenv

REQUIRED = {
    "SUPABASE_URL": "Postgres + storage, all phases",
    "SUPABASE_SERVICE_ROLE_KEY": "Postgres + storage, all phases",
}

OPTIONAL = {
    "SLACK_WEBHOOK_URL": "Phase 4 delivery",
    "RESEND_API_KEY": "Phase 4 delivery",
    "NOTION_TOKEN": "Phase 4 review lane",
    "ANTHROPIC_API_KEY": "Phase 3 extraction",
}


def main():
    found = load_dotenv()
    print(f".env loaded: {found}")

    ok = True
    for key, purpose in REQUIRED.items():
        val = os.environ.get(key, "")
        status = "SET" if val else "MISSING"
        if not val:
            ok = False
        print(f"  [{status:7}] {key:28} {purpose}")

    print("--- optional, phases not yet built ---")
    for key, purpose in OPTIONAL.items():
        val = os.environ.get(key, "")
        status = "SET" if val else "unset"
        print(f"  [{status:7}] {key:28} {purpose}")

    if not ok:
        print("Missing required credentials.", file=sys.stderr)
        sys.exit(1)
    print("check_env: ok")


if __name__ == "__main__":
    main()
