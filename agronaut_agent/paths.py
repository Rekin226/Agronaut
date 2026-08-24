"""Where Agronaut reads its corpus and writes its state.

One module answers this, because the answer differs by how Agronaut was installed and
getting it wrong is silent. In a checkout everything lives beside the source, which is what
`pip install -e .` and `docker compose` both give you. Under a plain `pip install .` the
package lands in site-packages, and site-packages is the wrong place to look for the
knowledge base and a worse place to write a cache: often read-only, often root-owned,
sometimes shared between users, and wiped on uninstall.

Every lookup here is overridable by an environment variable, so a container or a packager
can put the corpus and the state wherever it belongs without patching code.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# The source tree's root — the directory holding knowledge/, urls.txt and data/ in a
# checkout. Under a wheel install this is site-packages, which is exactly the case the
# fallbacks below exist for.
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def corpus_root() -> Path:
    """Directory containing `knowledge/` and `urls.txt`.

    A missing corpus is not loud: the RAG layer logs at most a warning and then answers
    every question with KNOWLEDGE_UNAVAILABLE, so the assistant sounds fine and cites
    nothing. Prefer the checkout, fall back to where a wheel's data files install.
    """
    override = os.environ.get("AGRONAUT_CORPUS_DIR")
    if override:
        return Path(override)
    if (PROJECT_ROOT / "knowledge").is_dir():
        return PROJECT_ROOT
    return Path(sys.prefix) / "share" / "agronaut"


def cache_dir() -> Path:
    """Directory for regenerable caches (the fetched-page sqlite).

    Kept in the source tree for a checkout, so a working copy keeps the cache it already
    warmed; otherwise a per-user cache directory.
    """
    override = os.environ.get("AGRONAUT_CACHE_DIR")
    if override:
        return Path(override)
    if _is_source_tree():
        return PROJECT_ROOT
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return _ensured(Path(base) / "agronaut")


def data_dir() -> Path:
    """Directory for state worth keeping — the memory DB and the usage log."""
    override = os.environ.get("AGRONAUT_DATA_DIR")
    if override:
        return Path(override)
    if _is_source_tree():
        return PROJECT_ROOT / "data"
    base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return _ensured(Path(base) / "agronaut")


def _ensured(path: Path) -> Path:
    """Create the per-user directory on the way out — nothing downstream expects to have to.

    Only the fallback branches call this: a checkout's own directories already exist, and an
    explicit override is the caller's to manage.
    """
    path.mkdir(parents=True, exist_ok=True)
    return path


def _is_source_tree() -> bool:
    """True in a checkout (or an editable install), false once installed into site-packages.

    Writability is the wrong question: site-packages in a user-owned venv is writable, and
    that is precisely where state must not go — wiped on uninstall, shared between users of
    a system interpreter, and baked into container layers. The project file is the signal.
    """
    return (PROJECT_ROOT / "pyproject.toml").is_file()
