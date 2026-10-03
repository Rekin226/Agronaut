"""`agronaut doctor`: the checks, and the promise that none of them can raise.

Built after the maintainer typed `agronaut doctor` twice while stuck on a problem it answers
in a second: a stale copy of the package in site-packages was shadowing an editable install,
so the checkout was "installed", every file in it was correct, and none of it was running.
That check is the one this file guards hardest.
"""

from pathlib import Path

import pytest

from agronaut_agent import doctor as D
from agronaut_agent.whatsapp_doctor import FAIL, OK, WARN, Check

CHECKOUT = Path("/home/grower/Agronaut")
SITE = Path("/usr/lib/python3.12/site-packages")


def _install(**kw):
    from agronaut_agent.version_info import Install

    base = dict(version="1.1.0", code_dir=CHECKOUT / "agronaut_agent",
                editable_from=CHECKOUT, recorded_version=None)
    base.update(kw)
    return Install(**base)


# --- the check this command exists for -----------------------------------------------------

def test_a_stale_copy_shadowing_an_editable_install_is_a_failure(monkeypatch):
    """pip records an editable install of the checkout, and the code that loads comes from
    site-packages instead. Everything looks installed and nothing you edit takes effect."""
    monkeypatch.setattr(D, "__name__", D.__name__)   # keep module identity explicit
    from agronaut_agent import version_info

    monkeypatch.setattr(version_info, "current",
                        lambda: _install(code_dir=SITE / "agronaut_agent"))
    checks = D.check_install()
    bad = [c for c in checks if c.status == FAIL]
    assert bad, "shadowing was not reported"
    assert "SHADOWING" in bad[0].label
    assert str(SITE / "agronaut_agent") in bad[0].detail
    assert "rm -rf" in bad[0].fix, "no concrete fix offered"


def test_a_healthy_editable_install_is_not_reported_as_shadowed(monkeypatch):
    from agronaut_agent import version_info

    monkeypatch.setattr(version_info, "current", lambda: _install())
    assert not [c for c in D.check_install() if c.status == FAIL]


def test_pythonpath_is_flagged(monkeypatch):
    """It overrides installed packages, which is how a verification stops meaning anything."""
    from agronaut_agent import version_info

    monkeypatch.setattr(version_info, "current", lambda: _install())
    monkeypatch.setenv("PYTHONPATH", "/somewhere")
    labels = [c.label for c in D.check_install()]
    assert any("PYTHONPATH is set" in x for x in labels)


def test_stale_pip_metadata_is_a_warning_not_a_failure(monkeypatch):
    from agronaut_agent import version_info

    monkeypatch.setattr(version_info, "current", lambda: _install(recorded_version="1.0.0"))
    checks = D.check_install()
    assert not [c for c in checks if c.status == FAIL]
    assert any(c.status == WARN and "1.0.0" in c.label for c in checks)


# --- nothing may raise ---------------------------------------------------------------------

def test_a_check_that_explodes_is_reported_not_raised():
    """This is what you run when things are already broken."""
    def boom():
        raise RuntimeError("disk on fire")

    out = D._safe(boom, "install")
    assert len(out) == 1 and out[0].status == FAIL
    assert "disk on fire" in out[0].detail


def test_run_checks_never_raises_even_with_everything_unset(monkeypatch):
    for var in ("LLM_PROVIDER", "LLM_MODEL", "ANTHROPIC_API_KEY", "TELEGRAM_BOT_TOKEN",
                "WHATSAPP_TOKEN", "AGRONAUT_ALLOWED_IDS", "PYTHONPATH"):
        monkeypatch.delenv(var, raising=False)
    checks = D.run_checks()
    assert checks, "no checks ran"
    assert all(isinstance(c, Check) for c in checks)


# --- the missing-corpus case, which is the worst failure the project can have ---------------

