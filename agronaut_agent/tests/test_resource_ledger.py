"""Shared-budget conservation, atomic replacement, race safety and history recovery."""

import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from threading import Barrier, Thread

import pytest

from agronaut_agent.crop_records import CropRecords
from agronaut_agent.farm_resources import execute_farm_resources
from agronaut_agent.farm_store import FarmStore
from agronaut_agent.resource_ledger import ResourceLedger
from agronaut_agent.store import SCHEMA_VERSION, ConversationStore, _Db
from agronaut_agent.tests.test_farm_resources import setup as setup
from agronaut_agent.tests.test_farm_store import seed


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    import agronaut_agent.resource_ledger as module

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 19, 12, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(module, "datetime", Clock)


def budget(**changes):
    return dict(dict(resource_key="borehole", kind="water", unit="L", capacity="1000", source="Synthetic net supply allowance",
        period=dict(start="2026-10-19", end="2026-10-25", timezone="Asia/Taipei")), **changes)


def plan(amount="600", unit="unit", **changes):
    return dict(dict(title="Synthetic work", source="Synthetic requirements", period=dict(start="2026-10-20", end="2026-10-20", timezone="Asia/Taipei"),
        resources=[dict(id="water", kind="water", unit="L", available=amount, source="Scenario capacity")],
        jobs=[dict(id="work", unit_id=unit, cycle_id="cycle" if unit == "unit" else None, title="Synthetic irrigation",
            block_label="Whole event", priority=1, requested_blocks=1, minimum_blocks=1, requirements=dict(water=amount), source="Synthetic work")]), **changes)


def prepared(service):
    ledger = ResourceLedger(service.db)
    pool = ledger.save_budget("owner", "farm", budget(), request_id="budget")
    saved = service.save("owner", "farm", plan(), request_id="plan")
    return ledger, pool, saved


def commit(ledger, pool, saved, request="commit", **changes):
    return ledger.commit_plan("owner", "farm", saved["id"], {"water": pool["id"]}, "Synthetic explicit allocation", False,
                              request_id=request, **changes)


def test_two_fields_share_capacity_and_release_does_not_write_actuals(setup):
    service, farms = setup
    ledger, pool, first = prepared(service)
    second = service.save("owner", "farm", plan("400", "hydro"), request_id="second")
    a, b = commit(ledger, pool, first), commit(ledger, pool, second, "second-commit")
    view = ledger.ledger("owner", "farm")
    assert view["budgets"][0]["committed"] == "1000" and view["budgets"][0]["remaining"] == "0"
    assert len(view["active"]) == 2
    assert a["plan_snapshot"] == first and a["budget_snapshots"][pool["root_id"]] == pool
    before = farms.export("owner")
    extra = service.save("owner", "farm", plan("0.000001", "hydro"), request_id="extra")
    with pytest.raises(ValueError, match="insufficient"):
        commit(ledger, pool, extra, "over")
    assert farms.export("owner")["resource_commitments"] == before["resource_commitments"]
    released = ledger.release_commitment("owner", "farm", a["id"], "Operator", "Cancelled", request_id="release")
    assert released["status"] == "released"
    assert ledger.ledger("owner", "farm")["budgets"][0]["remaining"] == "600"
    assert [r["id"] for r in ledger.ledger("owner", "farm")["active"]] == [b["id"]]
    exported = farms.export("owner")
    for key in ("water_days", "observations", "crop_records"):
        assert exported[key] == before[key]


