"""Atomic commitments and recorded usage against named farm budgets."""

import json
import re
import uuid
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from agro_model.resources import KINDS, _amount, _display, _object
from agro_model.validation import choice, text

from .farm_resources import _farm, _json, _read, _review, _snapshot
from .field_io import _date

TABLES = ("farm_resource_budgets", "farm_resource_commitments")
NOTE = (
    "Commitments reserve declared quantities in Agronaut's farm budget ledger, not physical supply or equipment times. "
    "Use one stable resource key for each distinct supply; aliases and overlapping real-world supplies cannot be identified automatically. "
    "Budget capacity is the total available to this ledger for its period, net of commitments outside Agronaut. "
    "Active commitments stay counted until explicitly released or replaced, even after dates or evidence change. "
    "Release unused or cancelled allowances only; used amounts must remain counted against the original period. "
    "Explicit settlement records complete usage totals; unknown totals retain an allowance and overruns remain visible as overdrafts. "
    "Release does not record consumption or replenish physical water. No automatic journals, purchases, task completion or equipment actions. "
    "Pump/labor hours are aggregate allowances; concurrent use, flow, storage and cross-plan timing need separate checks."
)


def migrate_resource_ledger(conn):
    for table in TABLES:
        conn.execute(f"""CREATE TABLE IF NOT EXISTS {table} (
            id TEXT PRIMARY KEY, farm_id TEXT NOT NULL REFERENCES farms(id) ON DELETE CASCADE,
            request_id TEXT NOT NULL, supersedes_id TEXT UNIQUE REFERENCES {table}(id) ON DELETE SET NULL,
            created_at TEXT NOT NULL, payload TEXT NOT NULL, UNIQUE(farm_id,request_id))""")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_farm ON {table}(farm_id)")


def _history(conn, table, farm_id):
    rows = [json.loads(r[0]) for r in conn.execute(f"SELECT payload FROM {table} WHERE farm_id=? ORDER BY rowid", (farm_id,))]
    superseded = {r["supersedes_id"] for r in rows if r["supersedes_id"]}
    return [dict(r, superseded=r["id"] in superseded) for r in rows]


def _latest(conn, table, farm_id, key):
    text("revision ID", key)
    row = conn.execute(f"SELECT payload FROM {table} WHERE id=? AND farm_id=?", (key, farm_id)).fetchone()
    if row is None:
        raise ValueError("unknown budget or commitment for this farm")
    if conn.execute(f"SELECT 1 FROM {table} WHERE supersedes_id=?", (key,)).fetchone():
        raise ValueError("revision already superseded; read the latest ledger revision")
    return json.loads(row[0])


def _retry(conn, table, farm_id, request_id, operation):
    text("request_id", request_id)
    row = conn.execute(f"SELECT payload FROM {table} WHERE farm_id=? AND request_id=?", (farm_id, request_id)).fetchone()
    if row:
        saved = json.loads(row[0])
        if saved["operation"] != operation:
            raise ValueError("request_id already used with different data")
        return saved


def _insert(conn, table, owner, farm_id, request_id, operation, supersedes_id, **payload):
    saved = dict(id=uuid.uuid4().hex, owner_id=owner, farm_id=farm_id, request_id=request_id,
        supersedes_id=supersedes_id, operation=operation, created_at=datetime.now(timezone.utc).isoformat(), **payload)
    if saved.get("root_id") is None:
        saved["root_id"] = saved["id"]
    conn.execute(f"INSERT INTO {table} VALUES(?,?,?,?,?,?)", (saved["id"], farm_id, request_id, supersedes_id, saved["created_at"], _json(saved)))
    return saved


