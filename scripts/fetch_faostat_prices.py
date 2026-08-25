"""Pull farm-gate producer prices from FAOSTAT for the revenue side of the business case.

FAOSTAT's Producer Prices (PP) domain is the only global, official, free series of what
farmers are actually PAID — the number a business case needs and that retail listings
cannot give. Two things about it are worth knowing before trusting it, both discovered by
running this:

  1. COVERAGE IS UNEVEN AND OFTEN STALE. Burkina Faso's vegetable series stops in 2007
     (tomatoes at 80 XOF/kg, and no lettuce at all), while neighbouring Mali publishes
     lettuce, tomato, okra and cabbage through 2024 in the SAME currency. A stale price is
     worse than no price, so this script reports the latest year per item and lets the
     caller decide; the price book records which country a proxy came from.
  2. TAIWAN IS ABSENT ENTIRELY (the Asia file carries only China mainland and Hong Kong
     SAR). Taiwan revenue has to come from its own agricultural market system.

FAOSTAT's REST API now demands an authorization header; the bulk ZIPs remain public, so
this pulls those. Network lives here, outside the trust zone.

Usage:
    python scripts/fetch_faostat_prices.py --area Mali --items lettuce tomato okra
    python scripts/fetch_faostat_prices.py --area "Burkina Faso" --region-file Africa --all
"""

from __future__ import annotations

import argparse
import io
import sys
import urllib.request
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CACHE = REPO_ROOT / "data" / "raw" / "faostat"
BULK = "https://bulks-faostat.fao.org/production/Prices_E_{region}.zip"
ELEMENT = "Producer Price (LCU/tonne)"


def load(region_file: str):
    import pandas as pd

    CACHE.mkdir(parents=True, exist_ok=True)
    dest = CACHE / f"Prices_E_{region_file}_NOFLAG.csv"
    if not dest.exists():
        url = BULK.format(region=region_file)
        print(f"fetch {url}")
        with urllib.request.urlopen(url, timeout=180) as r:
            payload = r.read()
        with zipfile.ZipFile(io.BytesIO(payload)) as z:
            name = f"Prices_E_{region_file}_NOFLAG.csv"
            dest.write_bytes(z.read(name))
        print(f"      {dest.stat().st_size:,} B -> {dest.relative_to(REPO_ROOT)}")
    return pd.read_csv(dest, encoding="latin-1", low_memory=False)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--area", required=True, help='country as FAOSTAT names it, e.g. "Mali"')
    ap.add_argument("--region-file", default="Africa",
                    help="bulk file to pull: Africa | Asia | Americas | Europe | Oceania")
    ap.add_argument("--items", nargs="*", default=[],
                    help="case-insensitive substrings, e.g. lettuce tomato")
    ap.add_argument("--all", action="store_true", help="list every priced item")
    args = ap.parse_args(argv)

    import pandas as pd

    df = load(args.region_file)
    ycols = [c for c in df.columns if c.startswith("Y") and c[1:].isdigit()]
    sub = df[(df["Area"].str.lower() == args.area.lower())
             & (df["Element"] == ELEMENT)
             & (df["Months"] == "Annual value")]
    if sub.empty:
        areas = sorted(df["Area"].unique())
        print(f"no producer prices for {args.area!r} in the {args.region_file} file.\n"
              f"Areas present: {', '.join(areas[:20])}...", file=sys.stderr)
        return 2

    rows = []
    for _, r in sub.iterrows():
        name = str(r["Item"])
        if args.items and not any(i.lower() in name.lower() for i in args.items):
            continue
        vals = {y[1:]: r[y] for y in ycols if pd.notna(r[y])}
        if not vals:
            continue
        last = max(vals)
        rows.append((name, last, vals[last], len(vals)))

    if not rows:
        print("no priced items matched", file=sys.stderr)
        return 1
    unit = sub["Unit"].iloc[0] if "Unit" in sub.columns else "LCU/tonne"
    print(f"\n{args.area} — {ELEMENT} ({unit})")
    stale = 0
    for name, last, value, n in sorted(rows, key=lambda t: -int(t[1])):
        flag = ""
        if int(last) < 2018:
            flag = "  <- STALE, do not price a 2026 business case on this"
            stale += 1
        print(f"  {name[:44]:46} {last}  {value:>13,.0f}  "
              f"({value / 1000:>8,.1f} per kg, n={n}){flag}")
    if stale:
        print(f"\n{stale} of {len(rows)} items are pre-2018. Consider a neighbouring country "
              "with the same currency as a labelled proxy.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
