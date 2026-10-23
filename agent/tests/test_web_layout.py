"""The web app's shape after the 2026-10-04 tidy: four sections, the Assistant first when a
model works, Design otherwise, and nothing on screen that reads like debug output."""

import pathlib
import tomllib

import pytest

pytest.importorskip("streamlit.testing.v1")
from streamlit.testing.v1 import AppTest  # noqa: E402

from agent.calculator_ui import envelope_rows, nitrogen_summary  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
_APP = str(ROOT / "app.py")


def _no_model(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "no-such-provider")


def _modes(at):
    return next(r for r in at.sidebar.radio if r.label == "Mode")


def test_four_sections_and_design_first_when_no_model_works(monkeypatch):
    _no_model(monkeypatch)
    at = AppTest.from_file(_APP).run(timeout=60)
    assert not at.exception
    radio = _modes(at)
    assert list(radio.options)[:4] == ["Assistant", "Design", "My Twin", "Quality"]
    assert radio.value == "Design"
    assert any("Assistant is off" in str(c.value) for c in at.sidebar.caption)


def test_design_holds_both_tools_as_tabs(monkeypatch):
    _no_model(monkeypatch)
    at = AppTest.from_file(_APP).run(timeout=60)
    assert [t.label for t in at.tabs] == ["Size a system", "Find the best ratio"]
    # the tab label is the heading; the tools' own subheaders would repeat it
    assert "Design Calculator" not in [s.value for s in at.subheader]
    assert "Optimize Ratio" not in [s.value for s in at.subheader]


def test_the_title_is_shown_once_in_the_sidebar(monkeypatch):
    _no_model(monkeypatch)
    at = AppTest.from_file(_APP).run(timeout=60)
    assert not at.title
    assert any("Agronaut" in str(m.value) for m in at.sidebar.markdown)


def test_streamlits_developer_toolbar_is_hidden():
    cfg = tomllib.loads((ROOT / ".streamlit" / "config.toml").read_text())
    assert cfg["client"]["toolbarMode"] == "minimal"


def test_the_operating_envelope_reads_as_rows_not_json():
    rows = envelope_rows({"ph_target": [6.0, 7.0], "dissolved_oxygen_min_mg_l": 5.0,
                          "something_new": "x"})
    assert rows[0] == {"reading": "pH", "": "target", "value": "6–7"}
    assert rows[1]["reading"] == "Dissolved oxygen (mg/L)" and rows[1]["value"] == 5.0
    assert rows[2]["reading"] == "something new"          # unknown keys still shown


def test_the_nitrogen_check_is_one_sentence_with_its_verdict():
    line = nitrogen_summary({"n_implied_area_m2": 6.51, "frr_grow_area_m2": 6.0,
                             "disagreement_fraction": 0.084, "agrees": True, "flag": None})
    assert "6.51 m²" in line and "6 m²" in line and "agree" in line and "8% apart" in line
    bad = nitrogen_summary({"n_implied_area_m2": 2, "frr_grow_area_m2": 6,
                            "disagreement_fraction": 0.67, "agrees": False,
                            "flag": "Undersized biofilter."})
    assert "DISAGREE" in bad and "Undersized biofilter." in bad


def test_a_sized_design_shows_no_raw_json(monkeypatch):
    at = AppTest.from_string(
        "from agent.calculator_ui import render_calculator; render_calculator()"
    ).run(timeout=60)
    at.button[0].click().run(timeout=60)
    assert not at.exception
    assert not at.json
