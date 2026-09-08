"""Minimal .env loader — no third-party dependency.

Reads KEY=VALUE lines into os.environ. Real environment variables always win, so a shell
export or a systemd unit overrides the file rather than fighting it.

Where it looks, in order, first file wins:

1. `$AGRONAUT_ENV_FILE` — an explicit path, for a service unit or a second deployment.
2. `./.env` in the working directory — what someone expects after `cd ~/my-farm`.
3. `~/.config/agronaut/.env` (or `$XDG_CONFIG_HOME`) — the per-user home for an
   installed tool, and the answer for anyone who did `pip install agronaut`.
4. The project root, for a source checkout.

Order 2 and 3 exist because of a real gap: the loader used to look only at the project
root, computed from this file's location. Inside an installed wheel that resolves to
`site-packages/.env`, a path nobody will ever create — so a user who installed from PyPI
had no way to configure a Telegram token at all except re-exporting variables in every new
shell. State already went to the right per-user place; configuration did not follow it.
"""

from __future__ import annotations

import os
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def user_config_dir() -> Path:
    """Per-user config directory. Honours XDG, as the data and cache dirs already do."""
    base = os.environ.get("XDG_CONFIG_HOME")
    return (Path(base) if base else Path.home() / ".config") / "agronaut"


def candidates() -> list[Path]:
    """Every place a .env may live, in precedence order."""
    explicit = os.environ.get("AGRONAUT_ENV_FILE")
    found = [Path(explicit)] if explicit else []
    found.append(Path.cwd() / ".env")
    found.append(user_config_dir() / ".env")
    found.append(_PROJECT_ROOT / ".env")
    # Preserve order while dropping repeats (a checkout run from its own root hits the
    # working directory and the project root with the same path).
    seen, out = set(), []
    for p in found:
        key = str(p)
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def load_env(path: str | os.PathLike | None = None) -> Path | None:
    """Load the first .env found and return it, or None when there is nothing to load."""
    paths = [Path(path)] if path else candidates()
    for env_path in paths:
        try:
            if not env_path.is_file():
                continue
            text = env_path.read_text()
        except OSError:
            # An unreadable candidate is not fatal: fall through to the next one rather
            # than stop a bot from starting over a permissions problem on a stale path.
            continue
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val
        return env_path
    return None
