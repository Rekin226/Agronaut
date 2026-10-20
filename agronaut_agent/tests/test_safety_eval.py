"""The advice-safety golden set runs hermetically (no LLM/network) and scores the
deterministic advice surface Agronaut relays: trust-gate refusals, sizing sanity, and the
honesty layer (cited sources + 'not modeled' on every design). A regression signal per
release, per the Gates/GIZ AIEP golden-set recommendation.
"""

import json
from types import SimpleNamespace

import pytest

from scripts import safety_eval


@pytest.fixture(scope="module")
def report():
    # These assertions inspect the same deterministic scorecard. Generate it once
    # rather than repeat every sizing/optimizer probe for each reporting check.
    return safety_eval.run()


def test_golden_set_has_at_least_100_probes(report):
    assert report["total"] >= 100


def test_all_critical_probes_pass_on_current_code(report):
    critical_failures = [f for f in report["failures"] if f["severity"] == "critical"]
    assert critical_failures == [], critical_failures


def test_report_is_scored_and_structured(report):
    assert set(report) >= {"total", "passed", "failed", "score", "failures", "by_category"}
    assert 0.0 <= report["score"] <= 1.0
    # trust-gate and honesty categories must be represented
    assert "trust_gate" in report["by_category"]
    assert "honesty" in report["by_category"]


def test_trust_gate_probes_actually_reject(report):
    # sanity: the eval would catch a broken trust gate (an unknown species must be refused)
    tg = report["by_category"]["trust_gate"]
    assert tg["total"] > 0 and tg["passed"] == tg["total"]


def test_field_gate_covers_supported_methods_and_explicit_refusals():
    probes = safety_eval._field_probes()
    assert len({p["id"] for p in probes}) == len(probes)
    assert {p["args"]["task"] for p in probes} >= {
        "crop_water", "root_zone_budget", "drip_design", "sprinkler_design", "surface_evaluation",
        "surface_advance", "surface_cutoff", "drip_lateral", "drip_zone", "drip_manifold", "sprinkler_lateral", "nutrient_budget", "field_guidance",
        "sprinkler_catch_test", "drip_emitter_test"}
    assert all(p["severity"] == "critical" and p["assertions"] for p in probes)
    for probe in probes:
        assert safety_eval._check(probe) == (True, ""), probe["id"]


@pytest.mark.parametrize("value", [None, 0, True, "null"])
def test_structured_null_requires_the_key_and_the_correct_type(monkeypatch, value):
    probe = dict(tool="field_calculation", args={}, assertions=[dict(path=["result", "amount"], equals=None)])
    monkeypatch.setattr(safety_eval, "_invoke", lambda *args: json.dumps({"result": {"amount": value}}))
    assert safety_eval._check(probe)[0] is (value is None)
    monkeypatch.setattr(safety_eval, "_invoke", lambda *args: '{"result": {}}')
    assert not safety_eval._check(probe)[0]


@pytest.mark.parametrize("identifier,path,value", [
    ("field-unknown-soil-is-not-zero", ["result", "days", 0, "requirement", "values", "gross_irrigation_l"], 0),
    ("field-failed-drip-has-no-schedule", ["result", "schedule"], [{"runtime_hours": 1}]),
    ("field-advance-is-not-cutoff", ["result", "cutoff_recommendation"], 65),
    ("field-lateral-is-not-irrigation-schedule", ["evidence_status"], "field_validated"),
    ("field-demand-is-not-feasibility", ["sources"], []),
    ("field-french-guide-remains-draft", ["result", "translation_status"], "reviewed"),
])
def test_field_gate_detects_intentionally_corrupted_tool_outputs(monkeypatch, identifier, path, value):
    probe = next(p for p in safety_eval._field_probes() if p["id"] == identifier)
    output = json.loads(safety_eval._invoke(probe["tool"], probe["args"]))
    parent = safety_eval._path(output, path[:-1]) if len(path) > 1 else output
    parent[path[-1]] = value
    monkeypatch.setattr(safety_eval, "_invoke", lambda *args: json.dumps(output))
    assert not safety_eval._check(probe)[0]


def test_field_eval_isolated_from_current_unit_without_erasing_callers_context():
    from agronaut_agent import runtime

    context = SimpleNamespace(production_system="aquaponics")
    token = runtime._unit.set(context)
    try:
        probe = next(p for p in safety_eval._field_probes() if p["id"] == "field-demand-is-not-feasibility")
        assert safety_eval._check(probe) == (True, "")
        assert runtime.get_unit_context() is context
    finally:
        runtime._unit.reset(token)


def test_field_fixture_edits_do_not_rewrite_example_files():
    before = {p: p.read_bytes() for p in safety_eval._EXAMPLES.glob("*.json")}
    safety_eval._field_probes()
    assert before == {p: p.read_bytes() for p in before}


def test_aquatic_probes_also_leave_the_callers_stores_and_unit_untouched():
    from agronaut_agent import runtime

    class NoStoreAccess:
        def __getattr__(self, name):
            raise AssertionError("evaluation touched a caller's store")

    current = (NoStoreAccess(), "caller")
    context = SimpleNamespace(production_system="soil")
    current_token = runtime._current.set(current)
    unit_token = runtime._unit.set(context)
    try:
        probe = next(p for p in safety_eval._generated_probes() if p["tool"] == "size_aquaponics")
        assert safety_eval._check(probe) == (True, "")
        assert runtime.get_current() is current
        assert runtime.get_unit_context() is context
    finally:
        runtime._current.reset(current_token)
        runtime._unit.reset(unit_token)


def test_critical_field_failure_controls_the_standalone_exit_status(monkeypatch):
    monkeypatch.setattr(safety_eval, "run", lambda: dict(total=1, passed=0, score=0,
        by_category={"field_boundary": dict(total=1, passed=0)},
        failures=[dict(id="withheld-cutoff", category="field_boundary", severity="critical", reason="unexpected advice")]))
    assert safety_eval.main() == 1
