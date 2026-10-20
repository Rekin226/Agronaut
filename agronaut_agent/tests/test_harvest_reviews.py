"""Harvest denominators, batch scope, immutable corrections and current evidence checks."""

import json
import subprocess
import sys
from datetime import datetime, timezone
from decimal import localcontext
from fractions import Fraction

import pytest

from agronaut_agent.crop_records import execute_crop_records
from agronaut_agent.harvest_reviews import HarvestReviews
from agronaut_agent.tests.test_crop_records import create, harvest
from agronaut_agent.tests.test_crop_records import setup as setup


def data(ids, **changes):
    return dict(dict(entry="north-week1", start="2026-07-01", end="2026-07-10", area_m2="300",
        location="Synthetic north plot", area_source="Synthetic ground survey",
        coverage="partial", scope_note="These whole batches are repeated pickings from the same 300 m² footprint; other pickings are missing.",
        source="Synthetic collection notebook", harvest_record_ids=ids), **changes)


def prepared(records):
    cycle = create(records)["cycle"]["id"]
    batch = records.record_harvest("owner", "field", cycle, harvest(), request_id="batch")
    return HarvestReviews(records.db), cycle, batch


def test_exact_mass_and_denominator_once_across_pickings_with_snapshot_export(setup):
    records, farms = setup
    service, cycle, first = prepared(records)
    second = records.record_harvest("owner", "field", cycle, harvest(batch="b2", quantity=500, unit="g"), request_id="b2")
    before = farms.export("owner")
    request = data([first["id"], second["id"]])
    with localcontext() as context:
        context.prec = 3
        saved = service.save("owner", "field", cycle, request, request_id="review")
    group = saved["accounting"]["groups"][0]
    assert group["quantity"] == dict(exact="21/2", display="10.500000")
    assert group["per_ha"] == dict(exact="350", display="350.000000")
    assert group["per_ha_unit"] == "kg/ha"
    assert len(group["record_ids"]) == 2 and saved["data"]["area_m2"] == "300"
    assert saved["accounting"]["selected_records"][0]["data"] == first["data"]
    assert service.save("owner", "field", cycle, request, request_id="review") == saved
    after = farms.export("owner")
    assert after["crop_records"] == before["crop_records"] + [saved]
    assert {k: v for k, v in before.items() if k != "crop_records"} == {k: v for k, v in after.items() if k != "crop_records"}
    assert records.get("owner", "field", cycle)["harvest_reviews"]["current"][0]["review"] == dict(needs_review=False, flags=[])
    assert records.get("owner", "field", cycle)["harvest_totals"][0]["quantity"] == "10.500"


def test_groups_remain_separate_and_nonterminating_ratio_is_exact(setup):
    records, _ = setup
    service, cycle, first = prepared(records)
    ids = [first["id"]]
    for index, change in enumerate((dict(unit="count"), dict(basis="dry"), dict(grade="marketable"),
                                  dict(provenance="estimated"), dict(product="seed"), dict(unit="t", quantity=0.001))):
        ids.append(records.record_harvest("owner", "field", cycle, harvest(batch=str(index), **change), request_id=str(index))["id"])
    report = service.save("owner", "field", cycle, data(ids), request_id="review")
    groups = report["accounting"]["groups"]
    assert len(groups) == 6
    mass = next(g for g in groups if len(g["record_ids"]) == 2)
    assert mass["quantity"]["exact"] == "11"
    assert mass["per_ha"] == dict(exact="1100/3", display="366.666667")
    assert next(g for g in groups if g["unit"] == "count")["per_ha"]["exact"] == "1000/3"
    assert "total" not in report["accounting"]


@pytest.mark.parametrize("changes", [dict(area_m2=300), dict(area_m2="0"), dict(area_m2="-1"), dict(area_m2="NaN"),
    dict(area_m2="1e2"), dict(area_m2="1.0000001"), dict(area_m2="1000000000000"), dict(area_source=" "),
    dict(scope_note=""), dict(coverage=True), dict(coverage="validated"), dict(start="2026-07-11"),
    dict(start="2026-06-30"), dict(end="9999-01-01"), dict(harvest_record_ids=[]), dict(harvest_record_ids="batch"),
    dict(harvest_record_ids=[None]), dict(harvest_record_ids=["no", "no"]), dict(unexpected=True)])