def test_a_missing_corpus_is_a_failure_not_a_warning(monkeypatch, tmp_path):
    """It answers every question with KNOWLEDGE_UNAVAILABLE while sounding fine, so it must
    not be reported as a soft warning."""
    from agronaut_agent import paths

    monkeypatch.setattr(paths, "corpus_root", lambda: tmp_path)   # no knowledge/ inside
    checks = D.check_knowledge()
    assert checks[0].status == FAIL
    assert "cite nothing" in checks[0].fix


def test_a_present_corpus_passes(monkeypatch, tmp_path):
    from agronaut_agent import paths

    (tmp_path / "knowledge").mkdir()
    for i in range(21):
        (tmp_path / "knowledge" / f"doc{i}.md").write_text("x")
    monkeypatch.setattr(paths, "corpus_root", lambda: tmp_path)
    assert D.check_knowledge()[0].status == OK


# --- the report contract -------------------------------------------------------------------

def test_exit_code_is_nonzero_only_when_something_failed():
    _, code = D.report([Check(OK, "fine"), Check(WARN, "hmm")])
    assert code == 0
    _, code = D.report([Check(OK, "fine"), Check(FAIL, "broken")])
    assert code == 1


def test_the_report_does_not_claim_the_advice_is_good():
    """A green doctor means the configuration is coherent. Saying more would undo the thing
    this project is careful about everywhere else."""
    text, _ = D.report([Check(OK, "fine")])
    assert "not the accuracy of the advice" in text
    assert "twin_validation.json" in text


def test_the_cli_exposes_doctor():
    from agronaut_agent.cli import _build_parser

    names = set()
    for a in _build_parser()._actions:
        if getattr(a, "choices", None):
            names.update(a.choices)
    assert "doctor" in names


@pytest.mark.parametrize("newer", [2, 5])
def test_a_future_database_schema_is_a_failure(monkeypatch, tmp_path, newer):
    import sqlite3

    from agronaut_agent import store

    db = tmp_path / "agronaut.sqlite3"
    store._Db(db)
    conn = sqlite3.connect(str(db))
    conn.execute(f"PRAGMA user_version = {store.SCHEMA_VERSION + newer}")
    conn.commit()
    conn.close()
    monkeypatch.setenv("AGRONAUT_DB", str(db))
    checks = D.check_database()
    assert checks[0].status == FAIL
    assert "newer than this build" in checks[0].label


def test_editable_detection_survives_the_cli_building_its_parser():
    """The bug this pins: `_build_parser()` puts the project root on sys.path, so
    `importlib.metadata` starts finding the checkout's legacy `agronaut.egg-info`. It carries
    no direct_url.json, so editability came back False and the shadowing check above silently
    never ran. Found by planting a real stale copy, not by a mock, because the mock passed.
    """
    from agronaut_agent import version_info
    from agronaut_agent.cli import _build_parser

    before = version_info._editable_target()
    _build_parser()
    after = version_info._editable_target()
    assert before == after, (
        "building the parser changed editable detection: "
        f"{before} -> {after}. _editable_target must scan every distribution, "
        "not trust whichever sys.path order surfaces first.")


def test_distributions_returns_every_visible_agronaut():
    """More than one is a real state and itself a symptom, so it must not be collapsed."""
    from agronaut_agent import version_info

    found = version_info.distributions()
    assert isinstance(found, list)
    for d in found:
        assert (d.metadata.get("Name") or "").lower() == "agronaut"


# --- what the model is entitled to claim ---------------------------------------------------

def test_the_validation_line_is_printed_whole():
    """It was cut at 88 characters, which stopped at "It beat both a flat and a tren" and hid
    the "0 of 7" after it: a record saying the twin lost to both baselines read like a win.
    The line doctor shows must be the line the record produces, count included."""
    from aqua_model import validation_status as vs

    first = vs.validation_lines()[0]
    [check] = D.check_validation_record()
    assert check.status == OK
    assert first in check.render()


# --- the Ollama context window (#181) ------------------------------------------------------

