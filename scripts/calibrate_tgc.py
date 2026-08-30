"""Fit the African-catfish TGC to the 12-pond Kaggle dataset — the twin's first growth
coefficient calibrated on real fish rather than literature.

Data: Ogbuokiri/Udanor et al., "Sensor Based Aquaponics Fish Pond Datasets" (Kaggle DOI
10.34740/kaggle/dsv/2681778; Data in Brief 43:108400, CC BY). Eleven ponds of Clarias
gariepinus (~1000 fingerlings each, Nigeria, Jun-Oct 2021): continuous water temperature
plus fortnightly manual length/weight sampling — the growth-with-environment pairing the
registry survey (#87) identified as this dataset's unique value.

Method: within each pond, the fortnightly mean weights form W(t); the TGC model says
W^(1/3) is linear in accumulated degree-days (Cho & Bureau 1998). We build degree-days
from the pond's own daily median temperature (sentinels and impossible readings dropped),
locate the days the recorded weight actually changes (manual sampling events), and fit
the slope of W^(1/3) against cumulative T·dt by least squares. TGC (x1000 convention)
is that slope x 1000.

Honesty notes, also written into the artifact:
  - feeding is NOT recorded, so this is realized growth under the farm's own (unknown)
    ration — a floor on potential growth, exactly what a seed should be;
  - ponds whose weight series is too short or non-increasing are reported and excluded,
    not silently averaged in.

Writes data/tgc_calibration.json and prints the comparison against the current seed.
"""

from __future__ import annotations

import glob
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

DATA_DIR = REPO_ROOT / "data" / "raw" / "kaggle_catfish_12ponds"
ARTIFACT = REPO_ROOT / "data" / "tgc_calibration.json"

TEMP_PLAUSIBLE = (15.0, 40.0)     # tropical pond water; outside this is a sensor fault
MIN_EVENTS = 3                    # fewer sampling points cannot support a slope
MIN_SPAN_G = 5.0                  # a weight span smaller than this is noise, not growth


def _col(df: pd.DataFrame, *needles: str) -> str | None:
    for c in df.columns:
        low = c.lower().replace("_", "").replace(" ", "")
        if any(n in low for n in needles):
            return c
    return None