def test_atomic_replacement_family_duplicates_and_restore(setup):
    service, _ = setup
    ledger, pool, first = prepared(service)
    initial = commit(ledger, pool, first)
    alternative = service.save("owner", "farm", plan("900"), request_id="alternative", parent_id=first["id"])
    with pytest.raises(ValueError, match="family"):
        commit(ledger, pool, alternative, "duplicate")
    too_large = service.save("owner", "farm", plan("1001"), request_id="large", parent_id=first["id"])
    with pytest.raises(ValueError, match="insufficient"):
        commit(ledger, pool, too_large, "failed", supersedes_id=initial["id"], reason="Try larger plan")
    assert [r["id"] for r in ledger.ledger("owner", "farm")["active"]] == [initial["id"]]
    replaced = commit(ledger, pool, alternative, "replace", supersedes_id=initial["id"], reason="Updated plan")
    assert ledger.ledger("owner", "farm")["budgets"][0]["remaining"] == "100"
    assert commit(ledger, pool, first) == initial
    with pytest.raises(ValueError, match="superseded"):
        ledger.release_commitment("owner", "farm", initial["id"], "Operator", "Stale", request_id="stale")
    released = ledger.release_commitment("owner", "farm", replaced["id"], "Operator", "Cancel", request_id="release")
    restored = commit(ledger, pool, first, "restore", supersedes_id=released["id"], reason="Resume original")
    assert restored["root_id"] == initial["root_id"]
    assert ledger.release_commitment("owner", "farm", replaced["id"], "Operator", "Cancel", request_id="release") == released
    with pytest.raises(ValueError, match="different data"):
        commit(ledger, pool, alternative)


def test_budget_revision_conservation_overlap_identity_and_staleness(setup):
    service, _ = setup
    ledger, pool, saved = prepared(service)
    commit(ledger, pool, saved)
    with pytest.raises(ValueError, match="below active"):
        ledger.save_budget("owner", "farm", budget(capacity="599.999999"), request_id="lower", supersedes_id=pool["id"], reason="Correction")
    for changes in (dict(resource_key="another"), dict(period=dict(budget()["period"], end="2026-10-26")), dict(kind="cash", unit="USD")):
        with pytest.raises(ValueError, match="preserve"):
            ledger.save_budget("owner", "farm", budget(**changes), request_id="bad", supersedes_id=pool["id"], reason="Correction")
    with pytest.raises(ValueError, match="overlapping"):
        ledger.save_budget("owner", "farm", budget(resource_key="BOREHOLE"), request_id="alias")
    updated = ledger.save_budget("owner", "farm", budget(capacity="1200"), request_id="new", supersedes_id=pool["id"], reason="Checked allowance")
    view = ledger.ledger("owner", "farm")
    assert view["budgets"][0]["remaining"] == "600"
    assert view["active"][0]["review"]["changed_budget_roots"] == [pool["root_id"]]
    assert ledger.save_budget("owner", "farm", budget(), request_id="budget") == pool
    second = service.save("owner", "farm", plan("500", "hydro"), request_id="second")
    with pytest.raises(ValueError, match="superseded"):
        commit(ledger, pool, second, "stale")
    commit(ledger, updated, second, "fresh")
    later = dict(budget()["period"], start="2026-10-26", end="2026-10-30")
    ledger.save_budget("owner", "farm", budget(period=later), request_id="later")
    with pytest.raises(ValueError, match="kind and unit"):
        ledger.save_budget("owner", "farm", budget(period=dict(later, start="2026-11-01", end="2026-11-02"), kind="cash", unit="USD"), request_id="kind")


@pytest.mark.parametrize("changes", [dict(capacity="-1"), dict(capacity=1000), dict(capacity="nan"), dict(capacity="0.0000001"),
    dict(resource_key="two supplies"), dict(resource_key=""), dict(kind="energy"), dict(unit="m3"), dict(kind="cash", unit="usd"),
    dict(source=""), dict(period=dict(start="2026-10-20", end="2026-10-19", timezone="Asia/Taipei")),
    dict(period=dict(start="2026-10-20", end="2026-10-21", timezone="Not/a-zone")), dict(extra=True)])
def test_bad_budget_is_atomic(setup, changes):
    service, farms = setup
    before = farms.export("owner")
    with pytest.raises(ValueError):
        ResourceLedger(service.db).save_budget("owner", "farm", budget(**changes), request_id="bad")
    assert farms.export("owner") == before