def test_invalid_requests_are_atomic(setup, changes):
    records, farms = setup
    service, cycle, batch = prepared(records)
    before = farms.export("owner")
    with pytest.raises(ValueError):
        service.save("owner", "field", cycle, data([batch["id"]], **changes), request_id="bad")
    assert farms.export("owner") == before


def test_owned_current_whole_batches_and_dates_are_required(setup):
    records, _ = setup
    service, cycle, batch = prepared(records)
    other_cycle = create(records, request="next")["cycle"]["id"]
    hydro = create(records, unit="hydro")["cycle"]["id"]
    hydro_batch = records.record_harvest("owner", "hydro", hydro, harvest(), request_id="hb")
    for owner, unit, selected_cycle, payload in (
        ("other", "field", cycle, data([batch["id"]])), ("owner", "other", cycle, data([batch["id"]])),
        ("owner", "field", other_cycle, data([batch["id"]])), ("owner", "field", cycle, data([hydro_batch["id"]])),
        ("owner", "hydro", hydro, data([hydro_batch["id"]])), ("owner", "field", cycle, data([batch["id"]], end="2026-07-05"))):
        with pytest.raises(ValueError):
            service.save(owner, unit, selected_cycle, payload, request_id="bad")
    void = records.void_harvest("owner", "field", batch["id"], "Duplicate", request_id="void")
    for key in (batch["id"], void["id"]):
        with pytest.raises(ValueError, match="current, non-voided"):
            service.save("owner", "field", cycle, data([key]), request_id="bad")


def test_review_correct_void_restore_cas_and_retry_original_snapshot(setup):
    records, farms = setup
    service, cycle, batch = prepared(records)
    payload = data([batch["id"]])
    first = service.save("owner", "field", cycle, payload, request_id="review")
    with pytest.raises(ValueError, match="reason"):
        service.save("owner", "field", cycle, payload, request_id="correct", supersedes_id=first["id"])
    for changed_cycle, changed_payload in ((create(records, request="second")["cycle"]["id"], payload), (cycle, dict(payload, entry="other"))):
        with pytest.raises(ValueError, match="preserve"):
            service.save("owner", "field", changed_cycle, changed_payload, request_id="bad", supersedes_id=first["id"], reason="Typo")
    second = service.save("owner", "field", cycle, dict(payload, area_m2="200"), request_id="correct", supersedes_id=first["id"], reason="Survey correction")
    assert second["accounting"]["groups"][0]["per_ha"]["exact"] == "500"
    with pytest.raises(ValueError, match="latest revision"):
        service.void("owner", "field", first["id"], "Stale", request_id="bad")
    with pytest.raises(ValueError, match="entry already exists"):
        service.save("owner", "field", cycle, payload, request_id="duplicate")
    with pytest.raises(ValueError, match="different data"):
        service.save("owner", "field", cycle, dict(payload, area_m2="200"), request_id="review")
    void = service.void("owner", "field", second["id"], "Wrong scope", request_id="void-review")
    assert records.get("owner", "field", cycle)["harvest_reviews"]["current"] == []
    assert void["accounting"] == second["accounting"]
    restored = service.save("owner", "field", cycle, payload, request_id="restore", supersedes_id=void["id"], reason="Checked original scope")
    current = records.get("owner", "field", cycle)["harvest_reviews"]["current"]
    assert [r["id"] for r in current] == [restored["id"]]
    assert service.save("owner", "field", cycle, payload, request_id="review") == first
    assert service.void("owner", "field", second["id"], "Wrong scope", request_id="void-review") == void
    with pytest.raises(ValueError, match="record kind"):
        records.void_harvest("owner", "field", restored["id"], "Wrong kind", request_id="bad")
    farms.purge("owner")
    assert all(not rows for rows in farms.export("owner").values())


