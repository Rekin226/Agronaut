"""`agronaut doctor` — which half of your setup is broken.

Built because the maintainer typed `agronaut doctor` twice, on two different days, while
stuck on a problem this command answers in a second. The problem was that a stale copy of
the package, left in site-packages by an older wheel, was shadowing an editable install; the
tool showed the old behaviour, `pip show` reported a version that matched neither, and
nothing anywhere would say which files were actually loaded.

The design copies `whatsapp_doctor`, which already worked: a flat list of `Check`, each with
a status, a label, a detail and a `fix:` line, rendered together with an exit code. That
command becomes one section here rather than a separate thing to remember.

Two rules the checks all follow:

* **Nothing may raise.** This is what you run when things are already broken, so a check that
  throws is reported as that check failing, never as a traceback.
* **Offline is a normal state.** A check that needs the network says it was skipped rather
  than claiming a failure it did not observe.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from .whatsapp_doctor import FAIL, OK, WARN, Check

SKIP = "warn"          # rendered as WARN; a check that could not run is not a pass


def _safe(fn, label: str) -> list[Check]:
    """Run one check group, converting any escape into a reported failure."""
    try:
        return fn()
    except Exception as err:  # noqa: BLE001 — a diagnostic must never be what breaks
        return [Check(FAIL, f"{label}: the check itself failed", f"{type(err).__name__}: {err}",
                      "please paste this into an issue, it is a bug in doctor")]


# --- the checks ---------------------------------------------------------------------------

def check_install() -> list[Check]:
    """Which code is running, and whether something is shadowing it.

    The shadowing check is the reason this command exists. An editable install puts a finder
    at the END of sys.path, so a real package directory left in site-packages by an older
    wheel wins over it silently: the checkout is "installed", every file in it is correct,
    and none of it is what runs.
    """
    from . import version_info

    install = version_info.current()
    out = [Check(OK, f"agronaut {install.version}", f"running from {install.code_dir}")]

    if install.is_editable:
        checkout = install.editable_from
        try:
            install.code_dir.relative_to(checkout)
            out.append(Check(OK, f"editable install tracking {checkout}"))
        except ValueError:
            out.append(Check(
                FAIL, "an installed copy is SHADOWING your editable install",
                f"pip records an editable install of {checkout}, but the code actually "
                f"loaded is {install.code_dir}. Edits to the checkout do nothing.",
                f"remove the stale copy: rm -rf {install.code_dir} "
                f"(and its sibling aqua_model/, agent/, srcs/), then reinstall"))
        if install.recorded_version:
            out.append(Check(
                WARN, f"pip still records {install.recorded_version}",
                "an editable install writes its metadata once; the version above comes from "
                "the checkout, which is the code that runs.",
                f"pip install -e {checkout}   # only to make `pip show` agree"))
    dists = version_info.distributions()
    if len(dists) > 1:
        where = ", ".join(str(getattr(d, "_path", "?")) for d in dists)
        out.append(Check(
            WARN, f"{len(dists)} installs of agronaut are visible at once", where,
            "python picks one by sys.path order, so which code runs can change between "
            "commands. Usually a checkout's agronaut.egg-info sitting beside a real install; "
            "`rm -rf agronaut.egg-info` in the checkout clears it."))

    if os.getenv("PYTHONPATH"):
        out.append(Check(
            WARN, "PYTHONPATH is set", f"PYTHONPATH={os.getenv('PYTHONPATH')}",
            "it overrides installed packages, so what you test may not be what you shipped"))
    return out


def check_config() -> list[Check]:
    """Which .env applies, and whether a second one is waiting to confuse you."""
    from .setup_wizard import env_path

    active = Path(env_path())
    others = []
    try:
        from agent.env import user_config_dir

        candidates = {Path.cwd() / ".env", Path(user_config_dir()) / ".env"}
        others = [p for p in candidates if p.is_file() and p != active]
    except Exception:  # noqa: BLE001
        pass

    if not active.is_file():
        out = [Check(WARN, f"no .env at {active}", "nothing is configured from a file",
                     "run `agronaut setup`")]
    else:
        out = [Check(OK, f"config {active}")]
    for p in others:
        out.append(Check(WARN, f"a second .env exists at {p}",
                         "which one applies depends on your working directory",
                         "delete the one you do not want, or always run from the same place"))
    return out


def check_provider() -> list[Check]:
    """Is a model configured, and can it be reached. Network, so it can be skipped."""
    from . import setup_wizard as W

    provider = (os.getenv("LLM_PROVIDER") or "").strip()
    model = (os.getenv("LLM_MODEL") or "").strip()
    if not provider:
        return [Check(WARN, "no model provider configured",
                      "chat is off. The sizing engine does not need one and still works.",
                      "run `agronaut setup`, or pick Ollama to run one locally with no key")]

    out = [Check(OK, f"provider {provider}" + (f", model {model}" if model else ""))]
    if provider == "ollama":
        ok, msg = W.check_ollama()
        out.append(Check(OK if ok else FAIL, f"ollama: {msg}",
                         fix="" if ok else "start it with `ollama serve`"))
    elif provider == "anthropic":
        key = (os.getenv("ANTHROPIC_API_KEY") or "").strip()
        if not key:
            out.append(Check(FAIL, "ANTHROPIC_API_KEY is not set",
                             fix="run `agronaut setup`"))
        else:
            ok, msg = W.check_anthropic_key(key)
            out.append(Check(OK if ok else FAIL, f"anthropic: {msg}",
                             fix="" if ok else "run `agronaut setup` to re-enter the key"))
    else:
        out.append(Check(SKIP, f"reachability of {provider} not checked",
                         "doctor only probes ollama and anthropic so far"))
    return out


def check_knowledge() -> list[Check]:
    """The corpus. A missing one is a FAIL, not a warning.

    `paths.corpus_root` says why: the RAG layer logs a warning and then answers every
    question with KNOWLEDGE_UNAVAILABLE, so the assistant sounds fine and cites nothing.
    Looking like it works is the worst failure this project can have.
    """
    from . import paths

    root = Path(paths.corpus_root())
    docs = sorted((root / "knowledge").glob("*.md"))
    if not docs:
        return [Check(FAIL, "the knowledge corpus is missing", f"looked in {root / 'knowledge'}",
                      "answers will cite nothing while still sounding confident. Reinstall.")]
    status = OK if len(docs) >= 20 else WARN
    return [Check(status, f"{len(docs)} knowledge documents", f"in {root / 'knowledge'}")]


def check_reference_tables() -> list[Check]:
    """The seven cited data tables. Their absence is what shipped broken in 1.0.0."""
    from aqua_model.reference_data import SHIPPED, reference_dir

    d = Path(reference_dir())
    missing = [n for n in SHIPPED if not (d / n).is_file()]
    if missing:
        return [Check(FAIL, f"{len(missing)} of {len(SHIPPED)} reference tables missing",
                      ", ".join(missing), "reinstall: pip install --force-reinstall agronaut")]
    return [Check(OK, f"all {len(SHIPPED)} reference tables present", f"in {d}")]


def check_validation_record() -> list[Check]:
    """What the twin is entitled to claim. Derived, never asserted."""
    from aqua_model import validation_status as vs

    first = vs.validation_lines()[0]
    if first.startswith("PREDICTIVE SKILL UNMEASURED"):
        return [Check(FAIL, "the validation record did not ship",
                      "every projection will disclaim itself as unmeasured",
                      "reinstall: pip install --force-reinstall agronaut")]
    return [Check(OK, "validation record present", first[:88])]


def check_database() -> list[Check]:
    """Present, readable, and a schema this build understands."""
    from .paths import data_dir
    from .store import SCHEMA_VERSION

    path = Path(os.getenv("AGRONAUT_DB") or (Path(data_dir()) / "agronaut.sqlite3"))
    if not path.is_file():
        return [Check(OK, "no database yet", f"one will be created at {path} on first use")]
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        found = int(conn.execute("PRAGMA user_version").fetchone()[0])
        users = conn.execute("SELECT count(*) FROM users").fetchone()[0]
    finally:
        conn.close()
    if found > SCHEMA_VERSION:
        return [Check(FAIL, f"database schema v{found} is newer than this build (v{SCHEMA_VERSION})",
                      f"at {path}", "upgrade: agronaut update")]
    return [Check(OK, f"database schema v{found or SCHEMA_VERSION}, {users} users", str(path))]


def check_channels() -> list[Check]:
    """Telegram and WhatsApp, only as far as their configuration goes."""
    out: list[Check] = []
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        out.append(Check(SKIP, "Telegram not configured", "no TELEGRAM_BOT_TOKEN"))
    else:
        from .setup_wizard import check_telegram_token

        ok, msg = check_telegram_token(token)
        out.append(Check(OK if ok else FAIL, f"Telegram: {msg}",
                         fix="" if ok else "run `agronaut setup` and paste a fresh token"))
        if not (os.getenv("AGRONAUT_ALLOWED_IDS") or "").strip():
            out.append(Check(WARN, "AGRONAUT_ALLOWED_IDS is empty",
                             "anyone who finds the bot can talk to it"))
    if not (os.getenv("WHATSAPP_TOKEN") or "").strip():
        out.append(Check(SKIP, "WhatsApp not configured", "no WHATSAPP_TOKEN"))
    else:
        out.append(Check(OK, "WhatsApp is configured",
                         "run `agronaut whatsapp --check` for the full chain"))
    return out


_GROUPS = (
    ("install", check_install),
    ("config", check_config),
    ("model provider", check_provider),
    ("knowledge", check_knowledge),
    ("reference tables", check_reference_tables),
    ("validation record", check_validation_record),
    ("database", check_database),
    ("channels", check_channels),
)


def run_checks() -> list[Check]:
    out: list[Check] = []
    for label, fn in _GROUPS:
        out.extend(_safe(fn, label))
    return out


def report(checks: list[Check]) -> tuple[str, int]:
    """Render, and an exit code: 0 if nothing FAILed, so it is usable in a script."""
    lines = [c.render() for c in checks]
    failed = [c for c in checks if c.status == FAIL]
    warned = [c for c in checks if c.status == WARN]
    lines.append("")
    if failed:
        lines.append(f"{len(failed)} thing(s) above are broken. Each has a fix: line.")
    elif warned:
        lines.append(f"Nothing is broken. {len(warned)} thing(s) worth a look.")
    else:
        lines.append("Everything checks out.")
    # Deliberately narrow: a green doctor means the CONFIGURATION is coherent. What the model
    # is entitled to claim is a separate question, answered by data/twin_validation.json, and
    # a report that read as a clean bill of health for the whole system would undo the thing
    # this project is careful about everywhere else.
    lines.append("This checks your setup, not the accuracy of the advice. "
                 "For that, see data/twin_validation.json.")
    return "\n".join(lines), (1 if failed else 0)


def main() -> int:
    text, code = report(run_checks())
    print(text)
    return code
