"""Owned measurement adoption at an explicit daily soil-state boundary."""

import json
from dataclasses import asdict
from datetime import datetime, timezone

from agro_model.soil_state import SoilStateUpdate
from agro_model.validation import text

from .field_io import _date, json_ready
from .field_workflow import _apply_water_record, _calculate, _json, _read, _retry, _scope
from .observation_quality import reviewed_observation


def _parent(conn, owner, unit_id, calculation_id):
    _scope(conn, owner, unit_id)
    saved = _read(conn, "field_calculations", unit_id, calculation_id)
    if saved["task"] != "root_zone_budget":
        raise ValueError("soil-state updates require a root_zone_budget calculation")
    return saved


def review_soil(workflow, owner, unit_id, calculation_id):
    with workflow.db.transaction() as conn:
        _scope(conn, owner, unit_id)
        saved = _read(conn, "field_calculations", unit_id, calculation_id)
        if saved["task"] not in ("root_zone_budget", "coupled_soil_water"):
            raise ValueError("soil review requires a root_zone_budget or coupled_soil_water calculation")
        originals = [json.loads(r[0]) for r in conn.execute(
            "SELECT payload FROM field_observations WHERE unit_id=? AND (cycle_id IS NULL OR cycle_id IS ?) "
            "ORDER BY julianday(observed_at),id", (unit_id, saved["cycle_id"]))]
        measurements = [reviewed_observation(conn, r) for r in originals
                        if r["quantity"] in ("soil_depletion", "soil_water_content")]
        if saved["task"] == "coupled_soil_water":
            return dict(calculation_id=calculation_id,
                measurements=[r for r in measurements if r["quantity"] == "soil_water_content"],
                note="Compare water content for an explicitly represented depth interval and patch footprint. "
                     "Supply a signed water-content adjustment to closing midnight and declare prior use in initialization or calibration. "
                     "Positive adjustment means wetter. Comparisons are read-only; apply_coupled_state separately requires complete-layer coverage and reviewed drying memory.")
        by_id = {r["id"]: r for r in measurements}
        linked = []
        for evidence in saved.get("soil_state_evidence", []):
            current = by_id.get(evidence["observation"]["id"])
            old_review = evidence["observation"]["quality_review"]
            new_review = current["quality_review"] if current else None
            changed = current is None or (old_review["id"] if old_review else None) != (new_review["id"] if new_review else None)
            linked.append(dict(snapshot=evidence, review_changed=changed,
                               current_quality=current["effective_quality"] if current else None,
                               current_review_id=new_review["id"] if new_review else None))
        return dict(calculation_id=calculation_id, measurements=measurements, linked_measurements=linked,
                    updates=saved["inputs"].get("soil_state_updates", []), needs_review=any(r["review_changed"] for r in linked),
                    note="A point reading is not automatically a root-zone or field average. Supply represented depth, "
                         "sampling rationale and an explicit depletion adjustment to closing local midnight. "
                         "Positive adjustment means drier; zero needs an explicit timing assumption. "
                         "Adoption changes model state, not water logs, review status or observed provenance.")


def _save(conn, owner, unit_id, parent, request_id, operation, evidence):
    inputs = parent["inputs"]
    for identifier in parent["applied_water_record_ids"]:
        _apply_water_record(inputs, _read(conn, "field_water_days", unit_id, identifier))
    return _calculate(conn, owner, unit_id, parent["cycle_id"], request_id, parent["task"], inputs, operation,
                      parent_id=parent["id"], applied_records=parent["applied_water_record_ids"],
                      stage_evidence=parent.get("crop_stage_evidence", []), soil_evidence=evidence)


