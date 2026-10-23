"""Crop journal ownership, corrections, meaningful totals and migration durability."""

import sqlite3
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from agro_model.farm import Farm, ProductionUnit
from agronaut_agent.crop_records import CropRecords, execute_crop_records
from agronaut_agent.farm_store import FarmStore
from agronaut_agent.store import SCHEMA_VERSION, _Db


@pytest.fixture
def setup(tmp_path):
    db = _Db(tmp_path/"crops.sqlite3")
    farms = FarmStore(db)
    farms.create_farm(Farm("farm", "owner", "Home", "UTC"))
    farms.create_farm(Farm("other-farm", "other", "Other", "UTC"))
    for name, domain, farm in (("field", "soil", "farm"), ("hydro", "hydroponics", "farm"),
                               ("aqua", "aquaponics", "farm"), ("other", "soil", "other-farm")):
        farms.create_unit("other" if name == "other" else "owner", ProductionUnit(
            name, farm, name, domain, "soil" if domain == "soil" else "water",
            "open_field" if domain == "soil" else "greenhouse", "rainfed" if domain == "soil" else "fully_irrigated"))
    yield CropRecords(db), farms
    db._conn.close()


def create(records, unit="field", request="plant", owner="owner"):
    return records.create(owner, unit, "tomato", "2026-07-01", "Synthetic planting fixture", request_id=request)


def harvest(**changes):
    result = dict(batch="batch-1", day="2026-07-06", quantity=10, unit="kg", product="fruit",
                  basis="fresh", grade="ungraded", source="Synthetic harvest fixture", provenance="measured")
    result.update(changes)
    return result


def test_corrections_voids_and_restoration_preserve_history_without_double_counting(setup):
    records, _ = setup
    cycle = create(records)["cycle"]["id"]
    original = records.record_harvest("owner", "field", cycle, harvest(), request_id="first")
    corrected = records.record_harvest("owner", "field", cycle, harvest(quantity=12), request_id="correct",
                                        supersedes_id=original["id"])
    records.record_harvest("owner", "field", cycle, harvest(batch="batch-2", quantity=501, unit="g"), request_id="second")
    journal = records.get("owner", "field", cycle)
    assert Decimal(journal["harvest_totals"][0]["quantity"]) == Decimal("12.501")
    assert journal["harvest_totals"][0]["batches"] == 2
    assert journal["records"][1]["data"]["quantity"] == 10
    assert journal["records"][1]["superseded"]
    void = records.void_harvest("owner", "field", corrected["id"], "Wrong weighing", request_id="void")
    assert Decimal(records.get("owner", "field", cycle)["harvest_totals"][0]["quantity"]) == Decimal("0.501")
    restored = records.record_harvest("owner", "field", cycle, harvest(quantity=11), request_id="restore",
                                       supersedes_id=void["id"])
    assert restored["supersedes_id"] == void["id"]
    assert Decimal(records.get("owner", "field", cycle)["harvest_totals"][0]["quantity"]) == Decimal("11.501")
    for old in (original, corrected, void):
        with pytest.raises(ValueError, match="latest revision"):
            records.void_harvest("owner", "field", old["id"], "Stale correction", request_id="bad")


def test_totals_keep_products_bases_grades_counts_and_provenance_separate(setup):
    records, _ = setup
    cycle = create(records)["cycle"]["id"]
    changes = [{}, dict(basis="dry"), dict(grade="marketable"), dict(provenance="estimated"),
               dict(product="seed"), dict(unit="count"), dict(unit="t", quantity=0.001)]
    for index, change in enumerate(changes):
        records.record_harvest("owner", "field", cycle, harvest(batch=f"b{index}", **change), request_id=f"h{index}")
    totals = records.get("owner", "field", cycle)["harvest_totals"]
    assert len(totals) == 6
    combined = next(r for r in totals if r["batches"] == 2)
    assert combined == dict(product="fruit", basis="fresh", grade="ungraded", provenance="measured",
                            unit="kg", quantity="11.000", batches=2)


