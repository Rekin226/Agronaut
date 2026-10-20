"""Reviewed compartment observations can condition a new owned continuation state."""

import json
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from fractions import Fraction

from agro_model.resources import _object, _rows
from agro_model.soil_evaporation import SoilEvaporationMemory
from agro_model.soil_reconciliation import CompartmentReading, reconcile_soil_water
from agro_model.soil_snapshots import dumps_soil_snapshot, loads_soil_snapshot
from agro_model.validation import choice, text

from .coupled_soil_evaluation import compare_coupled_soil
from .coupled_water_io import _decimal, _indexed, _json_value, _record
from .field_workflow import _json, _read, _retry, _scope
from .observation_quality import reviewed_observation
from .surface_water_records import _parent, validate_inherited


def continuation_checkpoint(saved):
    revision = saved.get("coupled_state_revision")
    return revision["ending_checkpoint"] if revision else saved["result"]["result"]["ending_checkpoint"]


def evidence_review(conn, parent):
    rows = []
    for evidence in parent.get("coupled_state_evidence", []):
        original = _read(conn, "field_observations", parent["unit_id"], evidence["observation"]["id"])
        current = reviewed_observation(conn, original)
        before, after = evidence["observation"]["quality_review"], current["quality_review"]
        changed = (before["id"] if before else None) != (after["id"] if after else None)
        rows.append(dict(evidence=evidence, current=current, needs_review=changed or current["effective_quality"] == "rejected"))
    return rows


def validate_state_evidence(conn, parent, replacing=()):
    for row in evidence_review(conn, parent):
        e = row["evidence"]
        if (e["as_of"], e["patch_id"], e["layer_id"]) not in replacing and row["needs_review"]:
            raise ValueError("adopted soil measurement review changed; revise or withdraw the state at its original boundary before continuing")


def _cycle_context(conn, parent, tz, cycle):
    if cycle:
        from .crop_establishment import calculation_cycle_review
        from .cycle_lifecycle import lifecycle
        review = calculation_cycle_review(parent, dict(cycle), tz, lifecycle(conn, parent["unit_id"], parent["cycle_id"]))
        if review["changed_fields"] or review["conflicts"]:
            raise ValueError("parent crop-cycle context changed; review initialization before a state revision")


def _insert(conn, parent, request_id, operation, evidence, revision):
    saved = json.loads(_json(parent))
    saved.update(id=uuid.uuid4().hex, parent_id=parent["id"], request_id=request_id,
        created_at=datetime.now(timezone.utc).isoformat(), operation=operation)
    saved.pop("coupled_state_revision", None)
    saved.pop("coupled_state_evidence", None)
    if revision:
        saved["coupled_state_revision"] = revision
    if evidence:
        saved["coupled_state_evidence"] = evidence
    conn.execute("INSERT INTO field_calculations VALUES(?,?,?,?,?,?,?)", (saved["id"], saved["unit_id"], saved["cycle_id"],
        request_id, parent["id"], saved["created_at"], _json(saved)))
    return saved