def test_a_context_window_smaller_than_the_prompt_is_a_warning(monkeypatch):
    """Ollama cuts an overflowing prompt without an error, so doctor is where it surfaces."""
    monkeypatch.setenv("AGRONAUT_OLLAMA_NUM_CTX", "4096")
    check = D._ollama_context_check()
    assert check.status == WARN
    assert "AGRONAUT_OLLAMA_NUM_CTX" in check.fix


def test_the_default_context_window_passes(monkeypatch):
    monkeypatch.delenv("AGRONAUT_OLLAMA_NUM_CTX", raising=False)
    check = D._ollama_context_check()
    assert check.status == OK and "32768" in check.label


# --- optional Phoenix tracing -----------------------------------------------------------------

@pytest.fixture
def phoenix_env(monkeypatch):
    for key in ("AGRONAUT_PHOENIX", "AGRONAUT_PHOENIX_ENDPOINT", "AGRONAUT_PHOENIX_ALLOW_REMOTE"):
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


def _libs(monkeypatch, present: bool):
    import importlib.util
    real = importlib.util.find_spec

    def spec(name, *a, **k):
        if name.startswith(("phoenix", "openinference")):
            if not present:
                raise ModuleNotFoundError(name)
            return object()
        return real(name, *a, **k)

    monkeypatch.setattr(importlib.util, "find_spec", spec)


def test_tracing_off_is_the_normal_state_and_passes(phoenix_env):
    (c,) = D.check_tracing()
    assert c.status == OK and "off" in c.label and "shapes only" in c.detail


def test_tracing_to_another_host_is_a_failure(phoenix_env):
    phoenix_env.setenv("AGRONAUT_PHOENIX", "on")
    phoenix_env.setenv("AGRONAUT_PHOENIX_ENDPOINT", "https://phoenix.example.com/v1/traces")
    (c,) = D.check_tracing()
    assert c.status == FAIL and "not this machine" in c.label


def test_tracing_on_without_the_libraries_is_a_failure_and_does_not_raise(phoenix_env):
    phoenix_env.setenv("AGRONAUT_PHOENIX", "on")
    _libs(phoenix_env, present=False)
    (c,) = D.check_tracing()
    assert c.status == FAIL and "agronaut[phoenix]" in c.fix


def test_tracing_on_with_the_server_down_warns_that_traces_are_dropped(phoenix_env):
    import urllib.request
    phoenix_env.setenv("AGRONAUT_PHOENIX", "on")
    _libs(phoenix_env, present=True)

    def refuse(*a, **k):
        raise OSError("connection refused")

    phoenix_env.setattr(urllib.request, "urlopen", refuse)
    (c,) = D.check_tracing()
    assert c.status == WARN and "dropped" in c.detail


def test_tracing_on_with_the_server_up_passes(phoenix_env):
    import contextlib
    import urllib.request
    phoenix_env.setenv("AGRONAUT_PHOENIX", "on")
    _libs(phoenix_env, present=True)
    phoenix_env.setattr(urllib.request, "urlopen",
                        lambda *a, **k: contextlib.nullcontext())
    (c,) = D.check_tracing()
    assert c.status == OK and "127.0.0.1:6006" in c.detail


def test_a_stale_scripts_package_beside_an_editable_install_is_flagged(monkeypatch, tmp_path):
    """The bug that hid the Quality page's answers report on 2026-10-04."""
    import sysconfig

    from agronaut_agent import version_info
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "__init__.py").write_text("")
    monkeypatch.setattr(sysconfig, "get_paths", lambda *a, **k: {"purelib": str(tmp_path)})
    monkeypatch.setattr(version_info, "current", lambda: _install())
    monkeypatch.setattr(version_info, "distributions", lambda: [])
    monkeypatch.delenv("PYTHONPATH", raising=False)
    stale = [c for c in D.check_install() if "stale `scripts`" in c.label]
    assert stale and stale[0].status == WARN and str(tmp_path / "scripts") in stale[0].fix
