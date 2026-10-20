"""The private Agronaut Twin add-on is optional: the public app works with or without it."""

import importlib.util
import sys

import pytest

pytest.importorskip("streamlit.testing.v1")
from streamlit.testing.v1 import AppTest  # noqa: E402

_APP = "import sys; sys.modules.pop('app', None)\nimport app; app.main()"


@pytest.fixture
def without_addon(monkeypatch):
    """Behave as if the add-on were not installed (restored after the test)."""
    for name in ("agronaut_twin", "agronaut_twin.ui", "agronaut_twin.ui.studio",
                 "agronaut_twin.bridge"):
        monkeypatch.setitem(sys.modules, name, None)


def _modes(at):
    return next(r for r in at.sidebar.radio if r.label == "Mode").options


def test_public_app_without_the_addon_is_unchanged(without_addon):
    at = AppTest.from_string(_APP).run(timeout=60)
    assert not at.exception
    assert "Digital Twin Studio" not in _modes(at)
    assert "Design" in _modes(at)


def test_calculator_runs_without_the_addon(without_addon):
    at = AppTest.from_string(
        "from agent.calculator_ui import render_calculator; render_calculator()"
    ).run(timeout=60)
    at.button[0].click().run(timeout=60)
    assert not at.exception
    assert "twin_studio_sized_design" not in at.session_state


@pytest.mark.skipif(
    importlib.util.find_spec("agronaut_twin") is None, reason="private add-on not installed"
)
def test_installed_addon_adds_the_studio_and_receives_sized_designs():
    at = AppTest.from_string(_APP).run(timeout=60)
    assert not at.exception
    assert "Digital Twin Studio" in _modes(at)
    calc = AppTest.from_string(
        "from agent.calculator_ui import render_calculator; render_calculator()"
    ).run(timeout=60)
    calc.button[0].click().run(timeout=60)
    assert not calc.exception
    assert "twin_studio_sized_design" in calc.session_state