def test_source_changes_flag_but_never_rewrite_saved_arithmetic(setup):
    records, _ = setup
    service, cycle, batch = prepared(records)
    payload = data([batch["id"]], coverage="complete")
    saved = service.save("owner", "field", cycle, payload, request_id="review")
    newer = records.record_harvest("owner", "field", cycle, harvest(quantity=20), request_id="correct", supersedes_id=batch["id"])
    current = records.get("owner", "field", cycle)["harvest_reviews"]["current"][0]
    assert current["accounting"] == saved["accounting"]
    assert current["review"]["flags"] == ["selected_harvest_revised_or_voided", "harvest_records_in_period_changed"]
    assert service.save("owner", "field", cycle, payload, request_id="review") == saved
    with pytest.raises(ValueError, match="current, non-voided"):
        service.save("owner", "field", cycle, payload, request_id="bad", supersedes_id=saved["id"], reason="Recheck")
    revised = service.save("owner", "field", cycle, dict(payload, harvest_record_ids=[newer["id"]]),
                           request_id="update", supersedes_id=saved["id"], reason="Use corrected weight")
    assert Fraction(revised["accounting"]["groups"][0]["per_ha"]["exact"]) == Fraction(2000, 3)
    records.record_harvest("owner", "field", cycle, harvest(batch="late", quantity=2), request_id="late")
    assert records.get("owner", "field", cycle)["harvest_reviews"]["current"][0]["review"]["flags"] == ["harvest_records_in_period_changed"]
    journal = records.get("owner", "field", cycle)
    records.correct_planting("owner", "field", cycle, "pepper", "2026-07-02", "Notebook", "Correct identity",
                            request_id="plant-correct", expected_revision=journal["planting"]["revision_id"])
    records.close_cycle("owner", "field", cycle, "2026-07-06", "Ended", "Notebook", request_id="close", expected_revision=None)
    records.db.execute("UPDATE farms SET timezone='Asia/Taipei' WHERE id='farm'")
    current = records.season_review("owner", "field", cycle)["journal"]["harvest_reviews"]["current"][0]
    assert set(current["review"]["flags"]) == {"harvest_records_in_period_changed", "planting_changed", "cycle_lifecycle_changed", "farm_timezone_changed"}
    assert current["accounting"] == revised["accounting"]


def test_completed_local_dates_closure_and_comparison_with_unknowns(setup, monkeypatch):
    import agronaut_agent.harvest_reviews as module

    records, _ = setup
    service, cycle, batch = prepared(records)
    records.db.execute("UPDATE farms SET timezone='Pacific/Kiritimati' WHERE id='farm'")
    fixed = datetime(2026, 7, 6, 12, 30, tzinfo=timezone.utc)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed.astimezone(tz)

    monkeypatch.setattr(module, "datetime", Clock)
    payload = data([batch["id"]], end="2026-07-07", coverage="unknown")
    saved = service.save("owner", "field", cycle, payload, request_id="review")
    with pytest.raises(ValueError, match="today"):
        service.save("owner", "field", cycle, dict(payload, entry="future", end="2026-07-08"), request_id="future")
    records.close_cycle("owner", "field", cycle, "2026-07-06", "Done", "Notebook", request_id="close", expected_revision=None)
    with pytest.raises(ValueError, match="after the crop cycle end"):
        service.save("owner", "field", cycle, dict(payload, entry="after"), request_id="after")
    second = create(records, request="next")["cycle"]["id"]
    compared = records.compare_seasons("owner", "field", [cycle, second])
    current = compared["seasons"][0]["harvest_reviews"]["current"][0]
    assert current["id"] == saved["id"] and current["review"]["needs_review"]
    assert compared["seasons"][1]["harvest_reviews"]["current"] == []
    assert current["data"]["coverage"] == "unknown"


def test_stdlib_cli_discovery_save_and_readback(setup, tmp_path):
    records, _ = setup
    _, cycle, batch = prepared(records)
    # Match the identity the CLI resolves without touching the operator's database.
    from agronaut_agent.store import ConversationStore
    owner = ConversationStore(records.db).get_or_create_user("cli", "tester")
    records.db.execute("UPDATE farms SET owner_id=? WHERE id='farm'", (owner,))
    contract = execute_crop_records(records.db, owner, "field", "describe", {})
    assert "record_harvest_review" in contract["operations"]
    payload = tmp_path / "review.json"
    payload.write_text(json.dumps(dict(cycle_id=cycle, data=data([batch["id"]]), request_id="cli")))
    command = [sys.executable, "-S", "-m", "agronaut_agent.cli", "crop", "--db", str(tmp_path / "crops.sqlite3"),
               "--user", "tester", "--unit", "field"]
    completed = subprocess.run([*command, "record_harvest_review", "--input", str(payload)], capture_output=True, text=True, check=True)
    saved = json.loads(completed.stdout)
    assert saved["accounting"]["groups"][0]["per_ha"]["exact"] == "1000/3"
    assert records.get(owner, "field", cycle)["harvest_reviews"]["current"][0]["id"] == saved["id"]
