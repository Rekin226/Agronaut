"""CRITICAL — the acceptance gate. Reproduce the founder's REAL running system.

This test is the line between "a model that runs" and "a model you can trust." It stays
SKIPPED until the founder fills in REAL_SYSTEM from the one-sheet calibration record of the
actual Taiwan system. The moment those numbers exist, this runs and must pass within the
agreed tolerances (±15% on feed/day and grow area). Nitrate is intentionally NOT gated here
(too sampling-dependent) — it belongs in a sanity band, not an acceptance test.

To activate: replace REAL_SYSTEM = None with the measured values below.
"""

import math

import pytest

from aqua_model import size_system
from aqua_model.validate import validate_design_input

# Fill this from the calibration sheet to activate the gate, e.g.:
# REAL_SYSTEM = {
#     "fish_species": "tilapia",
#     "crop": "lettuce",
#     "grow_area_m2": 6.0,
#     "temperature_c": 26.0,
#     "water_budget_lpd": 200.0,
#     "measured_feed_g_per_day": 360.0,   # what you actually feed
#     "measured_grow_area_m2": 6.0,       # actual planted area (sanity cross-check)
# }
REAL_SYSTEM = None

FEED_TOLERANCE = 0.15   # ±15%
AREA_TOLERANCE = 0.15   # ±15%


@pytest.mark.skipif(REAL_SYSTEM is None, reason="Awaiting founder's calibration sheet (Taiwan system).")
def test_model_reproduces_real_system_within_tolerance():
    di = validate_design_input(
        fish_species=REAL_SYSTEM["fish_species"],
        crop=REAL_SYSTEM["crop"],
        grow_area_m2=REAL_SYSTEM["grow_area_m2"],
        temperature_c=REAL_SYSTEM["temperature_c"],
        water_budget_lpd=REAL_SYSTEM["water_budget_lpd"],
    )
    out = size_system(di)

    feed_err = abs(out.feed_g_per_day - REAL_SYSTEM["measured_feed_g_per_day"]) / REAL_SYSTEM["measured_feed_g_per_day"]
    assert feed_err <= FEED_TOLERANCE, (
        f"feed/day off by {feed_err:.0%}: model {out.feed_g_per_day} vs "
        f"measured {REAL_SYSTEM['measured_feed_g_per_day']}"
    )

    area_err = abs(out.grow_area_m2 - REAL_SYSTEM["measured_grow_area_m2"]) / REAL_SYSTEM["measured_grow_area_m2"]
    assert area_err <= AREA_TOLERANCE


@pytest.mark.skipif(REAL_SYSTEM is None, reason="Awaiting founder's calibration sheet (Taiwan system).")
def test_real_system_is_feasible_or_flags_why_not():
    di = validate_design_input(
        fish_species=REAL_SYSTEM["fish_species"],
        crop=REAL_SYSTEM["crop"],
        grow_area_m2=REAL_SYSTEM["grow_area_m2"],
        temperature_c=REAL_SYSTEM["temperature_c"],
        water_budget_lpd=REAL_SYSTEM["water_budget_lpd"],
    )
    out = size_system(di)
    # A real, running system should not be flagged infeasible; if it is, the warning
    # must explain why (a signal our coefficients need calibration).
    assert out.feasible or out.warnings