class CoupledStateWorkflow:
    def __init__(self, db):
        self.db = db

    def review(self, owner, unit_id, calculation_id):
        with self.db.transaction() as conn:
            parent, _, _ = _parent(conn, owner, unit_id, calculation_id)
            originals = [json.loads(r[0]) for r in conn.execute(
                "SELECT payload FROM field_observations WHERE unit_id=? AND (cycle_id IS NULL OR cycle_id IS ?) "
                "ORDER BY julianday(observed_at),id", (unit_id, parent["cycle_id"]))]
            readings = [reviewed_observation(conn, r) for r in originals if r["quantity"] == "soil_water_content"]
            return dict(calculation_id=calculation_id, measurements=readings,
                current_state=_json_value(loads_soil_snapshot(continuation_checkpoint(parent), expected_kind="state")),
                linked_measurements=evidence_review(conn, parent),
                note="Adopt only observations representing complete modeled layers and patch footprints at the final local-day boundary. "
                     "Each affected patch needs explicit reviewed drying memory. Unselected layers and stored surface water remain unchanged. "
                     "Storage adjustments are separate from the original prediction and water-flux balance.")

    def apply(self, owner, unit_id, calculation_id, readings, evaporation_memories, dataset_kind, reason, *, request_id):
        choice("dataset_kind", dataset_kind, ("synthetic", "field"))
        text("state revision reason", reason)
        operation = json.loads(_json(dict(kind="apply_coupled_state", calculation_id=calculation_id, readings=readings,
            evaporation_memories=evaporation_memories, dataset_kind=dataset_kind, reason=reason)))
        readings, evaporation_memories = operation["readings"], operation["evaporation_memories"]
        with self.db.transaction() as conn:
            _scope(conn, owner, unit_id)
            if existing := _retry(conn, "field_calculations", unit_id, request_id, operation):
                return existing
            parent, tz, cycle = _parent(conn, owner, unit_id, calculation_id)
            _cycle_context(conn, parent, tz, cycle)
            validate_inherited(conn, parent)
            state = loads_soil_snapshot(continuation_checkpoint(parent), expected_kind="state")
            day = state.as_of.isoformat()
            cells = {(p.id, c.id): c for p in state.soil.patches for c in p.compartments}
            comparisons, targets = [], []
            for row in _rows(readings, "compartment readings", 1000):
                row = _object(row, ("observation_id", "patch_id", "layer_id", "top_m", "bottom_m", "representation_note",
                    "water_content_adjustment", "adjustment_source", "expected_review"), "compartment reading")
                text("patch_id", row["patch_id"])
                text("layer_id", row["layer_id"])
                key = (row["patch_id"], row["layer_id"])
                cell = cells.get(key)
                if cell is None or key in targets:
                    raise ValueError("readings require unique existing patch/layer pairs")
                if _decimal(row["top_m"]) != cell.top_m or _decimal(row["bottom_m"]) != cell.bottom_m:
                    raise ValueError("adoption requires coverage of the complete modeled layer; partial samples cannot initialize a whole cell")
                targets.append(key)
                comparisons.append(dict({k:v for k,v in row.items() if k != "layer_id"}, as_of=day, independence="used_for_initialization"))
            replacing = {(day, *key) for key in targets}
            validate_state_evidence(conn, parent, replacing)
            compared = compare_coupled_soil(conn, parent, comparisons, dataset_kind, reason, tz)
            by_id = {r["observation"]["id"]: r for r in compared["comparisons"]}
            parsed, evidence = [], {(e["as_of"], e["patch_id"], e["layer_id"]): e for e in parent.get("coupled_state_evidence", [])}
            for row in readings:
                match = by_id[row["observation_id"]]
                parsed.append(CompartmentReading(row["patch_id"], row["layer_id"], Fraction(match["values"]["reference_water_content"]["exact"]),
                    f"Observation {row['observation_id']}: {match['observation']['source']}; {row['adjustment_source']}"))
                evidence[(day, row["patch_id"], row["layer_id"])] = dict(as_of=day, patch_id=row["patch_id"], layer_id=row["layer_id"],
                    observation=match["observation"], reading=row, dataset_kind=dataset_kind)
            current_ids = [e["observation"]["id"] for e in evidence.values() if e["as_of"] == day]
            if len(current_ids) != len(set(current_ids)):
                raise ValueError("one observation cannot initialize multiple modeled cells at the same boundary")
            memories = []
            for row in _indexed(evaporation_memories, "reviewed drying memories"):
                if "retain" in row:
                    row = _object(row, ("id", "retain", "source"), "retained drying memory")
                    if row["retain"] is not True or row["id"] not in dict(state.evaporation_memories):
                        raise ValueError("retain must be explicitly true for an existing patch memory")
                    text("drying memory review source", row["source"])
                    memories.append((row["id"], replace(dict(state.evaporation_memories)[row["id"]], source=row["source"])))
                    continue
                row = _object(row, ("id", "stage_one_remaining_mm", "active_depth_m", "stage_two_start_fraction", "source"), "reviewed drying memory")
                memories.append((row["id"], _record(SoilEvaporationMemory, {k:v for k,v in row.items() if k != "id"})))
            changed = reconcile_soil_water(state, parsed, tuple(memories), f"Reviewed state revision: {reason}")
            baseline = loads_soil_snapshot(parent["result"]["result"]["ending_checkpoint"], expected_kind="state")
            revision = dict(as_of=day, reason=reason, dataset_kind=dataset_kind, prior_checkpoint=continuation_checkpoint(parent),
                ending_checkpoint=dumps_soil_snapshot(changed["ending"]),
                reconciliation=_json_value(changed),
                cumulative_storage_adjustment_l=_json_value(changed["ending"].soil.water_l-baseline.soil.water_l))
            return _insert(conn, parent, request_id, operation, [evidence[k] for k in sorted(evidence)], revision)

    def remove(self, owner, unit_id, calculation_id, reason, *, request_id):
        text("state withdrawal reason", reason)
        operation = dict(kind="remove_coupled_state", calculation_id=calculation_id, reason=reason)
        with self.db.transaction() as conn:
            _scope(conn, owner, unit_id)
            if existing := _retry(conn, "field_calculations", unit_id, request_id, operation):
                return existing
            parent, tz, cycle = _parent(conn, owner, unit_id, calculation_id)
            _cycle_context(conn, parent, tz, cycle)
            if not parent.get("coupled_state_revision"):
                raise ValueError("no ending-state revision to withdraw; revise the ancestor supplying the initial checkpoint instead")
            day = parent["coupled_state_revision"]["as_of"]
            evidence = [e for e in parent.get("coupled_state_evidence", []) if e["as_of"] < day]
            return _insert(conn, parent, request_id, operation, evidence, None)
