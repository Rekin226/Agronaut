"""Patch/interval evidence, owned scope and read-only coupled diagnostics."""

import json
import subprocess
import sys
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from fractions import Fraction
from zoneinfo import ZoneInfo

import pytest

from agro_model.farm import Observation, ProductionCycle
from agronaut_agent.coupled_soil_evaluation import render_coupled_soil_comparison
from agronaut_agent.field_workflow import execute_field_workflow
from agronaut_agent.observation_quality import ObservationReviews
from agronaut_agent.tests.test_coupled_water_workflow import ROOT, example, next_day
from agronaut_agent.tests.test_field_workflow import setup as setup


def observation(store, identifier="soil", **changes):
    stamp = datetime(2026, 7, 6, 18, tzinfo=timezone.utc)
    data = dict(id=identifier, owner_id="owner", unit_id="field", cycle_id="cycle", quantity="soil_water_content",
        value=.23, unit="m3/m3", observed_at=stamp, recorded_at=stamp, provenance="measured", source="Synthetic interval sample")
    data.update(changes)
    data["recorded_at"] = max(data["recorded_at"], data["observed_at"])
    store.record_observation(data["owner_id"], Observation(**data))


def entry(**changes):
    data = dict(observation_id="soil", as_of="2026-07-06", patch_id="plot", top_m="0", bottom_m="1",
        representation_note="Synthetic complete patch, 0–1 m depth mean", water_content_adjustment=-.01,
        adjustment_source="Synthetic timing correction", expected_review=None, independence="held_out")
    data.update(changes)
    return data


def saved(workflow, data=None):
    return workflow.save("owner", "field", "coupled_soil_water", data or example(), request_id="coupled", cycle_id="cycle")


def compare(workflow, base, entries=None):
    return execute_field_workflow(workflow.db, "owner", "field", "compare_soil", dict(calculation_id=base["id"],
        comparisons=[entry()] if entries is None else entries, dataset_kind="synthetic", selection_note="Synthetic selected observations"))


def test_exact_prediction_signed_timing_adjustment_and_no_farm_mutation(setup):
    workflow, store = setup
    observation(store)
    base = saved(workflow)
    before = store.export("owner")
    report = compare(workflow, base)
    assert store.export("owner") == before
    row = report["comparisons"][0]
    assert row["values"]["predicted_water_content"]["exact"] == "223/1000"
    assert row["values"]["reference_water_content"]["exact"] == "11/50"
    assert row["values"]["error"]["exact"] == "3/1000"
    assert report["metrics"]["bias"] == pytest.approx(.003)
    assert report["metrics"]["unit"] == "m3/m3" and report["independence_status"] == "not_established"
    assert report["groups"][0]["patch_id"] == "plot"
    assert report["calculation"] == base and "positive error = wetter" in render_coupled_soil_comparison(report)
    assert workflow.review_soil("owner", "field", calculation_id=base["id"])["measurements"][0]["id"] == "soil"


def test_partial_intervals_use_exact_saved_layers_not_rounded_display_or_whole_profile(setup):
    workflow, store = setup
    observation(store)
    base = saved(workflow)
    report = compare(workflow, base, [entry(top_m="0.05", bottom_m="0.2", water_content_adjustment=0)])
    cells = base["result"]["result"]["days"][0]["ending_soil"]["patches"][0]["compartments"]
    expected = (Fraction(cells[0]["water_content"]["exact"])*Fraction(5,100) +
                Fraction(cells[1]["water_content"]["exact"])*Fraction(1,10))/Fraction(15,100)
    row = report["comparisons"][0]
    assert Fraction(row["values"]["predicted_water_content"]["exact"]) == expected
    assert len(row["represented_interval"]["layers"]) == 2


@pytest.mark.parametrize("use", ["used_for_initialization", "used_for_calibration", "unknown"])
def test_non_held_out_observations_are_visible_but_excluded(setup, use):
    workflow, store = setup
    observation(store)
    report = compare(workflow, saved(workflow), [entry(independence=use)])
    assert report["metrics"]["count"] == 0 and report["metrics"]["rmse"] is None
    assert report["excluded_count"] == 1 and not report["groups"]
    assert report["comparisons"][0]["exclusion_reason"]


