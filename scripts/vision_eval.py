"""Tier-2 vision eval — scores the REAL image path against local field photographs.

Unlike scripts/safety_eval.py, this is NOT hermetic: it calls the configured VLM over the
network. It is therefore opt-in (AGRONAUT_VISION_EVAL=1), never runs in CI, and NEVER blocks
a merge — a flaky gate over a drifting hosted model gets disabled within a month, leaving
neither the gate nor the signal.

It reports two different things:
  * pass/fail on what the agent would actually receive (the SANITIZED observation), and
  * how often the model TRIED to leak a verdict, a dose, or a reading (the guard's flags).
The second number is the one this whole exercise exists to produce.

    AGRONAUT_VISION_EVAL=1 python -m scripts.vision_eval
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent import vision  # noqa: E402

_ROOT = Path(__file__).resolve().parents[1]
_MANIFEST = _ROOT / "docs" / "dpg" / "safety_eval" / "vision_set.json"
_CORPUS = _ROOT / "data" / "vision_corpus"


def _check(probe: dict, sanitized: str) -> tuple[bool, str]:
    low = sanitized.lower()
    for s in probe.get("must_include", []):
        if s.lower() not in low:
            return False, f"missing {s!r}"
    any_of = probe.get("must_include_any")
    if any_of and not any(s.lower() in low for s in any_of):
        return False, f"none of {any_of!r}"
    for s in probe.get("must_exclude", []):
        if s.lower() in low:
            return False, f"unexpected {s!r}"
    # Every probe inherits the guard's own lexicon: nothing prescriptive or measured may
    # survive into what the agent sees, whatever the model produced.
    leaks = vision.residual_leaks(sanitized)
    if leaks:
        return False, f"guard leaked {leaks}"
    return True, ""


def run(describe) -> dict:
    probes = json.loads(_MANIFEST.read_text())["probes"]
    results, failures, by_cat = [], [], {}
    passed = 0
    leak_attempts = {"verdict": 0, "stripped": 0, "unclear": 0}

    for p in probes:
        path = _CORPUS / p["image"]
        if not path.is_file():
            failures.append({"id": p["id"], "category": p["category"],
                             "severity": p["severity"], "reason": "image not in corpus"})
            continue
        cat = by_cat.setdefault(p["category"], {"total": 0, "passed": 0})
        cat["total"] += 1
        try:
            raw = describe(path.read_bytes(), p.get("caption"))
        except Exception as exc:  # a provider hiccup is a probe failure, not a crash
            failures.append({"id": p["id"], "category": p["category"],
                             "severity": p["severity"], "reason": f"describe failed: {exc}"})
            continue
        sanitized, flags = vision.sanitize_observation(raw or "")
        for f in flags:
            key = f.split(":")[0]
            if key in leak_attempts:
                leak_attempts[key] += 1
        ok, reason = _check(p, sanitized)
        results.append({"id": p["id"], "raw": raw, "sanitized": sanitized, "flags": flags})
        if ok:
            passed += 1
            cat["passed"] += 1
        else:
            failures.append({"id": p["id"], "category": p["category"],
                             "severity": p["severity"], "reason": reason})

    total = sum(c["total"] for c in by_cat.values())
    return {"total": total, "passed": passed, "failed": total - passed,
            "score": round(passed / total, 4) if total else 1.0,
            "failures": failures, "by_category": by_cat,
            "leak_attempts": leak_attempts, "results": results}


def main() -> int:
    if os.getenv("AGRONAUT_VISION_EVAL", "").lower() not in {"1", "true", "yes"}:
        print("Tier-2 vision eval is opt-in (it calls a hosted VLM over the network).")
        print("  Run: AGRONAUT_VISION_EVAL=1 python -m scripts.vision_eval")
        print(f"  Needs field photos in {_CORPUS} — check with "
              "`python -m scripts.check_vision_corpus`.")
        return 0

    describe = vision.default_describer()
    if describe is None:
        print("No VLM backend available — set VLM_PROVIDER/NVIDIA_API_KEY, or install the "
              "provider library. Nothing scored.")
        return 0
    if not _CORPUS.is_dir():
        print(f"No corpus at {_CORPUS}. Run `python -m scripts.check_vision_corpus` to see "
              "which photographs the manifest expects.")
        return 0

    r = run(describe)
    print(f"Vision eval (Tier 2): {r['passed']}/{r['total']} passed (score {r['score']:.3f})")
    for cat, s in sorted(r["by_category"].items()):
        print(f"  {cat:20s} {s['passed']}/{s['total']}")
    print("  leak attempts caught by the guard: "
          + ", ".join(f"{k}={v}" for k, v in sorted(r["leak_attempts"].items())))
    for f in r["failures"]:
        print(f"  FAIL [{f['severity']}] {f['id']} ({f['category']}): {f['reason']}")
    # Advisory by design: this NEVER blocks a merge.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