def test_multiple_scenario_pools_mapped_to_one_budget_are_summed(setup):
    service, _ = setup
    ledger, pool, _ = prepared(service)
    inputs = plan("600")
    inputs["resources"].append(dict(inputs["resources"][0], id="water2", available="500"))
    inputs["jobs"][0]["requirements"]["water2"] = "500"
    saved = service.save("owner", "farm", inputs, request_id="double")
    with pytest.raises(ValueError, match="insufficient"):
        ledger.commit_plan("owner", "farm", saved["id"], dict(water=pool["id"], water2=pool["id"]), "Shared supply", False, request_id="bad")
    inputs["jobs"][0]["requirements"]["water2"] = "400"
    saved = service.save("owner", "farm", inputs, request_id="fits")
    committed = ledger.commit_plan("owner", "farm", saved["id"], dict(water=pool["id"], water2=pool["id"]), "Shared supply", False, request_id="ok")
    assert committed["amounts"] == {pool["root_id"]: "1000"}


def test_partial_allocation_explicit_acceptance_scope_period_and_units(setup):
    service, _ = setup
    ledger, pool, _ = prepared(service)
    inputs = plan("600")
    inputs["jobs"][0]["requested_blocks"] = 2
    saved = service.save("owner", "farm", inputs, request_id="partial")
    with pytest.raises(ValueError, match="accept_partial"):
        commit(ledger, pool, saved)
    committed = ledger.commit_plan("owner", "farm", saved["id"], dict(water=pool["id"]), "Accept one block", True, request_id="accepted")
    assert committed["amounts"] == {pool["root_id"]: "600"}
    for bindings in ({}, {"water": pool["id"], "extra": pool["id"]}, {"water": "missing"}):
        with pytest.raises(ValueError):
            ledger.commit_plan("owner", "farm", saved["id"], bindings, "Operator", True, request_id="bad",
                               supersedes_id=committed["id"], reason="Check")
    narrow = ledger.save_budget("owner", "farm", budget(resource_key="narrow", period=dict(budget()["period"], start="2026-10-21")), request_id="narrow")
    wrong = ledger.save_budget("owner", "farm", budget(resource_key="cash", kind="cash", unit="USD"), request_id="cash")
    for target in (narrow, wrong):
        with pytest.raises(ValueError):
            commit(ledger, target, saved, "bad", supersedes_id=committed["id"], reason="Check")


def test_changed_evidence_and_expired_periods_do_not_release_existing_commitments(setup):
    service, _ = setup
    ledger, pool, saved = prepared(service)
    original = commit(ledger, pool, saved)
    CropRecords(service.db).correct_planting("owner", "unit", "cycle", "pepper", "2026-07-02", "Notebook", "Typo", request_id="crop", expected_revision=None)
    with pytest.raises(ValueError, match="evidence changed"):
        commit(ledger, pool, saved, "bad", supersedes_id=original["id"], reason="Try again")
    view = ledger.ledger("owner", "farm")
    assert view["active"][0]["review"]["needs_review"] and view["budgets"][0]["committed"] == "600"
    old = service.save("owner", "farm", plan(period=dict(start="2026-10-18", end="2026-10-18", timezone="Asia/Taipei")), request_id="old")
    with pytest.raises(ValueError, match="ended"):
        commit(ledger, pool, old, "old")
    service.db.execute("UPDATE farms SET timezone='UTC' WHERE id='farm'")
    updated = ledger.save_budget("owner", "farm", budget(capacity="1100"), request_id="capacity", supersedes_id=pool["id"], reason="Updated source")
    assert updated["data"]["period"]["timezone"] == "Asia/Taipei"
    assert ledger.ledger("owner", "farm")["budgets"][0]["timezone_changed"]
    ledger.release_commitment("owner", "farm", original["id"], "Operator", "Cancel after farm changes", request_id="release")


def test_cross_owner_and_farm_isolation_export_erasure(setup):
    service, farms = setup
    ledger, pool, saved = prepared(service)
    seed(farms, "other", "other")
    seed(farms, "owner", "same")
    for owner, farm in (("other", "farm"), ("owner", "farmother"), ("owner", "farmsame")):
        with pytest.raises(ValueError):
            ledger.commit_plan(owner, farm, saved["id"], dict(water=pool["id"]), "Operator", False, request_id="bad")
    committed = commit(ledger, pool, saved)
    with pytest.raises(ValueError):
        ledger.release_commitment("owner", "farmsame", committed["id"], "Operator", "Wrong farm", request_id="bad")
    exported = farms.export("owner")
    assert exported["resource_budgets"] == [pool] and exported["resource_commitments"] == [committed]
    other = farms.export("other")
    farms.purge("owner")
    assert all(not rows for rows in farms.export("owner").values())
    assert farms.export("other") == other


