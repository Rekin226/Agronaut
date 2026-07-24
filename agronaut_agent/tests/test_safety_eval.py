"""The advice-safety golden set runs hermetically (no LLM/network) and scores the
deterministic advice surface Agronaut relays: trust-gate refusals, sizing sanity, and the
honesty layer (cited sources + 'not modeled' on every design). A regression signal per
release, per the Gates/GIZ AIEP golden-set recommendation.
"""

from scripts import safety_eval


def test_golden_set_has_at_least_100_probes():
    report = safety_eval.run()
    assert report["total"] >= 100


def test_all_critical_probes_pass_on_current_code():
    report = safety_eval.run()
    critical_failures = [f for f in report["failures"] if f["severity"] == "critical"]
    assert critical_failures == [], critical_failures


def test_report_is_scored_and_structured():
    report = safety_eval.run()
    assert set(report) >= {"total", "passed", "failed", "score", "failures", "by_category"}
    assert 0.0 <= report["score"] <= 1.0
    # trust-gate and honesty categories must be represented
    assert "trust_gate" in report["by_category"]
    assert "honesty" in report["by_category"]


def test_trust_gate_probes_actually_reject():
    # sanity: the eval would catch a broken trust gate (an unknown species must be refused)
    report = safety_eval.run()
    tg = report["by_category"]["trust_gate"]
    assert tg["total"] > 0 and tg["passed"] == tg["total"]
