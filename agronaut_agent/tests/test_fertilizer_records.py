"""Actual fertilizer evidence stays distinct from plans, with scoped immutable revisions."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from agro_model.farm import ProductionCycle
from agronaut_agent.crop_records import execute_crop_records
from agronaut_agent.field_workflow import FieldWorkflow
from agronaut_agent.tests.test_crop_records import create
from agronaut_agent.tests.test_crop_records import setup as setup
from agronaut_agent.tests.test_field_workflow import inputs as water_inputs
from agronaut_agent.tests.test_nutrition_cli import inputs as budget_inputs


def application(**changes):
    data = dict(entry="application-1", day="2026-07-06", phase="in_crop", area_m2=5000,
                location="Synthetic north half", placement="Synthetic broadcast", source="Synthetic operator log",
                provenance="measured", products=budget_inputs()["products"])
    return dict(data, **changes)


def test_actual_mass_accounting_has_no_target_and_retains_an_immutable_snapshot(setup, monkeypatch):
    import agronaut_agent.crop_records as module

    records, _ = setup
    cycle = create(records)["cycle"]["id"]
    saved = records.record_fertilizer("owner", "field", cycle, application(), request_id="actual")
    assert saved["accounting"]["model_version"] == "mineral-product-accounting-1"
    assert saved["accounting"]["area_ha"] == 0.5
    assert saved["accounting"]["nutrients"]["n"]["declared_total_kg"] == 35
    assert saved["accounting"]["nutrients"]["n"]["declared_kg_ha"] == 70
    assert "target_kg" not in saved["accounting"]["nutrients"]["n"]
    monkeypatch.setattr(module, "application_data", lambda _: (_ for _ in ()).throw(AssertionError("do not recompute history")))
    journal = records.get("owner", "field", cycle)
    assert journal["records"][-1]["accounting"] == saved["accounting"]
    total = journal["fertilizer"]["groups"][0]
    assert Decimal(total["nutrients"]["n"]["declared_total_kg"]) == 35
    assert "area_m2" not in total and "declared_kg_ha" not in total["nutrients"]["n"]
    assert FieldWorkflow(records.db).list("owner", "field")["calculations"] == []


def test_season_totals_separate_phase_and_provenance_and_keep_unknown_contributions(setup):
    records, _ = setup
    cycle = create(records)["cycle"]["id"]
    for index, changes in enumerate(({}, dict(day="2026-06-30", phase="preplant"), dict(provenance="estimated"))):
        records.record_fertilizer("owner", "field", cycle, application(entry=f"event-{index}", **changes), request_id=f"r{index}")
    products = application()["products"]
    products[1]["density_kg_l"] = products[1]["density_source"] = None
    unknown = records.record_fertilizer("owner", "field", cycle, application(products=products), request_id="unknown")
    review = records.season_review("owner", "field", cycle)
    assert review["current_record_counts"]["fertilizer"] == 4
    groups = review["journal"]["fertilizer"]["groups"]
    assert len(groups) == 3
    total = next(g for g in groups if g["phase"] == "in_crop" and g["provenance"] == "measured")
    assert total["applications"] == 2
    assert Decimal(total["nutrients"]["n"]["known_product_kg"]) == 45
    assert total["nutrients"]["n"]["declared_total_kg"] is None
    assert total["nutrients"]["n"]["unknown_record_ids"] == [unknown["id"]]
    assert Decimal(total["nutrients"]["p2o5"]["declared_total_kg"]) == 20
    assert not review["calculations"]


def test_retries_corrections_void_restore_and_stale_writes(setup):
    records, _ = setup
    cycle = create(records)["cycle"]["id"]
    data = application()
    first = records.record_fertilizer("owner", "field", cycle, data, request_id="first")
    with pytest.raises(ValueError, match="already exists"):
        records.record_fertilizer("owner", "field", cycle, data, request_id="duplicate")
    changed = application(products=[dict(data["products"][0], quantity=200)])
    second = records.record_fertilizer("owner", "field", cycle, changed, request_id="correct", supersedes_id=first["id"])
    assert records.record_fertilizer("owner", "field", cycle, data, request_id="first") == first
    with pytest.raises(ValueError, match="different data"):
        records.record_fertilizer("owner", "field", cycle, changed, request_id="first")
    void = records.void_fertilizer("owner", "field", second["id"], "Wrong amount", request_id="void")
    assert void["accounting"] == second["accounting"]
    assert records.void_fertilizer("owner", "field", second["id"], "Wrong amount", request_id="void") == void
    assert records.get("owner", "field", cycle)["fertilizer"]["groups"] == []
    restored = records.record_fertilizer("owner", "field", cycle, changed, request_id="restore", supersedes_id=void["id"])
    assert restored["accounting"]["nutrients"]["n"]["declared_total_kg"] == 20
    for prior in (first, second, void):
        with pytest.raises(ValueError, match="latest revision"):
            records.record_fertilizer("owner", "field", cycle, data, request_id="stale", supersedes_id=prior["id"])
    assert len(records.get("owner", "field", cycle)["records"]) == 5


@pytest.mark.parametrize("change", [dict(day="2099-01-01"), dict(day="2026-06-30"), dict(phase="preplant"),
    dict(phase="forecast"), dict(provenance="forecast"), dict(area_m2=0), dict(area_m2=True), dict(area_m2=1e-320),
    dict(location=""), dict(source=""), dict(placement=""), dict(entry=" "), dict(products=[]), dict(products=None),
    dict(owner_id="other"), dict(calculation_id="not-a-plan")])
def test_invalid_application_cannot_write_partial_records(setup, change):
    records, _ = setup
    cycle = create(records)["cycle"]["id"]
    with pytest.raises(ValueError):
        records.record_fertilizer("owner", "field", cycle, application(**change), request_id="bad")
    assert len(records.get("owner", "field", cycle)["records"]) == 1


def test_unknown_analysis_preserved_and_unsupported_products_rejected(setup):
    records, _ = setup
    cycle = create(records)["cycle"]["id"]
    product = application()["products"][0]
    product["analysis"]["n_pct"] = None
    saved = records.record_fertilizer("owner", "field", cycle, application(products=[product]), request_id="unknown")
    assert saved["accounting"]["nutrients"]["n"]["declared_total_kg"] is None
    for change in (dict(kind="organic"), dict(quantity=None), dict(quantity=0), dict(quantity=True),
                   dict(quantity=float("nan")), dict(unit="kg/ha"), dict(density_kg_l=1, density_source="Fixture")):
        with pytest.raises(ValueError):
            records.record_fertilizer("owner", "field", cycle, application(entry="bad", products=[dict(product, **change)]), request_id="bad")


def test_ownership_domain_cycle_and_correction_identity(setup):
    records, _ = setup
    first = create(records)["cycle"]["id"]
    second = create(records, request="second")["cycle"]["id"]
    hydro = create(records, unit="hydro")["cycle"]["id"]
    row = records.record_fertilizer("owner", "field", first, application(), request_id="first")
    for owner, unit, cycle in (("other", "field", first), ("owner", "hydro", first), ("owner", "hydro", hydro)):
        with pytest.raises(ValueError):
            records.record_fertilizer(owner, unit, cycle, application(), request_id="bad")
    for cycle, data in ((second, application()), (first, application(entry="renamed"))):
        with pytest.raises(ValueError, match="preserve the cycle and entry"):
            records.record_fertilizer("owner", "field", cycle, data, request_id="bad", supersedes_id=row["id"])
    with pytest.raises(ValueError, match="record kind"):
        records.void_observation("owner", "field", row["id"], "Wrong operation", request_id="bad")
    with pytest.raises(ValueError):
        records.void_fertilizer("other", "field", row["id"], "Wrong owner", request_id="bad")


def test_application_dates_constrain_closure_and_restoration(setup):
    records, _ = setup
    cycle = create(records)["cycle"]["id"]
    actual = records.record_fertilizer("owner", "field", cycle, application(), request_id="actual")
    closure = dict(ended_on="2026-07-05", reason="Finished", source="Synthetic log", request_id="close", expected_revision=None)
    with pytest.raises(ValueError, match="precedes a current actual"):
        records.close_cycle("owner", "field", cycle, **closure)
    void = records.void_fertilizer("owner", "field", actual["id"], "Wrong date", request_id="void")
    records.close_cycle("owner", "field", cycle, **closure)
    with pytest.raises(ValueError, match="after the crop cycle end"):
        records.record_fertilizer("owner", "field", cycle, application(), request_id="restore", supersedes_id=void["id"])
    records.record_fertilizer("owner", "field", cycle, application(day="2026-07-05"), request_id="restore", supersedes_id=void["id"])
    records.record_fertilizer("owner", "field", cycle,
        application(entry="preplant", day="2026-06-30", phase="preplant"), request_id="preplant")


def test_plan_links_are_scoped_optional_and_never_copy_quantities(setup):
    records, _ = setup
    cycle = create(records)["cycle"]["id"]
    other_cycle = create(records, request="second")["cycle"]["id"]
    workflow = FieldWorkflow(records.db)
    plan = workflow.save("owner", "field", "nutrient_budget", budget_inputs(), cycle_id=cycle, request_id="plan")
    unassigned = workflow.save("owner", "field", "nutrient_budget", budget_inputs(), request_id="unassigned")
    other = workflow.save("owner", "field", "nutrient_budget", budget_inputs(), cycle_id=other_cycle, request_id="other")
    water = workflow.save("owner", "field", "root_zone_budget", water_inputs(), cycle_id=cycle, request_id="water")
    assert records.get("owner", "field", cycle)["fertilizer"]["groups"] == []
    for wrong in (unassigned, other, water):
        with pytest.raises(ValueError, match="nutrient_budget for this unit and cycle"):
            records.record_fertilizer("owner", "field", cycle, application(calculation_id=wrong["id"]), request_id="bad")
    products = [dict(application()["products"][0], quantity=50)]
    saved = records.record_fertilizer("owner", "field", cycle, application(products=products, calculation_id=plan["id"]), request_id="actual")
    assert saved["accounting"]["nutrients"]["n"]["declared_total_kg"] == 5
    assert workflow.get("owner", "field", plan["id"]) == plan
    assert workflow.list("owner", "field")["water_days"] == []


def test_farm_local_day_and_completed_preplant_for_existing_future_cycle(setup, monkeypatch):
    import agronaut_agent.crop_records as module

    records, farms = setup
    fixed = datetime(2026, 7, 6, 12, 30, tzinfo=timezone.utc)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed.astimezone(tz) if tz else fixed.replace(tzinfo=None)

    monkeypatch.setattr(module, "datetime", Clock)
    records.db.execute("UPDATE farms SET timezone='Pacific/Kiritimati' WHERE id='farm'")
    farms.create_cycle("owner", ProductionCycle("future", "field", "tomato", "2026-07-10"))
    records.record_fertilizer("owner", "field", "future", application(phase="preplant", day="2026-07-07"), request_id="actual")
    with pytest.raises(ValueError, match="after today"):
        records.record_fertilizer("owner", "field", "future", application(phase="preplant", day="2026-07-08", entry="future"), request_id="bad")


def test_discovery_export_and_erasure_include_application_snapshots(setup):
    records, farms = setup
    cycle = create(records)["cycle"]["id"]
    schema = execute_crop_records(records.db, "owner", "field", "describe", {})
    assert schema["fertilizer"]["product_schema"]["minItems"] == 1
    saved = execute_crop_records(records.db, "owner", "field", "record_fertilizer",
                                 dict(cycle_id=cycle, data=application(), request_id="actual"))
    void = execute_crop_records(records.db, "owner", "field", "void_fertilizer",
                                dict(record_id=saved["id"], reason="Duplicate", request_id="void"))
    exported = farms.export("owner")["crop_records"]
    assert saved in exported and void in exported
    farms.purge("owner")
    assert all(not rows for rows in farms.export("owner").values())
