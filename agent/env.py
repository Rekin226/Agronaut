"""Minimal .env loader — no third-party dependency.

Reads KEY=VALUE lines from the project-root .env into os.environ. Real environment
variables win (we never override what's already set), so launch.json / shell exports
take precedence over the file. Quietly does nothing if .env is absent.
"""

from __future__ import annotations

import os
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_env(path: str | os.PathLike | None = None) -> None:
    env_path = Path(path) if path else _PROJECT_ROOT / ".env"
    if not env_path.is_file():
        return
    for raw in env_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val
