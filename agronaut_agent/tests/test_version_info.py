"""Which build is this, and how do you move it forward.

Written after an evening lost to the question. A maintainer with both a checkout and a
`pip install agronaut` ran `agronaut setup`, got the old wizard, and had no way to find out
why: `pip show` reported 1.0.0 for the stale copy in site-packages and for the live checkout
alike, and the CLI would not say which files it had loaded. The version number was the thing
that lied, so these tests are mostly about the parts printed next to it.
"""

from pathlib import Path

from agronaut_agent import version_info as V

CHECKOUT = Path("/home/grower/Agronaut")


def _install(version="1.0.0", editable=None):
    return V.Install(version=version, code_dir=CHECKOUT / "agronaut_agent",
                     editable_from=editable)


# --- comparing versions --------------------------------------------------------------------

def test_a_later_minor_is_behind():
    assert V.compare("1.0.0", "1.1.0") == "behind"


def test_ten_beats_nine():
    """String comparison would call 1.10.0 older than 1.9.0 and offer a downgrade."""
    assert V.compare("1.9.0", "1.10.0") == "behind"
    assert V.compare("1.10.0", "1.9.0") == "ahead"


def test_a_checkout_ahead_of_the_release_is_not_behind():
    """Normal for a maintainer, and it must not be reported as an available update."""
    assert V.compare("1.2.0", "1.1.0") == "ahead"


def test_equal_is_current():
    assert V.compare("1.1.0", "1.1.0") == "current"


# --- what --version prints -----------------------------------------------------------------

def test_the_path_is_printed_not_just_the_number():
    """The number alone is what misled everyone. The path is the answer."""
    out = _install().render()
    assert "1.0.0" in out
    assert str(CHECKOUT / "agronaut_agent") in out


def test_an_editable_install_says_so_and_says_where_updates_come_from():
    out = _install(editable=CHECKOUT).render()
    assert "editable" in out
    assert str(CHECKOUT) in out
    assert "git pull" in out


def test_a_normal_install_does_not_claim_to_be_editable():
    assert "editable" not in _install().render()


# --- agronaut update -----------------------------------------------------------------------

def test_update_refuses_to_overwrite_an_editable_install(monkeypatch, capsys):
    """pip would replace the checkout link with a released copy and silently detach the
    command from the code being edited. That is the exact failure this module exists for."""
    called = []
    monkeypatch.setattr(V, "current", lambda: _install(editable=CHECKOUT))
    monkeypatch.setattr(V, "latest_on_pypi", lambda timeout=0: ("1.1.0", ""))
    monkeypatch.setattr("subprocess.call", lambda *a, **k: called.append(a) or 0)

    assert V.run_update() == 0
    out = capsys.readouterr().out
    assert not called, "pip was run against an editable install"
    assert f"git -C {CHECKOUT} pull" in out
    assert "--force-reinstall" in out, "no escape hatch offered for leaving the checkout"


def test_update_installs_for_a_normal_install(monkeypatch, capsys):
    called = []
    monkeypatch.setattr(V, "current", lambda: _install())
    monkeypatch.setattr(V, "latest_on_pypi", lambda timeout=0: ("1.1.0", ""))
    monkeypatch.setattr("subprocess.call", lambda cmd, *a, **k: called.append(cmd) or 0)

    assert V.run_update() == 0
    assert called and called[0][:2] == V.upgrade_command()[:2]
    assert "--upgrade" in called[0]
    assert "1.1.0" in capsys.readouterr().out


def test_check_only_reports_without_installing(monkeypatch, capsys):
    called = []
    monkeypatch.setattr(V, "current", lambda: _install())
    monkeypatch.setattr(V, "latest_on_pypi", lambda timeout=0: ("1.1.0", ""))
    monkeypatch.setattr("subprocess.call", lambda *a, **k: called.append(a) or 0)

    assert V.run_update(check_only=True) == 0
    assert not called
    assert "1.0.0 -> 1.1.0" in capsys.readouterr().out


def test_a_build_ahead_of_pypi_is_not_offered_a_downgrade(monkeypatch, capsys):
    called = []
    monkeypatch.setattr(V, "current", lambda: _install(version="1.2.0"))
    monkeypatch.setattr(V, "latest_on_pypi", lambda timeout=0: ("1.1.0", ""))
    monkeypatch.setattr("subprocess.call", lambda *a, **k: called.append(a) or 0)

    assert V.run_update() == 0
    assert not called
    assert "newer than" in capsys.readouterr().out


def test_no_network_is_reported_not_swallowed(monkeypatch, capsys):
    monkeypatch.setattr(V, "current", lambda: _install())
    monkeypatch.setattr(V, "latest_on_pypi",
                        lambda timeout=0: (None, "could not reach PyPI: timed out"))
    assert V.run_update() == 1
    assert "could not reach PyPI" in capsys.readouterr().out


