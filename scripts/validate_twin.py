"""Validate the nitrogen twin's DYNAMICS against real ponds, with feed inferred from growth.

The gap this closes, and how honestly it can be closed
------------------------------------------------------
#87's finding stands: no public dataset pairs measured feeding with nitrogen chemistry.
But the 12-pond Kaggle dataset pairs fortnightly FISH WEIGHT with continuous nitrate — and
growth is the integral of feeding. So the forcing can be INFERRED from one observable
(weight, via literature FCR) and the model tested against a DIFFERENT observable (nitrate).
That breaks the circularity #87 warned about: inferring feed from the ammonia curve and
then "validating" ammonia proves nothing, but weight and nitrate are linked only through
the model being tested.

What absolute claim the data supports: none. The sensors are uncalibrated (nitrate in the
thousands of "mg/L", ammonia glitching to 4e11), pond volume is unpublished, and the
Population column (50/75) does not match the described 1,000 fingerlings. Every unknown
scale — volume, fish count, sensor gain — is therefore folded into ONE fitted constant per
pond, and the validation question becomes purely dynamical:

    Given growth-inferred feeding and measured temperature, does the twin's nitrate
    TRAJECTORY track the sensor's, out of sample in time, better than naive baselines?

Method, per pond:
  1. QC daily medians (temp 15-40 C; nitrate positive, sub-5000, >= _MIN_DAYS days).
  2. Weight sampling events -> W^(1/3) interpolated -> per-fish daily growth.
  3. Inferred feed shape = FCR(T) x dW/dt, FCR from Kasihmuddin et al. 2021 (dossier 1.4).
  4. Run twin.step daily with plant uptake ZERO (these ponds have no grow beds — a
     structural fact, not a fit) and feed scaled by S; fit S and the twin's declared-
     unfitted n_removal_per_day on the FIRST HALF only.
  5. Score the SECOND HALF: Pearson r and RMSE vs two nulls fitted on the same first
     half — a flat mean and a linear trend. The twin earns nothing unless it beats both.

Outputs data/twin_validation.json and data/inferred_feed_nitrogen.csv (the inferred paired
series, provenance in every row — created data, labelled as created).
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

from aqua_model.species import get_species          # noqa: E402
from aqua_model.twin import TwinState, mature_biofilter, step  # noqa: E402

DATA_DIR = REPO_ROOT / "data" / "raw" / "kaggle_catfish_12ponds"
ARTIFACT = REPO_ROOT / "data" / "twin_validation.json"
PAIRED_CSV = REPO_ROOT / "data" / "inferred_feed_nitrogen.csv"

TEMP_OK = (15.0, 40.0)
NITRATE_OK = (1.0, 5000.0)      # sensor units, not trusted mg/L — see module docstring
_MIN_DAYS = 40
_MIN_EVENTS = 4

# FCR vs temperature for C. gariepinus fingerlings (Kasihmuddin, Ghaffar & Das 2021,
# Animals 11:3497 — read in full for the dossier). Interpolated, clamped at the ends.
_FCR_T = np.array([26.0, 28.0, 30.0, 32.0])
_FCR_V = np.array([2.01, 1.79, 1.72, 1.64])


def _col(df, *needles):
    for c in df.columns:
        low = c.lower().replace("_", "").replace(" ", "")
        if any(n in low for n in needles):
            return c
    return None


def _daily(df):
    ts = pd.to_datetime(df[_col(df, "createdat", "date", "time")].astype(str)
                        .str.replace(r"\s+[A-Z]{2,4}$", "", regex=True), errors="coerce")
    day = ts.dt.floor("D")
    out = pd.DataFrame({
        "day": day,
        "temp": pd.to_numeric(df[_col(df, "temperature")], errors="coerce"),
        "no3": pd.to_numeric(df[_col(df, "nitrate")], errors="coerce"),
        "w": pd.to_numeric(df[_col(df, "weight")], errors="coerce"),
    }).dropna(subset=["day"])
    t = out[out["temp"].between(*TEMP_OK)].groupby("day")["temp"].median()
    n = out[out["no3"].between(*NITRATE_OK)].groupby("day")["no3"].median()
    w = out.groupby("day")["w"].median()
    return t, n, w


def _feed_shape(days: pd.DatetimeIndex, temp: pd.Series, w: pd.Series):
    """Per-fish daily feed (g), inferred: FCR(T) x daily growth from the weight events.

    Growth interpolates W^(1/3) between manual sampling events — the same cube-root
    domain the calibrated TGC fit showed to be linear in degree-days (r2 0.95-0.99)."""
    ww = w[w > 0.05].round(2)
    events = ww[ww.ne(ww.shift())]
    if len(events) < _MIN_EVENTS:
        return None
    x = events.index.view("int64")
    y = events.values ** (1.0 / 3.0)
    cbrt = np.interp(days.view("int64"), x, y)
    weights = cbrt ** 3
    growth = np.gradient(weights)                      # g/fish/day
    growth = np.clip(growth, 0.0, None)
    fcr = np.interp(temp.reindex(days).interpolate(limit_direction="both").values,
                    _FCR_T, _FCR_V)
    return fcr * growth, weights


# These are FISH PONDS, not aquaponic loops: the paper describes no grow beds, so plant
# uptake is structurally zero — a system-knowledge choice, not a fit. What IS fitted (on
# the training half only) is n_removal_per_day, the free parameter TwinParams documents as
# literature-typical and unfitted: it sets how fast nitrate equilibrates, and a near-static
# pond sits at the low end of it.
_K_GRID = (0.005, 0.01, 0.02, 0.03, 0.05, 0.08, 0.12)


def _run_twin(feed_scaled: np.ndarray, temp: np.ndarray, species,
              n_removal_per_day: float) -> np.ndarray:
    from aqua_model.twin import TwinParams

    params = TwinParams(n_removal_per_day=n_removal_per_day)
    aob, nob = mature_biofilter(species, float(feed_scaled[0] or 1.0))
    st = TwinState(volume_l=1000.0, aob_capacity_g_day=aob, nob_capacity_g_day=nob)
    out = np.empty(len(feed_scaled))
    for i, (f, t) in enumerate(zip(feed_scaled, temp)):
        r = step(st, species, feed_g_per_day=float(f), temperature_c=float(t),
                 plant_uptake_capacity_g_day=0.0, params=params)
        st = r.state
        out[i] = st.no3_mg_l
    return out


def validate_pond(path: Path, species) -> dict:
    temp, no3, w = _daily(pd.read_csv(path, low_memory=False))
    out: dict = {"pond": path.name, "usable": False}
    common = no3.index.intersection(temp.index)
    if len(no3) < _MIN_DAYS or len(common) < _MIN_DAYS:
        out["reason"] = f"only {len(common)} QC-clean days with nitrate + temperature"
        return out
    days = pd.date_range(common.min(), common.max(), freq="D")
    shaped = _feed_shape(days, temp, w)
    if shaped is None:
        out["reason"] = "too few weight sampling events to infer feeding"
        return out
    feed_per_fish, weights = shaped
    if feed_per_fish.max() <= 0:
        out["reason"] = "non-growing weight record — no feeding signal to infer"
        return out

    t_series = temp.reindex(days).interpolate(limit_direction="both").values
    obs = no3.reindex(days)
    half = len(days) // 2
    fit_mask = np.arange(len(days)) < half
    obs_fit, obs_val = obs[fit_mask].dropna(), obs[~fit_mask].dropna()
    if len(obs_fit) < _MIN_DAYS // 2 or len(obs_val) < _MIN_DAYS // 2:
        out["reason"] = "not enough nitrate days in one of the halves"
        return out

    # Two fitted quantities, BOTH on the training half only: S (one scalar absorbing fish
    # count, volume and sensor gain — nitrate is linear in feed at fixed dynamics) and
    # n_removal_per_day (the twin's own declared-unfitted rate constant). Holdout stays
    # untouched by the fit.
    fit_day_mask = np.isin(days[fit_mask], obs_fit.index)
    best = None
    for k in _K_GRID:
        unit = _run_twin(feed_per_fish, t_series, species, k)
        m = unit[fit_mask][fit_day_mask].mean()
        if m <= 0:
            continue
        S_k = float(obs_fit.mean() / m)
        train_rmse = float(np.sqrt(np.mean(
            (unit[fit_mask][fit_day_mask] * S_k - obs_fit.values) ** 2)))
        if best is None or train_rmse < best[0]:
            best = (train_rmse, k, S_k)
    if best is None:
        out["reason"] = "twin produced no nitrate at unit feed (degenerate record)"
        return out
    _, k_fit, S = best
    pred = _run_twin(feed_per_fish * S, t_series, species, k_fit)

    val_idx = np.isin(days, obs_val.index)
    p, o = pred[val_idx], obs_val.values
    # Nulls trained on the SAME first half: flat mean, and a linear trend extrapolated.
    flat = np.full_like(o, obs_fit.mean())
    day_num = np.arange(len(days), dtype=float)
    slope, icept = np.polyfit(day_num[fit_mask][np.isin(days[fit_mask], obs_fit.index)],
                              obs_fit.values, 1)
    trend = slope * day_num[val_idx] + icept

    def rmse(a):
        return float(np.sqrt(np.mean((a - o) ** 2)))

    r = float(np.corrcoef(p, o)[0, 1]) if len(o) > 2 and o.std() > 0 else float("nan")
    out.update({
        "usable": True,
        "days_total": int(len(days)),
        "days_validated": int(len(o)),
        "scale_fitted": round(S, 3),
        "n_removal_per_day_fitted": k_fit,
        "pearson_r_holdout": round(r, 3),
        "rmse_twin": round(rmse(p), 1),
        "rmse_null_flat": round(rmse(flat), 1),
        "rmse_null_trend": round(rmse(trend), 1),
        "beats_flat": bool(rmse(p) < rmse(flat)),
        "beats_trend": bool(rmse(p) < rmse(trend)),
        "weight_g": [round(float(weights[0]), 1), round(float(weights[-1]), 1)],
    })
    out["_paired"] = pd.DataFrame({
        "date": days.strftime("%Y-%m-%d"),
        "pond": path.name.replace(".csv", ""),
        "water_temp_c": np.round(t_series, 2),
        "feed_g_per_fish_INFERRED": np.round(feed_per_fish, 3),
        "fish_weight_g_interpolated": np.round(feed_per_fish * 0 + weights, 2),
        "nitrate_sensor_units": obs.reindex(days).round(1),
        "holdout_half": np.where(fit_mask, "fit", "validate"),
    })
    return out


def main() -> int:
    files = sorted(DATA_DIR.glob("IoT*.csv"))
    if not files:
        print("fetch the dataset first: scripts/data_registry.py fetch kaggle_catfish_12ponds",
              file=sys.stderr)
        return 2
    species = get_species("clarias")
    results = [validate_pond(f, species) for f in files]
    paired = [r.pop("_paired") for r in results if "_paired" in r]
    usable = [r for r in results if r.get("usable")]

    for r in results:
        if r.get("usable"):
            beats = ("both nulls" if r["beats_flat"] and r["beats_trend"]
                     else "flat only" if r["beats_flat"]
                     else "trend only" if r["beats_trend"] else "NEITHER null")
            print(f"  {r['pond']:16} r={r['pearson_r_holdout']:+.2f}  "
                  f"RMSE {r['rmse_twin']:>7.1f} vs flat {r['rmse_null_flat']:>7.1f} / "
                  f"trend {r['rmse_null_trend']:>7.1f}  -> beats {beats}  "
                  f"({r['days_validated']} holdout days)")
        else:
            print(f"  {r['pond']:16} EXCLUDED — {r.get('reason')}")

    if not usable:
        print("\nno pond survived QC — nothing validated")
        return 1
    n_beat = sum(1 for r in usable if r["beats_flat"] and r["beats_trend"])
    rs = [r["pearson_r_holdout"] for r in usable if not np.isnan(r["pearson_r_holdout"])]
    n_pos = sum(1 for r in rs if r > 0.2)
    print(f"\n  {len(usable)} ponds scored; twin beats BOTH nulls on {n_beat}; "
          f"holdout r median {np.median(rs):+.2f} (range {min(rs):+.2f}..{max(rs):+.2f})")

    # The verdict is computed from the numbers, not asserted — this run may fail, and a
    # validation script that cannot report failure is an advertisement.
    if n_beat >= max(2, len(usable) // 2):
        claim = ("SUPPORTED: with growth-inferred feeding and per-pond calibration of the "
                 "twin's declared free parameters on a training window, held-out nitrate "
                 "is predicted better than naive baselines on most ponds.")
    elif n_pos >= len(usable) // 2:
        claim = (f"MIXED: the twin tracks the DIRECTION of held-out nitrate on {n_pos} of "
                 f"{len(usable)} ponds (shape correlation), but does NOT beat a linear-"
                 "trend null on level — on uncalibrated pond sensors, level-prediction "
                 "skill is not demonstrated. The bottleneck is sensor quality, not the "
                 "absence of feed records: the create-your-own-data path "
                 "(docs/feed_response_protocol.md) is what moves this.")
    else:
        claim = ("NOT SUPPORTED on this data: the twin does not outperform naive "
                 "baselines. Do not cite this dataset as validating the twin.")
    print("  " + claim)
    print("  Never supported here: absolute mg/L accuracy (uncalibrated sensors; one "
          "fitted scale per pond).")

    ARTIFACT.write_text(json.dumps({
        "dataset": "Ogbuokiri/Udanor et al., Kaggle DOI 10.34740/kaggle/dsv/2681778",
        "method": "feed inferred from fortnightly weight via FCR(T) (Kasihmuddin 2021); "
                  "one scale per pond fitted on the first half; nitrate trajectory scored "
                  "on the held-out second half against flat and linear-trend nulls",
        "why_not_circular": "feed is inferred from WEIGHT; the model is scored on NITRATE "
                            "— the two are linked only through the model under test",
        "caveats": [
            "sensors are uncalibrated: nitrate treated as sensor units, not mg/L",
            "one fitted scalar per pond absorbs volume, fish count and sensor gain",
            "FCR is literature-typical, not measured for these ponds",
            "this validates dynamics/shape only — never absolute concentrations",
        ],
        "generated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "ponds": results,
        "summary": {
            "claim": claim,
            "n_scored": len(usable), "n_beats_both_nulls": n_beat,
            "n_positive_shape_correlation": n_pos,
            "holdout_r_median": round(float(np.median(rs)), 3) if rs else None,
            "holdout_r_range": [round(min(rs), 3), round(max(rs), 3)] if rs else None,
        },
    }, indent=1))
    all_paired = pd.concat(paired, ignore_index=True)
    header = ("# INFERRED paired feed+nitrogen series — created by scripts/validate_twin.py.\n"
              "# feed_g_per_fish_INFERRED is DERIVED from fortnightly weights via literature "
              "FCR(T), not measured.\n# nitrate_sensor_units are uncalibrated sensor readings, "
              "not mg/L. See data/twin_validation.json for method and caveats.\n")
    PAIRED_CSV.write_text(header + all_paired.to_csv(index=False))
    print(f"  wrote {ARTIFACT.relative_to(REPO_ROOT)} and {PAIRED_CSV.relative_to(REPO_ROOT)} "
          f"({len(all_paired)} paired rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
