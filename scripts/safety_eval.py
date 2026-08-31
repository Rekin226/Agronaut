"""Advice-safety golden-set scorer — hermetic (no LLM, no network).

Scores the deterministic advice surface Agronaut relays: trust-gate refusals, sizing sanity,
the honesty layer (every design cites its coefficients and lists what it does NOT model), and the vision observation guard.
Combines curated probes (docs/dpg/safety_eval/golden_set.json) with a generated matrix over
every supported species x crop, so a regression in the engine or serializer shows up as a
failed probe. This is the Gates/GIZ AIEP-recommended golden-set check, adapted to a
deterministic core we can score in CI without a model server.

Run standalone for a scorecard:
    python -m scripts.safety_eval          # exits non-zero if any CRITICAL probe fails
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.vision import sanitize_observation  # noqa: E402
from agronaut_agent.tools import (  # noqa: E402
    optimize_fish_crop_ratio,
    size_aquaponics_system,
    size_hydroponic_system_tool,
)
from aqua_model.crops import CROPS  # noqa: E402
from aqua_model.species import SPECIES  # noqa: E402

_GOLDEN = Path(__file__).resolve().parents[1] / "docs" / "dpg" / "safety_eval" / "golden_set.json"


def _invoke(tool: str, args: dict) -> str:
    if tool == "size_aquaponics":
        return size_aquaponics_system.invoke({
            "fish_species": args["fish"], "crop": args["crop"],
            "grow_area_m2": args["area"], "temperature_c": args["temp"],
            "water_budget_lpd": args["water"]})
    if tool == "size_hydroponics":
        return size_hydroponic_system_tool.invoke({
            "crop": args["crop"], "grow_area_m2": args["area"],
            "temperature_c": args["temp"], "water_budget_lpd": args["water"]})
    if tool == "optimize":
        return optimize_fish_crop_ratio.invoke({
            "grow_area_m2": args["area"], "temperature_c": args["temp"],
            "water_budget_lpd": args["water"], "objective": args.get("objective", "food")})
    if tool == "visual_triage":
        # Pure too: a regex feature-extractor plus a fixed table. No model, no network — so
        # the diagnosis half of the vision path is scored here alongside the sizing half.
        from agent.observation_features import extract_observation_features
        from aqua_model.triage import format_triage, triage_symptoms
        return format_triage(triage_symptoms(extract_observation_features(args["description"])))
    if tool == "vision_guard":
        # Pure function, no network — which is exactly why the vision guard can be scored
        # here without breaking this module's hermetic charter.
        cleaned, flags = sanitize_observation(args["observation"])
        return cleaned + "\n[flags] " + " ".join(sorted(flags))
    raise ValueError(f"unknown probe tool {tool!r}")


def _check(probe: dict) -> tuple[bool, str]:
    out = _invoke(probe["tool"], probe["args"])
    for s in probe.get("must_include", []):
        if s not in out:
            return False, f"missing {s!r}"
    for s in probe.get("must_exclude", []):
        if s in out:
            return False, f"unexpected {s!r}"
    return True, ""


def _curated_probes() -> list[dict]:
    return json.loads(_GOLDEN.read_text())["probes"]


def _generated_probes() -> list[dict]:
    """One honesty+sizing probe per (species, crop): with a generous water budget every design
    must be FEASIBLE, cite its coefficients, and list what it does NOT model. Plus one
    hydroponic honesty probe per crop."""
    probes = []
    crops = sorted(CROPS)
    for fish in sorted(SPECIES):
        for crop in crops:
            probes.append({
                "id": f"gen-aqua-{fish}-{crop}", "category": "honesty", "severity": "critical",
                "tool": "size_aquaponics",
                "args": {"fish": fish, "crop": crop, "area": 10, "temp": 26, "water": 1_000_000},
                "must_include": ["FEASIBLE", "source:", "NOT modeled"]})
    for crop in crops:
        probes.append({
            "id": f"gen-hydro-{crop}", "category": "honesty", "severity": "critical",
            "tool": "size_hydroponics",
            "args": {"crop": crop, "area": 10, "temp": 22, "water": 1_000_000},
            "must_include": ["FEASIBLE hydroponic", "source:", "NOT modeled"]})
    return probes


def run() -> dict:
    probes = _curated_probes() + _generated_probes()
    failures, by_cat = [], {}
    passed = 0
    for p in probes:
        cat = by_cat.setdefault(p["category"], {"total": 0, "passed": 0})
        cat["total"] += 1
        ok, reason = _check(p)
        if ok:
            passed += 1
            cat["passed"] += 1
        else:
            failures.append({"id": p["id"], "category": p["category"],
                             "severity": p["severity"], "reason": reason})
    total = len(probes)
    return {
        "total": total, "passed": passed, "failed": total - passed,
        "score": round(passed / total, 4) if total else 1.0,
        "failures": failures, "by_category": by_cat,
    }


def main() -> int:
    r = run()
    print(f"Advice-safety golden set: {r['passed']}/{r['total']} passed "
          f"(score {r['score']:.3f})")
    for cat, s in sorted(r["by_category"].items()):
        print(f"  {cat:12s} {s['passed']}/{s['total']}")
    critical = [f for f in r["failures"] if f["severity"] == "critical"]
    for f in r["failures"]:
        print(f"  FAIL [{f['severity']}] {f['id']} ({f['category']}): {f['reason']}")
    return 1 if critical else 0


if __name__ == "__main__":
    raise SystemExit(main())
