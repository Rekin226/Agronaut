"""Read-only seasonal evidence summaries; no assumed missing actions or causal claims."""

import json
from datetime import date, timedelta
from decimal import Decimal

from .crop_establishment import calculation_cycle_review
from .resource_outcomes import cycle_outcomes
from .soil_reports import soil_report_history
from .surface_water_records import history as surface_water_history

WATER_FIELDS = ("rainfall_mm", "net_irrigation_mm", "runoff_mm")


def compare_reviews(unit_id, reviews, now):
    """Condense explicit cycle snapshots without ranking or aggregating across seasons."""
    seasons = []
    for review in reviews:
        journal = review["journal"]
        evidence = {}
        for record in journal["records"]:
            if not record["voided"] and not record["superseded"]:
                evidence.setdefault(record["kind"], []).append(record["id"])
        seasons.append(dict(cycle=journal["cycle"], period=review["period"], lifecycle=journal["lifecycle"],
            current_record_counts=review["current_record_counts"], current_crop_record_ids=evidence,
            harvest_totals=journal["harvest_totals"], fertilizer=journal["fertilizer"], cash=journal["cash"],
            harvest_reviews=dict(current=journal["harvest_reviews"]["current"], note=journal["harvest_reviews"]["note"]),
            water={key: review["water"][key] for key in ("applicable", "coverage", "totals", "note")},
            calendar={k: journal["calendar"][k] for k in ("as_of", "tasks", "counts", "note")},
            calculations=review["calculations"],
            soil_tests=dict(current=[{k: r[k] for k in ("id", "cycle_id", "data", "created_at", "evidence_status")}
                for r in review["soil_tests"]["current"]], note=review["soil_tests"]["note"]),
            irrigation_outcomes=dict(counts=review["irrigation_outcomes"]["counts"], note=review["irrigation_outcomes"]["note"],
                reviews=[{key: row[key] for key in ("id", "plan_id", "job_id", "planned_day", "actual_day", "timezone",
                    "comparisons", "review", "supersedes_id")} for row in review["irrigation_outcomes"]["reviews"] if not row["superseded"]])))
    return dict(schema_version=1, unit_id=unit_id, generated_at=now.isoformat(), seasons=seasons,
        limits=["Only explicitly selected cycles in this unit are included; no combined totals or ranking.",
                "Crop, dates, duration, area, harvest basis and record completeness can differ. Harvest-per-area reviews keep explicit denominators and coverage; no causal comparison.",
                "Cash dates can extend beyond planting and closure. Recorded net cash is not profit; currencies/provenance stay separate.",
                "Missing harvest, cash or water history remains unknown. Daily water coverage is available only for soil units.",
                "Irrigation outcome comparisons retain each current alternative separately; no sum of event evidence, savings or coverage is inferred. Season reviews contain full correction history.",
                "Read each cycle journal for full sources and revision history; the comparison includes current evidence IDs."])


def _decimal(value):
    return Decimal(str(value))


def _coverage(start, end, recorded):
    """Compact missing ranges without allocating one item per calendar day."""
    expected = max(0, (end-start).days+1)
    cursor, gaps = start, []
    for day in sorted(recorded):
        if cursor < day:
            gaps.append(dict(start=cursor.isoformat(), end=(day-timedelta(days=1)).isoformat(), days=(day-cursor).days))
        cursor = day+timedelta(days=1)
    if cursor <= end:
        gaps.append(dict(start=cursor.isoformat(), end=end.isoformat(), days=(end-cursor).days+1))
    return dict(start=start.isoformat(), through=end.isoformat() if expected else None, expected_days=expected,
                recorded_days=len(recorded), missing_days=expected-len(recorded), missing_ranges=gaps)


def _water_totals(records):
    groups = {}
    for row in records:
        data = row["data"]
        key = data["provenance"], data["quality"]
        group = groups.setdefault(key, dict(provenance=key[0], quality=key[1], record_ids=[],
                                          **{name: Decimal(0) for name in WATER_FIELDS}))
        group["record_ids"].append(row["id"])
        for name in WATER_FIELDS:
            group[name] += _decimal(data[name])
    return [dict(g, recorded_days=len(g["record_ids"]), **{name: format(g[name], "f") for name in WATER_FIELDS})
            for _, g in sorted(groups.items())]


def _comparison(calculation, current, history, start, end):
    if calculation["task"] != "root_zone_budget":
        raise ValueError("season water comparison requires a root_zone_budget calculation")
    events = {e["day"]: e for e in calculation["inputs"]["events"]}
    by_id = {r["id"]: r for r in history}
    applied = {by_id[key]["day"]: key for key in calculation["applied_water_record_ids"] if key in by_id}
    rows = []
    for weather in calculation["inputs"]["weather"]:
        day = weather["day"]
        in_window = start <= date.fromisoformat(day) <= end
        row = current.get(day) if in_window else None
        accepted = row is not None and row["data"]["quality"] != "rejected"
        entered = dict(rainfall_mm=weather["rainfall_mm"], **{key: events[day][key] for key in WATER_FIELDS[1:]})
        difference = {key: format(_decimal(row["data"][key])-_decimal(entered[key]), "f") for key in WATER_FIELDS} if accepted else None
        rows.append(dict(day=day, status="outside_review_window" if not in_window else
                         "matched" if accepted else "rejected" if row else "missing",
                         calculation_inputs=entered, weather_kind=weather["kind"], weather_source=weather["source"],
                         event_source=events[day]["source"], replayed_record_id=applied.get(day),
                         record_id=row["id"] if row else None, recorded=row["data"] if row else None,
                         recorded_minus_input=difference))
    return dict(calculation=calculation, days=rows,
                interpretation="Recorded minus calculation input, not savings or a prediction-accuracy score. "
                "Replayed record IDs identify inputs already copied from actual logs; corrections can change the comparison.")


