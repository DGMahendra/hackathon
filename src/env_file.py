"""src/env_file.py — load ANTHROPIC_API_KEY from the repo-root .env file (Task 3.2).

Claude.md §4 allows exactly one environment variable, ANTHROPIC_API_KEY. The engineer keeps
it in a gitignored `.env` at the repo root. This loader reads only that name, never
overrides a value already in the environment, and never prints or logs the value.
No third-party dotenv package is used (not in the Fixed Stack).
"""

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = REPO_ROOT / ".env"
ALLOWED = ("ANTHROPIC_API_KEY",)


def load(path=None) -> bool:
    """Set ANTHROPIC_API_KEY from path (default: repo-root .env) if not already set; return True if it is now set."""
    path = Path(path or ENV_FILE)
    if path.is_file():
        for name, value in _assignments(path.read_text(encoding="utf-8-sig")):
            if name in ALLOWED and value and not os.environ.get(name):
                os.environ[name] = value
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _assignments(text: str) -> list:
    """Return (name, value) pairs from KEY=VALUE lines; comments, blanks and junk are skipped."""
    pairs = []
    for line in text.splitlines():
        name, sep, value = line.strip().removeprefix("export ").partition("=")
        if sep and name.strip() and not name.lstrip().startswith("#"):
            pairs.append((name.strip(), value.strip().strip("\"'")))
    return pairs