def test_the_upgrade_uses_this_interpreter(monkeypatch):
    """A bare `pip` would upgrade whichever Python is first on PATH, which is how someone
    ends up running the upgrade and seeing no change at all."""
    import sys

    assert V.upgrade_command()[0] == sys.executable
    assert V.upgrade_command()[1:3] == ["-m", "pip"]


# --- wired into the CLI --------------------------------------------------------------------

def test_the_cli_exposes_version_and_update():
    from agronaut_agent.cli import _build_parser

    parser = _build_parser()
    flags = {o for a in parser._actions for o in a.option_strings}
    assert "--version" in flags
    names = set()
    for a in parser._actions:
        if getattr(a, "choices", None):
            names.update(a.choices)
    assert "update" in names


def test_version_output_keeps_its_line_breaks(capsys):
    """argparse's built-in version action re-wraps the string through the help formatter and
    turns the aligned paths into a paragraph, which is why this uses its own action."""
    import pytest

    from agronaut_agent.cli import _build_parser

    with pytest.raises(SystemExit):
        _build_parser().parse_args(["--version"])
    out = capsys.readouterr().out
    assert out.count("\n") >= 3, f"version output was flattened: {out!r}"
    assert "code" in out and "config" in out


# --- the stale-metadata bug this module was written to prevent, and then had ----------------

def _checkout(tmp_path, version="1.1.0"):
    (tmp_path / "pyproject.toml").write_text(
        f'[project]\nname = "agronaut"\nversion = "{version}"\n')
    return tmp_path


def test_an_editable_install_reports_the_checkout_not_the_frozen_metadata(tmp_path,
                                                                          monkeypatch):
    """An editable install writes its dist-info once and never again, so bumping pyproject
    leaves `importlib.metadata.version` answering a question about the past. Shipped as
    1.1.0, this reported 1.0.0 on the machine the release was cut from."""
    root = _checkout(tmp_path, "1.1.0")
    monkeypatch.setattr(V, "_editable_target", lambda: root)
    monkeypatch.setattr("importlib.metadata.version", lambda p: "1.0.0")

    install = V.current()
    assert install.version == "1.1.0", "reported the frozen metadata, not the running code"
    assert install.recorded_version == "1.0.0"
    assert "pip still records 1.0.0" in install.render()


def test_no_drift_note_when_the_metadata_is_current(tmp_path, monkeypatch):
    root = _checkout(tmp_path, "1.1.0")
    monkeypatch.setattr(V, "_editable_target", lambda: root)
    monkeypatch.setattr("importlib.metadata.version", lambda p: "1.1.0")

    install = V.current()
    assert install.version == "1.1.0"
    assert install.recorded_version is None
    assert "pip still records" not in install.render()


def test_a_normal_install_still_trusts_its_metadata(monkeypatch):
    """Only editable installs go stale. A wheel's metadata is written from the same build."""
    monkeypatch.setattr(V, "_editable_target", lambda: None)
    monkeypatch.setattr("importlib.metadata.version", lambda p: "1.1.0")
    assert V.current().version == "1.1.0"
    assert V.current().recorded_version is None


def test_an_unreadable_checkout_falls_back_rather_than_failing(tmp_path, monkeypatch):
    """No pyproject, or a dynamic version. A diagnostic must never be what breaks."""
    monkeypatch.setattr(V, "_editable_target", lambda: tmp_path)   # empty dir
    monkeypatch.setattr("importlib.metadata.version", lambda p: "1.0.0")
    assert V.current().version == "1.0.0"


def test_update_compares_the_running_version_not_the_stale_one(tmp_path, monkeypatch, capsys):
    """The visible consequence: `agronaut update` offered an upgrade that was installed."""
    root = _checkout(tmp_path, "1.1.0")
    monkeypatch.setattr(V, "_editable_target", lambda: root)
    monkeypatch.setattr("importlib.metadata.version", lambda p: "1.0.0")
    monkeypatch.setattr(V, "latest_on_pypi", lambda timeout=0: ("1.1.0", ""))

    assert V.run_update() == 0
    out = capsys.readouterr().out
    assert "Up to date" in out
    assert "1.0.0 -> 1.1.0" not in out, "offered an upgrade the checkout already has"


def test_the_same_install_seen_twice_is_not_two_installs(monkeypatch):
    """A site-packages directory can appear more than once on sys.path, and
    `importlib.metadata.distributions()` then yields the same install repeatedly. Reported
    raw, doctor said "2 installs of agronaut are visible at once" and printed one path twice,
    sending a reader hunting for a conflict that did not exist."""
    class _D:
        def __init__(self, path):
            self._path = path
            self.metadata = {"Name": "agronaut"}

    same = "/usr/lib/python3/site-packages/agronaut-1.1.0.dist-info"
    monkeypatch.setattr("importlib.metadata.distributions",
                        lambda: [_D(same), _D(same), _D("/other/agronaut.egg-info")])
    found = V.distributions()
    assert len(found) == 2, [str(d._path) for d in found]
    assert len({str(d._path) for d in found}) == 2