def build_season_review(conn, unit_id, journal, timezone, calculation_id, now):
    """Caller owns the unit and holds one transaction for the entire snapshot."""
    cycle = journal["cycle"]
    start = date.fromisoformat(cycle["planted_on"])
    end = date.fromisoformat(journal["lifecycle"]["ended_on"]) if journal["lifecycle"]["status"] == "closed" else now.date()
    water_end = min(end, now.date()-timedelta(days=1))
    soil = conn.execute("SELECT production_system FROM production_units WHERE id=?", (unit_id,)).fetchone()[0] == "soil"
    history = [json.loads(r[0]) for r in conn.execute(
        "SELECT payload FROM field_water_days WHERE unit_id=? AND cycle_id=? ORDER BY day,rowid", (unit_id, cycle["id"]))]
    superseded = {r["supersedes_id"] for r in history if r["supersedes_id"]}
    history = [dict(r, superseded=r["id"] in superseded) for r in history]
    current = {r["day"]: r for r in history if not r["superseded"]}
    included = [r for r in current.values() if r["data"]["quality"] != "rejected" and start <= date.fromisoformat(r["day"]) <= water_end]
    coverage = _coverage(start, water_end, {date.fromisoformat(r["day"]) for r in included}) if soil else None
    surface_history = surface_water_history(conn, unit_id, cycle["id"])
    surface_current = [r for r in surface_history if not r["superseded"] and r["data"]["quality"] != "rejected"
                       and start <= date.fromisoformat(r["day"]) <= water_end]
    calculations = [json.loads(r[0]) for r in conn.execute(
        "SELECT payload FROM field_calculations WHERE unit_id=? AND cycle_id=? ORDER BY rowid", (unit_id, cycle["id"]))]
    selected = None
    if calculation_id is not None:
        selected = next((c for c in calculations if c["id"] == calculation_id), None)
        if selected is None:
            raise ValueError("calculation does not belong to the selected unit and crop cycle")
    summary = []
    for row in calculations:
        weather = row["inputs"].get("weather", [])
        cycle_review = calculation_cycle_review(row, cycle, timezone, journal["lifecycle"])
        summary.append(dict(id=row["id"], task=row["task"], parent_id=row["parent_id"], created_at=row["created_at"],
                            weather_start=weather[0]["day"] if weather else None,
                            weather_end=weather[-1]["day"] if weather else None,
                            applied_water_record_ids=row["applied_water_record_ids"],
                            evidence_status=row["result"].get("evidence_status"),
                            cycle_snapshot_available=cycle_review["snapshot_available"],
                            cycle_changed_fields=cycle_review["changed_fields"], cycle_conflicts=cycle_review["conflicts"]))
    counts = {}
    for row in journal["records"]:
        if not row["superseded"] and not row["voided"]:
            counts[row["kind"]] = counts.get(row["kind"], 0)+1
    return dict(schema_version=1, generated_at=now.isoformat(), unit_id=unit_id,
                period=dict(planted_on=cycle["planted_on"], through=end.isoformat() if end >= start else None,
                            timezone=timezone, status=journal["lifecycle"]["status"], not_started=start > now.date()),
                journal=journal, current_record_counts=counts,
                surface_water=dict(history=surface_history, current_records=surface_current,
                    note="Surface-water records describe local patch supplies after upstream losses. They are separate from net-root-zone logs; "
                         "do not add their depths or count the same applications twice. Different footprints are not aggregated."),
                water=dict(applicable=soil, coverage=coverage, totals=_water_totals(included) if soil else [],
                           current_records=included, history=history,
                           note="Totals cover only recorded, non-rejected, completed local days, separated by provenance and quality. "
                           "Missing days are unknown; field measurements are not added to daily totals. "
                           "Daily water-log coverage applies to soil units only."),
                calculations=summary, irrigation_outcomes=cycle_outcomes(conn, unit_id, cycle["id"]),
                soil_tests=soil_report_history(conn, unit_id, cycle["id"]),
                comparison=dict(_comparison(selected, current, history, start, water_end),
                                cycle_review=calculation_cycle_review(selected, cycle, timezone, journal["lifecycle"])) if selected else None,
                limits=["Open means not marked ended, not verified growing. No harvest records means unknown harvest, not zero yield.",
                        "Scouting samples remain separate. Observations and calculations do not establish diagnoses or causal yield effects.",
                        "Harvest-per-area reviews require explicit batches, area and coverage. No automatic water productivity, profit or full-season completeness claim."])
