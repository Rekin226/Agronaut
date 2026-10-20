"""Explicit event-total evidence beside an immutable irrigation resource allocation."""

import json
import uuid
from datetime import datetime, timezone
from fractions import Fraction
from zoneinfo import ZoneInfo

from agro_model.resource_timing import _number
from agro_model.resources import _object
from agro_model.validation import text

from .field_io import _date
from .observation_quality import reviewed_observation

METRICS = {"water": ("irrigation_volume", "L", "water_resource_id"), "pump": ("runtime", "h", "pump_resource_id")}


def migrate_resource_outcomes(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS resource_outcome_reviews (
        id TEXT PRIMARY KEY, farm_id TEXT NOT NULL REFERENCES farms(id) ON DELETE CASCADE,
        unit_id TEXT NOT NULL REFERENCES production_units(id) ON DELETE CASCADE,
        plan_id TEXT NOT NULL REFERENCES farm_resource_plans(id) ON DELETE CASCADE,
        job_id TEXT NOT NULL, request_id TEXT NOT NULL,
        supersedes_id TEXT UNIQUE REFERENCES resource_outcome_reviews(id) ON DELETE SET NULL,
        created_at TEXT NOT NULL, payload TEXT NOT NULL, UNIQUE(farm_id,request_id))""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_resource_outcomes_plan ON resource_outcome_reviews(plan_id,job_id)")


def _json(value):
    return json.dumps(value, sort_keys=True, allow_nan=False)


def _parent(conn, owner, farm_id, plan_id, job_id=None):
    from .farm_resources import _farm, _read
    _farm(conn, owner, farm_id)
    plan = _read(conn, farm_id, plan_id)
    if job_id is None:
        return plan
    text("job_id", job_id)
    job = next((j for j in plan["inputs"]["jobs"] if j["id"] == job_id), None)
    allocation = next((r for r in plan["result"]["allocations"] if r["job_id"] == job_id), None)
    evidence = next((r for r in plan["snapshot"].get("irrigation_designs", []) if r["job_id"] == job_id), None)
    if job is None or "irrigation_design" not in job or evidence is None or allocation is None or allocation["allocated_blocks"] != 1:
        raise ValueError("select an allocated, linked complete irrigation event in this saved plan")
    return plan, job, allocation, evidence


def _inputs(data):
    data = dict(_object(data, ("actual_day", "water", "pump", "attribution", "source"), "outcome review"))
    data["actual_day"] = _date(data["actual_day"]).isoformat()
    for key in ("attribution", "source"):
        data[key] = text(key, data[key]).strip()
    for key in METRICS:
        if data[key] is not None:
            row = dict(_object(data[key], ("observation_id", "expected_review_id"), key+" evidence"))
            row["observation_id"] = text("observation_id", row["observation_id"]).strip()
            if row["expected_review_id"] is not None:
                row["expected_review_id"] = text("expected_review_id", row["expected_review_id"]).strip()
            data[key] = row
    if data["water"] is None and data["pump"] is None:
        raise ValueError("select at least one event-total measurement; leave the other metric explicitly null if unknown")
    return data


def _observation(conn, unit_id, cycle_id, identifier):
    row = conn.execute("SELECT payload FROM field_observations WHERE id=? AND unit_id=? AND cycle_id IS ?",
                       (identifier, unit_id, cycle_id)).fetchone()
    if row is None:
        raise ValueError("unknown measurement for this job's exact unit and crop cycle")
    return reviewed_observation(conn, json.loads(row[0]))


def _evaluate(conn, owner, farm_id, plan_id, job_id, data):
    from .farm_resources import _review
    plan, job, allocation, evidence = _parent(conn, owner, farm_id, plan_id, job_id)
    zone_name = plan["inputs"]["period"]["timezone"]
    zone = ZoneInfo(zone_name)
    now = datetime.now(timezone.utc)
    if _date(data["actual_day"]) > now.astimezone(zone).date():
        raise ValueError("actual event date cannot be in the future in the saved plan timezone")
    selected, comparisons = {}, []
    for key, (quantity, unit, pool_key) in METRICS.items():
        pool_id = job["irrigation_design"][pool_key]
        budget = Fraction(allocation["assigned"][pool_id])
        reference = data[key]
        observed = None
        if reference is not None:
            row = _observation(conn, job["unit_id"], job["cycle_id"], reference["observation_id"])
            revision = row["quality_review"]["id"] if row["quality_review"] else None
            if reference["expected_review_id"] != revision:
                raise ValueError("measurement review changed; reload candidates and use the current expected_review_id")
            if row["quantity"] != quantity or row["unit"] != unit:
                raise ValueError(f"{key} evidence requires {quantity} in {unit}; net depth and flow rates are not event totals")
            if row["effective_quality"] == "rejected" or row["provenance"] == "forecast":
                raise ValueError("outcome evidence must be non-rejected actual measurements, reports or estimates")
            stamp = datetime.fromisoformat(row["observed_at"])
            if stamp > now or stamp.astimezone(zone).date().isoformat() != data["actual_day"]:
                raise ValueError("measurement timestamp must be nonfuture and fall on actual_day in the saved plan timezone")
            selected[key] = row
            observed = Fraction(str(row["value"]))
        comparisons.append(dict(metric=key, resource_id=pool_id, unit=unit, planned_allowance=_number(budget),
            observed_total=_number(observed) if observed is not None else None,
            observed_minus_allowance=_number(observed-budget) if observed is not None else None,
            relation="unknown" if observed is None else "above_allowance" if observed > budget else "below_allowance" if observed < budget else "equal_to_allowance",
            observation_id=reference["observation_id"] if reference else None,
            provenance=selected[key]["provenance"] if key in selected else None,
            quality=selected[key]["effective_quality"] if key in selected else None))
    planned_day = job["irrigation_design"]["day"]
    return dict(model_version="irrigation-outcome-v1", evidence_status="operator_attributed", farm_id=farm_id,
        unit_id=job["unit_id"], cycle_id=job["cycle_id"], plan_id=plan_id, job_id=job_id, inputs=data,
        generated_at=now.isoformat(), timezone=zone_name, planned_day=planned_day,
        actual_day=data["actual_day"], actual_minus_planned_days=(_date(data["actual_day"])-_date(planned_day)).days,
        comparisons=comparisons, observations=selected,
        plan_snapshot=dict(id=plan["id"], title=plan["inputs"]["title"], created_at=plan["created_at"],
            period=plan["inputs"]["period"], job=job, allocation=allocation, design_evidence=evidence,
            timing_work=[row for row in plan.get("timing_inputs", {}).get("work", []) if row["job_id"] == job_id]),
        plan_review=_review(conn, plan),
        limits=["The operator attributes each reading to this complete event, including maintenance, on the mapped source-water and pump basis. IDs and dates cannot prove physical attribution.",
                "Use event totals, not cumulative meter counters, net soil irrigation depth, flow rates or totals shared with other work. No conversion, allocation or subtraction of other events is inferred.",
                "Comparison is against conservative rounded resource allowances, not a prediction-accuracy, savings, crop-response or efficiency score. Below an allowance does not prove adequate irrigation.",
                "Missing metrics remain unknown. Measured, reported, estimated and operator-reviewed quality labels are retained; they are not independent field validation.",
                "Dates use the saved plan timezone. The date difference does not prove task completion or actual start/finish times.",
                "Other plans and review revisions are alternatives. Do not sum their linked readings; the same evidence may appear in more than one comparison.",
                "Saving this review creates no measurements, water totals, task completion, reservations or equipment actions."])


def _review_saved(conn, owner, farm_id, saved):
    from .farm_resources import _review
    plan = _parent(conn, owner, farm_id, saved["plan_id"])
    rows = []
    for key, before in saved["observations"].items():
        issues = []
        try:
            current = _observation(conn, saved["unit_id"], saved["cycle_id"], before["id"])
            if current != before:
                issues.append("measurement or its quality review changed since this comparison was saved")
            if current["effective_quality"] == "rejected":
                issues.append("linked measurement is now rejected")
        except ValueError:
            issues.append("linked measurement is missing from the original scope")
        rows.append(dict(metric=key, observation_id=before["id"], issues=issues, needs_review=bool(issues)))
    plan_review = _review(conn, plan)
    return dict(plan=plan_review, observations=rows, needs_review=plan_review["needs_review"] or any(r["needs_review"] for r in rows),
                note="Original comparison and evidence remain unchanged. Review current flags before reuse; corrections create a new revision.")


def cycle_outcomes(conn, unit_id, cycle_id):
    """Caller owns this unit/cycle and holds the season's snapshot transaction."""
    scope = conn.execute("SELECT u.farm_id,f.owner_id FROM production_units u JOIN farms f ON f.id=u.farm_id WHERE u.id=?",
                         (unit_id,)).fetchone()
    if scope is None:
        raise ValueError("unknown production unit")
    rows = [json.loads(r[0]) for r in conn.execute(
        "SELECT payload FROM resource_outcome_reviews WHERE unit_id=? ORDER BY rowid", (unit_id,))]
    rows = [row for row in rows if row["cycle_id"] == cycle_id]
    superseded = {row["supersedes_id"] for row in rows if row["supersedes_id"]}
    reviews = [dict(row, superseded=row["id"] in superseded, review=_review_saved(conn, scope[1], scope[0], row)) for row in rows]
    current = [row for row in reviews if not row["superseded"]]
    return dict(reviews=reviews, counts=dict(reviews=len(reviews), current=len(current),
        superseded=len(reviews)-len(current), current_needing_review=sum(row["review"]["needs_review"] for row in current)),
        note="Reviews assigned to this exact crop cycle, including corrections and alternative plans. Counts describe reviews, not distinct actual events. "
             "No reviews means unknown comparison history, not zero irrigation. Original event dates retain each saved plan's timezone; "
             "changed crop dates or timezone do not remove history. Gross source-water and pump-runtime comparisons are not added to daily net-water totals or coverage. "
             "The same measurements may support alternative plans; do not sum their comparisons or interpret differences as savings.")


class ResourceOutcomes:
    def __init__(self, db):
        self.db = db

    def inputs(self, owner, farm_id, plan_id, job_id):
        from .farm_resources import _review
        with self.db.transaction() as conn:
            plan, job, allocation, evidence = _parent(conn, owner, farm_id, plan_id, job_id)
            rows = conn.execute("SELECT payload FROM field_observations WHERE unit_id=? AND cycle_id IS ? ORDER BY observed_at,id", (job["unit_id"], job["cycle_id"]))
            candidates = []
            for row in rows:
                observation = reviewed_observation(conn, json.loads(row[0]))
                if observation["quantity"] not in {"irrigation_volume", "runtime"}:
                    continue
                candidates.append(dict(observation, expected_review_id=observation["quality_review"]["id"] if observation["quality_review"] else None))
            return dict(plan_id=plan_id, job=job, allocation=allocation, design_evidence=evidence,
                        timezone=plan["inputs"]["period"]["timezone"], observations=candidates, plan_review=_review(conn, plan))

    def evaluate(self, owner, farm_id, plan_id, job_id, data):
        data = _inputs(data)
        with self.db.transaction() as conn:
            return _evaluate(conn, owner, farm_id, plan_id, job_id, data)

    def save(self, owner, farm_id, plan_id, job_id, data, *, request_id, supersedes_id=None):
        from .farm_resources import _farm
        text("request_id", request_id)
        data = _inputs(data)
        operation = dict(plan_id=plan_id, job_id=job_id, data=data, supersedes_id=supersedes_id)
        with self.db.transaction() as conn:
            _farm(conn, owner, farm_id)
            row = conn.execute("SELECT payload FROM resource_outcome_reviews WHERE farm_id=? AND request_id=?", (farm_id, request_id)).fetchone()
            if row:
                saved = json.loads(row[0])
                if saved["operation"] != operation:
                    raise ValueError("request_id already used with different outcome data")
                return saved
            current = conn.execute("SELECT r.id FROM resource_outcome_reviews r WHERE farm_id=? AND plan_id=? AND job_id=? "
                "AND NOT EXISTS (SELECT 1 FROM resource_outcome_reviews n WHERE n.supersedes_id=r.id)", (farm_id, plan_id, job_id)).fetchone()
            expected = current[0] if current else None
            if supersedes_id != expected:
                raise ValueError("outcome review changed; use its latest ID as supersedes_id, or null for the first review")
            result = _evaluate(conn, owner, farm_id, plan_id, job_id, data)
            saved = dict(result, id=uuid.uuid4().hex, request_id=request_id, supersedes_id=supersedes_id, operation=operation)
            conn.execute("INSERT INTO resource_outcome_reviews VALUES(?,?,?,?,?,?,?,?,?)",
                         (saved["id"], farm_id, saved["unit_id"], plan_id, job_id, request_id, supersedes_id, saved["generated_at"], _json(saved)))
            return saved

    def list(self, owner, farm_id, plan_id):
        with self.db.transaction() as conn:
            _parent(conn, owner, farm_id, plan_id)
            rows = [json.loads(r[0]) for r in conn.execute("SELECT payload FROM resource_outcome_reviews WHERE farm_id=? AND plan_id=? ORDER BY rowid", (farm_id, plan_id))]
            superseded = {r["supersedes_id"] for r in rows if r["supersedes_id"]}
            return dict(reviews=[dict(row, superseded=row["id"] in superseded, review=_review_saved(conn, owner, farm_id, row)) for row in rows])
