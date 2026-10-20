"""Owned equipment calculations as indivisible, dated resource-plan work blocks."""

import json
from decimal import ROUND_CEILING, Decimal, localcontext

from agro_model.resources import _amount, _object, validate_plan
from agro_model.validation import number, text

from .cycle_lifecycle import ensure_cycle_day
from .equipment_targets import review as review_target
from .field_io import _date


def _amount_up(value):
    number("equipment resource total", value, high=1e15, positive=True)
    with localcontext() as ctx:
        ctx.prec = 70
        result = format(Decimal(str(value)).quantize(Decimal("0.000001"), rounding=ROUND_CEILING), "f")
    _amount(result)
    return result.rstrip("0").rstrip(".")


def _calculation(conn, farm, calculation_id):
    text("calculation_id", calculation_id)
    row = conn.execute("SELECT c.payload,u.production_system FROM field_calculations c JOIN production_units u ON u.id=c.unit_id "
                       "WHERE c.id=? AND u.farm_id=?", (calculation_id, farm["id"])).fetchone()
    if row is None:
        raise ValueError("unknown irrigation calculation for this farm")
    if row[1] != "soil":
        raise ValueError("irrigation design requires a current soil production unit")
    saved = json.loads(row[0])
    if saved["task"] not in ("drip_design", "sprinkler_design"):
        raise ValueError("select a saved drip or sprinkler equipment design")
    result = saved["result"]["result"]
    if result.get("feasible") is not True or result.get("schedule") is None:
        raise ValueError("equipment design fails its supplied checks; revise it before creating resource work")
    return saved


def evidence(conn, farm, job):
    link = job["irrigation_design"]
    saved = _calculation(conn, farm, link["calculation_id"])
    if saved["unit_id"] != job["unit_id"] or saved["cycle_id"] != job["cycle_id"]:
        raise ValueError("linked design must belong to the job's exact unit and crop cycle")
    if saved["cycle_id"] is not None:
        cycle = conn.execute("SELECT * FROM production_cycles WHERE id=? AND unit_id=?", (saved["cycle_id"], saved["unit_id"])).fetchone()
        before = saved.get("cycle_snapshot")
        if cycle is None or before is None or any(before[key] != cycle[key] for key in ("crop", "planted_on")):
            raise ValueError("equipment design crop or planting assumptions need review; save a revised design")
        if link["day"] < cycle["planted_on"]:
            raise ValueError("irrigation event cannot precede crop planting")
        ensure_cycle_day(conn, saved["unit_id"], saved["cycle_id"], link["day"])
    reviewed = review_target(conn, farm["owner_id"], saved["unit_id"], saved)
    if reviewed["needs_review"]:
        raise ValueError("equipment water target needs review: "+"; ".join(reviewed["issues"]))
    if saved.get("water_target") and link["day"] <= saved["water_target"]["state_as_of"]:
        raise ValueError("event must follow the adopted end-of-day soil-water state; it is not a same-day completed irrigation")
    result = saved["result"]["result"]
    water, hours = (_amount_up(result[key]) for key in ("candidate_daily_upper_water_l", "candidate_daily_hours"))
    if (job["requirements"].get(link["water_resource_id"]) != water
            or job["requirements"].get(link["pump_resource_id"]) != hours):
        raise ValueError("linked water and pump-time requirements must match the rounded saved design totals")
    return dict(job_id=job["id"], calculation=saved, day=link["day"], conversion=dict(
        water_l=water, pump_hours=hours, original_water_l=result["candidate_daily_upper_water_l"],
        original_hours=result["candidate_daily_hours"], method="Decimal form of saved totals, rounded upward to six decimal places"),
        assumptions=["One block is the complete supplied irrigation event, including maintenance allowances; no partial layout or zone scaling.",
                     "The mapped pump pool budgets all declared operation and maintenance hours. The operator must confirm that mapping is applicable.",
                     "Upper-bound water and time remain conditional on the saved experimental equipment model; importing does not validate its hydraulics.",
                     "Labor, cash and every other resource are independently supplied. Timing windows, concurrent units and run duration are not inferred.",
                     "Uniform flow within a resource-timing interval does not reproduce the equipment design's changing zone flows or maintenance events.",
                     "No resources are reserved and no actual water or work is recorded."])


def design_job(conn, farm, data):
    data = _object(data, ("calculation_id", "day", "job_id", "title", "priority", "water_resource_id", "pump_resource_id",
                          "resources", "other_requirements", "source"), "irrigation job request")
    saved = _calculation(conn, farm, data["calculation_id"])
    day = _date(data["day"]).isoformat()
    link = {key: data[key] for key in ("calculation_id", "water_resource_id", "pump_resource_id", "source")}
    link["day"] = day
    if not isinstance(data["other_requirements"], dict):
        raise ValueError("other_requirements must explicitly provide every remaining pool, including zero when unused")
    water, pump = data["water_resource_id"], data["pump_resource_id"]
    if not isinstance(water, str) or not isinstance(pump, str) or water == pump:
        raise ValueError("select distinct water and pump-time resource IDs")
    if water in data["other_requirements"] or pump in data["other_requirements"]:
        raise ValueError("other_requirements must not override the linked water or pump-time amounts")
    requirements = dict(data["other_requirements"])
    result = saved["result"]["result"]
    requirements[water] = _amount_up(result["candidate_daily_upper_water_l"])
    requirements[pump] = _amount_up(result["candidate_daily_hours"])
    job = dict(id=data["job_id"], title=data["title"], unit_id=saved["unit_id"], cycle_id=saved["cycle_id"],
        priority=data["priority"], requested_blocks=1, minimum_blocks=1,
        block_label="One full irrigation event including supplied maintenance", source=data["source"],
        requirements=requirements, irrigation_design=link)
    plan = validate_plan(dict(title=data["title"], source=data["source"], resources=data["resources"], jobs=[job],
                              period=dict(start=day, end=day, timezone=farm["timezone"])))
    job = plan["jobs"][0]
    return dict(job=job, evidence=evidence(conn, farm, job),
                note="Preview only. Review the source calculation, event date and pool mapping before keeping this job in a resource scenario.")
