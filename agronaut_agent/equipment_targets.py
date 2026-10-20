"""Explicit adoption of an immutable end-of-day water budget for equipment checks."""

import json
from math import isclose

from agro_model.validation import number, text

from .crop_establishment import calculation_cycle_review
from .field_io import _date
from .observation_quality import reviewed_observation


def catalog(conn, unit_id):
    result = []
    for row in conn.execute("SELECT payload FROM field_calculations WHERE unit_id=? ORDER BY created_at DESC,id", (unit_id,)):
        saved = json.loads(row[0])
        if saved["task"] == "root_zone_budget":
            result.append(dict(id=saved["id"], cycle_id=saved["cycle_id"], crop=saved["inputs"]["crop"]["name"],
                               area_m2=saved["inputs"]["area_m2"], created_at=saved["created_at"]))
        if len(result) == 10:
            break
    return result


def _choices(draft):
    return [r for r in draft.get("water_budgets", []) if r["cycle_id"] == draft["data"].get("cycle_id")]


def steps(draft):
    if draft.get("intake_version", 1) < 4:
        return []
    data = draft["data"]
    result = [("application_input", "Choose manual to supply an application target, or budget to adopt a saved soil-water budget's end-of-day refill amount.", "equipment_target")]
    if data.get("application_input") != "budget":
        return result
    labels = "\n".join(f"{i}. {r['id']} · {r['crop']} · {r['area_m2']:g} m² · saved {r['created_at']}"
                       for i, r in enumerate(_choices(draft), 1))
    result.append(("target_calculation", "Choose a listed number or enter a saved soil-water calculation ID for this unit and crop cycle. "
                   "Its whole area must match the layout. Recent versions:\n" + (labels or "None listed; you may enter another saved ID or /back to choose manual."), "equipment_target"))
    source = data.get("target_calculation")
    days = [r["day"] for r in source["result"]["result"]["days"]] if source else []
    result.append(("target_day", "Which closing soil-state date supplies the refill target? Choose YYYY-MM-DD from: "
                   + ", ".join(days) + ". This is an END-OF-DAY budget, not an irrigation action on that day.", "equipment_target"))
    result.append(("target_reason", "Why is this budget applicable to the entire equipment layout? Describe crop/root-zone applicability and, for drip, the limitations of using a uniform soil bucket with localized wetting.", "equipment_target"))
    summary = describe(receipt(draft)) if source and "target_day" in data else ""
    result.append(("target_accepted", summary + "\nReply use to adopt this amount and efficiency as an equipment-sizing scenario. "
                   "It is not a timed irrigation schedule and adds no allowance for later weather. /back edits the selection.", "equipment_target"))
    return result


def _layout_area(data):
    if data["task"] == "drip_design":
        keys = ("row_count", "emitters_per_row", "row_spacing_m", "emitter_spacing_m")
    else:
        keys = ("lateral_count", "sprinklers_per_lateral", "lateral_spacing_m", "sprinkler_spacing_m")
    area = 1
    for key in keys:
        area *= data[f"layout.{key}"]
    return number("layout area", area, positive=True)


def source_issues(conn, owner, unit_id, source, cycle_id, area_m2, timezone):
    from .field_workflow import _read, _scope
    current_timezone, cycle = _scope(conn, owner, unit_id, cycle_id)
    current = _read(conn, "field_calculations", unit_id, source["id"])
    issues = []
    if current != source:
        issues.append("saved water calculation differs from the adopted snapshot")
    if source["task"] != "root_zone_budget":
        return issues + ["select a soil-water budget; crop demand alone is not a refill target"]
    if source["cycle_id"] != cycle_id:
        issues.append("water calculation must belong to the equipment draft's crop cycle")
    if source["inputs"]["site"]["timezone"] != current_timezone or timezone != current_timezone:
        issues.append("water calculation and equipment draft must use the current farm timezone")
    if not isclose(source["inputs"]["area_m2"], area_m2, rel_tol=1e-9, abs_tol=1e-6):
        issues.append("water calculation area must match the entire equipment layout; no partial-area scaling is inferred")
    if cycle:
        review = calculation_cycle_review(source, dict(cycle), current_timezone)
        if review["changed_fields"] or review["conflicts"]:
            issues.append("water calculation's crop or planting assumptions need review")
    for record in source.get("crop_stage_evidence", []):
        if conn.execute("SELECT 1 FROM crop_records WHERE unit_id=? AND supersedes_id=?", (unit_id, record["id"])).fetchone():
            issues.append("linked crop-stage evidence was corrected; revise the water calculation first")
    for identifier in source.get("applied_water_record_ids", []):
        if conn.execute("SELECT 1 FROM field_water_days WHERE unit_id=? AND supersedes_id=?", (unit_id, identifier)).fetchone():
            issues.append("linked daily water totals were corrected; replay the current records first")
    for evidence in source.get("soil_state_evidence", []):
        old = evidence["observation"]
        row = conn.execute("SELECT payload FROM field_observations WHERE id=? AND unit_id=?", (old["id"], unit_id)).fetchone()
        current = reviewed_observation(conn, json.loads(row[0])) if row else None
        if current is None or current["quality_review"] != old["quality_review"] or current["effective_quality"] == "rejected":
            issues.append("linked soil measurement review changed; review and revise the water calculation first")
    return sorted(set(issues))


