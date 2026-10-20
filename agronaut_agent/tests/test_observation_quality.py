"""Quality reviews preserve originals and govern their use in crop closure."""

import json
import sqlite3
from datetime import datetime, timezone

import pytest

from agro_model.farm import Observation
from agronaut_agent.crop_records import execute_crop_records
from agronaut_agent.observation_quality import ObservationReviews
from agronaut_agent.store import SCHEMA_VERSION, _Db
from agronaut_agent.tests.test_crop_records import create
from agronaut_agent.tests.test_crop_records import setup as setup
from agronaut_agent.tests.test_cycle_lifecycle import close, reopen


def measure(farms, cycle=None, *, unit="field", owner="owner", key="gauge", quality="unverified", provenance="measured"):
    at = datetime(2026, 7, 11, 12, tzinfo=timezone.utc)
    original = Observation(key, owner, unit, cycle, "rainfall", 5, "mm", at, at, provenance, "Synthetic gauge", quality)
    farms.record_observation(owner, original)
    return original


def review(records, quality="rejected", revision=None, request="reject", *, unit="field", owner="owner", key="gauge"):
    return ObservationReviews(records.db).review(owner, unit, key, quality, "Operator review", "Synthetic notebook",
                                                request_id=request, expected_revision=revision)


def test_review_reject_close_restore_reopen_history_and_original_retry(setup):
    records, farms = setup
    cycle = create(records)["cycle"]["id"]
    original = measure(farms, cycle)
    with pytest.raises(ValueError, match="precedes a current actual"):
        close(records, cycle)
    rejected = review(records)
    assert review(records) == rejected
    assert farms.record_observation("owner", original) is False
    closed = close(records, cycle)
    rows = farms.observations("owner", "field")
    assert rows[0]["quality"] == "unverified" and rows[0]["effective_quality"] == "rejected"
    assert rows[0]["quality_history"] == [rejected]
    assert rows[0]["value"] == original.value and rows[0]["source"] == original.source
    with pytest.raises(ValueError, match="after the crop cycle end"):
        review(records, "checked", rejected["id"], "restore")
    with pytest.raises(ValueError, match="after the crop cycle end"):
        review(records, "unverified", rejected["id"], "restore-unverified")
    reopened = reopen(records, cycle, closed["id"])
    restored = review(records, "checked", rejected["id"], "restore")
    assert restored["supersedes_id"] == rejected["id"]
    assert review(records) == rejected  # Retrying old rejection cannot reapply it.
    row = records.get("owner", "field", cycle)["measurements"][0]
    assert row["effective_quality"] == "checked" and len(row["quality_history"]) == 2
    with pytest.raises(ValueError, match="precedes a current actual"):
        close(records, cycle, revision=reopened["id"], request="close-again")
    close(records, cycle, day="2026-07-11", revision=reopened["id"], request="correct-end")
    exported = farms.export("owner")
    assert exported["observations"][0]["quality"] == "unverified"
    assert exported["observation_reviews"] == [rejected, restored]
    assert json.loads(records.db.query("SELECT payload FROM field_observations WHERE id='gauge'")[0][0])["quality"] == "unverified"


@pytest.mark.parametrize("unit", ["field", "hydro", "aqua"])
def test_list_filter_scope_dispatch_and_unassigned_measurements(setup, unit):
    records, farms = setup
    cycle = create(records, unit)["cycle"]["id"]
    measure(farms, cycle, unit=unit)
    measure(farms, unit=unit, key="unassigned")
    own = records.list_measurements("owner", unit)
    assert len(own["measurements"]) == 2
    assert len(records.list_measurements("owner", unit, cycle)["measurements"]) == 1
    args = dict(observation_id="unassigned", quality="checked", reason="Read notebook", source="Synthetic fixture",
                request_id="review", expected_revision=None)
    row = execute_crop_records(records.db, "owner", unit, "review_measurement", args)
    assert row["observation_id"] == "unassigned"
    assert next(r for r in execute_crop_records(records.db, "owner", unit, "list_measurements", {})["measurements"]
                if r["id"] == "unassigned")["effective_quality"] == "checked"
    for owner, target in (("other", unit), ("owner", "other"), ("owner", "hydro" if unit != "hydro" else "field")):
        with pytest.raises(ValueError):
            review(records, unit=target, owner=owner)
    with pytest.raises(ValueError):
        records.list_measurements("other", unit)
    with pytest.raises(ValueError, match="does not belong"):
        records.list_measurements("owner", "hydro" if unit != "hydro" else "field", cycle)


def test_stale_revision_conflicting_retry_and_missing_expected_revision_fail_without_writes(setup):
    records, farms = setup
    measure(farms)
    first = review(records)
    with pytest.raises(ValueError, match="different data"):
        review(records, "checked")
    with pytest.raises(ValueError, match="review changed"):
        review(records, "checked", None, "stale")
    with pytest.raises(ValueError, match="review changed"):
        review(records, "checked", "foreign-review", "stale")
    args = dict(observation_id="gauge", quality="checked", reason="Review", source="Fixture", request_id="missing")
    with pytest.raises(ValueError, match="expected_revision"):
        execute_crop_records(records.db, "owner", "field", "review_measurement", args)
    with pytest.raises(ValueError, match="unknown fields"):
        execute_crop_records(records.db, "owner", "field", "review_measurement", dict(args, expected_revision=first["id"], value=2))
    assert len(farms.export("owner")["observation_reviews"]) == 1


