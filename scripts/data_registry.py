"""Registry-driven dataset survey, fetch, and vetting gate (#87, proposed work items 1+2).

The survey in #87 found that open aquaculture data is abundant in ML-ready form and scarce in
physically-usable form — and that verdicts get lost unless they are recorded next to the data.
This script makes the registry (`data/dataset_registry.json`) executable:

    python scripts/data_registry.py list                  # the survey, with verdicts
    python scripts/data_registry.py fetch <id>            # download an entry into data/raw/<id>/
    python scripts/data_registry.py vet <id | csv-path>   # run the acceptance gate on real files

The vetting gate encodes what the survey learned the hard way:
  - PHYSICAL UNITS or nothing: a channel squashed into [0,1], or a negative dissolved-oxygen
    median, means the inverse transform is gone and mass balance cannot use it (Mendeley case).
  - DEAD SENSORS LIE PLAUSIBLY: -127 is a DS18B20 with no probe attached, not a cold day.
    Sentinel and saturation checks come from `aqua_model.sensor_qc` (#86).
  - THE PAIRING IS THE PRIZE: `feed_g` in the same row as ammonia/nitrite/nitrate is what no
    public dataset provides; the gate reports which schema channels a file actually covers.

Adding a dataset is a data change (edit the JSON), not a code change.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from aqua_model import sensor_qc  # noqa: E402

REGISTRY = REPO_ROOT / "data" / "dataset_registry.json"
RAW_DIR = REPO_ROOT / "data" / "raw"

# Columns that map onto the logging-schema vocabulary, however the source spells them.
_CHANNEL_ALIASES = {
    "water_temp_c": ["temp", "temperature"],
    "ph": ["ph"],
    "dissolved_oxygen_mg_l": ["oxygen", "dissolved", "disolved", "do(", "oxg"],
    "ammonia_mg_l": ["ammonia", "nh3", "nh4"],
    "nitrite_mg_l": ["nitrite", "no2"],
    "nitrate_mg_l": ["nitrate", "no3"],
    "feed_g": ["feed", "food"],
    "turbidity_ntu": ["turbid"],
    "tds_ppm": ["tds"],
}


def load_registry() -> dict:
    return json.loads(REGISTRY.read_text())


def cmd_list() -> int:
    reg = load_registry()
    width = max(len(d["id"]) for d in reg["datasets"])
    for d in reg["datasets"]:
        print(f"{d['verdict']:<7} {d['id']:<{width}}  {d['role']}")
        print(f"{'':7} {'':{width}}  {d['verdict_reason']}")
    print(f"\n{len(reg['datasets'])} datasets surveyed. "
          "ACCEPT = physical units + open licence + unattended fetch.")
    return 0


def cmd_fetch(dataset_id: str) -> int:
    reg = load_registry()
    entry = next((d for d in reg["datasets"] if d["id"] == dataset_id), None)
    if entry is None:
        print(f"unknown dataset id: {dataset_id!r} — see `list`", file=sys.stderr)
        return 2
    method = entry["fetch"]["method"]
    ref = entry["fetch"]["ref"]
    if method == "script":
        print(f"{dataset_id} is fetched by its own script:\n    python {ref}")
        return 0
    if method == "kaggle":
        print(f"{dataset_id} needs a (free) Kaggle API key:\n"
              f"    kaggle datasets download -d {ref} -p data/raw/{dataset_id}/")
        return 0
    if method == "manual":
        print(f"{dataset_id} has no machine-fetchable deposit. Source:\n    {ref}\n"
              f"    note: {entry['fetch'].get('note', '')}")
        return 0
    # direct URL
    dest_dir = RAW_DIR / dataset_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = entry["fetch"].get("filename") or ref.rstrip("/").split("/")[-1].split("?")[0] or "download"
    dest = dest_dir / name
    if dest.exists() and dest.stat().st_size > 0:
        print(f"exists: {dest} ({dest.stat().st_size:,} B)")
        return 0
    note = entry["fetch"].get("note", "")
    if note:
        print(f"note: {note}")
    print(f"fetch {ref}\n  ->  {dest}")
    urllib.request.urlretrieve(ref, dest)
    print(f"      {dest.stat().st_size:,} B")
    return 0


def _match_channel(col: str) -> str | None:
    low = col.lower()
    for channel, needles in _CHANNEL_ALIASES.items():
        if any(n in low for n in needles):
            return channel
    return None


def vet_csv(path: Path) -> dict:
    """Run the acceptance gate on one CSV. Returns the verdict record."""
    import pandas as pd

    df = pd.read_csv(path, low_memory=False)
    findings: list[str] = []
    fatal: list[str] = []
    channels: dict[str, str] = {}

    for col in df.columns:
        s = pd.to_numeric(df[col], errors="coerce").dropna()
        if len(s) < 10 or s.nunique() <= 2:
            continue
        channel = _match_channel(col)
        if channel:
            channels[channel] = col

        # normalised-units test: a real physical channel does not live inside [0, 1.1].
        # EXCEPT the ones that physically do: ammonia and nitrite in a healthy pond sit
        # under 1 mg/L — a sub-unit range there is good husbandry, not lost units.
        if channel not in ("ammonia_mg_l", "nitrite_mg_l"):
            if -0.11 <= s.min() and s.max() <= 1.1 and s.std() > 0.01:
                fatal.append(f"{col}: bounded to [{s.min():.2f}, {s.max():.2f}] — "
                             "normalised, physical units are gone")
        if channel == "dissolved_oxygen_mg_l" and s.median() < 0:
            fatal.append(f"{col}: negative median DO ({s.median():.2f}) — not physical")

        frac, sentinel_name = sensor_qc.sentinel_fraction(s.tolist())
        if frac > 0.02:
            findings.append(f"{col}: {frac:.0%} dead-sensor sentinel ({sentinel_name})")
        pinned = (s == s.max()).mean()
        if pinned > 0.25:
            findings.append(f"{col}: {pinned:.0%} of readings pinned at max "
                            f"({s.max():g}) — sensor saturation")

    n_species = ["ammonia_mg_l", "nitrite_mg_l", "nitrate_mg_l"]
    has_feed = "feed_g" in channels
    has_n = [c for c in n_species if c in channels]
    if has_feed and has_n:
        findings.append("PAIRS FEED WITH NITROGEN — the record no surveyed dataset provides; "
                        "adopt this")
    verdict = "REJECT" if fatal else ("REVIEW" if findings else "ACCEPT")
    return {
        "file": str(path),
        "rows": len(df),
        "channels_recognised": channels,
        "missing_decisive": [c for c in ["feed_g", "nitrite_mg_l"] if c not in channels],
        "fatal": fatal,
        "findings": findings,
        "verdict": verdict,
    }


def cmd_vet(target: str) -> int:
    p = Path(target)
    files = [p] if p.suffix == ".csv" and p.exists() else sorted((RAW_DIR / target).glob("*.csv"))
    if not files:
        print(f"nothing to vet: no CSVs at {target!r} (fetch first?)", file=sys.stderr)
        return 2
    worst = "ACCEPT"
    for f in files:
        r = vet_csv(f)
        print(f"\n{r['verdict']}  {f.name}  ({r['rows']:,} rows)")
        for k, col in r["channels_recognised"].items():
            print(f"    {k:<24} <- {col}")
        for line in r["fatal"]:
            print(f"    FATAL  {line}")
        for line in r["findings"]:
            print(f"    check  {line}")
        if r["missing_decisive"]:
            print(f"    missing the decisive channels: {', '.join(r['missing_decisive'])}")
        order = {"ACCEPT": 0, "REVIEW": 1, "REJECT": 2}
        if order[r["verdict"]] > order[worst]:
            worst = r["verdict"]
    print(f"\noverall: {worst}")
    return 0 if worst != "REJECT" else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    f = sub.add_parser("fetch")
    f.add_argument("id")
    v = sub.add_parser("vet")
    v.add_argument("target", help="dataset id (vets data/raw/<id>/*.csv) or a CSV path")
    args = ap.parse_args(argv)
    if args.cmd == "list":
        return cmd_list()
    if args.cmd == "fetch":
        return cmd_fetch(args.id)
    return cmd_vet(args.target)


if __name__ == "__main__":
    raise SystemExit(main())