def _window(period):
    _object(period, ("start", "end", "timezone"), "budget period")
    start, end = _date(period["start"]), _date(period["end"])
    if start > end:
        raise ValueError("budget period end precedes start")
    try:
        zone = ZoneInfo(text("timezone", period["timezone"]))
    except ZoneInfoNotFoundError as exc:
        raise ValueError("budget timezone must be an IANA timezone") from exc
    try:
        window = (datetime.combine(start, time(), zone).astimezone(timezone.utc),
                  datetime.combine(end + timedelta(days=1), time(), zone).astimezone(timezone.utc))
        if window[1] <= window[0]:
            raise ValueError("budget period has no elapsed time in its timezone")
        return window
    except OverflowError as exc:
        raise ValueError("budget period is outside supported calendar boundaries") from exc


def budget_data(value):
    data = json.loads(_json(_object(value, ("resource_key", "kind", "unit", "period", "capacity", "source"), "budget")))
    data["resource_key"] = text("resource_key", data["resource_key"]).strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,79}", data["resource_key"]):
        raise ValueError("resource_key requires 1–80 letters, numbers, dots, underscores or hyphens")
    choice("kind", data["kind"], KINDS)
    if data["kind"] == "cash":
        if not isinstance(data["unit"], str) or not re.fullmatch(r"[A-Z]{3}", data["unit"]):
            raise ValueError("cash unit requires a three-uppercase-letter currency code")
    elif data["unit"] != KINDS[data["kind"]]:
        raise ValueError("budget unit does not match its kind")
    _window(data["period"])
    data["capacity"] = _display(_amount(data["capacity"]))
    data["source"] = text("source", data["source"]).strip()
    return data


def _charged(conn, farm_id):
    return [r for r in _history(conn, TABLES[1], farm_id) if not r["superseded"] and r["status"] != "released"]


def _usage(active, root_id):
    return sum(_amount(r["amounts"].get(root_id, "0")) for r in active)


def _signed(value):
    return "-" + _display(-value) if value < 0 else _display(value)


def _family(conn, farm_id, plan):
    seen = set()
    while plan["parent_id"] is not None:
        if plan["id"] in seen:
            raise ValueError("resource plan ancestry is cyclic")
        seen.add(plan["id"])
        plan = _read(conn, farm_id, plan["parent_id"])
    return plan["id"]


