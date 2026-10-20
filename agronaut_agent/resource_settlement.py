"""Explicit actual-use reconciliation without erasing reservations or inconvenient overruns."""

import json
from datetime import datetime
from fractions import Fraction
from zoneinfo import ZoneInfo

from agro_model.resources import _amount, _display, _object
from agro_model.validation import choice, text

from .farm_resources import _farm, _json
from .field_io import _date
from .resource_ledger import NOTE, TABLES, _history, _insert, _latest, _retry
from .resource_outcomes import _review_saved

MANUAL_PROVENANCE = ("measured", "documented", "reported", "estimated")
SETTLEMENT_NOTE = (
    "Settlement records complete usage totals for the committed work that finished or stopped in the stated actual period. "
    "Remaining future work needs a separate plan. Sources and attribution are operator-supplied, not independent verification. "
    "Known totals stay charged as recorded use; unknown totals retain at least the original allowance and previous charge. "
    "Unknown is not zero. An explicit known zero releases that allowance. Reported overruns are accepted and show a budget deficit; "
    "they never invent extra supply. Correct settlements explicitly; release/replacement cannot erase settled usage. "
    "No measurements, water logs, cash entries, task completions or equipment commands are created."
)


def _data(value, roots):
    data = json.loads(_json(_object(value, ("actual_start", "actual_end", "attribution", "source", "usage"), "settlement")))
    if _date(data["actual_start"]) > _date(data["actual_end"]):
        raise ValueError("actual_start must be on or before actual_end")
    for key in ("source", "attribution"):
        data[key] = text(key, data[key]).strip()
    _object(data["usage"], roots, "settlement usage")
    for row in data["usage"].values():
        _object(row, ("amount", "provenance", "source", "outcome_ids"), "budget usage")
        row["source"] = text("usage source", row["source"]).strip()
        ids = row["outcome_ids"]
        if not isinstance(ids, list) or len(ids) > 1000:
            raise ValueError("outcome_ids must be a list of at most 1000 current review IDs")
        for key in ids:
            text("outcome ID", key)
        if len(set(ids)) != len(ids):
            raise ValueError("select distinct outcome review IDs")
        if row["amount"] is None:
            if row["provenance"] is not None or ids:
                raise ValueError("unknown usage requires null provenance and no linked outcome totals")
        else:
            row["amount"] = _display(_amount(row["amount"]))
            choice("usage provenance", row["provenance"], ("linked_outcomes",) if ids else MANUAL_PROVENANCE)
    return data


def _pool_roots(prior):
    roots = {r["id"]: key for key, r in prior["budget_snapshots"].items()}
    return {pool: roots[key] for pool, key in prior["bindings"].items()}


def _evidence(conn, owner, farm_id, prior, root, data, usage):
    if not usage["outcome_ids"]:
        return []
    pools = _pool_roots(prior)
    required = {(r["job_id"], pool) for r in prior["plan_snapshot"]["result"]["allocations"]
                for pool, amount in r["assigned"].items() if pools[pool] == root and _amount(amount) > 0}
    covered, observations, total, evidence = set(), set(), Fraction(0), []
    for key in usage["outcome_ids"]:
        raw = conn.execute("SELECT payload FROM resource_outcome_reviews WHERE id=? AND farm_id=? AND plan_id=?",
                           (key, farm_id, prior["plan_id"])).fetchone()
        if raw is None:
            raise ValueError("linked outcome must belong to the exact committed plan in this farm")
        saved = json.loads(raw[0])
        if conn.execute("SELECT 1 FROM resource_outcome_reviews WHERE supersedes_id=?", (key,)).fetchone():
            raise ValueError("linked outcome was superseded; select its current revision")
        if not data["actual_start"] <= saved["actual_day"] <= data["actual_end"]:
            raise ValueError("linked outcome date must fall inside the stated actual period")
        review = _review_saved(conn, owner, farm_id, saved)
        metrics = []
        for metric in saved["comparisons"]:
            pool = metric["resource_id"]
            if pools.get(pool) != root or metric["observed_total"] is None:
                continue
            if metric["unit"] != prior["budget_snapshots"][root]["data"]["unit"]:
                raise ValueError("linked outcome unit differs from the budget unit")
            pair = saved["job_id"], pool
            if pair not in required or pair in covered or metric["observation_id"] in observations:
                raise ValueError("linked outcomes must cover each allocated job/pool once, without duplicate readings")
            if any(r["metric"] == metric["metric"] and r["needs_review"] for r in review["observations"]):
                raise ValueError("linked outcome measurement evidence changed; save a current outcome review first")
            covered.add(pair)
            observations.add(metric["observation_id"])
            total += Fraction(metric["observed_total"]["exact"])
            metrics.append({k: metric[k] for k in ("metric", "resource_id", "observation_id", "observed_total", "provenance", "quality")})
        if not metrics:
            raise ValueError("linked outcome has no known actual total for this budget")
        evidence.append(dict(budget_root=root, outcome_id=key, snapshot=saved, metrics=metrics))
    if covered != required:
        raise ValueError("linked outcomes must cover every positively allocated job/pool for this budget; use an explicit manual total otherwise")
    if total != Fraction(usage["amount"]):
        raise ValueError("usage amount must equal the exact sum of linked outcome totals; no rounding or extra unlinked usage")
    return evidence


