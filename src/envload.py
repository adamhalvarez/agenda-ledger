#!/usr/bin/env python3
"""Single .env loader, ported from record-scanner/scripts/envload.py.

Existing environment values always win, so a deploy environment's real
variables are never shadowed by a stray .env checked out alongside them.
"""

import os
import shlex
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _unquote(value):
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _parse(line):
    try:
        words = shlex.split(line, comments=False, posix=True)
    except ValueError:
        words = []
    if len(words) == 1 and "=" in words[0]:
        k, _, v = words[0].partition("=")
        return k.strip(), v
    k, _, v = line.partition("=")
    return k.strip(), _unquote(v.strip())


def load_dotenv(path=None):
    """Returns whether a .env was found and read."""
    env = Path(path) if path else ROOT / ".env"
    if not env.exists():
        return False
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = _parse(line)
        os.environ.setdefault(k, v)
    return True