def fit_pond(path: Path) -> dict:
    df = pd.read_csv(path, low_memory=False)
    tcol = _col(df, "temperature")
    wcol = _col(df, "weight")
    dcol = _col(df, "createdat", "date", "time")
    out: dict = {"pond": path.name, "usable": False}
    if not (tcol and wcol and dcol):
        out["reason"] = f"missing columns (temp={tcol}, weight={wcol}, date={dcol})"
        return out

    # Timestamps carry a trailing timezone word ("CET") in some ponds; strip it.
    ts = pd.to_datetime(df[dcol].astype(str).str.replace(r"\s+[A-Z]{2,4}$", "", regex=True),
                        errors="coerce")
    temp = pd.to_numeric(df[tcol], errors="coerce")
    weight = pd.to_numeric(df[wcol], errors="coerce")
    day = ts.dt.floor("D")

    daily = pd.DataFrame({"day": day, "temp": temp, "weight": weight}).dropna(subset=["day"])
    t_ok = daily["temp"].between(*TEMP_PLAUSIBLE)
    daily_t = daily[t_ok].groupby("day")["temp"].median()
    daily_w = daily.groupby("day")["weight"].median().dropna()
    if daily_t.empty or daily_w.empty:
        out["reason"] = "no plausible temperature or weight days"
        return out

    # Sampling events: the days the recorded weight changes (manual fortnightly updates).
    w = daily_w[daily_w > 0.05].round(2)
    events = w[w.ne(w.shift())]
    # Merge immediate jitter: keep the first day of each distinct value run.
    if len(events) < MIN_EVENTS:
        out["reason"] = f"only {len(events)} weight sampling events"
        return out
    if events.iloc[-1] - events.iloc[0] < MIN_SPAN_G:
        out["reason"] = (f"weight span {events.iloc[0]:.2f}->{events.iloc[-1]:.2f} g "
                         "too small to fit growth")
        return out

    # Degree-days on the pond's own daily median temperature, gaps filled by interpolation
    # (a missing sensor day is not a cold day).
    full_days = pd.date_range(daily_w.index.min(), daily_w.index.max(), freq="D")
    t_series = daily_t.reindex(full_days).interpolate(limit_direction="both")
    dd = t_series.cumsum()

    x = np.array([dd.loc[d] for d in events.index if d in dd.index])
    y = np.array([events.loc[d] for d in events.index if d in dd.index]) ** (1.0 / 3.0)
    if len(x) < MIN_EVENTS:
        out["reason"] = "sampling events fall outside the temperature record"
        return out

    slope, intercept = np.polyfit(x, y, 1)
    pred = slope * x + intercept
    ss_res = float(((y - pred) ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum())
    out.update({
        "usable": bool(slope > 0),
        "tgc_x1000": round(float(slope) * 1000.0, 3),
        "r2": round(1.0 - ss_res / ss_tot, 3) if ss_tot > 0 else None,
        "n_events": int(len(x)),
        "weight_g": [round(float(events.iloc[0]), 2), round(float(events.iloc[-1]), 2)],
        "days": int((events.index[-1] - events.index[0]).days),
        "temp_median_c": round(float(daily_t.median()), 1),
    })
    if slope <= 0:
        out["reason"] = "non-increasing weight series (mortality event or unit change?)"
    return out


def main() -> int:
    files = sorted(DATA_DIR.glob("IoT*.csv"))
    if not files:
        print(f"no pond CSVs under {DATA_DIR} — fetch the dataset first "
              "(scripts/data_registry.py fetch kaggle_catfish_12ponds)", file=sys.stderr)
        return 2
    results = [fit_pond(f) for f in files]
    usable = [r for r in results if r.get("usable")]
    tgcs = sorted(r["tgc_x1000"] for r in usable)

    for r in results:
        if r.get("usable"):
            print(f"  {r['pond']:16} TGC {r['tgc_x1000']:6.3f}  r²={r['r2']}  "
                  f"{r['weight_g'][0]:>7.2f}->{r['weight_g'][1]:<8.2f} g over {r['days']} d "
                  f"@ {r['temp_median_c']} C  ({r['n_events']} samplings)")
        else:
            print(f"  {r['pond']:16} EXCLUDED — {r.get('reason')}")

    if not tgcs:
        print("\nno usable ponds — nothing to calibrate")
        return 1
    med = float(np.median(tgcs))
    q1, q3 = float(np.percentile(tgcs, 25)), float(np.percentile(tgcs, 75))
    print(f"\n  {len(tgcs)} usable ponds: TGC median {med:.2f}, IQR {q1:.2f}-{q3:.2f}, "
          f"full range {tgcs[0]:.2f}-{tgcs[-1]:.2f}")

    from aqua_model.fishgrowth import TGC
    seed = TGC["clarias"]
    verdict = "WITHIN" if seed.low <= med <= seed.high else "OUTSIDE"
    print(f"  current clarias seed: {seed.value} [{seed.low}-{seed.high}] -> "
          f"fitted median is {verdict} the seed range")

    ARTIFACT.write_text(json.dumps({
        "dataset": "Ogbuokiri/Udanor et al., Kaggle DOI 10.34740/kaggle/dsv/2681778 "
                   "(Data in Brief 43:108400, CC BY); Clarias gariepinus, Nigeria 2021",
        "method": "per-pond least squares of W^(1/3) vs cumulative degree-days from the "
                  "pond's own daily median temperature; fortnightly manual weight samplings",
        "caveats": [
            "feeding unrecorded: realized growth under the farm's own ration, not potential",
            "aggregate pond means; no size structure",
            "temperature outside 15-40 C dropped as sensor faults; gaps interpolated",
        ],
        "generated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "ponds": results,
        "summary": {"n_usable": len(tgcs), "tgc_median": round(med, 3),
                    "tgc_iqr": [round(q1, 3), round(q3, 3)],
                    "tgc_range": [round(tgcs[0], 3), round(tgcs[-1], 3)]},
    }, indent=1))
    print(f"  wrote {ARTIFACT.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