def apply_soil_state(workflow, owner, unit_id, calculation_id, observation_id, as_of, represented_depth_m,
                     representation_note, depletion_adjustment_mm, adjustment_source, reason, expected_review, *, request_id):
    text("reason", reason)
    text("observation_id", observation_id)
    if expected_review is not None:
        text("expected_review", expected_review)
    day = _date(as_of, "as_of")
    operation = dict(kind="apply_soil_state", calculation_id=calculation_id, observation_id=observation_id, as_of=as_of,
                     represented_depth_m=represented_depth_m, representation_note=representation_note,
                     depletion_adjustment_mm=depletion_adjustment_mm, adjustment_source=adjustment_source,
                     reason=reason, expected_review=expected_review)
    with workflow.db.transaction() as conn:
        _scope(conn, owner, unit_id)
        previous = _retry(conn, "field_calculations", unit_id, request_id, operation)
        if previous:
            return previous
        parent = _parent(conn, owner, unit_id, calculation_id)
        tz, _ = _scope(conn, owner, unit_id, parent["cycle_id"])
        original = _read(conn, "field_observations", unit_id, observation_id)
        if original["cycle_id"] is not None and original["cycle_id"] != parent["cycle_id"]:
            raise ValueError("soil observation belongs to another crop cycle")
        observation = reviewed_observation(conn, original)
        current = observation["quality_review"]
        if expected_review != (current["id"] if current else None):
            raise ValueError("measurement review changed; review_soil and supply the current expected_review")
        update = SoilStateUpdate(day, datetime.fromisoformat(original["observed_at"]), tz,
            original["quantity"], original["value"], original["unit"], represented_depth_m, representation_note,
            depletion_adjustment_mm, adjustment_source,
            f"{original['source']}; observation {observation_id}", original["provenance"], observation["effective_quality"])
        if update.effective_at > datetime.now(timezone.utc):
            raise ValueError("soil-state adoption requires a completed local day")
        entry = json_ready(asdict(update))
        entries = {r["as_of"]: r for r in parent["inputs"].get("soil_state_updates", [])}
        entries[as_of] = entry
        parent["inputs"]["soil_state_updates"] = [entries[k] for k in sorted(entries)]
        evidence = {r["as_of"]: r for r in parent.get("soil_state_evidence", [])}
        evidence[as_of] = dict(as_of=as_of, observation=observation, update=entry)
        return _save(conn, owner, unit_id, parent, request_id, operation, [evidence[k] for k in sorted(evidence)])


def remove_soil_state(workflow, owner, unit_id, calculation_id, as_of, reason, *, request_id):
    _date(as_of, "as_of")
    text("reason", reason)
    operation = dict(kind="remove_soil_state", calculation_id=calculation_id, as_of=as_of, reason=reason)
    with workflow.db.transaction() as conn:
        _scope(conn, owner, unit_id)
        previous = _retry(conn, "field_calculations", unit_id, request_id, operation)
        if previous:
            return previous
        parent = _parent(conn, owner, unit_id, calculation_id)
        entries = parent["inputs"].get("soil_state_updates", [])
        if not any(r["as_of"] == as_of for r in entries):
            raise ValueError("no soil-state update exists on that date")
        remaining = [r for r in entries if r["as_of"] != as_of]
        if remaining:
            parent["inputs"]["soil_state_updates"] = remaining
        else:
            parent["inputs"].pop("soil_state_updates")
        evidence = [r for r in parent.get("soil_state_evidence", []) if r["as_of"] != as_of]
        return _save(conn, owner, unit_id, parent, request_id, operation, evidence)


def revise_roots(workflow, owner, unit_id, calculation_id, root_zone_schedule, reason, *, request_id):
    text("reason", reason)
    schedule = json.loads(_json(root_zone_schedule))
    if not isinstance(schedule, list):
        raise ValueError("root_zone_schedule must be an array; [] restores the initial fixed depth")
    operation = dict(kind="revise_roots", calculation_id=calculation_id, root_zone_schedule=schedule, reason=reason)
    with workflow.db.transaction() as conn:
        _scope(conn, owner, unit_id)
        previous = _retry(conn, "field_calculations", unit_id, request_id, operation)
        if previous:
            return previous
        parent = _parent(conn, owner, unit_id, calculation_id)
        if schedule:
            parent["inputs"]["root_zone_schedule"] = schedule
        else:
            parent["inputs"].pop("root_zone_schedule", None)
        # Recalculation revalidates every retained soil checkpoint at its new depth;
        # a mismatch cannot silently reinterpret a measurement's sampled volume.
        return _save(conn, owner, unit_id, parent, request_id, operation, parent.get("soil_state_evidence", []))
