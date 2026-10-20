"""Independent mass-balance checks and strict mineral-label nutrient boundaries."""

from dataclasses import replace

import pytest

from agro_model.nutrition import (
    FertilizerProduct,
    NutrientTarget,
    NutritionBudget,
    ProductAnalysis,
    account_products,
    evaluate_nutrition,
)


def spec():
    target = NutrientTarget(60, 20, 40, "remaining_application", "Synthetic target", "Illustrative, no regional recommendation")
    dry = FertilizerProduct("dry", "Synthetic 10-10-20", "mineral", 100, "kg", ProductAnalysis(10, 10, 20, "mass_percent", "Synthetic label"))
    liquid = FertilizerProduct("liquid", "Synthetic liquid 20-0-0", "mineral", 100, "L",
        ProductAnalysis(20, 0, 0, "mass_percent", "Synthetic label"), 1.25, "Synthetic density")
    return NutritionBudget(5000, "tomato", "planning", "2026-07-01", "2026-07-31", target, (dry, liquid))


def test_label_mass_and_liquid_density_accounting_is_not_a_feasibility_recommendation():
    out = evaluate_nutrition(spec())
    assert out["area_ha"] == 0.5
    assert out["products"][1]["mass_kg"] == 125
    # 100 kg at 10% N + 100 L at 1.25 kg/L and 20% N = 35 kg N over 0.5 ha.
    assert out["nutrients"]["n"]["declared_total_kg"] == 35
    assert out["nutrients"]["n"]["declared_kg_ha"] == 70
    assert out["nutrients"]["n"]["target_kg"] == 30
    assert out["nutrients"]["n"]["excess_kg"] == 5
    assert out["nutrients"]["n"]["shortfall_kg"] == 0
    assert out["nutrients"]["p2o5"]["declared_total_kg"] == 10
    assert out["nutrients"]["k2o"]["declared_total_kg"] == 20
    assert out["nutrients"]["p2o5"]["supplied_minus_target_kg"] == 0
    assert out["missing_inputs"] == [] and out["feasible"] is None


def test_unknown_liquid_density_leaves_mass_and_nonzero_nutrients_unknown_but_preserves_explicit_zero():
    data = spec()
    liquid = replace(data.products[1], density_kg_l=None, density_source=None)
    out = evaluate_nutrition(replace(data, products=(data.products[0], liquid)))
    n = out["nutrients"]["n"]
    assert n["known_product_kg"] == 10 and n["declared_total_kg"] is None
    assert n["shortfall_kg"] is None and n["excess_kg"] is None
    assert n["unknown_product_ids"] == ["liquid"]
    assert out["products"][1]["mass_kg"] is None
    assert out["products"][1]["nutrients"]["p2o5_kg"] == 0
    assert out["nutrients"]["p2o5"]["declared_total_kg"] == 10
    assert "products.liquid.density_kg_l" in out["missing_inputs"]


def test_unknown_analysis_and_targets_remain_unknown_without_discarding_known_contributions():
    data = spec()
    dry = replace(data.products[0], analysis=replace(data.products[0].analysis, n_pct=None))
    out = evaluate_nutrition(replace(data, products=(dry, data.products[1]), target=replace(data.target, k2o_kg_ha=None)))
    assert out["nutrients"]["n"]["known_product_kg"] == 25
    assert out["nutrients"]["n"]["declared_kg_ha"] is None
    assert out["nutrients"]["k2o"]["declared_kg_ha"] == 40
    assert out["nutrients"]["k2o"]["target_kg"] is None
    assert out["nutrients"]["k2o"]["supplied_minus_target_kg"] is None
    assert set(out["missing_inputs"]) == {"products.dry.analysis.n_pct", "target.k2o_kg_ha"}


def test_no_products_means_no_declared_input_not_unknown_soil_supply():
    out = evaluate_nutrition(replace(spec(), products=()))
    assert out["nutrients"]["n"]["declared_total_kg"] == 0
    assert out["nutrients"]["n"]["shortfall_kg"] == 30
    assert out["feasible"] is None
    assert any("No soil-test interpretation" in item for item in out["limitations"])


def test_scaling_area_and_all_product_quantities_preserves_per_hectare_budget():
    data = spec()
    base = evaluate_nutrition(data)
    scaled = evaluate_nutrition(replace(data, area_m2=data.area_m2*3,
        products=tuple(replace(p, quantity=p.quantity*3) for p in data.products)))
    for nutrient in ("n", "p2o5", "k2o"):
        assert scaled["nutrients"][nutrient]["declared_kg_ha"] == base["nutrients"][nutrient]["declared_kg_ha"]
        assert scaled["nutrients"][nutrient]["target_kg"] == base["nutrients"][nutrient]["target_kg"]*3


@pytest.mark.parametrize("value", [True, -1, 101, float("nan"), float("inf"), "10"])
def test_invalid_percentages_are_rejected(value):
    with pytest.raises(ValueError):
        ProductAnalysis(value, 0, 0, "mass_percent", "Fixture")


@pytest.mark.parametrize("change", [dict(kind="organic"), dict(quantity=0), dict(quantity=True), dict(unit="gal"),
    dict(density_kg_l=0), dict(density_kg_l=-1), dict(density_source=""), dict(name=" ")])
def test_invalid_product_and_density_inputs_are_rejected(change):
    with pytest.raises(ValueError):
        replace(spec().products[1], **change)


def test_incompatible_bases_duplicate_ids_and_invalid_scope_are_rejected():
    data = spec()
    with pytest.raises(ValueError, match="basis"):
        replace(data.target, basis="crop_uptake")
    with pytest.raises(ValueError, match="basis"):
        replace(data.products[0].analysis, basis="volume_percent")
    with pytest.raises(ValueError, match="density"):
        replace(data.products[0], density_kg_l=1, density_source="Wrong basis")
    with pytest.raises(ValueError, match="unique"):
        replace(data, products=(data.products[0], replace(data.products[1], id=" dry ")))
    with pytest.raises(ValueError, match="period_end"):
        replace(data, period_end="2026-06-30")
    with pytest.raises(ValueError):
        replace(data, area_m2=True)
    with pytest.raises(ValueError, match="finite"):
        evaluate_nutrition(replace(data, area_m2=1e-320))


def test_product_accounting_does_not_require_a_fabricated_crop_target():
    data = spec()
    out = account_products(data.area_m2, data.products)
    assert out["nutrients"]["n"]["declared_total_kg"] == 35
    assert out["nutrients"]["p2o5"]["declared_kg_ha"] == 20
    assert out["nutrients"]["k2o"]["declared_kg_ha"] == 40
    assert "inputs" not in out and "target_kg_ha" not in out["nutrients"]["n"]
    with pytest.raises(ValueError):
        account_products(True, data.products)
    with pytest.raises(ValueError, match="unique"):
        account_products(data.area_m2, (data.products[0], data.products[0]))
