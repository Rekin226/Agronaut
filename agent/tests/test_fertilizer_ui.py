"""Model-free application entry, correction, voiding and incomplete label evidence."""

from datetime import date
from decimal import Decimal

from agent.tests.test_crop_ui import planting


def fill(at, cycle):
    prefix = f"crop_fertilizer_{cycle}_new_"
    at.text_input(key=prefix+"entry").set_value("preplant-1")
    at.date_input(key=prefix+"day").set_value(date(2026, 6, 30))
    at.selectbox(key=prefix+"phase").set_value("preplant")
    at.selectbox(key=prefix+"provenance").set_value("reported")
    at.number_input(key=prefix+"area_m2").set_value(5000.0)
    at.text_input(key=prefix+"location").set_value("Synthetic north half")
    at.text_input(key=prefix+"placement").set_value("Synthetic broadcast")
    at.text_input(key=prefix+"source").set_value("Synthetic notebook")
    p = prefix+"product_0_"
    at.text_input(key=p+"name").set_value("Synthetic 10-10-20")
    at.number_input(key=p+"quantity").set_value(100.0)
    at.selectbox(key=p+"unit").set_value("kg")
    for nutrient, amount in (("n", 10.0), ("p2o5", 10.0), ("k2o", 20.0)):
        at.number_input(key=p+nutrient).set_value(amount)
    at.text_input(key=p+"source").set_value("Synthetic label")
    return prefix


def save(at):
    next(b for b in at.button if b.label == "Save fertilizer application").click().run(timeout=15)
    assert not at.exception


def test_preplant_application_correction_void_restore_and_season_download(tmp_path):
    at, brain, _, cycle = planting(tmp_path)
    fill(at, cycle)
    save(at)
    assert not at.error
    journal = brain.crop_records("web", "operator", "get", dict(cycle_id=cycle))
    first = journal["records"][-1]
    assert first["accounting"]["nutrients"]["n"]["declared_total_kg"] == 10
    assert journal["fertilizer"]["groups"][0]["phase"] == "preplant"
    at.selectbox(key=f"crop_fertilizer_correction_{cycle}").set_value(first["id"]).run(timeout=15)
    prefix = f"crop_fertilizer_{cycle}_{first['id']}_"
    assert at.text_input(key=prefix+"entry").disabled
    at.number_input(key=prefix+"product_0_quantity").set_value(200.0)
    at.text_input(key=prefix+"source").set_value("Synthetic corrected notebook")
    save(at)
    assert not at.error
    journal = brain.crop_records("web", "operator", "get", dict(cycle_id=cycle))
    assert Decimal(journal["fertilizer"]["groups"][0]["nutrients"]["n"]["declared_total_kg"]) == 20
    at.text_input(key=f"crop_fertilizer_void_{cycle}_reason").set_value("Duplicate entry")
    at.button(key=f"crop_fertilizer_void_{cycle}_submit").click().run(timeout=15)
    assert not at.exception and not at.error
    journal = brain.crop_records("web", "operator", "get", dict(cycle_id=cycle))
    assert not journal["fertilizer"]["groups"]
    void = journal["records"][-1]
    at.selectbox(key=f"crop_fertilizer_correction_{cycle}").set_value(void["id"]).run(timeout=15)
    prefix = f"crop_fertilizer_{cycle}_{void['id']}_"
    at.text_input(key=prefix+"source").set_value("Synthetic restoration")
    save(at)
    assert not at.error
    review = brain.crop_records("web", "operator", "season_review", dict(cycle_id=cycle))
    assert review["current_record_counts"]["fertilizer"] == 1
    assert review["journal"]["fertilizer"]["groups"][0]["applications"] == 1
    assert any(b.label == "Download season review" for b in at.get("download_button"))


def test_unknown_liquid_density_keeps_nutrients_unknown_and_missing_quantity_is_rejected(tmp_path):
    at, brain, _, cycle = planting(tmp_path)
    prefix = fill(at, cycle)
    at.selectbox(key=prefix+"product_0_unit").set_value("L")
    at.number_input(key=prefix+"product_0_quantity").set_value(None)
    save(at)
    assert at.error
    assert len(brain.crop_records("web", "operator", "get", dict(cycle_id=cycle))["records"]) == 1
    at.number_input(key=prefix+"product_0_quantity").set_value(100.0)
    save(at)
    assert not at.error
    row = brain.crop_records("web", "operator", "get", dict(cycle_id=cycle))["records"][-1]
    assert row["data"]["products"][0]["density_kg_l"] is None
    assert row["accounting"]["nutrients"]["n"]["declared_total_kg"] is None


def test_unit_switch_clears_application_inputs_and_hides_soil_only_form(tmp_path):
    at, brain, _, cycle = planting(tmp_path)
    prefix = fill(at, cycle)
    brain.create_unit("web", "operator", {"farm": {"name": "Other", "timezone": "UTC"},
        "unit": {"name": "Hydro", "production_system": "hydroponics", "medium": "water",
                 "environment": "greenhouse", "water_strategy": "fully_irrigated"}})
    brain.crop_records("web", "operator", "create", dict(crop="tomato", planted_on="2026-07-01",
        source="Synthetic planting", request_id="plant"))
    at.run(timeout=15)
    assert not at.exception and not at.error
    assert not any(b.label == "Save fertilizer application" for b in at.button)
    assert prefix+"entry" not in at.session_state


def test_adding_product_to_imported_application_preserves_custom_ids_without_collision(tmp_path):
    from agronaut_agent.tests.test_fertilizer_records import application

    at, brain, _, cycle = planting(tmp_path)
    product = application()["products"][0]
    product["id"] = " product-2 "
    original = brain.crop_records("web", "operator", "record_fertilizer", dict(cycle_id=cycle,
        data=application(products=[product]), request_id="imported"))
    at.run(timeout=15)
    at.selectbox(key=f"crop_fertilizer_correction_{cycle}").set_value(original["id"]).run(timeout=15)
    prefix = f"crop_fertilizer_{cycle}_{original['id']}_"
    at.number_input(key=prefix+"count").set_value(2).run(timeout=15)
    at.text_input(key=prefix+"source").set_value("Synthetic correction")
    p = prefix+"product_1_"
    at.text_input(key=p+"name").set_value("Synthetic second product")
    at.number_input(key=p+"quantity").set_value(50.0)
    at.selectbox(key=p+"unit").set_value("kg")
    at.text_input(key=p+"source").set_value("Synthetic missing label analysis")
    save(at)
    assert not at.error
    latest = brain.crop_records("web", "operator", "get", dict(cycle_id=cycle))["records"][-1]
    assert latest["data"]["products"][0]["id"] == " product-2 "
    assert latest["data"]["products"][1]["id"].strip() != "product-2"
    assert latest["accounting"]["nutrients"]["n"]["declared_total_kg"] is None
