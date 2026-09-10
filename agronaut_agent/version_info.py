"""Which Agronaut is this, where did it come from, and how do you move it forward.

This module exists because of a real evening lost to the question. A maintainer with both a
git checkout and a `pip install agronaut` ran `agronaut setup`, got the old wizard, and had
no way to tell why: `pip show agronaut` said 1.0.0 for the checkout and for a stale copy in
site-packages alike, and nothing in the CLI would say which files it had actually loaded. The
answer turned out to be orphaned package directories left in site-packages, shadowing an
editable install that was pointing at the fixed code all along.

So `agronaut --version` prints the version AND the path it loaded from AND whether that is an
editable install, because the version number on its own was exactly the thing that lied.

Pure and offline apart from `latest_on_pypi`, which is the only function here that touches the
network and is the only one a caller has to be ready to have fail.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

PACKAGE = "agronaut"
PYPI_JSON = f"https://pypi.org/pypi/{PACKAGE}/json"
TIMEOUT = 15


@dataclass(frozen=True)
class Install:
    """Where this Agronaut is running from."""

    version: str
    code_dir: Path
    editable_from: Path | None      # the checkout, when installed with `pip install -e`
    recorded_version: str | None = None   # what pip has on file, when it has gone stale

    @property
    def is_editable(self) -> bool:
        return self.editable_from is not None

    def render(self) -> str:
        lines = [f"{PACKAGE} {self.version}"]
        if self.is_editable:
            lines.append(f"  code    {self.code_dir}")
            lines.append(f"          editable install, tracking {self.editable_from}")
            lines.append("          so `git pull` there is what updates it, not `agronaut update`")
            if self.recorded_version:
                lines.append(f"          version read from that checkout; pip still records "
                             f"{self.recorded_version} until you reinstall")
        else:
            lines.append(f"  code    {self.code_dir}")
        lines.append(f"  python  {sys.executable}")
        lines.append(f"  config  {_config_path()}")
        return "\n".join(lines)


def _config_path() -> str:
    """The .env this process would actually read.

    Worth printing next to the version for the same reason: it changes with the working
    directory (a checkout keeps its own), so "which config am I editing" is the second
    question people get wrong after "which code am I running".
    """
    try:
        from .setup_wizard import env_path

        return str(env_path())
    except Exception:      # noqa: BLE001 — a diagnostic must never be the thing that breaks
        return "unknown"


def distributions() -> list:
    """Every discoverable distribution named `agronaut`, not just the first one found.

    More than one is possible and is itself a symptom: a checkout on sys.path carries a
    legacy `agronaut.egg-info` beside whatever pip installed, and `importlib.metadata` returns
    them in sys.path order. `agronaut_agent/cli.py` puts the project root on sys.path while
    building its parser, so the egg-info can win, and it carries no `direct_url.json`.

    That is not hypothetical. It silently disabled the shadowed-install check in `doctor`:
    the checkout's egg-info was found first, editability came back False, and the one check
    written to catch a stale copy never ran. Scanning them all is the fix.
    """
    try:
        from importlib.metadata import distributions as _all

        seen: set[str] = set()
        out = []
        for d in _all():
            if (d.metadata.get("Name") or "").lower() != PACKAGE:
                continue
            # Deduplicate by location. The same site-packages can appear more than once on
            # sys.path, and `distributions()` then yields the same install repeatedly. Left
            # raw, the doctor reported "2 installs of agronaut are visible at once" and
            # printed one path twice, sending a reader hunting for a conflict that was not
            # there. A duplicate is not a second install.
            where = str(getattr(d, "_path", "") or d.metadata.get("Name"))
            if where in seen:
                continue
            seen.add(where)
            out.append(d)
        return out
    except Exception:      # noqa: BLE001
        return []


def _editable_target() -> Path | None:
    """The checkout an editable install points at, or None for a normal install.

    Read from the installer's own `direct_url.json` (PEP 610) rather than guessed from paths,
    so it says what pip recorded rather than what the layout suggests. Checks every
    distribution, because the first one found is not reliably the installed one.
    """
    for dist in distributions():
        try:
            raw = dist.read_text("direct_url.json")
            if not raw:
                continue
            data = json.loads(raw)
            if not (data.get("dir_info") or {}).get("editable"):
                continue
            url = data.get("url", "")
            prefix = "file://"
            if url.startswith(prefix):
                return Path(url[len(prefix):])
        except Exception:  # noqa: BLE001 — a broken sibling must not hide a good one
            continue
    return None


def _checkout_version(root: Path) -> str | None:
    """The version in a checkout's pyproject.toml, which is the one actually running.

    An editable install writes its dist-info once, at install time, and never again. Bump
    pyproject and the recorded metadata stays behind until someone reinstalls, so
    `importlib.metadata.version` answers a question about the past. That is precisely the
    lie this module was written to stop, and it caught this module out: shipped as 1.1.0,
    `agronaut --version` reported 1.0.0 on the machine the release was cut from, and
    `agronaut update` then offered an upgrade that was already installed.
    """
    try:
        import tomllib

        with (root / "pyproject.toml").open("rb") as f:
            value = tomllib.load(f)["project"]["version"]
        return str(value) if value else None
    except Exception:      # noqa: BLE001 — no pyproject, unreadable, or a dynamic version
        return None


def current() -> Install:
    """Describe the running install. Never raises: this is what you run when things are odd."""
    try:
        from importlib.metadata import version as _v

        ver = _v(PACKAGE)
    except Exception:      # noqa: BLE001 — a source checkout with nothing installed
        ver = "unknown (not installed as a package)"

    editable = _editable_target()
    recorded = None
    if editable is not None:
        live = _checkout_version(editable)
        if live and live != ver:
            ver, recorded = live, ver
    return Install(version=ver,
                   code_dir=Path(__file__).resolve().parent,
                   editable_from=editable,
                   recorded_version=recorded)


def latest_on_pypi(timeout: int = TIMEOUT) -> tuple[str | None, str]:
    """The newest released version, or None and a reason. The only network call in here."""
    try:
        with urllib.request.urlopen(PYPI_JSON, timeout=timeout) as f:
            return json.load(f)["info"]["version"], ""
    except urllib.error.HTTPError as e:
        return None, f"PyPI returned HTTP {e.code}"
    except Exception as e:  # noqa: BLE001
        return None, f"could not reach PyPI: {e}"


def _as_tuple(v: str) -> tuple:
    """Compare versions numerically where possible, so 1.10.0 beats 1.9.0.

    Falls back to string comparison for anything with a suffix, which is deliberately crude:
    this decides whether to print "an update is available", not whether to install one.
    """
    parts = []
    for chunk in v.split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def compare(installed: str, latest: str) -> str:
    """'current', 'behind', or 'ahead'. Ahead is normal for a maintainer on a checkout."""
    try:
        a, b = _as_tuple(installed), _as_tuple(latest)
    except Exception:      # noqa: BLE001
        return "current" if installed == latest else "behind"
    if a == b:
        return "current"
    return "behind" if a < b else "ahead"


def upgrade_command() -> list[str]:
    """Upgrade using THIS interpreter's pip.

    `sys.executable`, not a bare `pip`: the console script is routinely invoked by absolute
    path from a venv whose bin/ is not on PATH, and a bare `pip` would then upgrade a
    different Python's copy and leave the user exactly where they started.
    """
    return [sys.executable, "-m", "pip", "install", "--upgrade", PACKAGE]


def run_update(*, check_only: bool = False) -> int:
    """`agronaut update`. Reports honestly, and refuses when upgrading would be wrong."""
    import subprocess

    install = current()
    print(install.render())
    print()

    latest, why = latest_on_pypi()
    if latest is None:
        print(f"Could not check for updates: {why}")
        return 1
    state = compare(install.version, latest)
    if state == "current":
        print(f"Up to date. {latest} is the newest release.")
        return 0
    if state == "ahead":
        print(f"This build ({install.version}) is newer than the newest release ({latest}).")
        print("Nothing to update to. That is normal when running from a checkout.")
        return 0

    print(f"Update available: {install.version} -> {latest}")

    if install.is_editable:
        # Overwriting an editable install with a PyPI copy would silently detach the command
        # from the checkout the developer is editing, which is the exact failure that made
        # this module necessary. Refuse, and say where the real update comes from.
        print("\nThis is an editable install, so pip would replace your checkout link with a")
        print(f"released copy. Update the source instead:\n\n    git -C {install.editable_from} pull\n")
        print("To leave the checkout behind and track releases:")
        print(f"    {' '.join(upgrade_command())} --force-reinstall")
        return 0

    if check_only:
        print(f"\nRun `agronaut update` to install it, or:\n    {' '.join(upgrade_command())}")
        return 0

    print()
    result = subprocess.call(upgrade_command())
    if result != 0:
        print("\npip could not complete the upgrade. The command it ran was:")
        print(f"    {' '.join(upgrade_command())}")
        return result
    print(f"\nUpdated. Check it with `agronaut --version` (expect {latest}).")
    return 0
