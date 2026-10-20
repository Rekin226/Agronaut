"""Measurement reconciliation preserves immutable evidence and physical water logs."""

from dataclasses import asdict
from datetime import datetime, timezone

import pytest

from agro_model.farm import Observation, ProductionCycle
from agro_model.tests.test_soil_state import update
from agronaut_agent.field_io import evaluate_field_request, json_ready, render_field_report
from agronaut_agent.field_workflow import execute_field_workflow
from agronaut_agent.observation_quality import ObservationReviews
from agronaut_agent.tests.test_crop_coefficient_workflow import series
from agronaut_agent.tests.test_field_workflow import inputs, water
from agronaut_agent.tests.test_field_workflow import setup as setup


def measurement(store, *, identifier="soil", unit="field", cycle="cycle", quality="unverified", provenance="measured"):
    stamp = datetime(2026, 7, 6, 18, tzinfo=timezone.utc)
    reading = Observation(identifier, "owner", unit, cycle, "soil_water_content", .25, "m3/m3",
                          stamp, stamp, provenance, "Synthetic soil sampling", quality)
    store.record_observation("owner", reading)
    return reading


def adoption(parent, **changes):
    data = dict(calculation_id=parent["id"], observation_id="soil", as_of="2026-07-06", represented_depth_m=.8,
        representation_note="Synthetic root-zone/field mean", depletion_adjustment_mm=2,
        adjustment_source="Explicit net depletion before midnight", reason="Supplied sampling result",
        expected_review=None, request_id="adopt")
    data.update(changes)
    return data


def test_state_update_runs_after_balance_and_controls_next_day_without_inventing_fluxes():
    data = series()
    original = evaluate_field_request(data, "irrigation-plan")
    data["soil_state_updates"] = [json_ready(asdict(update()))]
    result = evaluate_field_request(data, "irrigation-plan")
    first, second = result["days"][:2]
    assert first["balance"] == original["days"][0]["balance"]
    assert first["soil_state_update"]["values"]["adopted_depletion_mm"] == pytest.approx(58)
    assert second["balance"]["inputs"]["state"]["depletion_mm"] == pytest.approx(58)
    assert result["inputs"]["events"] == original["inputs"]["events"]
    assert "separate from irrigation, rain and drainage" in render_field_report(result)
    data["initial_state"] = None
    uninitialized = evaluate_field_request(data, "irrigation-plan")
    assert uninitialized["days"][0]["balance"] is None
    assert uninitialized["days"][0]["soil_state_update"]["values"]["reconciliation_mm"] is None
    assert uninitialized["days"][1]["balance"]["inputs"]["state"]["depletion_mm"] == pytest.approx(58)


def test_initial_checkpoint_requires_valid_original_state_and_unique_dates():
    data = inputs()
    entry = json_ready(asdict(update()))
    entry.update(as_of="2026-07-05", observed_at="2026-07-06T00:00:00+00:00")
    data["soil_state_updates"] = [entry]
    result = evaluate_field_request(data, "irrigation-plan")
    assert result["initial_soil_state_update"]["values"]["adopted_depletion_mm"] == pytest.approx(58)
    assert result["days"][0]["balance"]["inputs"]["state"]["depletion_mm"] == pytest.approx(58)
    data["soil_state_updates"].append(entry)
    with pytest.raises(ValueError, match="duplicate"):
        evaluate_field_request(data, "irrigation-plan")
    data["soil_state_updates"].pop()
    data["initial_state"]["depletion_mm"] = 1000
    with pytest.raises(ValueError, match="initial depletion"):
        evaluate_field_request(data, "irrigation-plan")


def test_adoption_quality_race_stable_retry_and_explicit_withdrawal(setup):
    workflow, store = setup
    measurement(store)
    base = workflow.save("owner", "field", "root_zone_budget", series(), request_id="base", cycle_id="cycle")
    args = adoption(base)
    child = execute_field_workflow(workflow.db, "owner", "field", "apply_soil_state", args)
    assert child["parent_id"] == base["id"]
    assert child["inputs"]["initial_state"] == base["inputs"]["initial_state"]
    assert child["soil_state_evidence"][0]["observation"]["effective_quality"] == "unverified"
    assert not workflow.review_soil("owner", "field", calculation_id=child["id"])["needs_review"]
    review = ObservationReviews(workflow.db).review("owner", "field", "soil", "checked", "Reviewed notebook", "Fixture",
                                                  request_id="review", expected_revision=None)
    assert workflow.review_soil("owner", "field", calculation_id=child["id"])["needs_review"]
    assert workflow.apply_soil_state("owner", "field", **args) == child
    with pytest.raises(ValueError, match="review changed"):
        workflow.apply_soil_state("owner", "field", **adoption(child, request_id="stale"))
    renewed = workflow.apply_soil_state("owner", "field", **adoption(child, expected_review=review["id"], request_id="renew"))
    assert renewed["soil_state_evidence"][0]["observation"]["effective_quality"] == "checked"
    withdrawn = workflow.remove_soil_state("owner", "field", calculation_id=renewed["id"], as_of="2026-07-06",
                                           reason="Withdraw supplied state", request_id="remove")
    assert withdrawn["result"] == base["result"]
    assert "soil_state_evidence" not in withdrawn
    assert workflow.get("owner", "field", base["id"]) == base
    assert workflow.get("owner", "field", child["id"]) == child
    assert workflow.list("owner", "field")["water_days"] == []
    assert len(store.observations("owner", "field")) == 1


