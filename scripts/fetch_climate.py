"""Fetch a site's daily climate into data/climate/<name>.json for the production twin.

The twin simulates a SYSTEM AT A PLACE: the same design behaves differently in Ouagadougou
and Taichung, and the difference is climate. This script pulls a daily series (air temperature
and solar radiation) for any lat/lon so a simulation can be forced with the weather the site
actually has, rather than a single "design temperature".

Two providers, both keyless, both verified live (see data/dataset_registry.json):

  NASA POWER   daily, 1981..near-present, MERRA-2 based. The agroclimatology standard.
  Open-Meteo   ERA5 archive, hourly source aggregated here to daily. Cross-check for POWER.

Usage:
    python scripts/fetch_climate.py --lat 12.36 --lon -1.53 --name ouagadougou \
        --start 2024-01-01 --end 2024-12-31
    python scripts/fetch_climate.py --lat 24.15 --lon 120.68 --name taichung --provider open-meteo

Network lives here, OUTSIDE the trust zone; `aqua_model.climate` consumes the written file's
records as plain data and never fetches anything.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from datetime import UTC, date, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "data" / "climate"

_POWER_FILL = -999.0


def _get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.load(r)


def fetch_nasa_power(lat: float, lon: float, start: str, end: str) -> list[dict]:
    url = ("https://power.larc.nasa.gov/api/temporal/daily/point"
           "?parameters=T2M,T2M_MIN,T2M_MAX,ALLSKY_SFC_SW_DWN&community=AG"
           f"&longitude={lon}&latitude={lat}"
           f"&start={start.replace('-', '')}&end={end.replace('-', '')}&format=JSON")
    p = _get_json(url)["properties"]["parameter"]
    days = []
    for key in sorted(p["T2M"]):
        row = {
            "date": f"{key[:4]}-{key[4:6]}-{key[6:]}",
            "t_mean_c": p["T2M"][key],
            "t_min_c": p["T2M_MIN"][key],
            "t_max_c": p["T2M_MAX"][key],
            "solar_mj_m2": p["ALLSKY_SFC_SW_DWN"][key],
        }
        # POWER marks missing days (recent dates before assimilation) with -999.
        if any(v == _POWER_FILL for v in row.values() if isinstance(v, float)):
            continue
        days.append(row)
    return days


def fetch_open_meteo(lat: float, lon: float, start: str, end: str) -> list[dict]:
    url = ("https://archive-api.open-meteo.com/v1/archive"
           f"?latitude={lat}&longitude={lon}&start_date={start}&end_date={end}"
           "&daily=temperature_2m_mean,temperature_2m_min,temperature_2m_max,"
           "shortwave_radiation_sum&timezone=auto")
    d = _get_json(url)["daily"]
    days = []
    for i, day in enumerate(d["time"]):
        vals = (d["temperature_2m_mean"][i], d["temperature_2m_min"][i],
                d["temperature_2m_max"][i], d["shortwave_radiation_sum"][i])
        if any(v is None for v in vals):
            continue
        days.append({"date": day, "t_mean_c": vals[0], "t_min_c": vals[1],
                     "t_max_c": vals[2], "solar_mj_m2": vals[3]})
    return days


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--lat", type=float, required=True)
    ap.add_argument("--lon", type=float, required=True)
    ap.add_argument("--name", required=True, help="output slug: data/climate/<name>.json")
    ap.add_argument("--start", default=f"{date.today().year - 1}-01-01")
    ap.add_argument("--end", default=f"{date.today().year - 1}-12-31")
    ap.add_argument("--provider", choices=["nasa-power", "open-meteo"], default="nasa-power")
    args = ap.parse_args(argv)

    fetch = fetch_nasa_power if args.provider == "nasa-power" else fetch_open_meteo
    days = fetch(args.lat, args.lon, args.start, args.end)
    if not days:
        print("provider returned no usable days — check the date range", file=sys.stderr)
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    dest = OUT_DIR / f"{args.name}.json"
    dest.write_text(json.dumps({
        "site": {"name": args.name, "lat": args.lat, "lon": args.lon},
        "provider": args.provider,
        "fetched_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "units": {"t_*_c": "degC air at 2 m", "solar_mj_m2": "MJ/m2/day, all-sky shortwave"},
        "days": days,
    }, indent=1))
    t = [d["t_mean_c"] for d in days]
    print(f"wrote {dest.relative_to(REPO_ROOT)}: {len(days)} days, "
          f"T2M {min(t):.1f}..{max(t):.1f} °C")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