def test_retry_identity_and_duplicate_batch_protection(setup):
    records, _ = setup
    planting = create(records)
    assert create(records) == planting
    cycle = planting["cycle"]["id"]
    first = records.record_harvest("owner", "field", cycle, harvest(), request_id="batch")
    assert records.record_harvest("owner", "field", cycle, harvest(), request_id="batch") == first
    with pytest.raises(ValueError, match="different data"):
        records.record_harvest("owner", "field", cycle, harvest(quantity=20), request_id="batch")
    with pytest.raises(ValueError, match="batch already exists"):
        records.record_harvest("owner", "field", cycle, harvest(), request_id="another")
    void = records.void_harvest("owner", "field", first["id"], "Duplicate weighing", request_id="void")
    assert records.void_harvest("owner", "field", first["id"], "Duplicate weighing", request_id="void") == void
    assert records.get("owner", "field", cycle)["harvest_totals"] == []


@pytest.mark.parametrize("changes", [dict(quantity=None), dict(quantity=True), dict(quantity=0),
    dict(quantity=-1), dict(quantity=float("nan")), dict(quantity=1.5, unit="count"), dict(unit="L"),
    dict(day="2099-01-01"), dict(day="2026-06-30"), dict(source=""), dict(basis="unknown"),
    dict(provenance="forecast"), dict(owner_id="other")])
def test_invalid_or_unperformed_harvest_never_writes_a_record(setup, changes):
    records, _ = setup
    cycle = create(records)["cycle"]["id"]
    with pytest.raises(ValueError):
        records.record_harvest("owner", "field", cycle, harvest(**changes), request_id="bad")
    assert len(records.get("owner", "field", cycle)["records"]) == 1


def test_same_service_supports_all_crop_systems_but_enforces_unit_and_cycle_boundaries(setup):
    records, _ = setup
    first = create(records)["cycle"]["id"]
    other = create(records, "hydro")["cycle"]["id"]
    create(records, "aqua")
    row = records.record_harvest("owner", "field", first, harvest(), request_id="one")
    for owner, unit, cycle in (("other", "field", first), ("owner", "hydro", first), ("owner", "field", other)):
        with pytest.raises(ValueError):
            records.get(owner, unit, cycle)
    with pytest.raises(ValueError, match="preserve the cycle and batch"):
        records.record_harvest("owner", "field", first, harvest(batch="renamed"), request_id="bad", supersedes_id=row["id"])
    with pytest.raises(ValueError, match="unknown fields"):
        execute_crop_records(records.db, "owner", "field", "list", dict(owner_id="other"))
    with pytest.raises(ValueError):
        records.get("owner", "field", None)


def test_export_erasure_and_late_writes_respect_owner(setup):
    records, farms = setup
    for owner, unit in (("owner", "field"), ("other", "other")):
        cycle = create(records, unit, owner=owner)["cycle"]["id"]
        records.record_harvest(owner, unit, cycle, harvest(), request_id="harvest")
    assert len(farms.export("owner")["crop_records"]) == 2
    farms.purge("owner")
    assert all(not items for items in farms.export("owner").values())
    assert len(farms.export("other")["crop_records"]) == 2
    with pytest.raises(ValueError):
        create(records)


def test_failed_planting_record_rolls_back_the_cycle_and_future_plantings_are_rejected(setup, monkeypatch):
    import agronaut_agent.crop_records as module

    records, _ = setup
    with pytest.raises(ValueError, match="actual planting"):
        records.create("owner", "field", "tomato", "2099-01-01", "Future plan", request_id="future")

    def fail(*args, **kwargs):
        raise RuntimeError("simulated journal failure")

    monkeypatch.setattr(module, "_save", fail)
    with pytest.raises(RuntimeError, match="journal failure"):
        create(records)
    assert records.list("owner", "field")["cycles"] == []


