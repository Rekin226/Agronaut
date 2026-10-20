"""Explicit per-cycle comparisons retain provenance, date windows and unknown history."""

import pytest

from agronaut_agent.crop_records import execute_crop_records
from agronaut_agent.field_workflow import FieldWorkflow
from agronaut_agent.tests.test_cash_records import cash
from agronaut_agent.tests.test_crop_records import create, harvest
from agronaut_agent.tests.test_crop_records import setup as setup
from agronaut_agent.tests.test_fertilizer_records import application
from agronaut_agent.tests.test_field_workflow import water


def test_comparison_preserves_each_cycles_units_dates_coverage_and_evidence(setup):
    records, _ = setup
    first = create(records)["cycle"]["id"]
    second = create(records, request="second")["cycle"]["id"]
    received = records.record_cash("owner", "field", first, cash(direction="income", category="sales", amount="100.25"), request_id="income")
    records.record_cash("owner", "field", second, cash(currency="XOF", amount="7500"), request_id="expense")
    wet = records.record_harvest("owner", "field", first, harvest(), request_id="fresh")
    records.record_harvest("owner", "field", second, harvest(basis="dry", quantity=2), request_id="dry")
    fertilizer = records.record_fertilizer("owner", "field", first, application(), request_id="fertilizer")
    FieldWorkflow(records.db).record_day("owner", "field", water(), cycle_id=first, request_id="water")
    records.close_cycle("owner", "field", first, "2026-07-10", "Finished", "Synthetic", request_id="close", expected_revision=None)
    before = records.get("owner", "field", first)
    compared = execute_crop_records(records.db, "owner", "field", "compare_seasons", dict(cycle_ids=[second, first]))
    assert [r["cycle"]["id"] for r in compared["seasons"]] == [second, first]
    a, b = compared["seasons"]
    assert a["period"]["status"] == "open" and b["period"]["through"] == "2026-07-10"
    assert a["harvest_totals"][0]["basis"] == "dry" and b["harvest_totals"][0]["basis"] == "fresh"
    assert a["cash"]["totals"][0]["currency"] == "XOF" and b["cash"]["totals"][0]["currency"] == "USD"
    assert b["current_crop_record_ids"]["cash"] == [received["id"]]
    assert b["current_crop_record_ids"]["harvest"] == [wet["id"]]
    assert b["fertilizer"]["groups"][0]["record_ids"] == [fertilizer["id"]]
    assert b["water"]["coverage"]["recorded_days"] == 1 and b["water"]["coverage"]["missing_days"] == 9
    assert a["water"]["coverage"]["recorded_days"] == 0 and a["water"]["totals"] == []
    assert "totals" not in compared and "ranking" not in compared
    assert records.get("owner", "field", first) == before


def test_missing_and_voided_histories_are_unknown_and_latest_revisions_are_used(setup):
    records, _ = setup
    first = create(records)["cycle"]["id"]
    second = create(records, request="second")["cycle"]["id"]
    old = records.record_cash("owner", "field", first, cash(amount="10.00"), request_id="first")
    new = records.record_cash("owner", "field", first, cash(amount="20.00"), request_id="new", supersedes_id=old["id"])
    one, two = records.compare_seasons("owner", "field", [first, second])["seasons"]
    assert one["cash"]["totals"][0]["recorded_expense"] == "20.00"
    assert one["current_crop_record_ids"]["cash"] == [new["id"]]
    assert two["cash"]["totals"] == [] and two["harvest_totals"] == []
    records.void_cash("owner", "field", new["id"], "Duplicate", request_id="void")
    one, _ = records.compare_seasons("owner", "field", [first, second])["seasons"]
    assert one["cash"]["totals"] == [] and "cash" not in one["current_crop_record_ids"]


@pytest.mark.parametrize("selection", [None, "cycle", [], ["one"], ["one", "one"], ["one", None],
                                       ["one", True], [str(n) for n in range(11)]])
def test_invalid_season_selections_are_rejected(setup, selection):
    records, _ = setup
    with pytest.raises(ValueError):
        records.compare_seasons("owner", "field", selection)


def test_comparison_cannot_include_another_unit_or_owner_and_aquatic_coverage_is_not_invented(setup):
    records, _ = setup
    first = create(records)["cycle"]["id"]
    second = create(records, request="second")["cycle"]["id"]
    hydro = create(records, unit="hydro")["cycle"]["id"]
    hydro2 = create(records, unit="hydro", request="hydro2")["cycle"]["id"]
    for owner, cycles in (("other", [first, second]), ("owner", [first, hydro]), ("owner", [first, "unknown"])):
        with pytest.raises(ValueError):
            records.compare_seasons(owner, "field", cycles)
    compared = records.compare_seasons("owner", "hydro", [hydro, hydro2])
    assert all(s["water"]["coverage"] is None and not s["water"]["applicable"] for s in compared["seasons"])
