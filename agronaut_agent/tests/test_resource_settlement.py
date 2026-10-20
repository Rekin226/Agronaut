"""Actual use stays charged, unknowns stay held, and evidence/corrections stay scoped."""

import copy
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from agronaut_agent.farm_resources import execute_farm_resources
from agronaut_agent.farm_store import FarmStore
from agronaut_agent.resource_ledger import ResourceLedger
from agronaut_agent.resource_settlement import ResourceSettlement
from agronaut_agent.store import SCHEMA_VERSION, ConversationStore, _Db
from agronaut_agent.tests import test_field_workflow as field_fixtures
from agronaut_agent.tests.test_farm_resources import setup as setup
from agronaut_agent.tests.test_resource_ledger import budget, commit, plan, prepared
from agronaut_agent.tests.test_resource_outcomes import scenario

linked_setup = field_fixtures.setup


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 20, 12, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr("agronaut_agent.resource_ledger.datetime", Clock)
    monkeypatch.setattr("agronaut_agent.resource_settlement.datetime", Clock)


def usage(commitment, amount="450.25", **changes):
    return dict(dict(actual_start="2026-10-20", actual_end="2026-10-20", attribution="Complete use for the finished committed work, including maintenance",
        source="Synthetic reconciliation notebook", usage={key: dict(amount=amount, provenance="reported" if amount is not None else None,
        source="Synthetic full total" if amount is not None else "Actual total is unknown", outcome_ids=[]) for key in commitment["amounts"]}), **changes)


def prepared_settlement(service):
    ledger, pool, saved = prepared(service)
    reserved = commit(ledger, pool, saved)
    return ledger, ResourceSettlement(service.db), pool, saved, reserved


def settle(service, prior, data, key="settle"):
    return service.settle_commitment("owner", "farm", prior["id"], data, "Operator reconciled complete work", request_id=key)


def test_known_use_replaces_reservation_and_cannot_be_released_or_reserved_again(setup):
    service, farms = setup
    ledger, settlement, pool, saved, reserved = prepared_settlement(service)
    before = farms.export("owner")
    data = usage(reserved)
    result = settle(settlement, reserved, data)
    view = ledger.ledger("owner", "farm")
    balance = view["budgets"][0]
    assert result["status"] == "settled" and view["active"] == []
    assert (balance["reserved"], balance["recorded_use"], balance["committed"], balance["remaining"]) == ("0", "450.25", "450.25", "549.75")
    assert result["plan_snapshot"] == reserved["plan_snapshot"]
    assert result["baseline_amounts"] == reserved["amounts"]
    assert settle(settlement, reserved, data) == result
    for operation in (lambda: ledger.release_commitment("owner", "farm", result["id"], "Operator", "Wrong release", request_id="bad"),
                      lambda: commit(ledger, pool, saved, "bad", supersedes_id=result["id"], reason="Erase use"),
                      lambda: commit(ledger, pool, saved, "again")):
        with pytest.raises(ValueError):
            operation()
    after = farms.export("owner")
    assert {k: v for k, v in before.items() if k != "resource_commitments"} == {k: v for k, v in after.items() if k != "resource_commitments"}
    assert after["resource_commitments"] == before["resource_commitments"] + [result]
    corrected = settle(settlement, result, usage(result, "500"), "correct")
    assert corrected["used_amounts"][pool["root_id"]] == "500"
    with pytest.raises(ValueError, match="superseded"):
        settle(settlement, reserved, usage(reserved, "400"), "stale")
    with pytest.raises(ValueError, match="different data"):
        settle(settlement, reserved, usage(reserved, "400"))
    assert settle(settlement, reserved, data) == result


def test_unknown_retains_allowance_and_overrun_is_not_hidden_or_freed(setup):
    service, _ = setup
    ledger, settlement, pool, _, reserved = prepared_settlement(service)
    unknown = settle(settlement, reserved, usage(reserved, None))
    balance = ledger.ledger("owner", "farm")["budgets"][0]
    assert (balance["reserved"], balance["recorded_use"], balance["remaining"]) == ("600", "0", "400")
    assert ledger.ledger("owner", "farm")["settlements"][0]["review"]["settlement"]["unknown_budget_roots"] == [pool["root_id"]]
    over = settle(settlement, unknown, usage(unknown, "1200.000001"), "over")
    balance = ledger.ledger("owner", "farm")["budgets"][0]
    assert (balance["remaining"], balance["available"], balance["overdrawn"]) == ("-200.000001", "0", "200.000001")
    second = service.save("owner", "farm", plan("1", "hydro"), request_id="another")
    with pytest.raises(ValueError, match="insufficient"):
        commit(ledger, pool, second, "bad")
    with pytest.raises(ValueError, match="cannot be reduced"):
        ledger.save_budget("owner", "farm", budget(capacity="900"), request_id="lower", supersedes_id=pool["id"], reason="Lower")
    raised = ledger.save_budget("owner", "farm", budget(capacity="1100"), request_id="raise", supersedes_id=pool["id"], reason="More documented allowance")
    assert ledger.ledger("owner", "farm")["budgets"][0]["overdrawn"] == "100.000001"
    uncertain = settle(settlement, over, usage(over, None), "uncertain")
    assert uncertain["reserved_amounts"][pool["root_id"]] == "1200.000001"
    assert ledger.ledger("owner", "farm")["budgets"][0]["overdrawn"] == "100.000001"
    zero = settle(settlement, uncertain, usage(uncertain, "0"), "zero")
    assert zero["status"] == "settled"
    assert ledger.ledger("owner", "farm")["budgets"][0]["remaining"] == "1100"
    commit(ledger, raised, second, "now-fits")


