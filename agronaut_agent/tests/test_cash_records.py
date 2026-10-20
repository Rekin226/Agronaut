"""Actual cash entries preserve decimal amounts, ownership and incomplete-history limits."""

from datetime import datetime, timezone

import pytest

from agronaut_agent.crop_records import execute_crop_records
from agronaut_agent.tests.test_crop_establishment import correction
from agronaut_agent.tests.test_crop_records import create
from agronaut_agent.tests.test_crop_records import setup as setup


def cash(**changes):
    return dict(dict(entry="payment-1", day="2026-07-06", direction="expense", amount="12.50", currency="USD",
        category="seeds", description="Synthetic seed purchase", allocation_note="Entire synthetic payment assigned to this cycle",
        source="Synthetic receipt", provenance="documented"), **changes)


def test_decimal_cash_and_categories_do_not_mix_currencies_or_provenance(setup):
    records, _ = setup
    cycle = create(records)["cycle"]["id"]
    for index, changes in enumerate((dict(amount="0.10"), dict(amount="0.20"),
        dict(direction="income", amount="1.00", category="produce sales"),
        dict(currency="XOF", amount="500"), dict(provenance="estimated", amount="2.000001"))):
        records.record_cash("owner", "field", cycle, cash(entry=f"cash-{index}", **changes), request_id=f"r{index}")
    summary = records.get("owner", "field", cycle)["cash"]
    assert len(summary["totals"]) == 3
    usd = next(g for g in summary["totals"] if g["currency"] == "USD" and g["provenance"] == "documented")
    assert usd["recorded_expense"] == "0.30" and usd["recorded_income"] == "1.00"
    assert usd["recorded_net_cash"] == "0.70"
    assert usd["expense_entries"] == 2 and usd["income_entries"] == 1
    seeds = next(g for g in summary["by_category"] if g["currency"] == "USD" and g["provenance"] == "documented" and g["category"] == "seeds")
    assert seeds["amount"] == "0.30" and seeds["entries"] == 2
    assert "not profit" in summary["note"]
    review = records.season_review("owner", "field", cycle)
    assert review["journal"]["cash"] == summary
    assert review["current_record_counts"]["cash"] == 5
    assert review["journal"]["harvest_totals"] == []


def test_correction_void_restoration_and_retries_never_double_count(setup):
    records, _ = setup
    cycle = create(records)["cycle"]["id"]
    first = records.record_cash("owner", "field", cycle, cash(), request_id="first")
    assert records.record_cash("owner", "field", cycle, cash(), request_id="first") == first
    second = records.record_cash("owner", "field", cycle, cash(amount="15.75"), request_id="correct", supersedes_id=first["id"])
    assert records.record_cash("owner", "field", cycle, cash(), request_id="first") == first
    with pytest.raises(ValueError, match="different data"):
        records.record_cash("owner", "field", cycle, cash(amount="15.75"), request_id="first")
    with pytest.raises(ValueError, match="already exists"):
        records.record_cash("owner", "field", cycle, cash(), request_id="duplicate")
    void = records.void_cash("owner", "field", second["id"], "Wrong receipt", request_id="void")
    assert records.void_cash("owner", "field", second["id"], "Wrong receipt", request_id="void") == void
    assert records.get("owner", "field", cycle)["cash"]["totals"] == []
    restored = records.record_cash("owner", "field", cycle, cash(amount="15.75"), request_id="restore", supersedes_id=void["id"])
    summary = records.get("owner", "field", cycle)["cash"]
    assert summary["totals"][0]["recorded_net_cash"] == "-15.75"
    assert summary["totals"][0]["record_ids"] == [restored["id"]]
    assert summary["totals"][0]["income_entries"] == 0
    for stale in (first, second, void):
        with pytest.raises(ValueError, match="latest revision"):
            records.void_cash("owner", "field", stale["id"], "Stale", request_id="bad")


def test_large_precise_amounts_are_not_rounded_by_global_decimal_context(setup):
    from decimal import localcontext

    records, _ = setup
    cycle = create(records)["cycle"]["id"]
    for index in range(2):
        records.record_cash("owner", "field", cycle, cash(entry=f"large-{index}", amount="999999999999999.999999"), request_id=f"large-{index}")
    with localcontext() as context:
        context.prec = 6
        totals = records.get("owner", "field", cycle)["cash"]["totals"][0]
    assert totals["recorded_expense"] == "1999999999999999.999998"
    assert totals["recorded_net_cash"] == "-1999999999999999.999998"


@pytest.mark.parametrize("amount", [None, 12.5, 12, True, "", "0", "0.000000", "-1", "+1", "1e3", "1,000.00",
    "$12.00", "NaN", "Infinity", " 12.00", "12.00 ", "01.00", "1000000000000000", "1.0000001", "١٢.٠٠"])
