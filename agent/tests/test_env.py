"""Where configuration comes from, for a checkout and for a `pip install agronaut`.

The loader used to look only at the project root, computed from its own file location.
Inside an installed wheel that is `site-packages/.env` — a path nobody creates — so anyone
who installed from PyPI could not configure a bot token at all. These tests pin the search
order that fixes it.
"""

import os

import pytest

from agent import env as E


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for k in ("AGRONAUT_ENV_FILE", "XDG_CONFIG_HOME", "PROBE_KEY"):
        monkeypatch.delenv(k, raising=False)


def _write(path, body="PROBE_KEY=from_" + "x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    return path


def test_an_explicit_path_wins(tmp_path, monkeypatch):
    """A service unit or a second deployment needs to say exactly which file to use."""
    f = _write(tmp_path / "custom.env", "PROBE_KEY=explicit")
    monkeypatch.setenv("AGRONAUT_ENV_FILE", str(f))
    assert E.load_env() == f
    assert os.environ["PROBE_KEY"] == "explicit"


def test_the_working_directory_is_searched(tmp_path, monkeypatch):
    """What someone expects after `cd ~/my-farm && agronaut bot`."""
    monkeypatch.chdir(tmp_path)
    _write(tmp_path / ".env", "PROBE_KEY=cwd")
    assert E.load_env() == tmp_path / ".env"
    assert os.environ["PROBE_KEY"] == "cwd"


def test_the_user_config_dir_is_searched(tmp_path, monkeypatch):
    """The answer for `pip install agronaut`: no checkout, no project root."""
    monkeypatch.chdir(tmp_path)                       # nothing in the working directory
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    f = _write(tmp_path / "cfg" / "agronaut" / ".env", "PROBE_KEY=userconfig")
    assert E.load_env() == f
    assert os.environ["PROBE_KEY"] == "userconfig"


def test_the_working_directory_beats_the_user_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    _write(tmp_path / "cfg" / "agronaut" / ".env", "PROBE_KEY=userconfig")
    _write(tmp_path / ".env", "PROBE_KEY=cwd")
    E.load_env()
    assert os.environ["PROBE_KEY"] == "cwd"


def test_a_real_environment_variable_always_wins(tmp_path, monkeypatch):
    """A shell export or a systemd unit must override the file, not fight it."""
    monkeypatch.chdir(tmp_path)
    _write(tmp_path / ".env", "PROBE_KEY=fromfile")
    monkeypatch.setenv("PROBE_KEY", "fromshell")
    E.load_env()
    assert os.environ["PROBE_KEY"] == "fromshell"


def test_no_env_anywhere_is_not_an_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setattr(E, "_PROJECT_ROOT", tmp_path / "nowhere")
    assert E.load_env() is None


def test_an_unreadable_candidate_does_not_stop_the_bot(tmp_path, monkeypatch):
    """A permissions problem on a stale path must not prevent startup; fall through."""
    monkeypatch.chdir(tmp_path)
    bad = tmp_path / ".env"
    bad.mkdir()                                        # a directory, not a file
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    f = _write(tmp_path / "cfg" / "agronaut" / ".env", "PROBE_KEY=recovered")
    assert E.load_env() == f
    assert os.environ["PROBE_KEY"] == "recovered"


def test_the_search_order_is_documented_and_deduplicated(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    paths = E.candidates()
    assert len(paths) == len(set(map(str, paths))), "a candidate is listed twice"
    assert paths[0] == tmp_path / ".env"
    assert any("agronaut" in str(p) and ".config" in str(p) or "cfg" in str(p)
               for p in paths)
