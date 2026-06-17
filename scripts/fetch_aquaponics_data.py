"""Fetch the open aquaponics IoT pond dataset into data/raw/ (idempotent, stdlib-only).

Dataset: Udanor et al. (2022), Data in Brief 43:108400, DOI 10.1016/j.dib.2022.108400.
License: CC BY 4.0. Four ponds of per-minute water-quality + fish-growth readings.

We pull the cleaned per-pond CSVs from a public GitHub mirror so the download needs no
Kaggle/Mendeley credentials. The raw CSVs are gitignored (~20 MB); the small derived
summary `data/empirical_envelope.json` is what gets committed.

Usage:
    python scripts/fetch_aquaponics_data.py            # download + rebuild the artifact
    python scripts/fetch_aquaponics_data.py --no-build # download only
"""

from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = REPO_ROOT / "data" / "raw"

_MIRROR = (
    "https://raw.githubusercontent.com/AcerPing/xLSTMTime_AquaponicsPond/"
    "AcerPing/datasets/aquaponics"
)
PONDS = {
    "IoTPond1.csv": f"{_MIRROR}/cleaned_IoTPond1.csv",
    "IoTPond2.csv": f"{_MIRROR}/cleaned_IoTPond2.csv",
    "IoTPond3.csv": f"{_MIRROR}/cleaned_IoTPond3.csv",
    "IoTPond4.csv": f"{_MIRROR}/cleaned_IoTPond4.csv",
}


def fetch() -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    for name, url in PONDS.items():
        dest = RAW_DIR / name
        if dest.exists() and dest.stat().st_size > 0:
            print(f"  exists  {name} ({dest.stat().st_size:,} B)")
            continue
        print(f"  fetch   {name} <- {url}")
        urllib.request.urlretrieve(url, dest)
        print(f"          {dest.stat().st_size:,} B")


def main() -> int:
    fetch()
    if "--no-build" not in sys.argv:
        from aqua_model import datasets  # imported here so download works without pandas

        art = datasets.write_artifact()
        print(
            f"\nWrote {datasets.ARTIFACT.relative_to(REPO_ROOT)}: "
            f"{art['n_readings']:,} readings across {art['n_ponds']} ponds "
            f"({art['date_span'][0]} -> {art['date_span'][1]})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
