"""Headless smoke test of the Design Calculator Streamlit view.

Uses Streamlit's AppTest to render the calculator and drive the form end to end, without a
browser or the chat stack. Skips cleanly if streamlit's testing harness is unavailable.
"""

import pytest

pytest.importorskip("streamlit.testing.v1")
from streamlit.testing.v1 import AppTest  # noqa: E402

_APP = "from agent.calculator_ui import render_calculator; render_calculator()"


def test_calculator_renders_form():
    at = AppTest.from_string(_APP).run(timeout=30)
    assert not at.exception
    assert "Design Calculator" in [s.value for s in at.subheader]
    assert [sb.label for sb in at.selectbox] == ["Fish species", "Crop"]
    assert len(at.number_input) == 3


def test_calculator_sizes_a_system_on_submit():
    at = AppTest.from_string(_APP).run(timeout=30)
    at.selectbox[0].set_value("tilapia")
    at.selectbox[1].set_value("lettuce")
    at.number_input[0].set_value(6.0)    # grow area
    at.number_input[1].set_value(26.0)   # temperature
    at.number_input[2].set_value(200.0)  # water budget
    at.button[0].click()
    at.run(timeout=30)

    assert not at.exception
    assert "Feasible design." in [s.value for s in at.success]
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Feed"] == "360 g/day"
    assert "head" in metrics["Fish"]


def test_calculator_shows_reality_check_vs_real_ponds():
    at = AppTest.from_string(_APP).run(timeout=30)
    at.selectbox[0].set_value("tilapia")
    at.selectbox[1].set_value("lettuce")
    at.number_input[0].set_value(6.0)
    at.number_input[1].set_value(26.0)
    at.number_input[2].set_value(200.0)
    at.button[0].click()
    at.run(timeout=30)

    assert not at.exception
    text = " ".join(m.value for m in at.markdown)
    # Real ponds ran cooler than the tilapia optimum — the check must surface that, not hide it.
    assert "Water temperature" in text
    assert "below target" in text


def test_calculator_shows_basil_frr_calibration():
    at = AppTest.from_string(_APP).run(timeout=30)
    at.selectbox[0].set_value("tilapia")
    at.selectbox[1].set_value("basil")
    at.number_input[0].set_value(6.0)
    at.number_input[1].set_value(26.0)
    at.number_input[2].set_value(200.0)
    at.button[0].click()
    at.run(timeout=30)

    assert not at.exception
    text = " ".join(m.value for m in at.markdown)
    # Basil FRR is now calibrated to 85 (mid the UVI band), so it reads in-range.
    assert "Basil feeding-rate ratio" in text
    assert "within empirical range" in text


def test_calculator_flags_infeasible_water_budget():
    at = AppTest.from_string(_APP).run(timeout=30)
    at.selectbox[0].set_value("tilapia")
    at.selectbox[1].set_value("lettuce")
    at.number_input[0].set_value(50.0)   # large area
    at.number_input[1].set_value(28.0)
    at.number_input[2].set_value(10.0)   # tiny water budget
    at.button[0].click()
    at.run(timeout=30)

    assert not at.exception
    warnings = " ".join(w.value for w in at.warning)
    assert "binding constraint" in warnings.lower() or "water" in warnings.lower()