@pytest.mark.parametrize("change", ["negative", "float", "too_precise", "exponent", "missing_root", "extra_root", "source", "provenance", "unknown_provenance",
    "unknown_link", "duplicate_link", "future", "before_budget", "reversed", "attribution", "extra_field"])
def test_invalid_settlement_is_atomic(setup, change):
    service, farms = setup
    _, settlement, _, _, reserved = prepared_settlement(service)
    data = usage(reserved)
    row = next(iter(data["usage"].values()))
    if change in ("negative", "float", "too_precise", "exponent"):
        row["amount"] = {"negative": "-1", "float": 10, "too_precise": "0.0000001", "exponent": "1e2"}[change]
    elif change == "missing_root":
        data["usage"] = {}
    elif change == "extra_root":
        data["usage"]["foreign"] = dict(row)
    elif change == "source":
        row["source"] = ""
    elif change == "provenance":
        row["provenance"] = "verified"
    elif change == "unknown_provenance":
        row["amount"] = None
    elif change == "unknown_link":
        row.update(amount=None, provenance=None, outcome_ids=["not-a-review"])
    elif change == "duplicate_link":
        row.update(provenance="linked_outcomes", outcome_ids=["duplicate", "duplicate"])
    elif change == "future":
        data["actual_end"] = "2026-10-21"
    elif change == "before_budget":
        data["actual_start"] = "2026-10-18"
    elif change == "reversed":
        data["actual_start"] = "2026-10-21"
    elif change == "attribution":
        data["attribution"] = ""
    else:
        data["complete"] = True
    before = farms.export("owner")
    with pytest.raises(ValueError):
        settle(settlement, reserved, data)
    assert farms.export("owner") == before


def test_late_use_after_release_is_recordable_and_family_overlap_flagged(setup):
    service, _ = setup
    ledger, settlement, pool, saved, reserved = prepared_settlement(service)
    released = ledger.release_commitment("owner", "farm", reserved["id"], "Operator", "Thought unused", request_id="release")
    newer = commit(ledger, pool, saved, "new")
    late = settle(settlement, released, usage(released, "600"))
    view = ledger.ledger("owner", "farm")
    assert view["budgets"][0]["overdrawn"] == "200"
    assert view["settlements"][0]["review"]["family_conflict_ids"] == [newer["id"]]
    assert view["active"][0]["review"]["family_conflict_ids"] == [late["id"]]
    ledger.release_commitment("owner", "farm", newer["id"], "Operator", "Duplicate unused reservation", request_id="release-new")
    assert ledger.ledger("owner", "farm")["budgets"][0]["recorded_use"] == "600"


def test_scope_dates_use_original_timezone_export_and_erasure(setup):
    service, farms = setup
    ledger, settlement, _, _, reserved = prepared_settlement(service)
    for owner, farm in (("other", "farm"), ("owner", "missing")):
        with pytest.raises(ValueError):
            settlement.settle_commitment(owner, farm, reserved["id"], usage(reserved), "Scope", request_id="bad")
        with pytest.raises(ValueError):
            settlement.settlement_inputs(owner, farm, reserved["id"])
    service.db.execute("UPDATE farms SET timezone='America/Los_Angeles' WHERE id='farm'")
    settled = settle(settlement, reserved, usage(reserved))
    assert settled in farms.export("owner")["resource_commitments"]
    assert ledger.ledger("owner", "farm")["settlements"][0]["review"]["needs_review"]
    farms.purge("owner")
    assert all(not rows for rows in farms.export("owner").values())


