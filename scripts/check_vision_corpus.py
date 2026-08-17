"""Validate the local Tier-2 vision corpus against the committed manifest.

The corpus is the operator's own field photographs: private and uncommittable. So unlike
scripts/fetch_aquaponics_data.py — which downloads a public dataset — this script FETCHES
NOTHING. It reports which images the manifest expects, which are present, and which files
are sitting in the directory unreferenced.

    python -m scripts.check_vision_corpus
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_MANIFEST = _ROOT / "docs" / "dpg" / "safety_eval" / "vision_set.json"
_CORPUS = _ROOT / "data" / "vision_corpus"


def check() -> dict:
    probes = json.loads(_MANIFEST.read_text())["probes"]
    expected = {p["image"] for p in probes}
    present = {f.name for f in _CORPUS.iterdir() if f.is_file()} if _CORPUS.is_dir() else set()
    return {
        "expected": sorted(expected),
        "missing": sorted(expected - present),
        "unreferenced": sorted(present - expected),
        "corpus_dir": str(_CORPUS),
    }


def main() -> int:
    r = check()
    print(f"Vision corpus: {_CORPUS}")
    print(f"  expected {len(r['expected'])}, missing {len(r['missing'])}, "
          f"unreferenced {len(r['unreferenced'])}")
    for name in r["missing"]:
        print(f"  MISSING      {name}")
    for name in r["unreferenced"]:
        print(f"  UNREFERENCED {name}")
    if r["missing"]:
        print("\nAdd the missing photographs to the corpus directory, or remove their "
              "entries from docs/dpg/safety_eval/vision_set.json.")
    return 1 if r["missing"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