class ResourceLedger:
    def __init__(self, db):
        self.db = db

    def save_budget(self, owner, farm_id, data, *, request_id, supersedes_id=None, reason=None):
        data = budget_data(data)
        if reason is not None:
            reason = text("reason", reason).strip()
        operation = dict(kind="save_budget", data=data, supersedes_id=supersedes_id, reason=reason)
        with self.db.transaction() as conn:
            farm = _farm(conn, owner, farm_id)
            if previous := _retry(conn, TABLES[0], farm_id, request_id, operation):
                return previous
            prior = _latest(conn, TABLES[0], farm_id, supersedes_id) if supersedes_id is not None else None
            if prior is None and data["period"]["timezone"] != farm["timezone"]:
                raise ValueError("new budget timezone must match the farm timezone")
            if prior:
                if reason is None:
                    raise ValueError("budget corrections require a reason")
                if any(data[k] != prior["data"][k] for k in ("resource_key", "kind", "unit", "period")):
                    raise ValueError("budget corrections must preserve resource identity, kind, unit and period")
            start, end = _window(data["period"])
            for row in _history(conn, TABLES[0], farm_id):
                if row["superseded"] or row["data"]["resource_key"] != data["resource_key"]:
                    continue
                if any(data[k] != row["data"][k] for k in ("kind", "unit")):
                    raise ValueError("a named resource must retain its kind and unit across periods")
                if prior and row["root_id"] == prior["root_id"]:
                    continue
                other_start, other_end = _window(row["data"]["period"])
                if start < other_end and other_start < end:
                    raise ValueError("named resource already has an overlapping budget; correct its current revision")
            if (prior and _amount(data["capacity"]) < _usage(_charged(conn, farm_id), prior["root_id"])
                    and _amount(data["capacity"]) < _amount(prior["data"]["capacity"])):
                raise ValueError("budget capacity cannot be reduced below active commitments and recorded usage")
            return _insert(conn, TABLES[0], owner, farm_id, request_id, operation, supersedes_id,
                           root_id=prior["root_id"] if prior else None, data=data, reason=reason)

    def commit_plan(self, owner, farm_id, plan_id, bindings, source, accept_partial, *, request_id, supersedes_id=None, reason=None):
        source = text("source", source).strip()
        if type(accept_partial) is not bool:
            raise ValueError("accept_partial must be an explicit boolean")
        if reason is not None:
            reason = text("reason", reason).strip()
        if not isinstance(bindings, dict):
            raise ValueError("bindings must map every plan pool ID to a current budget revision ID")
        bindings = json.loads(_json(bindings))
        operation = dict(kind="commit_plan", plan_id=plan_id, bindings=bindings, source=source,
                         accept_partial=accept_partial, supersedes_id=supersedes_id, reason=reason)
        with self.db.transaction() as conn:
            farm = _farm(conn, owner, farm_id)
            if previous := _retry(conn, TABLES[1], farm_id, request_id, operation):
                return previous
            prior = _latest(conn, TABLES[1], farm_id, supersedes_id) if supersedes_id is not None else None
            if prior and prior["status"] in ("settled", "partially_settled"):
                raise ValueError("a settlement cannot be replaced by a reservation; correct its settlement or plan separate remaining work")
            if prior and reason is None:
                raise ValueError("replacement commitments require a reason")
            plan = _read(conn, farm_id, plan_id)
            _snapshot(conn, farm, plan["inputs"])
            if _review(conn, plan)["needs_review"]:
                raise ValueError("plan evidence changed; save a reviewed scenario before committing")
            if "timing_result" in plan and not plan["timing_result"]["declared_checks_pass"]:
                raise ValueError("the plan's declared timing checks failed")
            if "schedule_result" in plan and plan["schedule_result"]["status"] != "feasible_schedule_found":
                raise ValueError("the plan has no feasible saved schedule")
            if not plan["result"]["all_requested_blocks_allocated"] and not accept_partial:
                raise ValueError("plan is partially allocated; explicitly accept_partial to commit only allocated blocks")
            period = plan["inputs"]["period"]
            if _date(period["end"]) < datetime.now(ZoneInfo(farm["timezone"])).date():
                raise ValueError("cannot create a commitment for an ended plan period")
            pools = {r["id"]: r for r in plan["result"]["resources"]}
            if set(bindings) != set(pools):
                raise ValueError("bindings must include every plan pool exactly once")
            active = [r for r in _charged(conn, farm_id) if not prior or r["root_id"] != prior["root_id"]]
            family = _family(conn, farm_id, plan)
            if any(r["plan_family_id"] == family for r in active):
                raise ValueError("this plan family already has a commitment or settlement; review its current record")
            budgets, amounts = {}, {}
            for key, pool in pools.items():
                budget = _latest(conn, TABLES[0], farm_id, bindings[key])
                data = budget["data"]
                if (data["kind"], data["unit"]) != (pool["kind"], pool["unit"]):
                    raise ValueError("plan pool and budget kind/unit must match")
                if (data["period"]["timezone"] != period["timezone"] or data["period"]["start"] > period["start"]
                        or data["period"]["end"] < period["end"]):
                    raise ValueError("budget must contain the complete plan period in the same timezone")
                root = budget["root_id"]
                budgets[root] = budget
                amounts[root] = amounts.get(root, 0) + _amount(pool["assigned"])
            if not any(amounts.values()):
                raise ValueError("the plan allocates no resource quantities")
            from .resource_settlement import settlement_review
            for existing in active:
                if "settlement" in existing:
                    evidence = settlement_review(conn, owner, farm_id, existing)["evidence"]
                    if any(row["budget_root"] in amounts and row["needs_review"] for row in evidence):
                        raise ValueError("settlement evidence changed for a mapped budget; correct its current settlement before reserving more")
            for root, amount in amounts.items():
                if amount + _usage(active, root) > _amount(budgets[root]["data"]["capacity"]):
                    raise ValueError(f"insufficient uncommitted capacity in {budgets[root]['data']['resource_key']}")
            return _insert(conn, TABLES[1], owner, farm_id, request_id, operation, supersedes_id,
                root_id=prior["root_id"] if prior else None, status="active", plan_id=plan_id, plan_family_id=family,
                bindings=bindings, amounts={k: _display(v) for k, v in amounts.items()},
                budget_snapshots=budgets, plan_snapshot=plan, source=source, reason=reason, note=NOTE)

    def release_commitment(self, owner, farm_id, commitment_id, source, reason, *, request_id):
        source, reason = text("source", source).strip(), text("reason", reason).strip()
        operation = dict(kind="release_commitment", commitment_id=commitment_id, source=source, reason=reason)
        with self.db.transaction() as conn:
            _farm(conn, owner, farm_id)
            if previous := _retry(conn, TABLES[1], farm_id, request_id, operation):
                return previous
            prior = _latest(conn, TABLES[1], farm_id, commitment_id)
            if prior["status"] != "active":
                raise ValueError("only an active unused commitment can be released; correct settled usage separately")
            fields = {k: prior[k] for k in ("root_id", "plan_id", "plan_family_id", "bindings", "amounts", "budget_snapshots", "plan_snapshot", "note")}
            return _insert(conn, TABLES[1], owner, farm_id, request_id, operation, commitment_id,
                           **fields, status="released", source=source, reason=reason)

    def ledger(self, owner, farm_id):
        from .resource_settlement import settlement_review

        with self.db.transaction() as conn:
            farm = _farm(conn, owner, farm_id)
            budgets = _history(conn, TABLES[0], farm_id)
            commitments = _history(conn, TABLES[1], farm_id)
            charged = [r for r in commitments if not r["superseded"] and r["status"] != "released"]
            evidence_reviews = {r["id"]: settlement_review(conn, owner, farm_id, r) for r in commitments}
            current = {r["root_id"]: r for r in budgets if not r["superseded"]}
            balances = []
            for root, row in current.items():
                used = _usage(charged, root)
                reserved = sum(_amount(r.get("reserved_amounts", r["amounts"] if r["status"] == "active" else {}).get(root, "0")) for r in charged)
                recorded = sum(_amount(r.get("used_amounts", {}).get(root, "0")) for r in charged)
                unknown = sum(root in r.get("settlement", {}).get("usage", {}) and r["settlement"]["usage"][root]["amount"] is None for r in charged)
                evidence_changed = any(e["budget_root"] == root and e["needs_review"] for r in charged for e in evidence_reviews[r["id"]]["evidence"])
                remaining = _amount(row["data"]["capacity"]) - used
                balances.append(dict(row, committed=_display(used), reserved=_display(reserved), recorded_use=_display(recorded),
                    remaining=_signed(remaining), available=_display(max(0, remaining)), overdrawn=_display(max(0, -remaining)),
                    unresolved_usage_count=unknown,
                    settlement_evidence_changed=evidence_changed,
                    timezone_changed=row["data"]["period"]["timezone"] != farm["timezone"]))
            for row in commitments:
                changed = [key for key, old in row.get("settlement_budget_snapshots", row["budget_snapshots"]).items() if current[key]["id"] != old["id"]]
                plan_review = _review(conn, row["plan_snapshot"])
                settlement = evidence_reviews[row["id"]]
                family_conflicts = [other["id"] for other in charged if other["root_id"] != row["root_id"]
                                    and other["plan_family_id"] == row["plan_family_id"]] if not row["superseded"] and row["status"] != "released" else []
                row["review"] = dict(plan=plan_review, changed_budget_roots=changed,
                                     settlement=settlement, family_conflict_ids=family_conflicts,
                                     needs_review=bool(changed or plan_review["needs_review"] or settlement["needs_review"] or family_conflicts))
            return dict(budgets=balances, budget_history=budgets, commitments=commitments,
                        settlements=[r for r in commitments if not r["superseded"] and "settlement" in r],
                        active=[r for r in commitments if not r["superseded"] and r["status"] == "active"], note=NOTE)
