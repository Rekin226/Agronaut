"""The UI<->model seam: option lists and the validated entry point (no streamlit needed)."""

import pytest

from agent import facts
from aqua_model.types import DesignInput


def test_option_lists_match_the_databases():
    assert "tilapia" in facts.available_species()
    assert "lettuce" in facts.available_crops()
    assert facts.available_species() == sorted(facts.available_species())


def test_design_from_form_returns_validated_input():
    di = facts.design_from_form(
        fish_species="tilapia", crop="lettuce", grow_area_m2=6.0,
        temperature_c=26.0, water_budget_lpd=200.0,
    )
    assert isinstance(di, DesignInput)
    assert di.fish_species == "tilapia"


def test_design_from_form_rejects_bad_input():
    with pytest.raises(facts.ValidationError):
        facts.design_from_form(
            fish_species="dragon", crop="lettuce", grow_area_m2=6.0,
            temperature_c=26.0, water_budget_lpd=200.0,
        )
