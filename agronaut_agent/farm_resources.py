"""Owned farm-wide resource scenarios, separate from reservations and actual activity."""

import json
import uuid
from datetime import datetime, timezone

from agro_model.resource_schedule import propose_schedule, validate_schedule
from agro_model.resource_timing import check_timing, validate_timing
from agro_model.resources import allocate_resources, validate_plan
from agro_model.validation import text

from .cycle_lifecycle import lifecycle
from .field_io import _mapping


def migrate_resource_plans(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS farm_resource_plans (
        id TEXT PRIMARY KEY, farm_id TEXT NOT NULL REFERENCES farms(id) ON DELETE CASCADE,
        request_id TEXT NOT NULL, parent_id TEXT REFERENCES farm_resource_plans(id) ON DELETE SET NULL,
        created_at TEXT NOT NULL, payload TEXT NOT NULL, UNIQUE(farm_id, request_id))""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_resource_plans_farm ON farm_resource_plans(farm_id,created_at)")


def _json(value):
    return json.dumps(value, sort_keys=True, allow_nan=False)


def _farm(conn, owner, farm_id):
    text("farm_id", farm_id)
    row = conn.execute("SELECT * FROM farms WHERE id=? AND owner_id=?", (farm_id, owner)).fetchone()
    if row is None:
        raise ValueError("unknown farm for this owner")
    return dict(row)


def _read(conn, farm_id, plan_id):
    text("plan_id", plan_id)
    row = conn.execute("SELECT payload FROM farm_resource_plans WHERE farm_id=? AND id=?", (farm_id, plan_id)).fetchone()
    if row is None:
        raise ValueError("unknown resource plan for this farm")
    return json.loads(row[0])


def _snapshot(conn, farm, plan):
    if plan["period"]["timezone"] != farm["timezone"]:
        raise ValueError("plan timezone must match the farm's known timezone")
    units, cycles, designs = {}, {}, []
    for job in plan["jobs"]:
        unit = conn.execute("SELECT * FROM production_units WHERE id=? AND farm_id=?", (job["unit_id"], farm["id"])).fetchone()
        if unit is None:
            raise ValueError("every job must belong to a unit in this owned farm")
        units[unit["id"]] = dict(unit)
        if job["cycle_id"] is not None:
            cycle = conn.execute("SELECT * FROM production_cycles WHERE id=? AND unit_id=?", (job["cycle_id"], unit["id"])).fetchone()
            if cycle is None:
                raise ValueError("job cycle does not belong to its production unit")
            cycles[cycle["id"]] = dict(cycle, lifecycle=lifecycle(conn, unit["id"], cycle["id"]))
        if "irrigation_design" in job:
            from .resource_designs import evidence
            designs.append(evidence(conn, farm, job))
    snapshot = dict(farm=farm, units=list(units.values()), cycles=list(cycles.values()))
    from .irrigation_zones import zone_snapshot
    zones = [zone_snapshot(conn, u["id"]) for u in units.values() if u.get("parent_unit_id")]
    if zones:
        snapshot["irrigation_zones"] = zones
        snapshot["parent_zone_overlap"] = [dict(parent_unit_id=u["parent_unit_id"], zone_unit_id=u["id"])
                                           for u in units.values() if u.get("parent_unit_id") in units]
        snapshot["zone_scope_note"] = "Zone requirements belong to the selected zone IDs. Field and child-zone work may overlap; review tasks and quantities before summing. No area, water demand or hydraulic connection is inherited."
    if designs:
        snapshot["irrigation_designs"] = designs
    return snapshot


def _review(conn, saved):
    changes = []
    for before in saved["snapshot"]["cycles"]:
        row = conn.execute("SELECT * FROM production_cycles WHERE id=? AND unit_id=?", (before["id"], before["unit_id"])).fetchone()
        if row is None:
            changes.append(dict(cycle_id=before["id"], fields=["missing"]))
            continue
        current = dict(row, lifecycle=lifecycle(conn, before["unit_id"], before["id"]))
        fields = [key for key in ("crop", "planted_on", "lifecycle") if before[key] != current[key]]
        if fields:
            changes.append(dict(cycle_id=before["id"], fields=fields))
    designs = []
    current_farm = dict(conn.execute("SELECT * FROM farms WHERE id=?", (saved["farm_id"],)).fetchone())
    timezone_changed = current_farm["timezone"] != saved["snapshot"]["farm"]["timezone"]
    for old in saved["snapshot"].get("irrigation_designs", []):
        from .resource_designs import evidence
        job = next(j for j in saved["inputs"]["jobs"] if j["id"] == old["job_id"])
        issues = []
        try:
            current = evidence(conn, current_farm, job)
            if current["calculation"] != old["calculation"]:
                issues.append("saved source calculation differs from the adopted snapshot")
        except ValueError as exc:
            issues.append(str(exc))
        designs.append(dict(job_id=job["id"], calculation_id=old["calculation"]["id"], issues=issues, needs_review=bool(issues)))
    return dict(cycle_changes=changes, irrigation_designs=designs, timezone_changed=timezone_changed,
                needs_review=bool(changes or timezone_changed or any(d["needs_review"] for d in designs)),
                note="Saved inputs and allocations are unchanged. Changed crop, lifecycle, timezone or linked irrigation evidence requires review before reuse.")


def _check_dependency_inputs(parent, data):
    if isinstance(data, dict) and "equipment" in data:
        raise ValueError("use the equipment schedule operations to read the owned equipment calendar; raw busy intervals are not accepted here")
    constraints = parent.get("timing_inputs", parent.get("schedule_inputs", {})).get("dependencies", [])
    if constraints and isinstance(data, dict) and "dependencies" not in data:
        raise ValueError("parent has work dependencies; include dependencies explicitly, or [] to intentionally remove them")
    delivery = parent.get("timing_inputs", parent.get("schedule_inputs", {})).get("water_delivery", [])
    if delivery and isinstance(data, dict) and "water_delivery" not in data:
        raise ValueError("parent has water delivery limits; include water_delivery explicitly, or [] to intentionally remove those checks")


def _review_record(conn, farm, saved):
    extra = {}
    if "equipment_schedule" in saved:
        from .equipment_schedule import review_equipment_schedule
        extra["equipment_schedule_review"] = review_equipment_schedule(conn, farm, saved)
    return dict(saved, review=_review(conn, saved), **extra)


class FarmResources:
    def __init__(self, db):
        self.db = db

    def evaluate(self, owner, farm_id, data):
        plan = validate_plan(data)
        with self.db.transaction() as conn:
            snapshot = _snapshot(conn, _farm(conn, owner, farm_id), plan)
            return dict(inputs=plan, snapshot=snapshot, result=allocate_resources(plan))

    def design_job(self, owner, farm_id, data):
        from .resource_designs import design_job
        with self.db.transaction() as conn:
            return design_job(conn, _farm(conn, owner, farm_id), data)

    def outcome_inputs(self, owner, farm_id, plan_id, job_id):
        from .resource_outcomes import ResourceOutcomes
        return ResourceOutcomes(self.db).inputs(owner, farm_id, plan_id, job_id)

    def evaluate_outcome(self, owner, farm_id, plan_id, job_id, data):
        from .resource_outcomes import ResourceOutcomes
        return ResourceOutcomes(self.db).evaluate(owner, farm_id, plan_id, job_id, data)

    def save_outcome(self, owner, farm_id, plan_id, job_id, data, *, request_id, supersedes_id=None):
        from .resource_outcomes import ResourceOutcomes
        return ResourceOutcomes(self.db).save(owner, farm_id, plan_id, job_id, data, request_id=request_id, supersedes_id=supersedes_id)

    def list_outcomes(self, owner, farm_id, plan_id):
        from .resource_outcomes import ResourceOutcomes
        return ResourceOutcomes(self.db).list(owner, farm_id, plan_id)

    def save(self, owner, farm_id, data, *, request_id, parent_id=None):
        return self._save_plan(owner, farm_id, data, request_id=request_id, parent_id=parent_id)

    def save_new_work(self, owner, farm_id, template_id, data, reason, *, request_id):
        """Create a distinct work family using an owned scenario as a reference only."""
        text("template_id", template_id)
        return self._save_plan(owner, farm_id, data, request_id=request_id,
                               template_id=template_id, reason=text("new work reason", reason).strip())

    def _save_plan(self, owner, farm_id, data, *, request_id, parent_id=None, template_id=None, reason=None):
        text("request_id", request_id)
        plan = validate_plan(data)
        operation = dict(data=plan, parent_id=parent_id)
        if template_id is not None:
            operation = dict(kind="save_new_work", data=plan, template_id=template_id, reason=reason)
        with self.db.transaction() as conn:
            farm = _farm(conn, owner, farm_id)
            existing = conn.execute("SELECT payload FROM farm_resource_plans WHERE farm_id=? AND request_id=?", (farm_id, request_id)).fetchone()
            if existing:
                existing = json.loads(existing[0])
                if existing["operation"] != operation:
                    raise ValueError("request_id already used with different data")
                return existing
            if parent_id is not None:
                _read(conn, farm_id, parent_id)
            origin = None
            if template_id is not None:
                template = _read(conn, farm_id, template_id)
                origin = dict(reason=reason, template=dict(id=template["id"], title=template["inputs"]["title"],
                    period=template["inputs"]["period"], created_at=template["created_at"]),
                    note="This is explicitly declared separate work. The template is provenance, not an alternative-work parent. "
                    "Current inputs and allocation were checked afresh; timing, schedules, commitments and actual usage are not copied.")
            snapshot = _snapshot(conn, farm, plan)
            result = allocate_resources(plan)
            record = dict(id=str(uuid.uuid4()), farm_id=farm_id, owner_id=owner, request_id=request_id,
                parent_id=parent_id, created_at=datetime.now(timezone.utc).isoformat(), inputs=plan,
                snapshot=snapshot, result=result, operation=operation)
            if origin is not None:
                record["new_work"] = origin
            conn.execute("INSERT INTO farm_resource_plans(id,farm_id,request_id,parent_id,created_at,payload) VALUES(?,?,?,?,?,?)",
                         (record["id"], farm_id, request_id, parent_id, record["created_at"], _json(record)))
            return record

    def get(self, owner, farm_id, plan_id):
        with self.db.transaction() as conn:
            farm = _farm(conn, owner, farm_id)
            saved = _read(conn, farm_id, plan_id)
            return _review_record(conn, farm, saved)

    def evaluate_timing(self, owner, farm_id, plan_id, data):
        with self.db.transaction() as conn:
            _farm(conn, owner, farm_id)
            parent = _read(conn, farm_id, plan_id)
            _check_dependency_inputs(parent, data)
            timing = validate_timing(data, parent["inputs"], parent["result"])
            return dict(parent_id=plan_id, timing_inputs=timing, timing_result=check_timing(timing, parent["inputs"], parent["result"]),
                        review=_review(conn, parent))

    def save_timed(self, owner, farm_id, plan_id, data, *, request_id):
        text("request_id", request_id)
        with self.db.transaction() as conn:
            _farm(conn, owner, farm_id)
            parent = _read(conn, farm_id, plan_id)
            _check_dependency_inputs(parent, data)
            timing = validate_timing(data, parent["inputs"], parent["result"])
            operation = dict(kind="save_timed", plan_id=plan_id, data=timing)
            existing = conn.execute("SELECT payload FROM farm_resource_plans WHERE farm_id=? AND request_id=?", (farm_id, request_id)).fetchone()
            if existing:
                existing = json.loads(existing[0])
                if existing["operation"] != operation:
                    raise ValueError("request_id already used with different data")
                return existing
            record = dict(id=str(uuid.uuid4()), farm_id=farm_id, owner_id=owner, request_id=request_id, parent_id=plan_id,
                created_at=datetime.now(timezone.utc).isoformat(), inputs=parent["inputs"], result=parent["result"],
                snapshot=parent["snapshot"], operation=operation, timing_inputs=timing,
                timing_result=check_timing(timing, parent["inputs"], parent["result"]))
            conn.execute("INSERT INTO farm_resource_plans(id,farm_id,request_id,parent_id,created_at,payload) VALUES(?,?,?,?,?,?)",
                         (record["id"], farm_id, request_id, plan_id, record["created_at"], _json(record)))
            return record

    def list(self, owner, farm_id):
        with self.db.transaction() as conn:
            _farm(conn, owner, farm_id)
            rows = [json.loads(r[0]) for r in conn.execute("SELECT payload FROM farm_resource_plans WHERE farm_id=? ORDER BY rowid", (farm_id,))]
            return dict(plans=[dict(id=r["id"], title=r["inputs"]["title"], period=r["inputs"]["period"], parent_id=r["parent_id"],
                                    work_template_id=r.get("new_work", {}).get("template", {}).get("id"),
                                    timing_checks_pass=r.get("timing_result", {}).get("declared_checks_pass"),
                                    schedule_status=r.get("schedule_result", {}).get("status"),
                                    created_at=r["created_at"], all_requested_blocks_allocated=r["result"]["all_requested_blocks_allocated"]) for r in rows])

    def propose_schedule(self, owner, farm_id, plan_id, data):
        parent = self.get(owner, farm_id, plan_id)
        _check_dependency_inputs(parent, data)
        inputs = validate_schedule(data, parent["inputs"], parent["result"])
        result = propose_schedule(inputs, parent["inputs"], parent["result"])
        # Search holds no database transaction. Recheck ownership and review state
        # after it completes, so erasure or edits during search cannot be bypassed.
        current = self.get(owner, farm_id, plan_id)
        return dict(parent_id=plan_id, schedule_inputs=inputs, **result, review=current["review"])

    def save_schedule(self, owner, farm_id, plan_id, data, *, request_id):
        text("request_id", request_id)
        parent = self.get(owner, farm_id, plan_id)
        _check_dependency_inputs(parent, data)
        inputs = validate_schedule(data, parent["inputs"], parent["result"])
        operation = dict(kind="save_schedule", plan_id=plan_id, data=inputs)

        def retry(conn):
            row = conn.execute("SELECT payload FROM farm_resource_plans WHERE farm_id=? AND request_id=?", (farm_id, request_id)).fetchone()
            if row:
                previous = json.loads(row[0])
                if previous["operation"] != operation:
                    raise ValueError("request_id already used with different data")
                return previous

        with self.db.transaction() as conn:
            _farm(conn, owner, farm_id)
            if previous := retry(conn):
                return previous
        result = propose_schedule(inputs, parent["inputs"], parent["result"])
        with self.db.transaction() as conn:
            _farm(conn, owner, farm_id)
            _read(conn, farm_id, plan_id)
            if previous := retry(conn):
                return previous
            record = dict(id=str(uuid.uuid4()), farm_id=farm_id, owner_id=owner, request_id=request_id, parent_id=plan_id,
                created_at=datetime.now(timezone.utc).isoformat(), inputs=parent["inputs"], result=parent["result"],
                snapshot=parent["snapshot"], operation=operation, schedule_inputs=inputs, **result)
            conn.execute("INSERT INTO farm_resource_plans(id,farm_id,request_id,parent_id,created_at,payload) VALUES(?,?,?,?,?,?)",
                         (record["id"], farm_id, request_id, plan_id, record["created_at"], _json(record)))
            return record

    def compare(self, owner, farm_id, plan_ids):
        if (not isinstance(plan_ids, list) or not 2 <= len(plan_ids) <= 10
                or not all(isinstance(key, str) and key for key in plan_ids) or len(set(plan_ids)) != len(plan_ids)):
            raise ValueError("select 2–10 distinct resource plan IDs")
        with self.db.transaction() as conn:
            farm = _farm(conn, owner, farm_id)
            rows = [_read(conn, farm_id, key) for key in plan_ids]
            if any(r["inputs"]["period"] != rows[0]["inputs"]["period"] for r in rows):
                raise ValueError("compare scenarios for the same period and timezone")
            return dict(farm_id=farm_id, scenarios=[_review_record(conn, farm, r) for r in rows],
                note="Alternative scenarios, not additive allocations. Compare resource sources, availability, work blocks and priorities; "
                     "changed block definitions make block counts incomparable. No automatic best scenario or reservations.")

    def describe(self, owner, farm_id):
        from .resource_ledger import NOTE as LEDGER_NOTE
        from .resource_settlement import SETTLEMENT_NOTE

        with self.db.transaction() as conn:
            farm = _farm(conn, owner, farm_id)
            units = [dict(r) for r in conn.execute("SELECT * FROM production_units WHERE farm_id=? ORDER BY id", (farm_id,))]
            cycles = [dict(r) for r in conn.execute("SELECT c.* FROM production_cycles c JOIN production_units u ON u.id=c.unit_id WHERE u.farm_id=? ORDER BY c.id", (farm_id,))]
            designs = []
            for row in conn.execute("SELECT c.payload FROM field_calculations c JOIN production_units u ON u.id=c.unit_id WHERE u.farm_id=? ORDER BY c.rowid DESC", (farm_id,)):
                saved = json.loads(row[0])
                if saved["task"] in ("drip_design", "sprinkler_design"):
                    designs.append({key: saved[key] for key in ("id", "unit_id", "cycle_id", "task", "created_at")})
        return dict(farm_id=farm_id, timezone=farm["timezone"], units=units, cycles=cycles, irrigation_designs=designs,
            operations=dict(evaluate="{data:plan}", save="{data:plan,request_id,parent_id?}", get="{plan_id}", list="{}",
                            save_new_work="{template_id:owned_saved_plan_id,data:complete_plan,reason,request_id}",
                            equipment_calendar="{}", equipment_inputs="{plan_id:saved_timed_plan}",
                            equipment_schedule_inputs="{plan_id}",
                            propose_equipment_schedule="{plan_id,data:{schedule:ordinary_schedule,assignments:{every_run_id:[current_equipment_revision_ids]},source,replaces_booking_id:latest_same_family_booking|null}}",
                            save_equipment_schedule="{plan_id,data:equipment_schedule,request_id}",
                            save_equipment="{data:{equipment_key,name,kind:pump|tractor|implement|vehicle|other,status:available|unavailable,source},request_id,supersedes_id?,reason?}",
                            book_equipment="{plan_id,assignments:{every_timing_work_id:[current_equipment_revision_ids]},source,accept_partial,request_id,supersedes_id?,reason?}",
                            reserve_equipment="{data:{equipment_ids:[current_revision_ids],start:offset_timestamp,end:offset_timestamp,title,source},request_id,supersedes_id?,reason?}",
                            release_equipment="{booking_id:latest_id,source,reason,request_id}",
                            ledger="{}", save_budget="{data:budget,request_id,supersedes_id?,reason?}",
                            commit_plan="{plan_id,bindings:{every_pool_id:current_budget_revision_id},source,accept_partial:boolean,request_id,supersedes_id?,reason?}",
                            release_commitment="{commitment_id:latest_id,source,reason,request_id}",
                            settlement_inputs="{commitment_id:latest_id}",
                            settle_commitment="{commitment_id:latest_id,data:settlement,reason,request_id}",
                            outcome_inputs="{plan_id,job_id}", evaluate_outcome="{plan_id,job_id,data:outcome}",
                            save_outcome="{plan_id,job_id,data:outcome,request_id,supersedes_id?}", list_outcomes="{plan_id}",
                            design_job="{data:{calculation_id,day:YYYY-MM-DD,job_id,title,priority,water_resource_id,pump_resource_id,resources:[resource],other_requirements:{every_remaining_pool:decimal_string},source}}",
                            evaluate_timing="{plan_id,data:timing}", save_timed="{plan_id,data:timing,request_id}",
                            propose_schedule="{plan_id,data:schedule}", save_schedule="{plan_id,data:schedule,request_id}",
                            compare="{plan_ids:[2–10 distinct saved IDs]}", describe="{}"),
            plan="{title,source,period:{start:YYYY-MM-DD,end:YYYY-MM-DD,timezone},resources:[resource],jobs:[job]}",
            equipment_rules=["Register each physical item once with a stable key. Corrections preserve key and kind; mark unavailable to prevent new planned-work bookings. Existing bookings remain held and flagged for review.",
                "Equipment-aware proposals require fixed per-run item assignments and read current scoped calendar records. They search starts no earlier than the captured clock time, avoiding relevant bookings and other runs on the same item, while retaining timing/storage/dependency checks. Call equipment_schedule_inputs to discover current equipment and active same-family bookings. Ordinary scheduling does not check the equipment calendar.",
                "An existing same-family booking must be explicitly selected as replaces_booking_id. Only that active latest booking is excluded from the search. It remains booked until an explicit atomic replacement; no other booking can be excluded. Calendar or evidence changes during search reject the result. Later changes flag saved proposal snapshots. Saving/searching never reserves equipment; book_equipment rechecks current conflicts.",
                "Call equipment_inputs on a saved plan with passing timing checks and current evidence. Assign every work interval explicitly, including [] where no equipment is used. Each interval needs exactly its declared concurrent count of distinct physical pumps; other equipment may be assigned explicitly. Kind/count alone do not prove hydraulic or operational compatibility.",
                "book_equipment books all assigned intervals atomically. Any overlap on the same equipment, inside one plan or across plans/blocks, is rejected. Touching endpoints are allowed. One active booking per plan family; alternatives require explicit replacement with latest booking ID and reason.",
                "reserve_equipment adds a manual calendar block for outside work, setup, travel or maintenance. It can block unavailable equipment and does not establish fitness for work. Times require explicit offsets matching the farm timezone, positive duration, and an end in the future. No setup or travel allowance is inferred.",
                "Bookings and releases never change budget commitments, settlements, actual journals or devices. Use both quantity and equipment workflows when needed. Equipment compatibility, operator qualifications, aliases, unknown outside use and physical supply require operator review.",
                "Replacements are atomic; a rejected change leaves the old booking active. Release needs source/reason and preserves history. Expired intervals remain historical records rather than disappearing. Export includes all equipment and booking revisions."],
            new_work=["save with parent_id means an alternative for the same work. Do not bypass a settled plan family by treating an alternative as another reservation.",
                      "Use save_new_work only on explicit instruction for separate work. Supply the whole reviewed plan, source and a reason distinguishing the new work. Dates and quantities are never shifted or inferred.",
                      "The same-farm template ID, original title/period/timestamp and reason are retained under new_work. parent_id is null, creating a distinct family. Current unit/cycle/design inputs and allocations are validated anew.",
                      "Existing timing checks, schedules, reservations, settlement or actual quantities are not copied. Saving does not commit anything. Explicitly map current budgets and commit later; existing charges still count.",
                      "Separate work may occur on the same day, so distinctness is an operator declaration, not an automatic duplicate detector. To revise the new work after saving, use save with its ID as parent_id."],
            settlement=dict(schema="{actual_start:YYYY-MM-DD,actual_end:YYYY-MM-DD,attribution,source,usage:{every_budget_root_id:{amount:decimal_string|null,provenance,source,outcome_ids:[]}}}",
                rules=[SETTLEMENT_NOTE,
                       "Use settlement_inputs and ledger to inspect the latest commitment, original budget roots and current outcome reviews. Every root requires an explicit row. Dates must be nonfuture in the saved budget timezone and inside every original budget period; actual dates may differ from the planned window.",
                       "Manual known totals require measured/documented/reported/estimated provenance and no outcome IDs. Null total requires null provenance, an explanation in source and no IDs; it retains max(original allowance, previous charge) as reserved. Known zero is explicit unused allowance.",
                       "Optional outcome IDs require linked_outcomes provenance. Choose current reviews from the exact committed plan, covering every positively allocated job/pool of that budget once. The supplied amount must equal their exact total, with no rounding. Otherwise use a sourced manual complete total.",
                       "Changed or rejected measurement evidence is rejected when saving links; outcome/quality changes later flag history without rewriting charges and block new reservations against affected budgets until corrected. A reading cannot be linked into two current settlements. Manual duplicates cannot be inferred; reconcile them explicitly.",
                       "Settlements and corrections always require reason and the latest commitment revision. They replace prior charges atomically, including recording overruns: negative remaining and positive overdrawn stay visible and block additional reservations.",
                       "A settled or partially settled record cannot be released or replaced with a reservation. Correct its usage explicitly. A late actual-use report against a released commitment remains recordable; another current record in that plan family is flagged for reconciliation.",
                       "Budget capacity cannot be reduced below charged usage/reservations; increases may partially repair an existing overdraft. Ledger balances separate reserved, recorded_use, remaining, available and overdrawn. committed remains the sum of reserved and recorded_use for compatibility."]),
            budget=dict(schema="{resource_key,kind:water|pump_time|labor_time|cash,unit:L|h|currency,period:{start,end,timezone},capacity:decimal_string,source}",
                rules=[LEDGER_NOTE,
                       "resource_key is a stable case-insensitive ID, 1–80 ASCII letters/numbers/dot/underscore/hyphen. Retain kind/unit across periods. Same-key budget periods cannot overlap, including across timezone changes.",
                       "Capacity is nonnegative with at most 15 integer/6 fractional digits. Correction requires latest supersedes_id and reason, preserving resource/period; capacity cannot fall below active commitments.",
                       "Commit only on explicit instruction. Read ledger for current budget IDs and balances. Map every plan pool; compatible mappings to one budget are summed. Each budget must contain the entire plan period in the same farm timezone.",
                       "Commit uses allocated amounts, not requests or pool availability. Partial allocations require accept_partial=true. Empty allocations, ended periods, changed plan evidence and saved failed timing/schedules are rejected.",
                       "Only one active scenario per parent-plan family. To replace, use the latest commitment as supersedes_id plus reason; release and new capacity checks are atomic. Failed replacement preserves the old reservation.",
                       "Release uses the latest commitment ID and requires source/reason. No automatic expiry. Released commitments can be replaced explicitly after new validation. Retries return original receipts.",
                       "ledger preserves history, source snapshots and change flags. Budget or plan changes never silently release quantities. Independent duplicate plan families and differently named supplies require operator reconciliation."]),
            resource="{id,kind:water|pump_time|labor_time|cash,unit:L|h|currency,available:decimal_string,source}",
            job="{id,unit_id,cycle_id:null|id,title,block_label,priority:unique_positive_int,requested_blocks:positive_int,"
                "minimum_blocks:positive_int,requirements:{every_resource_id:decimal_string_per_block},source,irrigation_design?:design_link}",
            design_link="{calculation_id,day:YYYY-MM-DD,water_resource_id,pump_resource_id,source}",
            outcome=dict(schema="{actual_day:YYYY-MM-DD,water:null|observation_reference,pump:null|observation_reference,attribution,source}",
                observation_reference="{observation_id,expected_review_id:null|current_quality_review_id}",
                rules=["Select an allocated linked irrigation job. outcome_inputs supplies observations from its exact unit/cycle; use its current expected_review_id for each selection.",
                       "At least one metric is required. Water uses irrigation_volume in L; pump uses runtime in h. Only non-rejected measured/reported/estimated readings, on actual_day in the saved plan timezone, are accepted. No future events or forecasts.",
                       "Explicit attribution must explain that readings are complete event totals including maintenance for the mapped source-water/pump pools. No net-depth conversion, cumulative-meter subtraction, shared-event allocation or inferred attribution.",
                       "Compare readings with rounded conservative resource allowances. Missing metrics stay unknown; differences are not water savings, recommendation accuracy or proof of irrigation adequacy.",
                       "Save creates a separate immutable review, not actual water, task completion or a new resource scenario. Corrections use the latest review ID as supersedes_id; old retries return their original receipt.",
                       "list_outcomes retains revisions and flags later measurement-quality or plan-evidence changes without rewriting comparisons. Same readings across alternatives are not additive. Owner export/erasure includes reviews."]),
            irrigation_rules=["design_job previews one complete dated drip/sprinkler event from an owned saved design that passes its supplied checks. Nothing is saved by this operation.",
                "Map one water and one pump-time pool explicitly. Conservative event totals including maintenance are rounded upward to six decimals; supply every other pool requirement, including explicit zeros.",
                "All operation and maintenance hours are charged to the chosen pump pool. Confirm applicability in source; labor, cash, timing, flow trajectories and suitable dates are not inferred.",
                "Keep the returned job and its link in a plan to retain the full source snapshot. Counts must stay 1/1; amounts, unit and cycle are checked against the owned design on evaluate/save.",
                "Event date must lie in the plan period; linked work intervals and run windows must stay within that local day. No partial-event scaling or split days.",
                "Changed crop/planting or water-target evidence requires a revised design. An adopted end-of-day water state permits only later event dates. Old saved resource scenarios retain their original evidence and review flags.",
                "To enter independent manual quantities, explicitly remove the link and provide their source; they are no longer derived from the design. No actual work or reservations are created."],
            dependency="{id,predecessor:work_or_run_id,successor:work_or_run_id,min_gap_seconds:integer_0..1000000,source}",
            schedule=dict(schema="{source,step_seconds:positive_int,search_limit:1..10000,runs:[run],storage:[storage_balance],availability:[availability],dependencies?:[dependency],water_delivery?:[delivery_pool]}",
                run="{id,job_id,blocks,duration_seconds:positive_int,concurrent:{every_pump_or_labor_resource_id:integer_units},windows:[{start,end}],source}",
                rules=["Storage and availability use the timing schemas below. Every allocated block must appear exactly once across runs; no work is dropped or reallocated.",
                       "At most 100 runs, 100 windows per run and 1000 run windows total. Duration and grid step are integers 1..1000000 seconds; duration must fit the period.",
                       "A whole run must fit one of its windows; [] means no permitted times. Sources must justify durations, units and windows. Start times alone are chosen.",
                       "Grid starts are elapsed seconds from local period start in UTC, including DST. Search assigns predecessors first, then uses priority and run ID among ready runs, tries earlier starts first, and backtracks.",
                       "Dependencies use run IDs, not job IDs: successor start >= predecessor end + min_gap_seconds. At most 1000, unique IDs/pairs, no self-reference, unknown runs or cycles. Gaps consume no resources.",
                       "Omitting dependencies preserves legacy unconstrained input. [] explicitly declares none; when the parent has dependencies they must be included or explicitly removed with []. Nothing is inferred from job priorities.",
                       "search_limit bounds attempted placements. feasible_schedule_found includes exact timing checks; no_feasible_schedule_on_grid rules out only this grid; search_limit_reached means unknown feasibility.",
                       "Priority is search ordering, not precedence or optimality. Save preserves the original allocation and assumption snapshot, including failed or limited searches. No actual activity or reservation."]),
            timing=dict(schema="{source,work:[work_interval],storage:[storage_balance],availability:[availability],dependencies?:[dependency],water_delivery?:[delivery_pool]}",
                work_interval="{id,job_id,blocks,start,end,concurrent:{every_pump_or_labor_resource_id:integer_units},source}",
                storage_balance="{resource_id,capacity_l:decimal_string,initial_l:decimal_string,minimum_l:decimal_string,inflows:[flow],external_withdrawals:[flow],source}",
                flow="{id,start,end,volume_l:positive_decimal_string,source}",
                availability="{resource_id,windows:[{id,start,end,units:positive_int,source}]}",
                delivery_pool="{resource_id,windows:[{id,start,end,max_flow_lph:nonnegative_decimal_string,source}]}",
                rules=["Timestamps use YYYY-MM-DDTHH:MM:SS with Z or explicit offset matching the farm zone, and lie within the plan's local dates.",
                       "Optional water_delivery supplies nonoverlapping outlet windows for every water pool; unlisted time has zero delivery capacity. [] or omission means unchecked. A parent with delivery limits requires explicit inclusion or [] to remove them. Same contract for timing and schedule search.",
                       "Delivery checks sum uniform job and external withdrawal rates in L/hour; inflows do not offset demand. At most 1000 windows per pool, 5000 total. Supplied limits and uniform rates do not establish actual peak flow, pressure or pump/pipe suitability.",
                       "Dependencies use the same schema/rules as scheduling, with work interval IDs. Checks compare exact elapsed-second gaps, including DST. Failure is reported without changing supplied intervals.",
                       "Work intervals must cover exactly allocated blocks, not all requested blocks. Sources and every time resource's concurrent count are explicit.",
                       "Supply storage for every water pool and availability for every pump/labor pool. [] means explicitly none; no missing values are inferred.",
                       "Available-unit windows cannot overlap within a pool; unlisted time has zero availability. Occupy listed units for the whole work interval.",
                       "Resource demand must fit the occupied interval; occupied time must fit its budget. Availability windows must support the declared budget.",
                       "Water work use and listed inflows/outside withdrawals are uniform over each interval. Reserve, initial volume and capacity are explicit.",
                       "Report depleted/overflowing storage without clipping balances. No implicit spills, refills, equipment connections or actuation.",
                       "A failed timing check may be saved for comparison; saving is not approval. Timed children retain the parent's exact allocation and assumption snapshot."]),
            rules=["Use only explicit operator inputs. Decimal strings are nonnegative, max 15 integer and 6 fractional digits.",
                   "Every job declares all resource pools; zero explicitly means unused. Missing is unknown and fails validation.",
                   "Give each physical pool one ID; available is net of commitments outside this scenario for the whole period.",
                   "Water uses L, pump/labor h, cash a three-letter uppercase currency. No unit or currency conversion.",
                   "Block counts and unique priorities are integers 1..1000000, minimum <= requested; lower priority numbers go first.",
                   "Allocate as many whole blocks as fit all pools. If fewer than minimum fit, assign none and continue to lower priorities.",
                   "Owned farm scope is explicit; every job's unit and optional cycle must belong to it. Never infer a crop treatment or its demands.",
                   "Aggregate budgets do not establish flow, pressure, timing, storage trajectory, crop response, profit or physical feasibility.",
                   "Saved plans are immutable scenarios. parent_id links alternatives for the same work. Saving does not reserve resources, approve work, create quantities or operate devices."])


def execute_farm_resources(db, owner, farm_id, operation, data):
    allowed = dict(describe=(), list=(), design_job=("data",), evaluate=("data",), save=("data", "request_id", "parent_id"),
                   equipment_schedule_inputs=("plan_id",), propose_equipment_schedule=("plan_id", "data"),
                   save_equipment_schedule=("plan_id", "data", "request_id"),
                   equipment_calendar=(), equipment_inputs=("plan_id",),
                   save_equipment=("data", "request_id", "supersedes_id", "reason"),
                   book_equipment=("plan_id", "assignments", "source", "accept_partial", "request_id", "supersedes_id", "reason"),
                   reserve_equipment=("data", "request_id", "supersedes_id", "reason"),
                   release_equipment=("booking_id", "source", "reason", "request_id"),
                   save_new_work=("template_id", "data", "reason", "request_id"),
                   ledger=(), save_budget=("data", "request_id", "supersedes_id", "reason"),
                   commit_plan=("plan_id", "bindings", "source", "accept_partial", "request_id", "supersedes_id", "reason"),
                   release_commitment=("commitment_id", "source", "reason", "request_id"),
                   settlement_inputs=("commitment_id",), settle_commitment=("commitment_id", "data", "reason", "request_id"),
                   outcome_inputs=("plan_id", "job_id"), evaluate_outcome=("plan_id", "job_id", "data"),
                   save_outcome=("plan_id", "job_id", "data", "request_id", "supersedes_id"), list_outcomes=("plan_id",),
                   evaluate_timing=("plan_id", "data"), save_timed=("plan_id", "data", "request_id"),
                   propose_schedule=("plan_id", "data"), save_schedule=("plan_id", "data", "request_id"),
                   get=("plan_id",), compare=("plan_ids",))
    if not isinstance(operation, str) or operation not in allowed:
        raise ValueError("unknown resource-plan operation")
    args = _mapping(data, allowed[operation], "resource-plan operation")
    try:
        if operation in ("equipment_schedule_inputs", "propose_equipment_schedule", "save_equipment_schedule"):
            from .equipment_schedule import EquipmentScheduling
            return getattr(EquipmentScheduling(db), operation)(owner, farm_id, **args)
        if operation in ("equipment_calendar", "equipment_inputs", "save_equipment", "book_equipment", "reserve_equipment", "release_equipment"):
            from .equipment_calendar import EquipmentCalendar
            return getattr(EquipmentCalendar(db), operation)(owner, farm_id, **args)
        if operation in ("settlement_inputs", "settle_commitment"):
            from .resource_settlement import ResourceSettlement
            return getattr(ResourceSettlement(db), operation)(owner, farm_id, **args)
        if operation in ("ledger", "save_budget", "commit_plan", "release_commitment"):
            from .resource_ledger import ResourceLedger
            return getattr(ResourceLedger(db), operation)(owner, farm_id, **args)
        return getattr(FarmResources(db), operation)(owner, farm_id, **args)
    except TypeError as exc:
        raise ValueError(f"invalid {operation} request: {exc}") from exc
