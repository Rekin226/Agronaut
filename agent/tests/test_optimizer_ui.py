"""Headless smoke test of the Optimize Ratio Streamlit view."""

import pytest

pytest.importorskip("streamlit.testing.v1")
from streamlit.testing.v1 import AppTest  # noqa: E402

_APP = "from agent.optimizer_ui import render_optimizer; render_optimizer()"


def test_optimizer_renders_form():
    at = AppTest.from_string(_APP).run(timeout=30)
    assert not at.exception
    assert "Optimize Ratio" in [s.value for s in at.subheader]
    assert len(at.number_input) == 3
    assert len(at.selectbox) == 1            # objective
    assert len(at.multiselect) == 2          # fish + crop palettes


def test_optimizer_runs_and_reports_a_best_ratio():
    at = AppTest.from_string(_APP).run(timeout=30)
    at.number_input[0].set_value(10.0)       # grow area
    at.number_input[1].set_value(28.0)       # temperature
    at.number_input[2].set_value(5000.0)     # water budget
    at.button[0].click()
    at.run(timeout=30)

    assert not at.exception
    successes = " ".join(s.value for s in at.success)
    assert "Best ratio" in successes
    # A delta-bearing metric (improvement vs even split) is shown.
    assert len(at.metric) >= 1


def test_optimizer_warns_when_no_feasible_design():
    at = AppTest.from_string(_APP).run(timeout=30)
    at.number_input[0].set_value(80.0)       # large area
    at.number_input[1].set_value(28.0)
    at.number_input[2].set_value(1.0)        # impossible budget
    at.button[0].click()
    at.run(timeout=30)

    assert not at.exception
    assert any("No feasible design" in w.value for w in at.warning)