def test_patch_depth_quality_and_weather_groups_do_not_merge(setup):
    workflow, store = setup
    data = example()
    other = deepcopy(data["initial_state"]["patches"][0])
    other.update(id="other", area_fraction="0.75")
    data["initial_state"]["patches"][0]["area_fraction"] = "0.25"
    data["initial_state"]["patches"].append(other)
    day = deepcopy(data["days"][0]["patches"][0])
    day["id"] = "other"
    data["days"][0]["patches"].append(day)
    observation(store)
    observation(store, "other", provenance="reported", quality="checked")
    report = compare(workflow, saved(workflow, data), [entry(), entry(observation_id="other", patch_id="other", bottom_m="0.3")])
    assert report["metrics"]["count"] == 2
    assert len(report["groups"]) == 2
    assert {g["patch_id"] for g in report["groups"]} == {"plot", "other"}


def test_review_revision_is_required_and_rejected_readings_cannot_be_used(setup):
    workflow, store = setup
    observation(store)
    base = saved(workflow)
    reviews = ObservationReviews(workflow.db)
    checked = reviews.review("owner", "field", "soil", "checked", "Synthetic review", "Synthetic", request_id="check", expected_revision=None)
    with pytest.raises(ValueError, match="review changed"):
        compare(workflow, base)
    assert compare(workflow, base, [entry(expected_review=checked["id"])])["groups"][0]["quality"] == "checked"
    rejected = reviews.review("owner", "field", "soil", "rejected", "Synthetic problem", "Synthetic", request_id="reject", expected_revision=checked["id"])
    with pytest.raises(ValueError, match="quality"):
        compare(workflow, base, [entry(expected_review=rejected["id"])])


@pytest.mark.parametrize("changes", [dict(patch_id="absent"), dict(top_m="0.5", bottom_m="0.2"), dict(bottom_m="1.1"),
    dict(top_m=0), dict(bottom_m="0"), dict(water_content_adjustment=None), dict(water_content_adjustment=True),
    dict(water_content_adjustment=float("nan")), dict(water_content_adjustment=.9), dict(water_content_adjustment=-.9),
    dict(representation_note=""), dict(adjustment_source=""), dict(independence=None), dict(as_of="2026-07-07"),
    dict(extra="ignored")])
def test_invalid_or_missing_evidence_fails_without_mutation(setup, changes):
    workflow, store = setup
    observation(store)
    base = saved(workflow)
    before = store.export("owner")
    with pytest.raises(ValueError):
        compare(workflow, base, [entry(**changes)])
    assert store.export("owner") == before


@pytest.mark.parametrize("case", ["unit", "cycle", "forecast", "depletion", "outside_day", "duplicate", "timezone"])
def test_observation_scope_type_time_and_duplicate_guards(setup, case):
    workflow, store = setup
    data = {}
    if case == "unit":
        data.update(unit_id="field2", cycle_id="cycle2")
    if case == "cycle":
        store.create_cycle("owner", ProductionCycle("another", "field", "tomato", "2026-07-01"))
        data["cycle_id"] = "another"
    if case == "forecast":
        data["provenance"] = "forecast"
    if case == "depletion":
        data.update(quantity="soil_depletion", unit="mm")
    if case == "outside_day":
        data["observed_at"] = datetime(2026, 7, 5, 23, 59, tzinfo=timezone.utc)
    observation(store, **data)
    base = saved(workflow)
    if case == "timezone":
        workflow.db.execute("UPDATE farms SET timezone='Asia/Taipei' WHERE id='farm'")
    before = store.export("owner")
    with pytest.raises(ValueError):
        compare(workflow, base, [entry(), entry()] if case == "duplicate" else None)
    with pytest.raises(ValueError):
        workflow.compare_soil("other", "field", calculation_id=base["id"], comparisons=[entry()], dataset_kind="synthetic", selection_note="Synthetic")
    assert store.export("owner") == before


