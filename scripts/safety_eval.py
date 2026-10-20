"""Advice-safety golden-set scorer — hermetic (no LLM, no network).

Scores the deterministic advice surface Agronaut relays: trust-gate refusals, sizing sanity,
the honesty layer (every design cites its coefficients and lists what it does NOT model), and the vision observation guard.
Combines curated aquatic/vision and field-boundary probes with a generated matrix over
every supported species x crop, so a regression in the engine or serializer shows up as a
failed probe. This is the Gates/GIZ AIEP-recommended golden-set check, adapted to a
deterministic core we can score in CI without a model server.

Field probes call the shared calculation tool on synthetic fixtures. They check typed
unknowns, refusals, evidence labels and withheld advice, not LLM behavior or field validity.

Run standalone for a scorecard:
    python -m scripts.safety_eval          # exits non-zero if any CRITICAL probe fails
"""

from __future__ import annotations

import contextvars
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.vision import sanitize_observation  # noqa: E402
from agronaut_agent.tools import (  # noqa: E402
    calculate_agriculture,
    optimize_fish_crop_ratio,
    size_aquaponics_system,
    size_hydroponic_system_tool,
)
from aqua_model.crops import CROPS  # noqa: E402
from aqua_model.species import SPECIES  # noqa: E402

_GOLDEN = Path(__file__).resolve().parents[1] / "docs" / "dpg" / "safety_eval" / "golden_set.json"
_FIELD_GOLDEN = _GOLDEN.with_name("field_set.json")
_EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def _invoke(tool: str, args: dict) -> str:
    # The evaluator must not inherit any caller's stores, selected unit or trace,
    # including when the aquatic tools normally capture supplied design facts.
    return contextvars.Context().run(_invoke_isolated, tool, args)


def _invoke_isolated(tool: str, args: dict) -> str:
    if tool == "field_calculation":
        # Exercise the real tool serializer without inheriting a caller's active
        # unit or persistence context. No model, retrieval or farm writes occur.
        return calculate_agriculture.invoke(args)
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
    if probe.get("assertions"):
        try:
            structured = json.loads(out)
        except (ValueError, TypeError):
            return False, "response is not structured JSON"
        for assertion in probe["assertions"]:
            try:
                actual = _path(structured, assertion["path"])
            except (KeyError, IndexError, TypeError):
                return False, f"missing result path {assertion['path']!r}"
            if "equals" in assertion:
                expected = assertion["equals"]
                if type(actual) is not type(expected) or actual != expected:
                    return False, f"unexpected value at {assertion['path']!r}"
            elif "contains" in assertion:
                if not isinstance(actual, (str, list)) or assertion["contains"] not in actual:
                    return False, f"missing required item at {assertion['path']!r}"
            elif assertion.get("nonempty") is True:
                if not isinstance(actual, (str, list, dict)) or not actual:
                    return False, f"empty evidence at {assertion['path']!r}"
            else:
                raise ValueError("field assertions require equals, contains or nonempty")
    return True, ""


def _path(value, path):
    """A missing key must fail an assertion, never masquerade as a JSON null."""
    if not isinstance(path, list) or not path:
        raise ValueError("probe paths must be nonempty key/index arrays")
    for key in path:
        value = value[key]
    return value


def _field_probes():
    probes = json.loads(_FIELD_GOLDEN.read_text())["probes"]
    ready = []
    for probe in probes:
        if fixture := probe.get("fixture"):
            if Path(fixture).name != fixture or not fixture.endswith(".json"):
                raise ValueError("field probe fixtures must be JSON basenames inside examples")
            inputs = json.loads((_EXAMPLES/fixture).read_text())
        else:
            inputs = probe.get("inputs", {})
        for edit in probe.get("edits", []):
            path = edit["path"]
            parent = _path(inputs, path[:-1]) if len(path) > 1 else inputs
            # Resolve before replacing: a misspelled path must not append unused data.
            parent[path[-1]]
            if edit.get("delete") is True:
                del parent[path[-1]]
            else:
                parent[path[-1]] = edit["value"]
        assertions = list(probe["assertions"])
        if status := probe.get("evidence_status"):
            assertions += [{"path": ["evidence_status"], "equals": status},
                           {"path": ["capability", "status"], "equals": status},
                           {"path": ["sources"], "nonempty": True},
                           {"path": ["limitations"], "nonempty": True}]
        ready.append(dict(id=probe["id"], category="field_boundary", severity="critical", tool="field_calculation",
            args=dict(production_system=probe.get("production_system", "soil"), task=probe["task"], inputs=inputs),
            assertions=assertions))
    return ready


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
    probes = _curated_probes() + _generated_probes() + _field_probes()
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