@pytest.mark.parametrize("quality,provenance", [("rejected", "measured"), ("unverified", "forecast")])
def test_ineligible_readings_cannot_be_adopted(setup, quality, provenance):
    workflow, store = setup
    measurement(store, quality=quality, provenance=provenance)
    base = workflow.save("owner", "field", "root_zone_budget", inputs(), request_id="base", cycle_id="cycle")
    with pytest.raises(ValueError):
        workflow.apply_soil_state("owner", "field", **adoption(base))
    assert len(workflow.list("owner", "field")["calculations"]) == 1


def test_scope_and_cycle_must_match_but_unassigned_measurement_is_explicitly_usable(setup):
    workflow, store = setup
    base = workflow.save("owner", "field", "root_zone_budget", inputs(), request_id="base", cycle_id="cycle")
    store.create_cycle("owner", ProductionCycle("different", "field", "tomato", "2026-07-01"))
    measurement(store, identifier="different", cycle="different")
    measurement(store, identifier="otherunit", unit="field2", cycle="cycle2")
    for identifier in ("different", "otherunit", "unknown"):
        with pytest.raises(ValueError):
            workflow.apply_soil_state("owner", "field", **adoption(base, observation_id=identifier))
    with pytest.raises(ValueError):
        workflow.review_soil("other", "field", calculation_id=base["id"])
    measurement(store, cycle=None)
    child = workflow.apply_soil_state("owner", "field", **adoption(base))
    assert child["soil_state_evidence"][0]["observation"]["cycle_id"] is None


def test_water_crop_and_weather_revisions_preserve_soil_evidence(setup):
    workflow, store = setup
    measurement(store)
    base = workflow.save("owner", "field", "root_zone_budget", inputs(), request_id="base", cycle_id="cycle")
    child = workflow.apply_soil_state("owner", "field", **adoption(base))
    actual = workflow.record_day("owner", "field", water(), request_id="water", cycle_id="cycle")
    replayed = workflow.replay("owner", "field", child["id"], [actual["id"]], request_id="replay")
    revised = workflow.revise_crop("owner", "field", replayed["id"],
        dict(coefficient=dict(value=.7, stage="Supplied", source="Synthetic Kc")), "Revised Kc", request_id="kc")
    refreshed = workflow.revise_weather("owner", "field", revised["id"],
        [dict(inputs()["weather"][0], net_radiation_mj_m2=16)], request_id="weather")
    for saved in (replayed, revised, refreshed):
        assert saved["soil_state_evidence"] == child["soil_state_evidence"]
        assert saved["inputs"]["soil_state_updates"] == child["inputs"]["soil_state_updates"]
        assert saved["applied_water_record_ids"] == [actual["id"]]
        assert saved["result"]["result"]["days"][0]["soil_state_update"]["values"]["adopted_depletion_mm"] == pytest.approx(58)


def test_replacing_one_checkpoint_then_removing_it_preserves_other_checkpoints(setup):
    from agronaut_agent.crop_records import CropRecords
    workflow, _ = setup
    records = CropRecords(workflow.db)
    data = dict(quantity="soil_depletion", value=40, unit="mm", observed_at="2026-07-06T18:00:00+00:00",
                source="Synthetic whole-root sample", provenance="reported")
    first = records.record_measurement("owner", "field", data, cycle_id="cycle", request_id="first")
    second = records.record_measurement("owner", "field", dict(data, value=30, observed_at="2026-07-07T18:00:00+00:00"),
                                        cycle_id="cycle", request_id="second")
    base = workflow.save("owner", "field", "root_zone_budget", series(), request_id="base", cycle_id="cycle")
    child = workflow.apply_soil_state("owner", "field", **adoption(base, observation_id=first["id"]))
    child = workflow.apply_soil_state("owner", "field", **adoption(child, observation_id=second["id"],
                                     as_of="2026-07-07", request_id="second"))
    replaced = workflow.apply_soil_state("owner", "field", **adoption(child, observation_id=first["id"],
                                        depletion_adjustment_mm=3, request_id="replace"))
    assert len(replaced["inputs"]["soil_state_updates"]) == len(replaced["soil_state_evidence"]) == 2
    assert replaced["result"]["result"]["days"][1]["balance"]["inputs"]["state"]["depletion_mm"] == 43
    removed = workflow.remove_soil_state("owner", "field", calculation_id=replaced["id"], as_of="2026-07-06",
                                         reason="Withdraw first checkpoint", request_id="remove")
    assert [r["as_of"] for r in removed["soil_state_evidence"]] == ["2026-07-07"]
    assert removed["result"]["result"]["days"][2]["balance"]["inputs"]["state"]["depletion_mm"] == 32


def test_persistent_adoption_requires_the_local_day_to_be_complete(setup, monkeypatch):
    from agronaut_agent import soil_workflow
    workflow, store = setup
    measurement(store)
    base = workflow.save("owner", "field", "root_zone_budget", inputs(), request_id="base", cycle_id="cycle")
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 7, 6, 19, tzinfo=timezone.utc)
    monkeypatch.setattr(soil_workflow, "datetime", Clock)
    with pytest.raises(ValueError, match="completed local day"):
        workflow.apply_soil_state("owner", "field", **adoption(base))
    assert len(workflow.list("owner", "field")["calculations"]) == 1


@pytest.mark.parametrize("change", [dict(timezone="Asia/Taipei"), dict(as_of="2026-07-09", observed_at="2026-07-09T18:00:00Z"),
                                   dict(observation_id="untrusted"), dict(represented_depth_m=.2)])
def test_standalone_updates_reject_wrong_site_window_unknown_identity_or_depth(change):
    data = inputs()
    data["soil_state_updates"] = [dict(json_ready(asdict(update())), **change)]
    with pytest.raises(ValueError):
        evaluate_field_request(data, "irrigation-plan")