def test_simultaneous_writers_cannot_overbook_one_budget(setup):
    service, _ = setup
    ledger, pool, first = prepared(service)
    second = service.save("owner", "farm", plan("600", "hydro"), request_id="second")
    connections = [_Db(service.db.path), _Db(service.db.path)]
    barrier, results = Barrier(2), []

    def reserve(index, saved):
        barrier.wait(timeout=5)
        try:
            results.append(commit(ResourceLedger(connections[index]), pool, saved, f"race-{index}"))
        except ValueError as exc:
            results.append(str(exc))

    workers = [Thread(target=reserve, args=(index, saved)) for index, saved in enumerate((first, second))]
    try:
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=10)
        assert not any(worker.is_alive() for worker in workers)
        assert sum(isinstance(r, dict) for r in results) == 1
        assert any(isinstance(r, str) and "insufficient" in r for r in results)
        assert ledger.ledger("owner", "farm")["budgets"][0]["committed"] == "600"
    finally:
        for db in connections:
            db._conn.close()


def test_v11_upgrade_rolls_back_and_preserves_restricted_backup(setup, monkeypatch):
    import agronaut_agent.resource_ledger as module

    service, farms = setup
    service.save("owner", "farm", plan(), request_id="plan")
    before, path = farms.export("owner"), service.db.path
    service.db.execute("DROP TABLE farm_resource_commitments")
    service.db.execute("DROP TABLE farm_resource_budgets")
    service.db.execute("PRAGMA user_version=11")
    service.db._conn.close()
    real = module.migrate_resource_ledger

    def fail(conn):
        real(conn)
        raise RuntimeError("Synthetic migration failure")

    monkeypatch.setattr(module, "migrate_resource_ledger", fail)
    with pytest.raises(RuntimeError):
        _Db(path)
    raw = sqlite3.connect(path)
    assert raw.execute("PRAGMA user_version").fetchone()[0] == 11
    assert raw.execute("SELECT name FROM sqlite_master WHERE name LIKE 'farm_resource_%' AND name != 'farm_resource_plans'").fetchall() == []
    raw.close()
    backup = next(path.parent.glob(f"*.pre-v{SCHEMA_VERSION}-*.bak"))
    assert backup.stat().st_mode & 0o777 == 0o600
    monkeypatch.setattr(module, "migrate_resource_ledger", real)
    reopened = _Db(path)
    assert FarmStore(reopened).export("owner") == before
    reopened._conn.close()


