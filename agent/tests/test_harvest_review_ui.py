"""No-model harvest-area review entry, correction and seasonal evidence rendering."""

from datetime import date

from agent.tests.test_crop_ui import planting
from agronaut_agent.tests.test_crop_records import harvest


def with_batch(tmp_path):
    at, brain, unit, cycle = planting(tmp_path)
    batch = brain.crop_records("web", "operator", "record_harvest", dict(cycle_id=cycle, data=harvest(), request_id="batch"))
    at.run(timeout=15)
    assert not at.exception
    return at, brain, unit, cycle, batch


def fill(at, cycle, batch):
    prefix = f"crop_harvest_review_{cycle}_new_"
    for key, value in dict(entry="north", area_m2="300", location="Synthetic north footprint",
        area_source="Synthetic survey", source="Synthetic collection notebook").items():
        at.text_input(key=prefix+key).set_value(value)
    at.text_area(key=prefix+"scope_note").set_value("This whole batch comes from the stated ground footprint. Other pickings are missing.")
    at.date_input(key=prefix+"start").set_value(date(2026, 7, 1))
    at.date_input(key=prefix+"end").set_value(date(2026, 7, 10))
    at.multiselect(key=prefix+"harvest_record_ids").set_value([batch["id"]])
    at.selectbox(key=prefix+"coverage").set_value("partial")
    return prefix


def save(at):
    next(b for b in at.button if b.label == "Save harvest review").click().run(timeout=15)
    assert not at.exception


def journal(brain, cycle):
    return brain.crop_records("web", "operator", "get", dict(cycle_id=cycle))


def test_save_exact_area_correct_void_restore_and_download(tmp_path):
    at, brain, _, cycle, batch = with_batch(tmp_path)
    fill(at, cycle, batch)
    save(at)
    assert not at.error
    first = journal(brain, cycle)["harvest_reviews"]["current"][0]
    assert first["accounting"]["groups"][0]["per_ha"]["exact"] == "1000/3"
    assert at.selectbox(key=f"crop_harvest_review_choice_{cycle}").value == first["id"]
    save(at)
    assert not at.error and len(journal(brain, cycle)["harvest_reviews"]["history"]) == 1
    prefix = f"crop_harvest_review_{cycle}_{first['id']}_"
    assert at.text_input(key=prefix+"entry").disabled
    at.text_input(key=prefix+"area_m2").set_value("200")
    at.text_input(key=prefix+"reason").set_value("Corrected ground survey")
    save(at)
    assert not at.error
    second = journal(brain, cycle)["harvest_reviews"]["current"][0]
    assert second["accounting"]["groups"][0]["per_ha"]["exact"] == "500"
    prefix = f"crop_harvest_review_{cycle}_{second['id']}_"
    at.text_input(key=prefix+"reason").set_value("Mistaken coverage")
    next(b for b in at.button if b.label == "Void harvest review").click().run(timeout=15)
    assert not at.exception and not at.error
    assert journal(brain, cycle)["harvest_reviews"]["current"] == []
    void = journal(brain, cycle)["harvest_reviews"]["history"][-1]
    prefix = f"crop_harvest_review_{cycle}_{void['id']}_"
    at.text_input(key=prefix+"reason").set_value("Checked original footprint")
    save(at)
    assert not at.error
    assert len(journal(brain, cycle)["harvest_reviews"]["current"]) == 1
    assert any(b.label == "Download harvest reviews" for b in at.get("download_button"))
    assert journal(brain, cycle)["harvest_totals"][0]["quantity"] == "10"


def test_missing_area_rejected_and_changed_batches_require_explicit_reselection(tmp_path):
    at, brain, _, cycle, batch = with_batch(tmp_path)
    prefix = fill(at, cycle, batch)
    at.text_input(key=prefix+"area_m2").set_value("")
    save(at)
    assert at.error and journal(brain, cycle)["harvest_reviews"]["current"] == []
    at.text_input(key=prefix+"area_m2").set_value("300")
    save(at)
    assert not at.error
    first = journal(brain, cycle)["harvest_reviews"]["current"][0]
    corrected = brain.crop_records("web", "operator", "record_harvest", dict(cycle_id=cycle,
        data=harvest(quantity=20), supersedes_id=batch["id"], request_id="correct-weight"))
    at.run(timeout=15)
    assert not at.exception
    assert any("selected batch" in w.value for w in at.warning)
    prefix = f"crop_harvest_review_{cycle}_{first['id']}_"
    assert at.multiselect(key=prefix+"harvest_record_ids").value == [batch["id"]]
    at.text_input(key=prefix+"reason").set_value("Recheck corrected harvest")
    save(at)
    assert at.error
    at.multiselect(key=prefix+"harvest_record_ids").set_value([corrected["id"]])
    save(at)
    assert not at.error
    assert not journal(brain, cycle)["harvest_reviews"]["current"][0]["review"]["needs_review"]


def test_season_comparison_keeps_review_scope_and_unit_switch_clears_forms(tmp_path):
    at, brain, _, cycle, batch = with_batch(tmp_path)
    fill(at, cycle, batch)
    save(at)
    second = brain.crop_records("web", "operator", "create", dict(crop="pepper", planted_on="2026-08-01",
        source="Synthetic other planting", request_id="second"))["cycle"]["id"]
    at.run(timeout=15)
    at.multiselect(key="crop_compare_cycles").set_value([cycle, second]).run(timeout=15)
    assert not at.exception and not at.error
    table = next(f.value for f in at.dataframe if "current_harvest_area_reviews" in f.value.columns)
    assert list(table["current_harvest_area_reviews"]) == [1, 0]
    results = next(f.value for f in at.dataframe if "cycle_id" in f.value.columns and "exact_per_ha" in f.value.columns)
    assert list(results["area_m2"]) == ["300"] and list(results["coverage"]) == ["partial"]
    assert list(results["exact_per_ha"]) == ["1000/3"]
    review = brain.crop_records("web", "operator", "season_review", dict(cycle_id=cycle))
    assert len(review["journal"]["harvest_reviews"]["history"]) == 1
    brain.create_unit("web", "operator", {"farm": {"name": "Other", "timezone": "UTC"},
        "unit": {"name": "Other soil", "production_system": "soil", "medium": "soil",
                 "environment": "open_field", "water_strategy": "rainfed"}})
    at.run(timeout=15)
    assert not at.exception
    assert f"crop_harvest_review_choice_{cycle}" not in at.session_state