def test_invalid_or_inexact_amounts_are_rejected_without_writing(setup, amount):
    records, _ = setup
    cycle = create(records)["cycle"]["id"]
    with pytest.raises(ValueError, match="amount"):
        records.record_cash("owner", "field", cycle, cash(amount=amount), request_id="bad")
    assert len(records.get("owner", "field", cycle)["records"]) == 1


@pytest.mark.parametrize("changes", [dict(day="2099-01-01"), dict(direction="quote"), dict(currency="usd"),
    dict(currency="US"), dict(currency="123"), dict(category=""), dict(description=""), dict(allocation_note=""),
    dict(source=""), dict(provenance="forecast"), dict(owner_id="other")])
def test_invalid_cash_metadata_does_not_write(setup, changes):
    records, _ = setup
    cycle = create(records)["cycle"]["id"]
    with pytest.raises(ValueError):
        records.record_cash("owner", "field", cycle, cash(**changes), request_id="bad")
    assert records.get("owner", "field", cycle)["cash"]["totals"] == []


def test_cash_dates_may_precede_planting_and_follow_closure_without_redating_them(setup):
    records, _ = setup
    cycle = create(records)["cycle"]["id"]
    records.record_cash("owner", "field", cycle, cash(day="2026-06-20"), request_id="preplant")
    records.record_cash("owner", "field", cycle, cash(entry="sale", day="2026-07-10", direction="income", amount="30.00"), request_id="sale")
    records.close_cycle("owner", "field", cycle, "2026-07-05", "Crop removed", "Synthetic notebook", request_id="close", expected_revision=None)
    records.record_cash("owner", "field", cycle, cash(entry="late-payment", day="2026-07-11"), request_id="late")
    correction(records, cycle, planted_on="2026-07-03")
    review = records.season_review("owner", "field", cycle)
    assert review["period"]["through"] == "2026-07-05"
    assert review["journal"]["cash"]["totals"][0]["first_day"] == "2026-06-20"
    assert review["journal"]["cash"]["totals"][0]["last_day"] == "2026-07-11"
    assert review["journal"]["cash"]["totals"][0]["recorded_net_cash"] == "5.00"


@pytest.mark.parametrize("unit", ["field", "hydro", "aqua"])
def test_cash_dispatch_across_systems_and_ownership_identity_guards(setup, unit):
    records, _ = setup
    cycle = create(records, unit)["cycle"]["id"]
    other_cycle = create(records, unit, request="second")["cycle"]["id"]
    original = execute_crop_records(records.db, "owner", unit, "record_cash", dict(cycle_id=cycle, data=cash(), request_id="cash"))
    assert original["unit_id"] == unit
    for owner, target in (("other", unit), ("owner", "other")):
        with pytest.raises(ValueError):
            records.record_cash(owner, target, cycle, cash(), request_id="bad")
        with pytest.raises(ValueError):
            records.void_cash(owner, target, original["id"], "Wrong owner", request_id="bad")
    for target, data in ((other_cycle, cash()), (cycle, cash(entry="renamed"))):
        with pytest.raises(ValueError, match="preserve the cycle and entry"):
            records.record_cash("owner", unit, target, data, request_id="bad", supersedes_id=original["id"])
    with pytest.raises(ValueError, match="record kind"):
        records.void_harvest("owner", unit, original["id"], "Wrong operation", request_id="bad")


def test_farm_local_cash_day_export_and_erasure(setup, monkeypatch):
    import agronaut_agent.crop_records as module

    records, farms = setup
    cycle = create(records)["cycle"]["id"]
    records.db.execute("UPDATE farms SET timezone='Pacific/Kiritimati' WHERE id='farm'")
    fixed = datetime(2026, 7, 6, 12, 30, tzinfo=timezone.utc)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed.astimezone(tz) if tz else fixed.replace(tzinfo=None)

    monkeypatch.setattr(module, "datetime", Clock)
    saved = records.record_cash("owner", "field", cycle, cash(day="2026-07-07"), request_id="today")
    with pytest.raises(ValueError, match="through today"):
        records.record_cash("owner", "field", cycle, cash(day="2026-07-08", entry="tomorrow"), request_id="future")
    void = records.void_cash("owner", "field", saved["id"], "Wrong entry", request_id="void")
    assert saved in farms.export("owner")["crop_records"] and void in farms.export("owner")["crop_records"]
    farms.purge("owner")
    assert all(not rows for rows in farms.export("owner").values())
    with pytest.raises(ValueError):
        records.record_cash("owner", "field", cycle, cash(), request_id="late")