def receipt(draft):
    data = draft["data"]
    source = data["target_calculation"]
    row = next((r for r in source["result"]["result"]["days"] if r["day"] == data["target_day"]), None)
    if row is None:
        raise ValueError("choose a date in the selected soil-water calculation")
    requirement = row["requirement"]
    net, gross = (requirement["values"][k] for k in ("net_irrigation_mm", "gross_irrigation_mm"))
    if net is None or gross is None:
        raise ValueError("this day's soil state is unknown; initialize it before adopting a refill target")
    number("net refill", net, 0)
    number("gross refill", gross, 0)
    return dict(calculation_id=source["id"], calculation_snapshot=source, state_as_of=row["day"],
                boundary="end_of_local_day", timezone=source["inputs"]["site"]["timezone"],
                area_m2=source["inputs"]["area_m2"], weather_kind=row["weather_kind"],
                net_depth_mm=net, gross_depth_mm=gross, efficiency=requirement["inputs"]["efficiency"],
                efficiency_source=requirement["inputs"]["efficiency_source"], reason=data.get("target_reason"))


def describe(target):
    return (f"Water target from calculation {target['calculation_id']}: END-OF-DAY state {target['state_as_of']} "
            f"({target['timezone']}); selected-day weather: {target['weather_kind']}; {target['area_m2']:g} m². "
            f"Refill: net {target['net_depth_mm']:g} mm, gross {target['gross_depth_mm']:g} mm; "
            f"efficiency {100 * target['efficiency']:g}%. This target includes that day's water inputs and "
            "describes the closing soil state; it is not a same-day completed irrigation or a timed schedule.")


def answer(conn, owner, draft, key, raw):
    from .field_workflow import _read
    raw = text("answer", raw).strip()
    data = draft["data"]
    if key == "application_input":
        value = raw.lower()
        if value not in ("manual", "budget"):
            raise ValueError("Choose manual or budget.")
    elif key == "target_calculation":
        identifiers = {str(i): r["id"] for i, r in enumerate(_choices(draft), 1)}
        value = _read(conn, "field_calculations", draft["unit_id"], identifiers.get(raw, raw))
        issues = source_issues(conn, owner, draft["unit_id"], value, data.get("cycle_id"), _layout_area(data), draft["timezone"])
        if issues:
            raise ValueError("; ".join(issues))
    elif key == "target_day":
        value = _date(raw).isoformat()
        receipt(dict(draft, data=dict(data, target_day=value)))
    elif key == "target_reason":
        if len(raw) > 2000 or raw.startswith("/"):
            raise ValueError("Supply applicability and limitations in at most 2000 characters.")
        value = raw
    else:
        if raw != "use":
            raise ValueError("Reply use to adopt the displayed target, or /back to edit.")
        validate(conn, owner, draft)
        target = receipt(draft)
        if data["task"] == "drip_design":
            if target["gross_depth_mm"] > 1000:
                raise ValueError("gross refill exceeds the drip engine's 1000 mm envelope")
            data["application.gross_depth_mm"] = target["gross_depth_mm"]
        else:
            if target["net_depth_mm"] > 1000:
                raise ValueError("net refill exceeds the sprinkler engine's 1000 mm envelope")
            data["application.net_depth_mm"] = target["net_depth_mm"]
            data["application.efficiency"] = target["efficiency"]
        value = True
    data[key] = value


def validate(conn, owner, draft):
    data = draft["data"]
    target = receipt(draft)
    issues = source_issues(conn, owner, draft["unit_id"], target["calculation_snapshot"], data.get("cycle_id"),
                           _layout_area(data), draft["timezone"])
    if issues:
        raise ValueError("; ".join(issues))
    return target


def review(conn, owner, unit_id, saved):
    target = saved.get("water_target")
    if target is None:
        return dict(calculation_id=saved["id"], water_target=None, needs_review=False, issues=[])
    issues = source_issues(conn, owner, unit_id, target["calculation_snapshot"], saved["cycle_id"],
                           saved["result"]["result"]["area_m2"], target["timezone"])
    return dict(calculation_id=saved["id"], water_target=target, needs_review=bool(issues), issues=issues,
                note="Review flags do not change the saved target, equipment calculation or actual water logs.")
