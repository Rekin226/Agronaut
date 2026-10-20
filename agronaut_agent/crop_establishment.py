"""Planting revision metadata and date consistency for owned, transaction-scoped callers."""

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from .cycle_lifecycle import lifecycle


def planting_state(conn, unit_id, cycle_id):
    row = conn.execute("SELECT payload FROM crop_records WHERE unit_id=? AND cycle_id=? "
                       "AND kind='planting' ORDER BY rowid DESC LIMIT 1", (unit_id, cycle_id)).fetchone()
    record = json.loads(row[0]) if row else None
    return dict(revision_id=record["id"] if record else None,
                source=record["data"]["source"] if record else None,
                recorded_at=record["created_at"] if record else None)


def ensure_planting_consistency(conn, unit_id, cycle_id, planted_on):
    state = lifecycle(conn, unit_id, cycle_id)
    if state["status"] == "closed" and planted_on > state["ended_on"]:
        raise ValueError("planting date is after the cycle end; correct or reopen the closure first")
    for table in ("crop_records", "field_water_days", "field_surface_water_days"):
        rows = conn.execute(f"SELECT r.payload FROM {table} r WHERE r.unit_id=? AND r.cycle_id=? "
                            f"AND NOT EXISTS (SELECT 1 FROM {table} n WHERE n.supersedes_id=r.id)",
                            (unit_id, cycle_id))
        for row in rows:
            record = json.loads(row[0])
            data = record["data"]
            if record.get("voided") or data.get("quality") == "rejected":
                continue
            kind = record["kind"] if table == "crop_records" else "water"
            if kind not in ("stage", "scouting", "harvest", "transplant", "fertilizer", "water"):
                continue
            preplant = kind == "fertilizer" and data["phase"] == "preplant"
            conflict = data["day"] > planted_on if preplant else data["day"] < planted_on
            if conflict:
                raise ValueError(f"planting date conflicts with current {kind} record {record['id']} on {data['day']}; "
                                 "correct, void or reject that entry first")
    # Typed measurements can legitimately predate planting (for example soil observations).
    # They keep their original timestamps and do not impose a planting-date lower bound.


def calculation_cycle_review(calculation, cycle, timezone, lifecycle=None):
    """Report dated/crop conflicts without rewriting a historical calculation."""
    snapshot = calculation.get("cycle_snapshot")
    changes = [key for key in ("crop", "planted_on") if snapshot and snapshot[key] != cycle[key]]
    conflicts = []
    inputs, task = calculation["inputs"], calculation["task"]
    if task in ("crop_water", "root_zone_budget", "nutrient_budget", "dual_crop_water", "coupled_soil_water"):
        if inputs["crop"]["name"].casefold() != cycle["crop"].casefold():
            conflicts.append("calculation crop differs from current cycle crop")
    if task in ("crop_water", "root_zone_budget", "dual_crop_water", "coupled_soil_water") and inputs["weather"][0]["day"] < cycle["planted_on"]:
        conflicts.append("calculation weather starts before current planting date")
    if task in ("crop_water", "root_zone_budget"):
        periods = inputs["crop"].get("coefficient_schedule", [])
        if periods and periods[0]["start"] < cycle["planted_on"]:
            conflicts.append("coefficient schedule starts before current planting date")
    if task in ("surface_evaluation", "sprinkler_catch_test", "drip_emitter_test"):
        started = datetime.fromisoformat(inputs["event"]["started_at"]).astimezone(ZoneInfo(timezone)).date().isoformat()
        if started < cycle["planted_on"]:
            label = {"surface_evaluation": "surface event", "sprinkler_catch_test": "catch test", "drip_emitter_test": "emitter test"}[task]
            conflicts.append(label+" starts before current planting date")
        if task in ("sprinkler_catch_test", "drip_emitter_test") and inputs["site"]["timezone"] != timezone:
            conflicts.append("irrigation test timezone differs from current farm timezone")
    if task == "dual_crop_water":
        if inputs["site"]["timezone"] != timezone:
            conflicts.append("dual crop-water timezone differs from current farm timezone")
        if lifecycle and lifecycle["status"] == "closed" and inputs["weather"][-1]["day"] > lifecycle["ended_on"]:
            conflicts.append("dual crop-water days extend after the current crop cycle end")
    if task == "coupled_soil_water":
        if inputs["site"]["timezone"] != timezone:
            conflicts.append("coupled soil-water timezone differs from current farm timezone")
        if lifecycle and lifecycle["status"] == "closed" and inputs["weather"][-1]["day"] > lifecycle["ended_on"]:
            conflicts.append("coupled soil-water days extend after the current crop cycle end")
    if task == "soil_profile":
        day = datetime.fromisoformat(inputs["profile"]["observed_at"]).astimezone(ZoneInfo(timezone)).date().isoformat()
        if day < cycle["planted_on"]:
            conflicts.append("soil profile precedes current planting date")
        if inputs["site"]["timezone"] != timezone:
            conflicts.append("soil profile timezone differs from current farm timezone")
        if lifecycle and lifecycle["status"] == "closed" and day > lifecycle["ended_on"]:
            conflicts.append("soil profile is after the current crop cycle end")
    return dict(snapshot_available=snapshot is not None, changed_fields=changes, conflicts=conflicts,
                note="Saved inputs and results remain unchanged. Older calculations may lack a cycle snapshot. "
                     "No detected conflict is not agronomic validation.")