def test_v5_migration_backup_preserves_existing_cycles_and_rolls_back_failure(setup, monkeypatch):
    import agronaut_agent.crop_records as module

    records, _ = setup
    cycle = create(records)["cycle"]
    path = records.db.path
    records.db.execute("DROP TABLE farm_resource_plans")
    records.db.execute("DROP TABLE observation_reviews")
    records.db.execute("DROP TABLE crop_records")
    records.db.execute("PRAGMA user_version=5")
    records.db._conn.close()
    original = module.migrate_crop_records

    def fail(conn):
        original(conn)
        raise RuntimeError("migration interrupted")

    monkeypatch.setattr(module, "migrate_crop_records", fail)
    with pytest.raises(RuntimeError, match="migration interrupted"):
        _Db(path)
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 5
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='crop_records'").fetchone() is None
    monkeypatch.setattr(module, "migrate_crop_records", original)
    upgraded = _Db(path)
    assert upgraded.query("PRAGMA user_version")[0][0] == SCHEMA_VERSION
    assert CropRecords(upgraded).get("owner", "field", cycle["id"])["cycle"] == cycle
    with sqlite3.connect(next(path.parent.glob(f"*.pre-v{SCHEMA_VERSION}-*.bak"))) as backup:
        assert backup.execute("PRAGMA user_version").fetchone()[0] == 5
        assert backup.execute("SELECT id FROM production_cycles").fetchone()[0] == cycle["id"]
    upgraded._conn.close()


def test_planting_and_harvest_today_follow_farm_timezone_not_utc(setup, monkeypatch):
    import agronaut_agent.crop_records as module

    records, _ = setup
    records.db.execute("UPDATE farms SET timezone='Pacific/Kiritimati' WHERE id='farm'")
    fixed = datetime(2026, 7, 6, 12, 30, tzinfo=timezone.utc)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed.astimezone(tz) if tz is not None else fixed.replace(tzinfo=None)

    monkeypatch.setattr(module, "datetime", Clock)
    planted = records.create("owner", "field", "lettuce", "2026-07-07", "Synthetic local-day fixture", request_id="plant")
    cycle = planted["cycle"]["id"]
    records.record_harvest("owner", "field", cycle, harvest(day="2026-07-07"), request_id="day")
    with pytest.raises(ValueError, match="between planting and today"):
        records.record_harvest("owner", "field", cycle, harvest(day="2026-07-08", batch="tomorrow"), request_id="bad")


def test_erasure_handles_long_crop_revision_chains(setup):
    import json

    records, farms = setup
    cycle = create(records)["cycle"]["id"]
    record = records.record_harvest("owner", "field", cycle, harvest(), request_id="first")
    parent = record["id"]
    with records.db.transaction() as conn:
        for index in range(1100):
            row_id = f"revision-{index}"
            payload = dict(record, id=row_id, request_id=row_id, supersedes_id=parent)
            conn.execute("INSERT INTO crop_records VALUES(?,?,?,?,?,?,?,?,?)",
                         (row_id, "field", cycle, row_id, "harvest", "batch-1", parent,
                          record["created_at"], json.dumps(payload)))
            parent = row_id
    farms.purge("owner")
    assert records.db.query("SELECT count(*) FROM crop_records")[0][0] == 0


def observation(kind="stage", **changes):
    data = dict(entry="sample-1", day="2026-07-06", location="Row 1, first ten plants",
                source="Synthetic inspection", provenance="observed")
    data.update(dict(stage="flowering") if kind == "stage" else
                dict(symptoms="yellow leaf margins", inspected_count=10, affected_count=2))
    return dict(data, **changes)