def settlement_review(conn, owner, farm_id, saved):
    if "settlement" not in saved:
        return dict(needs_review=False, unknown_budget_roots=[], evidence=[])
    unknown = [key for key, row in saved["settlement"]["usage"].items() if row["amount"] is None]
    evidence = []
    for source in saved["outcome_evidence"]:
        issues = []
        if conn.execute("SELECT 1 FROM resource_outcome_reviews WHERE supersedes_id=?", (source["outcome_id"],)).fetchone():
            issues.append("linked outcome has a newer revision")
        try:
            review = _review_saved(conn, owner, farm_id, source["snapshot"])
            metrics = {m["metric"] for m in source["metrics"]}
            if any(r["metric"] in metrics and r["needs_review"] for r in review["observations"]):
                issues.append("linked measurement evidence changed")
        except ValueError:
            issues.append("linked outcome evidence is no longer available in its original scope")
        evidence.append(dict(outcome_id=source["outcome_id"], budget_root=source["budget_root"], issues=issues, needs_review=bool(issues)))
    return dict(needs_review=bool(unknown or any(r["needs_review"] for r in evidence)), unknown_budget_roots=unknown, evidence=evidence)


class ResourceSettlement:
    def __init__(self, db):
        self.db = db

    def settlement_inputs(self, owner, farm_id, commitment_id):
        with self.db.transaction() as conn:
            _farm(conn, owner, farm_id)
            prior = _latest(conn, TABLES[1], farm_id, commitment_id)
            rows = [json.loads(r[0]) for r in conn.execute(
                "SELECT r.payload FROM resource_outcome_reviews r WHERE r.farm_id=? AND r.plan_id=? "
                "AND NOT EXISTS (SELECT 1 FROM resource_outcome_reviews n WHERE n.supersedes_id=r.id) ORDER BY r.rowid", (farm_id, prior["plan_id"]))]
            return dict(commitment=prior, pool_budget_roots=_pool_roots(prior),
                outcomes=[dict(row, review=_review_saved(conn, owner, farm_id, row)) for row in rows], note=SETTLEMENT_NOTE)

    def settle_commitment(self, owner, farm_id, commitment_id, data, reason, *, request_id):
        reason = text("reason", reason).strip()
        with self.db.transaction() as conn:
            _farm(conn, owner, farm_id)
            # Validate retries against the addressed original revision, before enforcing latest.
            original = conn.execute(f"SELECT payload FROM {TABLES[1]} WHERE id=? AND farm_id=?", (commitment_id, farm_id)).fetchone()
            if original is None:
                raise ValueError("unknown commitment for this farm")
            original = json.loads(original[0])
            data = _data(data, tuple(original["amounts"]))
            operation = dict(kind="settle_commitment", commitment_id=commitment_id, data=data, reason=reason)
            if previous := _retry(conn, TABLES[1], farm_id, request_id, operation):
                return previous
            prior = _latest(conn, TABLES[1], farm_id, commitment_id)
            baseline = prior.get("baseline_amounts", prior["amounts"])
            current = {r["root_id"]: r for r in _history(conn, TABLES[0], farm_id) if not r["superseded"]}
            used, reserved, evidence = {}, {}, []
            for root, usage in data["usage"].items():
                period = prior["budget_snapshots"][root]["data"]["period"]
                if data["actual_start"] < period["start"] or data["actual_end"] > period["end"]:
                    raise ValueError("actual usage period must fit every original budget period; do not charge another period's supply")
                if _date(data["actual_end"]) > datetime.now(ZoneInfo(period["timezone"])).date():
                    raise ValueError("actual usage dates cannot be future in the saved budget timezone")
                if usage["amount"] is None:
                    used[root] = "0"
                    reserved[root] = _display(max(_amount(baseline[root]), _amount(prior["amounts"][root])))
                else:
                    used[root], reserved[root] = usage["amount"], "0"
                    evidence.extend(_evidence(conn, owner, farm_id, prior, root, data, usage))
            selected = [m["observation_id"] for e in evidence for m in e["metrics"]]
            if len(set(selected)) != len(selected):
                raise ValueError("a measurement cannot be charged twice in one settlement")
            for other in _history(conn, TABLES[1], farm_id):
                if other["superseded"] or other["root_id"] == prior["root_id"]:
                    continue
                already = {m["observation_id"] for e in other.get("outcome_evidence", []) for m in e["metrics"]}
                if already.intersection(selected):
                    raise ValueError("a linked measurement is already charged by another current settlement")
            fields = {key: prior[key] for key in ("root_id", "plan_id", "plan_family_id", "bindings", "budget_snapshots", "plan_snapshot")}
            return _insert(conn, TABLES[1], owner, farm_id, request_id, operation, commitment_id, **fields,
                status="partially_settled" if any(row["amount"] is None for row in data["usage"].values()) else "settled",
                amounts={key: _display(_amount(used[key]) + _amount(reserved[key])) for key in used},
                baseline_amounts=baseline, used_amounts=used, reserved_amounts=reserved,
                settlement=data, settlement_budget_snapshots={key: current[key] for key in used}, outcome_evidence=evidence,
                source=data["source"], reason=reason, note=NOTE, settlement_note=SETTLEMENT_NOTE)