def linked(workflow, monkeypatch):
    class Earlier(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 7, 6, 12, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr("agronaut_agent.resource_ledger.datetime", Earlier)
    service, saved, data = scenario(workflow)
    ledger = ResourceLedger(workflow.db)
    budgets, bindings = {}, {}
    for pool in saved["inputs"]["resources"]:
        row = ledger.save_budget("owner", "farm", budget(resource_key=pool["id"], kind=pool["kind"], unit=pool["unit"], capacity=pool["available"],
            period=saved["inputs"]["period"]), request_id=pool["id"])
        budgets[pool["id"]] = row
        bindings[pool["id"]] = row["id"]
    reserved = ledger.commit_plan("owner", "farm", saved["id"], bindings, "Operator", False, request_id="commit")
    outcome = service.save_outcome("owner", "farm", saved["id"], "irrigate", data, request_id="outcome")
    report = usage(reserved, None, actual_start="2026-07-07", actual_end="2026-07-07")
    for key, amount in (("tank", "1000"), ("pump", "2")):
        report["usage"][budgets[key]["root_id"]].update(amount=amount, provenance="linked_outcomes", outcome_ids=[outcome["id"]])
    return service, ledger, ResourceSettlement(workflow.db), saved, reserved, outcome, data, report, budgets


def test_linked_event_totals_and_quality_changes_preserve_original_charges(linked_setup, monkeypatch):
    from agronaut_agent.observation_quality import ObservationReviews

    workflow, _ = linked_setup
    service, ledger, settlement, saved, reserved, outcome, source, report, budgets = linked(workflow, monkeypatch)
    inputs = settlement.settlement_inputs("owner", "farm", reserved["id"])
    assert inputs["outcomes"][0]["id"] == outcome["id"]
    settled = settle(settlement, reserved, report)
    assert settled["status"] == "partially_settled" and len(settled["outcome_evidence"]) == 2
    assert settled["used_amounts"][budgets["tank"]["root_id"]] == "1000"
    checked = ObservationReviews(workflow.db).review("owner", "field", source["water"]["observation_id"], "checked", "Checked meter",
        "Operator", request_id="quality", expected_revision=None)
    view = ledger.ledger("owner", "farm")["settlements"][0]
    assert view["amounts"] == settled["amounts"]
    assert next(r for r in view["review"]["settlement"]["evidence"] if r["budget_root"] == budgets["tank"]["root_id"])["needs_review"]
    assert next(b for b in ledger.ledger("owner", "farm")["budgets"] if b["root_id"] == budgets["tank"]["root_id"])["settlement_evidence_changed"]
    next_plan = service.save("owner", "farm", saved["inputs"], request_id="separate-work")
    with pytest.raises(ValueError, match="settlement evidence changed"):
        ledger.commit_plan("owner", "farm", next_plan["id"], reserved["bindings"], "More work", False, request_id="more")
    with pytest.raises(ValueError, match="evidence changed"):
        settle(settlement, settled, report, "stale-evidence")
    source["water"]["expected_review_id"] = checked["id"]
    updated = service.save_outcome("owner", "farm", saved["id"], "irrigate", source, request_id="new-outcome", supersedes_id=outcome["id"])
    with pytest.raises(ValueError, match="superseded"):
        settle(settlement, settled, report, "old-outcome")
    for key in ("tank", "pump"):
        report["usage"][budgets[key]["root_id"]]["outcome_ids"] = [updated["id"]]
    report["usage"][budgets["crew"]["root_id"]].update(amount="1.5", provenance="reported")
    corrected = settle(settlement, settled, report, "corrected")
    assert corrected["status"] == "settled"
    assert not ledger.ledger("owner", "farm")["settlements"][0]["review"]["needs_review"]
    ledger.commit_plan("owner", "farm", next_plan["id"], reserved["bindings"], "More work", False, request_id="more")


def test_linked_totals_require_exact_complete_scope_and_cannot_be_charged_twice(linked_setup, monkeypatch):
    workflow, _ = linked_setup
    service, ledger, settlement, saved, reserved, _, source, report, budgets = linked(workflow, monkeypatch)
    wrong = copy.deepcopy(report)
    wrong["usage"][budgets["tank"]["root_id"]]["amount"] = "999"
    with pytest.raises(ValueError, match="exact sum"):
        settle(settlement, reserved, wrong, "wrong")
    with pytest.raises(ValueError, match="actual period"):
        settle(settlement, reserved, dict(report, actual_start="2026-07-06", actual_end="2026-07-06"), "date")
    settle(settlement, reserved, report)
    alternate = service.save("owner", "farm", saved["inputs"], request_id="independent")
    other = ledger.commit_plan("owner", "farm", alternate["id"], reserved["bindings"], "Separate intended work", False, request_id="other")
    duplicate = service.save_outcome("owner", "farm", alternate["id"], "irrigate", source, request_id="duplicate")
    other_report = copy.deepcopy(report)
    for key in ("tank", "pump"):
        other_report["usage"][budgets[key]["root_id"]]["outcome_ids"] = [duplicate["id"]]
    with pytest.raises(ValueError, match="another current settlement"):
        settle(settlement, other, other_report, "double-charge")
    with pytest.raises(ValueError, match="exact committed plan"):
        settle(settlement, other, report, "foreign-plan")


def test_stdlib_cli_records_and_reads_settlement_without_model(setup, tmp_path, monkeypatch):
    monkeypatch.setattr("agronaut_agent.resource_ledger.datetime", datetime)
    service, _ = setup
    owner = ConversationStore(service.db).get_or_create_user("cli", "tester")
    service.db.execute("UPDATE farms SET owner_id=? WHERE id='farm'", (owner,))
    today = datetime.now(ZoneInfo("Asia/Taipei")).date().isoformat()
    period = dict(start=today, end=today, timezone="Asia/Taipei")
    ledger = ResourceLedger(service.db)
    pool = ledger.save_budget(owner, "farm", budget(period=period), request_id="pool")
    saved = service.save(owner, "farm", plan(period=period), request_id="plan")
    reserved = ledger.commit_plan(owner, "farm", saved["id"], dict(water=pool["id"]), "Operator", False, request_id="reserve")
    assert "settle_commitment" in execute_farm_resources(service.db, owner, "farm", "describe", {})["operations"]
    path = tmp_path / "settlement.json"
    path.write_text(json.dumps(dict(commitment_id=reserved["id"], data=usage(reserved, actual_start=today, actual_end=today), reason="Complete", request_id="cli")))
    command = [sys.executable, "-S", "-m", "agronaut_agent.cli", "resources", "--db", str(service.db.path), "--user", "tester", "--farm", "farm"]
    result = subprocess.run([*command, "settle_commitment", "--input", str(path)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout+result.stderr
    assert json.loads(result.stdout)["used_amounts"][pool["root_id"]] == "450.25"
    result = subprocess.run([*command, "ledger"], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0 and json.loads(result.stdout)["budgets"][0]["remaining"] == "549.75"


def test_linked_partial_event_set_cannot_claim_complete_budget_usage(linked_setup, monkeypatch):
    workflow, _ = linked_setup
    service, ledger, settlement, original, reserved, _, source, _, budgets = linked(workflow, monkeypatch)
    ledger.release_commitment("owner", "farm", reserved["id"], "Operator", "Replace unused scope", request_id="release")
    inputs = copy.deepcopy(original["inputs"])
    inputs["jobs"].append(dict(id="other", unit_id="field", cycle_id="cycle", title="Other work", block_label="One event",
        priority=2, requested_blocks=1, minimum_blocks=1, source="Synthetic other work",
        requirements={p["id"]: "50" if p["id"] == "tank" else "0" for p in inputs["resources"]}))
    saved = service.save("owner", "farm", inputs, request_id="larger-scope")
    reserved = ledger.commit_plan("owner", "farm", saved["id"], reserved["bindings"], "Two work events", False, request_id="two")
    outcome = service.save_outcome("owner", "farm", saved["id"], "irrigate", source, request_id="new-outcome")
    data = usage(reserved, None, actual_start="2026-07-07", actual_end="2026-07-07")
    water = data["usage"][budgets["tank"]["root_id"]]
    water.update(amount="1000", provenance="linked_outcomes", outcome_ids=[outcome["id"]])
    with pytest.raises(ValueError, match="every positively allocated"):
        settle(settlement, reserved, data)
    water.update(amount="1050", provenance="reported", outcome_ids=[], source="Synthetic notebook total for both work events")
    saved = settle(settlement, reserved, data, "manual-complete")
    assert saved["used_amounts"][budgets["tank"]["root_id"]] == "1050"


def test_v12_upgrade_preserves_active_ledger_and_older_reader_refuses_settlements(setup, monkeypatch):
    import agronaut_agent.store as module

    service, farms = setup
    prepared_settlement(service)
    before, path = farms.export("owner"), service.db.path
    service.db.execute("PRAGMA user_version=12")
    service.db._conn.close()
    reopened = _Db(path)
    assert reopened.query("PRAGMA user_version")[0][0] == SCHEMA_VERSION
    assert FarmStore(reopened).export("owner") == before
    assert ResourceLedger(reopened).ledger("owner", "farm")["budgets"][0]["remaining"] == "400"
    backup = next(path.parent.glob(f"*.pre-v{SCHEMA_VERSION}-*.bak"))
    assert backup.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(backup) as raw:
        assert raw.execute("PRAGMA user_version").fetchone()[0] == 12
    reopened._conn.close()
    with monkeypatch.context() as patch:
        patch.setattr(module, "SCHEMA_VERSION", 12)
        with pytest.raises(RuntimeError, match="newer"):
            _Db(path)