@pytest.mark.parametrize("kind", ["stage", "scouting"])
def test_observation_revision_retry_void_restore_and_identity(setup, kind):
    records, farms = setup
    cycle = create(records)["cycle"]["id"]
    write = getattr(records, f"record_{kind}")
    data = observation(kind)
    first = write("owner", "field", cycle, data, request_id="one")
    assert write("owner", "field", cycle, data, request_id="one") == first
    with pytest.raises(ValueError, match="different data"):
        write("owner", "field", cycle, dict(data, location="Row 2"), request_id="one")
    with pytest.raises(ValueError, match="already exists"):
        write("owner", "field", cycle, data, request_id="duplicate")
    corrected = write("owner", "field", cycle, dict(data, location="Row 2"),
                      request_id="correct", supersedes_id=first["id"])
    with pytest.raises(ValueError, match="latest revision"):
        records.void_observation("owner", "field", first["id"], "Stale", request_id="stale")
    void = records.void_observation("owner", "field", corrected["id"], "Wrong location", request_id="void")
    assert records.void_observation("owner", "field", corrected["id"], "Wrong location", request_id="void") == void
    with pytest.raises(ValueError, match="already voided"):
        records.void_observation("owner", "field", void["id"], "Again", request_id="again")
    restored = write("owner", "field", cycle, data, request_id="restore", supersedes_id=void["id"])
    assert not restored["voided"]
    with pytest.raises(ValueError, match="preserve the cycle and entry"):
        write("owner", "field", cycle, dict(data, entry="renamed"), request_id="rename", supersedes_id=restored["id"])
    second_cycle = create(records, request="plant-2")["cycle"]["id"]
    with pytest.raises(ValueError, match="preserve the cycle and entry"):
        write("owner", "field", second_cycle, data, request_id="move", supersedes_id=restored["id"])
    journal = records.get("owner", "field", cycle)
    assert len(journal["records"]) == 5
    assert all(r["superseded"] for r in journal["records"][1:-1])
    assert len(farms.export("owner")["crop_records"]) == 6
    farms.purge("owner")
    assert records.db.query("SELECT count(*) FROM crop_records")[0][0] == 0
    with pytest.raises(ValueError):
        write("owner", "field", cycle, data, request_id="late")


def test_stage_summary_retains_same_day_locations_and_falls_back_after_void(setup):
    records, _ = setup
    cycle = create(records)["cycle"]["id"]
    earlier = records.record_stage("owner", "field", cycle, observation(day="2026-07-05"), request_id="early")
    a = records.record_stage("owner", "field", cycle, observation(entry="a", stage="vegetative"), request_id="a")
    b = records.record_stage("owner", "field", cycle, observation(entry="b", location="Row 2", stage="flowering"), request_id="b")
    journal = records.get("owner", "field", cycle)
    assert {r["id"] for r in journal["latest_stage_reports"]} == {a["id"], b["id"]}
    records.void_observation("owner", "field", b["id"], "Wrong cycle", request_id="void-b")
    assert [r["id"] for r in records.get("owner", "field", cycle)["latest_stage_reports"]] == [a["id"]]
    records.void_observation("owner", "field", a["id"], "Wrong cycle", request_id="void-a")
    assert [r["id"] for r in records.get("owner", "field", cycle)["latest_stage_reports"]] == [earlier["id"]]


def test_kinds_share_identifiers_but_cannot_cross_correct_or_void(setup):
    records, _ = setup
    planted = create(records)
    cycle = planted["cycle"]["id"]
    a = records.record_stage("owner", "field", cycle, observation(), request_id="a")
    b = records.record_scouting("owner", "field", cycle, observation("scouting"), request_id="b")
    c = records.record_harvest("owner", "field", cycle, harvest(batch="sample-1"), request_id="c")
    for method, data, prior in ((records.record_stage, observation(), b),
                              (records.record_scouting, observation("scouting"), a),
                              (records.record_harvest, harvest(batch="sample-1"), a)):
        with pytest.raises(ValueError, match="record kind"):
            method("owner", "field", cycle, data, request_id="bad", supersedes_id=prior["id"])
    for method, prior in ((records.void_observation, c), (records.void_harvest, a),
                          (records.void_observation, planted["record"])):
        with pytest.raises(ValueError, match="record kind"):
            method("owner", "field", prior["id"], "Wrong kind", request_id="bad")
    assert records.get("owner", "field", cycle)["harvest_totals"][0]["quantity"] == "10"


@pytest.mark.parametrize("changes", [dict(inspected_count=True), dict(inspected_count=10.0),
    dict(inspected_count=0, affected_count=0), dict(affected_count=11), dict(affected_count=-1),
    dict(inspected_count=None), dict(affected_count=None), dict(affected_count=float("nan")),
    dict(inspected_count=1_000_000_001), dict(day="2099-01-01"), dict(day="2026-06-30"),
    dict(provenance="forecast"), dict(location=" "), dict(source=""), dict(symptoms=""), dict(diagnosis="disease")])
