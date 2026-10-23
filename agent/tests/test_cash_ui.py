"""Cash corrections and explicit season comparisons through the no-model crop page."""

from datetime import date

from agent.tests.test_crop_ui import planting
from agronaut_agent.tests.test_cash_records import cash


def fill_cash(at, cycle, *, amount="12.50", day=date(2026, 6, 20)):
    prefix = f"crop_cash_{cycle}_new_"
    for key, value in dict(entry="payment-1", amount=amount, currency="USD", category="seeds",
        description="Synthetic seed purchase", allocation_note="Entire synthetic payment allocated to this planting",
        source="Synthetic receipt").items():
        at.text_input(key=prefix+key).set_value(value)
    at.date_input(key=prefix+"day").set_value(day)
    at.selectbox(key=prefix+"direction").set_value("expense")
    at.selectbox(key=prefix+"provenance").set_value("documented")
    return prefix


def save(at):
    next(b for b in at.button if b.label == "Save cash entry").click().run(timeout=15)
    assert not at.exception


def test_cash_decimal_entry_correction_void_and_restore_without_model(tmp_path):
    at, brain, _, cycle = planting(tmp_path)
    fill_cash(at, cycle)
    save(at)
    assert not at.error
    journal = brain.crop_records("web", "operator", "get", dict(cycle_id=cycle))
    first = journal["records"][-1]
    assert first["data"]["amount"] == "12.50"
    assert journal["cash"]["totals"][0]["recorded_net_cash"] == "-12.50"
    at.selectbox(key=f"crop_cash_correction_{cycle}").set_value(first["id"]).run(timeout=15)
    prefix = f"crop_cash_{cycle}_{first['id']}_"
    assert at.text_input(key=prefix+"entry").disabled
    at.text_input(key=prefix+"amount").set_value("13.25")
    at.text_input(key=prefix+"source").set_value("Synthetic corrected receipt")
    save(at)
    assert not at.error
    assert brain.crop_records("web", "operator", "get", dict(cycle_id=cycle))["cash"]["totals"][0]["recorded_expense"] == "13.25"
    at.text_input(key=f"crop_cash_void_{cycle}_reason").set_value("Duplicate receipt")
    at.button(key=f"crop_cash_void_{cycle}_submit").click().run(timeout=15)
    assert not at.exception and not at.error
    journal = brain.crop_records("web", "operator", "get", dict(cycle_id=cycle))
    assert journal["cash"]["totals"] == []
    void = journal["records"][-1]
    at.selectbox(key=f"crop_cash_correction_{cycle}").set_value(void["id"]).run(timeout=15)
    prefix = f"crop_cash_{cycle}_{void['id']}_"
    at.text_input(key=prefix+"source").set_value("Synthetic restoration")
    save(at)
    assert not at.error
    review = brain.crop_records("web", "operator", "season_review", dict(cycle_id=cycle))
    assert review["journal"]["cash"]["totals"][0]["expense_entries"] == 1


def test_unknown_amount_does_not_become_zero_and_late_receipt_keeps_actual_date(tmp_path):
    at, brain, _, cycle = planting(tmp_path)
    prefix = fill_cash(at, cycle, amount="")
    save(at)
    assert at.error
    assert brain.crop_records("web", "operator", "get", dict(cycle_id=cycle))["cash"]["totals"] == []
    brain.crop_records("web", "operator", "close_cycle", dict(cycle_id=cycle, ended_on="2026-07-05",
        source="Synthetic notebook", reason="Finished", request_id="close", expected_revision=None))
    at.run(timeout=15)
    at.text_input(key=prefix+"amount").set_value("10.75")
    at.selectbox(key=prefix+"direction").set_value("income")
    at.date_input(key=prefix+"day").set_value(date(2026, 7, 10))
    save(at)
    assert not at.error
    journal = brain.crop_records("web", "operator", "get", dict(cycle_id=cycle))
    assert journal["lifecycle"]["ended_on"] == "2026-07-05"
    assert journal["cash"]["totals"][0]["last_day"] == "2026-07-10"
    assert journal["cash"]["totals"][0]["recorded_income"] == "10.75"


def test_comparison_requires_explicit_cycles_and_separates_currencies_and_unknowns(tmp_path):
    at, brain, _, first = planting(tmp_path)
    second = brain.crop_records("web", "operator", "create", dict(crop="pepper", planted_on="2026-08-01",
        source="Synthetic second planting", request_id="second"))["cycle"]["id"]
    third = brain.crop_records("web", "operator", "create", dict(crop="lettuce", planted_on="2026-09-01",
        source="Synthetic empty planting", request_id="third"))["cycle"]["id"]
    brain.crop_records("web", "operator", "record_cash", dict(cycle_id=first, data=cash(), request_id="usd"))
    brain.crop_records("web", "operator", "record_cash", dict(cycle_id=second, data=cash(currency="XOF", amount="500"), request_id="xof"))
    at.run(timeout=15)
    assert not at.exception and not at.error
    assert at.multiselect(key="crop_compare_cycles").value == []
    assert not any(b.label == "Download season comparison" for b in at.get("download_button"))
    at.multiselect(key="crop_compare_cycles").set_value([second, first, third]).run(timeout=15)
    assert not at.exception and not at.error
    table = next(f.value for f in at.dataframe if "cash_history" in f.value.columns)
    assert list(table["cycle_id"]) == [second, first, third]
    assert list(table["cash_history"]) == ["recorded", "recorded", "unknown"]
    cash_table = next(f.value for f in at.dataframe if "cycle_id" in f.value.columns and "recorded_net_cash" in f.value.columns)
    assert set(cash_table["currency"]) == {"USD", "XOF"}
    assert len(cash_table) == 2
    assert any(b.label == "Download season comparison" for b in at.get("download_button"))
    before = brain.crop_records("web", "operator", "get", dict(cycle_id=second))
    brain.crop_records("web", "operator", "correct_planting", dict(cycle_id=second, crop="onion", planted_on="2026-08-01",
        source="Synthetic correction", reason="Crop label typo", request_id="amend", expected_revision=before["planting"]["revision_id"]))
    at.run(timeout=15)
    assert not at.exception
    assert at.multiselect(key="crop_compare_cycles").value == [second, first, third]


def test_switching_units_clears_cash_and_comparison_selections(tmp_path):
    at, brain, _, cycle = planting(tmp_path)
    prefix = fill_cash(at, cycle)
    brain.create_unit("web", "operator", {"farm": {"name": "Other", "timezone": "UTC"},
        "unit": {"name": "Other soil", "production_system": "soil", "medium": "soil",
                 "environment": "open_field", "water_strategy": "rainfed"}})
    at.run(timeout=15)
    assert not at.exception
    assert prefix+"amount" not in at.session_state
    assert "crop_compare_cycles" not in at.session_state