def test_stdlib_cli_and_discovery_use_owned_farm(setup, tmp_path):
    service, _ = setup
    owner = ConversationStore(service.db).get_or_create_user("cli", "tester")
    service.db.execute("UPDATE farms SET owner_id=? WHERE id='farm'", (owner,))
    schema = execute_farm_resources(service.db, owner, "farm", "describe", {})
    assert "commit_plan" in schema["operations"] and "save_budget" in schema["operations"]
    path = tmp_path / "budget.json"
    period = dict(start="2099-01-01", end="2099-01-01", timezone="Asia/Taipei")
    path.write_text(json.dumps(dict(data=budget(period=period), request_id="cli")))
    command = [sys.executable, "-S", "-m", "agronaut_agent.cli", "resources", "--db", str(service.db.path), "--user", "tester", "--farm", "farm"]
    result = subprocess.run([*command, "save_budget", "--input", str(path)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout+result.stderr
    saved = json.loads(result.stdout)
    result = subprocess.run([*command, "ledger"], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0 and json.loads(result.stdout)["budgets"][0]["id"] == saved["id"]
    scenario = service.save(owner, "farm", plan(period=period), request_id="cli-plan")
    path.write_text(json.dumps(dict(plan_id=scenario["id"], bindings=dict(water=saved["id"]), source="Synthetic explicit decision",
                                    accept_partial=False, request_id="cli-commit")))
    result = subprocess.run([*command, "commit_plan", "--input", str(path)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout+result.stderr
    committed = json.loads(result.stdout)
    assert committed["amounts"] == {saved["root_id"]: "600"}
    path.write_text(json.dumps(dict(commitment_id=committed["id"], source="Synthetic operator", reason="Cancelled", request_id="cli-release")))
    result = subprocess.run([*command, "release_commitment", "--input", str(path)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0 and json.loads(result.stdout)["status"] == "released"


def test_failed_timing_or_schedule_cannot_be_committed_but_checked_plan_can(setup):
    from agro_model.tests.test_resource_schedule import request
    from agro_model.tests.test_resource_timing import scenario

    service, _ = setup
    service.db.execute("UPDATE farms SET timezone='UTC' WHERE id='farm'")
    inputs, timing = scenario()
    parent = service.save("owner", "farm", inputs, request_id="parent")
    ledger, bindings = ResourceLedger(service.db), {}
    for pool in inputs["resources"]:
        saved = ledger.save_budget("owner", "farm", budget(resource_key=pool["id"], kind=pool["kind"], unit=pool["unit"],
            capacity=pool["available"], period=inputs["period"]), request_id=pool["id"])
        bindings[pool["id"]] = saved["id"]
    search = request(timing)
    search["search_limit"] = 1
    limited = service.save_schedule("owner", "farm", parent["id"], search, request_id="limited")
    with pytest.raises(ValueError, match="no feasible saved schedule"):
        ledger.commit_plan("owner", "farm", limited["id"], bindings, "Operator", False, request_id="limited-commit")
    good = service.save_timed("owner", "farm", parent["id"], timing, request_id="good")
    timing["work"][1].update(start=timing["work"][0]["start"], end=timing["work"][0]["end"])
    bad = service.save_timed("owner", "farm", parent["id"], timing, request_id="bad")
    with pytest.raises(ValueError, match="timing checks failed"):
        ledger.commit_plan("owner", "farm", bad["id"], bindings, "Operator", False, request_id="bad-commit")
    saved = ledger.commit_plan("owner", "farm", good["id"], bindings, "Operator", False, request_id="good-commit")
    assert saved["plan_snapshot"]["timing_result"]["declared_checks_pass"]
    assert all(row["remaining"] == "0" for row in ledger.ledger("owner", "farm")["budgets"])


def test_micro_units_are_exact_and_empty_allocations_cannot_be_committed(setup):
    service, _ = setup
    ledger = ResourceLedger(service.db)
    pool = ledger.save_budget("owner", "farm", budget(capacity="0.300004"), request_id="pool")
    a = service.save("owner", "farm", plan("0.100001"), request_id="a")
    b = service.save("owner", "farm", plan("0.200002", "hydro"), request_id="b")
    commit(ledger, pool, a, "a")
    commit(ledger, pool, b, "b")
    assert ledger.ledger("owner", "farm")["budgets"][0]["remaining"] == "0.000001"
    empty = plan("1")
    empty["resources"][0]["available"] = "0"
    saved = service.save("owner", "farm", empty, request_id="zero")
    with pytest.raises(ValueError, match="no resource quantities"):
        ledger.commit_plan("owner", "farm", saved["id"], dict(water=pool["id"]), "Operator", True, request_id="empty")


def test_named_resource_windows_do_not_overlap_after_timezone_change(setup):
    service, _ = setup
    ledger = ResourceLedger(service.db)
    ledger.save_budget("owner", "farm", budget(), request_id="first")
    service.db.execute("UPDATE farms SET timezone='UTC' WHERE id='farm'")
    with pytest.raises(ValueError, match="overlapping"):
        ledger.save_budget("owner", "farm", budget(period=dict(start="2026-10-25", end="2026-10-26", timezone="UTC")), request_id="overlap")
    ledger.save_budget("owner", "farm", budget(period=dict(start="2026-10-26", end="2026-10-27", timezone="UTC")), request_id="next")
    assert len(ledger.ledger("owner", "farm")["budgets"]) == 2