def test_invalid_scouting_does_not_write(setup, changes):
    records, _ = setup
    cycle = create(records)["cycle"]["id"]
    with pytest.raises(ValueError):
        records.record_scouting("owner", "field", cycle, observation("scouting", **changes), request_id="bad")
    assert len(records.get("owner", "field", cycle)["records"]) == 1


def test_scouting_unknown_counts_and_overlapping_samples_remain_separate(setup):
    records, _ = setup
    cycle = create(records)["cycle"]["id"]
    for entry, counts in (("unknown", (None, None)), ("a", (10, 2)), ("b", (10, 0))):
        records.record_scouting("owner", "field", cycle, observation("scouting", entry=entry,
            inspected_count=counts[0], affected_count=counts[1]), request_id=entry)
    journal = records.get("owner", "field", cycle)
    samples = [r["data"] for r in journal["records"] if r["kind"] == "scouting"]
    assert [(s["inspected_count"], s["affected_count"]) for s in samples] == [(None, None), (10, 2), (10, 0)]
    assert journal["harvest_totals"] == []
    assert "scouting_totals" not in journal
    missing = observation("scouting")
    del missing["affected_count"]
    with pytest.raises(ValueError, match="required"):
        records.record_scouting("owner", "field", cycle, missing, request_id="missing")


@pytest.mark.parametrize("unit", ["field", "hydro", "aqua"])
def test_observation_dispatch_and_ownership_across_crop_systems(setup, unit):
    records, _ = setup
    cycle = create(records, unit)["cycle"]["id"]
    for kind in ("stage", "scouting"):
        args = dict(cycle_id=cycle, data=observation(kind), request_id=kind)
        result = execute_crop_records(records.db, "owner", unit, f"record_{kind}", args)
        assert result["unit_id"] == unit
        for owner, target in (("other", unit), ("owner", "other")):
            with pytest.raises(ValueError):
                execute_crop_records(records.db, owner, target, f"record_{kind}", args)
            with pytest.raises(ValueError):
                records.void_observation(owner, target, result["id"], "Wrong owner", request_id="void")
    contract = execute_crop_records(records.db, "owner", unit, "describe", {})
    assert {"record_stage", "record_scouting", "record_harvest"} <= contract["operations"].keys()
    with pytest.raises(ValueError):
        execute_crop_records(records.db, "other", unit, "describe", {})


@pytest.mark.parametrize("changes", [dict(day="2099-01-01"), dict(day="2026-06-30"),
    dict(stage=" "), dict(provenance="estimated"), dict(location=""), dict(entry=""), dict(source=None)])
def test_invalid_growth_stage_never_writes(setup, changes):
    records, _ = setup
    cycle = create(records)["cycle"]["id"]
    with pytest.raises(ValueError):
        records.record_stage("owner", "field", cycle, observation(**changes), request_id="bad")
    assert len(records.get("owner", "field", cycle)["records"]) == 1


def test_observations_follow_farm_calendar_and_cannot_target_another_units_cycle(setup, monkeypatch):
    import agronaut_agent.crop_records as module

    records, _ = setup
    cycle = create(records)["cycle"]["id"]
    records.db.execute("UPDATE farms SET timezone='Pacific/Kiritimati' WHERE id='farm'")
    fixed = datetime(2026, 7, 6, 12, 30, tzinfo=timezone.utc)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed.astimezone(tz) if tz is not None else fixed.replace(tzinfo=None)

    monkeypatch.setattr(module, "datetime", Clock)
    for kind in ("stage", "scouting"):
        write = getattr(records, f"record_{kind}")
        write("owner", "field", cycle, observation(kind, day="2026-07-07"), request_id=kind)
        with pytest.raises(ValueError, match="between planting and today"):
            write("owner", "field", cycle, observation(kind, entry="tomorrow", day="2026-07-08"), request_id="future")
        with pytest.raises(ValueError, match="does not belong"):
            write("owner", "hydro", cycle, observation(kind), request_id="cross-unit")