@pytest.mark.parametrize("changes", [dict(quality="approved"), dict(quality=True), dict(reason=" "), dict(source=""),
                                    dict(expected_revision=7), dict(observation_id=None), dict(request_id="")])
def test_invalid_review_does_not_change_effective_quality(setup, changes):
    records, farms = setup
    measure(farms)
    args = dict(observation_id="gauge", quality="rejected", reason="Wrong date", source="Fixture", request_id="review", expected_revision=None)
    with pytest.raises(ValueError):
        ObservationReviews(records.db).review("owner", "field", **dict(args, **changes))
    assert farms.observations("owner", "field")[0]["effective_quality"] == "unverified"
    assert farms.export("owner")["observation_reviews"] == []


def test_review_never_converts_forecast_to_actual_and_original_rejection_can_be_restored(setup):
    records, farms = setup
    cycle = create(records)["cycle"]["id"]
    measure(farms, cycle, provenance="forecast")
    review(records, "checked", request="check-forecast")
    closed = close(records, cycle)
    row = records.get("owner", "field", cycle)["measurements"][0]
    assert row["provenance"] == "forecast" and row["effective_quality"] == "checked"
    measure(farms, cycle, key="rejected", quality="rejected")
    with pytest.raises(ValueError, match="after the crop cycle end"):
        review(records, "unverified", key="rejected", request="restore-original")
    reopen(records, cycle, closed["id"])
    review(records, "unverified", key="rejected", request="restore-original")


def test_restore_checks_farm_local_date(setup):
    records, farms = setup
    records.db.execute("UPDATE farms SET timezone='Pacific/Kiritimati' WHERE id='farm'")
    cycle = create(records)["cycle"]["id"]
    # July 11 at noon UTC is July 12 at this farm.
    measure(farms, cycle, quality="rejected")
    close(records, cycle, day="2026-07-11")
    with pytest.raises(ValueError, match="after the crop cycle end"):
        review(records, "checked", request="restore")


def test_v6_upgrade_backup_and_failed_migration_preserve_originals(setup, monkeypatch):
    import agronaut_agent.observation_quality as module

    records, farms = setup
    original = measure(farms)
    path = records.db.path
    records.db.execute("DROP TABLE farm_resource_plans")
    records.db.execute("DROP TABLE observation_reviews")
    records.db.execute("PRAGMA user_version=6")
    records.db._conn.close()
    migration = module.migrate_observation_reviews

    def fail(conn):
        migration(conn)
        raise RuntimeError("Synthetic upgrade failure")

    monkeypatch.setattr(module, "migrate_observation_reviews", fail)
    with pytest.raises(RuntimeError, match="upgrade failure"):
        _Db(path)
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 6
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='observation_reviews'").fetchone() is None
        assert json.loads(conn.execute("SELECT payload FROM field_observations").fetchone()[0])["value"] == original.value
    monkeypatch.setattr(module, "migrate_observation_reviews", migration)
    upgraded = _Db(path)
    assert upgraded.query("PRAGMA user_version")[0][0] == SCHEMA_VERSION
    rows = module.reviewed_observation(upgraded._conn, json.loads(upgraded.query("SELECT payload FROM field_observations")[0][0]))
    assert rows["effective_quality"] == "unverified" and rows["quality_history"] == []
    for backup_path in path.parent.glob(f"*.pre-v{SCHEMA_VERSION}-*.bak"):
        with sqlite3.connect(backup_path) as backup:
            assert backup.execute("PRAGMA user_version").fetchone()[0] == 6
            assert backup.execute("SELECT count(*) FROM field_observations").fetchone()[0] == 1
    assert list(path.parent.glob(f"*.pre-v{SCHEMA_VERSION}-*.bak"))
    upgraded._conn.close()


def test_erasure_of_long_review_history_is_owned_and_rejects_late_writes(setup):
    records, farms = setup
    measure(farms)
    measure(farms, owner="other", unit="other", key="other-gauge")
    row = review(records)
    review(records, owner="other", unit="other", key="other-gauge")
    parent = row["id"]
    with records.db.transaction() as conn:
        for n in range(1100):
            identifier = f"review-{n}"
            payload = dict(row, id=identifier, supersedes_id=parent, request_id=identifier)
            conn.execute("INSERT INTO observation_reviews VALUES(?,?,?,?,?,?,?)",
                         (identifier, "field", "gauge", identifier, parent, row["created_at"], json.dumps(payload)))
            parent = identifier
    farms.purge("owner")
    assert all(not data for data in farms.export("owner").values())
    assert len(farms.export("other")["observation_reviews"]) == 1
    with pytest.raises(ValueError):
        review(records, "checked", parent, "late")