def test_midnight_reading_continuation_and_observed_model_disagreement_remain_visible(setup):
    workflow, store = setup
    observation(store, value=.9, observed_at=datetime(2026, 7, 8, tzinfo=timezone.utc))
    base = saved(workflow)
    weather, days = next_day(example())
    child = workflow.continue_coupled_water("owner", "field", base["id"], weather, days, "Synthetic continuation", request_id="next")
    report = compare(workflow, child, [entry(as_of="2026-07-07", water_content_adjustment=0)])
    assert report["comparisons"][0]["values"]["reference_water_content"]["exact"] == "9/10"
    assert report["metrics"]["bias"] < 0
    assert workflow.get("owner", "field", base["id"]) == base


@pytest.mark.parametrize("zone,day,hours", [("Africa/Ouagadougou", "2026-07-06", 24),
    ("Asia/Taipei", "2026-07-06", 24), ("America/New_York", "2026-03-08", 23), ("America/New_York", "2025-11-02", 25)])
def test_local_day_and_closing_midnight_match_region_and_dst(setup, zone, day, hours):
    workflow, store = setup
    workflow.db.execute("UPDATE farms SET timezone=? WHERE id='farm'", (zone,))
    workflow.db.execute("UPDATE production_cycles SET planted_on='2025-01-01' WHERE id='cycle'")
    data = example()
    target = date.fromisoformat(day)
    data["site"]["timezone"] = data["weather"][0]["timezone"] = zone
    data["weather"][0].update(day=day, period_hours=hours)
    data["days"][0]["day"] = day
    data["initial_state"]["as_of"] = (target-timedelta(days=1)).isoformat()
    closing = datetime.combine(target+timedelta(days=1), datetime.min.time(), ZoneInfo(zone))
    observation(store, observed_at=closing)
    base = saved(workflow, data)
    assert compare(workflow, base, [entry(as_of=day)])["metrics"]["count"] == 1
    observation(store, "late", observed_at=closing+timedelta(seconds=1))
    with pytest.raises(ValueError, match="local day"):
        compare(workflow, base, [entry(observation_id="late", as_of=day)])


def test_future_scenario_does_not_allow_a_completed_day_comparison(setup):
    workflow, store = setup
    target = datetime.now(timezone.utc).date()+timedelta(days=2)
    data = example()
    data["initial_state"]["as_of"] = (target-timedelta(days=1)).isoformat()
    data["weather"][0]["day"] = data["days"][0]["day"] = target.isoformat()
    observation(store, observed_at=datetime.combine(target, datetime.min.time(), timezone.utc))
    base = saved(workflow, data)
    with pytest.raises(ValueError, match="completed local day"):
        compare(workflow, base, [entry(as_of=target.isoformat())])


def test_stdlib_cli_compares_owned_measurements_without_saving_a_result(tmp_path):
    db, path = tmp_path/"diagnostic.sqlite3", tmp_path/"request.json"
    def command(group, operation, data):
        path.write_text(json.dumps(data))
        result = subprocess.run([sys.executable, "-S", "-m", "agronaut_agent.cli", group, "--db", str(db), operation, "--input", str(path)],
            cwd=ROOT, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout+result.stderr
        return json.loads(result.stdout)
    command("farm", "create", json.loads((ROOT/"examples/soil_unit.json").read_text()))
    data = example()
    data["site"]["timezone"] = data["weather"][0]["timezone"] = "Africa/Accra"
    base = command("field", "save", dict(task="coupled_soil_water", inputs=data, request_id="base"))
    reading = command("crop", "record_measurement", dict(request_id="reading", data=dict(quantity="soil_water_content", value=.23, unit="m3/m3",
        observed_at="2026-07-06T18:00:00+00:00", provenance="measured", source="Synthetic CLI sample")))
    report = command("field", "compare_soil", dict(calculation_id=base["id"], comparisons=[entry(observation_id=reading["id"])],
        dataset_kind="synthetic", selection_note="Synthetic CLI fixture"))
    assert report["metrics"]["count"] == 1
    assert len(command("field", "list", {})["calculations"]) == 1
