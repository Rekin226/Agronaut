"""The `agronaut` command — one front door over every entrypoint the project ships.

The dispatcher owns routing only: each subcommand must reach the real callable that
already implements it (the REPL, the deterministic sizing engine, the Streamlit app, the
bot, the maintainer tools). The trust gate has to survive the new front door — a bad
argument still exits non-zero rather than producing a wrong design.
"""

import contextlib
import io
import pathlib
import sys

import pytest

from agronaut_agent import cli

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _run(argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cli.main(argv)
    return code, buf.getvalue()


# --- chat: the bare command is the REPL -------------------------------------------------

def test_bare_command_starts_the_chat_repl(monkeypatch):
    from agronaut_agent import core
    called = []
    monkeypatch.setattr(core, "_repl", lambda: called.append(True))
    assert cli.main([]) == 0
    assert called == [True]


def test_chat_subcommand_starts_the_chat_repl(monkeypatch):
    from agronaut_agent import core
    called = []
    monkeypatch.setattr(core, "_repl", lambda: called.append(True))
    assert cli.main(["chat"]) == 0
    assert called == [True]


# --- sizing: the deterministic engine, reached through the top-level command ------------

def test_size_prints_a_cited_feasible_design():
    code, out = _run(["size", "--fish", "tilapia", "--crop", "lettuce",
                      "--area", "12", "--temp", "27", "--water", "3000"])
    assert code == 0
    assert "FEASIBLE" in out
    assert "source:" in out and "NOT modeled" in out


def test_size_hydro_prints_the_nutrient_target():
    code, out = _run(["size-hydro", "--crop", "lettuce",
                      "--area", "10", "--temp", "22", "--water", "500"])
    assert code == 0
    assert "hydroponic" in out.lower() and "EC" in out


def test_optimize_prints_the_best_ratio():
    code, out = _run(["optimize", "--area", "10", "--temp", "28", "--water", "5000",
                      "--objective", "food"])
    assert code == 0
    assert "Best ratio" in out


def test_list_shows_species_and_crops():
    code, out = _run(["list"])
    assert code == 0
    assert "tilapia" in out and "lettuce" in out


# The gate has to hold on EVERY sizing command, not just the one that was easy to test.
# `optimize` shipped with no gate at all: `--area -5` returned a confident "Best ratio" with
# negative yields at exit 0, and `--temp nan` scored identically to an optimal temperature.
# Exit code is asserted exactly: setuptools' console script does sys.exit(main()), and
# sys.exit(None) exits 0 — so `!= 0` would pass for a command that silently stopped
# returning its status, and an agent shelling out would read a rejected design as success.
_HOSTILE = [
    ("size", ["--fish", "shark", "--crop", "lettuce", "--area", "12", "--temp", "27",
              "--water", "3000"], "unknown fish_species"),
    ("size", ["--fish", "tilapia", "--crop", "lettuce", "--area", "-5", "--temp", "27",
              "--water", "3000"], "grow_area_m2"),
    ("size-hydro", ["--crop", "unobtainium", "--area", "10", "--temp", "22",
                    "--water", "500"], "unknown crop"),
    ("size-hydro", ["--crop", "lettuce", "--area", "10", "--temp", "nan",
                    "--water", "500"], "temperature_c"),
    ("optimize", ["--area", "-5", "--temp", "28", "--water", "5000"], "grow_area_m2"),
    ("optimize", ["--area", "10", "--temp", "nan", "--water", "5000"], "temperature_c"),
    ("optimize", ["--area", "10", "--temp", "28", "--water", "inf"], "water_budget_lpd"),
]


@pytest.mark.parametrize("cmd,args,expected", _HOSTILE,
                         ids=[f"{c}:{e}" for c, _, e in _HOSTILE])
def test_trust_gate_survives_the_new_front_door(cmd, args, expected):
    code, out = _run([cmd, *args])
    assert code == 2, f"{cmd} accepted {args} and exited {code}"
    assert "VALIDATION_FAILED" in out
    assert expected in out


def test_unknown_objective_is_rejected_not_defaulted():
    code, out = _run(["optimize", "--area", "10", "--temp", "28", "--water", "5000",
                      "--objective", "maximise_vibes"])
    assert code == 2
    assert "maximise_vibes" in out


def test_canonical_skill_names_still_work():
    # The portable skill CLI's own names stay valid at the top level, so docs written
    # against either surface keep working.
    code, out = _run(["size-aquaponics", "--fish", "tilapia", "--crop", "lettuce",
                      "--area", "12", "--temp", "27", "--water", "3000"])
    assert code == 0 and "FEASIBLE" in out


def test_design_is_an_alias_for_size_not_a_second_implementation():
    # `design` is the name issue #20 asked for. It must reach the same callable rather
    # than a parallel copy, or the two surfaces drift and only one gets fixed.
    args = ["--fish", "tilapia", "--crop", "lettuce", "--area", "12", "--temp", "27",
            "--water", "3000"]
    design_code, design_out = _run(["design", *args])
    size_code, size_out = _run(["size", *args])
    assert design_code == size_code == 0
    assert design_out == size_out


def test_design_rejects_an_unknown_species_non_zero():
    code, out = _run(["design", "--fish", "shark", "--crop", "lettuce", "--area", "12",
                      "--temp", "27", "--water", "3000"])
    assert code == 2
    assert "VALIDATION_FAILED" in out and "shark" in out


@pytest.mark.parametrize("system_type,expected", [
    ("raft", "raft"),
    ("nft", "NFT"),
    ("media_bed", "media bed"),
    ("vertical_tower", "vertical tower"),
])
def test_system_type_reaches_the_model(system_type, expected):
    # Every method the model knows is selectable from the CLI, not just from the web form.
    code, out = _run(["size", "--fish", "tilapia", "--crop", "lettuce", "--area", "12",
                      "--temp", "27", "--water", "3000", "--system-type", system_type])
    assert code == 0
    assert expected.lower() in out.lower()


def test_unknown_system_type_is_rejected_not_defaulted():
    with pytest.raises(SystemExit) as excinfo:
        _run(["size", "--fish", "tilapia", "--crop", "lettuce", "--area", "12",
              "--temp", "27", "--water", "3000", "--system-type", "hydroloop"])
    assert excinfo.value.code == 2


# --- the long-running services ----------------------------------------------------------

def test_web_launches_streamlit_on_the_real_app_file(monkeypatch):
    seen = {}

    def _fake_call(argv):
        seen["argv"] = argv
        return 0

    monkeypatch.setattr(cli.subprocess, "call", _fake_call)
    assert cli.main(["web"]) == 0
    argv = seen["argv"]
    # Launched through the running interpreter, not a bare "streamlit": an installed
    # command is routinely invoked by absolute path, with the venv's bin/ nowhere on PATH.
    assert argv[:3] == [sys.executable, "-m", "streamlit"]
    assert argv[3] == "run"
    app = pathlib.Path(argv[4])
    assert app.is_absolute() and app.is_file() and app.name == "app.py"


def test_web_forwards_extra_flags_to_streamlit(monkeypatch):
    # `agronaut web --server.headless=true` has to reach Streamlit; a front door that
    # swallows the server's own flags is worse than the command it replaces.
    seen = {}

    def _fake_call(argv):
        seen["argv"] = argv
        return 0

    monkeypatch.setattr(cli.subprocess, "call", _fake_call)
    assert cli.main(["web", "--server.headless=true", "--server.port=9000"]) == 0
    assert seen["argv"][-2:] == ["--server.headless=true", "--server.port=9000"]


def test_bot_runs_the_telegram_entrypoint(monkeypatch):
    import bot
    called = []
    monkeypatch.setattr(bot, "main", lambda: called.append(True))
    assert cli.main(["bot"]) == 0
    assert called == [True]


# --- maintainer commands ----------------------------------------------------------------

def test_review_runs_the_community_review_loop(monkeypatch):
    from agronaut_agent import review
    called = []
    monkeypatch.setattr(review, "main", lambda: called.append(True))
    assert cli.main(["review"]) == 0
    assert called == [True]


def test_analytics_runs_the_usage_summary(monkeypatch):
    from agronaut_agent import analytics
    called = []
    monkeypatch.setattr(analytics, "main", lambda: called.append(True) or 0)
    assert cli.main(["analytics"]) == 0
    assert called == [True]


# --- misuse -----------------------------------------------------------------------------

def test_unknown_subcommand_exits_nonzero():
    with pytest.raises(SystemExit) as err:
        cli.main(["harvest"])
    assert err.value.code != 0
