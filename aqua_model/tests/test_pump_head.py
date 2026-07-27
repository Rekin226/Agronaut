"""Pump HEAD, not just flow. A pump is specified by two numbers — the flow it moves (L/h)
and the head it moves it against (m). The turnover flow was already sized; this quantifies
the head (static lift + friction) and the resulting electrical power, so the vertical-tower
"needs more lift" note becomes a real number a buyer can shop with.

Physics anchor: electrical power = ρ g Q H / efficiency. Same flow at 5× the head costs ~5×
the pump power — which is exactly why towers are not a free lunch, now shown numerically.
"""

import math

import pytest

from aqua_model import size_system, size_hydroponic_system, validate_design_input, \
    validate_hydroponic_input
from aqua_model import coefficients as C


def test_every_design_reports_head_and_power():
    out = size_system(validate_design_input("tilapia", "lettuce", 12, 27, 3000))
    assert out.pump_head_m > 0
    assert out.pump_power_w > 0


def test_head_is_static_lift_plus_friction():
    from aqua_model.system_types import get_system_type
    out = size_system(validate_design_input("tilapia", "lettuce", 12, 27, 3000, system_type="raft"))
    lift = get_system_type("raft").lift_height_m
    expected = round(lift * (1 + C.FRICTION_HEAD_FRACTION.value), 2)
    assert out.pump_head_m == pytest.approx(expected)


def test_tower_costs_more_power_despite_moving_less_water():
    tower = size_system(validate_design_input("tilapia", "lettuce", 12, 27, 3000,
                                              system_type="vertical_tower"))
    raft = size_system(validate_design_input("tilapia", "lettuce", 12, 27, 3000, system_type="raft"))
    # A tower holds less water, so it actually recirculates a SMALLER flow than a raft...
    assert tower.pump_turnover_lph < raft.pump_turnover_lph
    # ...yet it lifts that water much higher, so both head and running power are larger.
    # (This is the honest "towers are not a free lunch" signal, now quantified.)
    assert tower.pump_head_m > raft.pump_head_m
    assert tower.pump_power_w > raft.pump_power_w


def test_power_matches_the_hydraulic_formula():
    out = size_system(validate_design_input("tilapia", "lettuce", 20, 27, 6000,
                                            system_type="vertical_tower"))
    q_m3s = out.pump_turnover_lph / 1000.0 / 3600.0
    hydraulic_w = 1000.0 * 9.81 * q_m3s * out.pump_head_m
    expected = round(hydraulic_w / C.PUMP_EFFICIENCY.value, 1)
    assert out.pump_power_w == pytest.approx(expected, rel=1e-3)


def test_hydroponic_pump_also_reports_head_and_power():
    out = size_hydroponic_system(validate_hydroponic_input("lettuce", 15, 22, 4000,
                                                           system_type="vertical_tower"))
    assert out.pump_head_m > 0 and out.pump_power_w > 0


def test_head_and_power_are_cited_and_surfaced():
    out = size_system(validate_design_input("tilapia", "lettuce", 12, 27, 3000,
                                            system_type="vertical_tower"))
    names = " ".join(c.name for c in out.coefficients_used).lower()
    assert "lift" in names and "friction" in names and "efficien" in names
    bom = " ".join(str(i["spec"]).lower() for i in out.bill_of_materials)
    assert "head" in bom and ("w" in bom)  # pump spec now carries head + power


def test_serialization_shows_head_and_power():
    from agronaut_agent.serialize import serialize_design_output
    out = size_system(validate_design_input("tilapia", "lettuce", 12, 27, 3000,
                                            system_type="vertical_tower"))
    text = serialize_design_output(out).lower()
    assert "head" in text and "w" in text
