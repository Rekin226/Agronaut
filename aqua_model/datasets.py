"""Ingest open aquaponics IoT datasets and validate the model's operating envelope
against real running systems — the first reality layer under the seed coefficients.

Source
------
Udanor, C.N. et al. (2022) "An internet of things labelled dataset for aquaponics fish
pond water quality monitoring system." Data in Brief 43:108400.
DOI: 10.1016/j.dib.2022.108400 — CC BY 4.0.
Public mirror (co-author Ogbuokiri): Kaggle `ogbuokiriblessing/sensor-based-aquaponics-fish-pond-datasets`.

Four ponds, ~233k per-minute readings, 19 Jun–31 Oct 2021. This is RAW IoT sensor data, and
we treat it that way: some channels (turbidity, ammonia) sit pinned at their sensor ceiling
and are flagged `low (sensor saturation)` rather than calibrated against. The channels we
trust — water temperature, pH, and the fish growth trajectory — are the ones the envelope
cross-check actually uses.

This module never resizes anything and never raises on clean input; it reports how real ponds
compare to what `size_system()` emits, in the same field vocabulary as the install-logging
standard (`logging_schema`) so real data and our own future logs share one schema.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from .logging_schema import SCHEMA_VERSION

_PKG_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _PKG_DIR.parent
RAW_DIR = _REPO_ROOT / "data" / "raw"
ARTIFACT = _REPO_ROOT / "data" / "empirical_envelope.json"

DATASET_CITATION = (
    "Udanor et al. (2022), Data in Brief 43:108400, "
    "DOI 10.1016/j.dib.2022.108400 (CC BY 4.0)"
)

# Raw source column -> canonical logging_schema vocabulary. Reusing the install-standard
# names is deliberate: the moat is one schema across real datasets and our own installs.
COLUMN_MAP = {
    "created_at": "timestamp",
    "temperature": "water_temp_c",
    "turbidity": "turbidity_ntu",          # not in logging_schema; kept for completeness
    "disolved_oxg": "dissolved_oxygen_mg_l",
    "ph": "ph",
    "ammonia": "ammonia_mg_l",
    "nitrate": "nitrate_mg_l",
    "fish_length": "fish_length_cm",        # growth trajectory (not a logging_schema field)
    "fish_weight": "fish_weight_g",
}

NUMERIC_CHANNELS = [v for k, v in COLUMN_MAP.items() if k != "created_at"]

# A channel counts as saturated (low trust) when this share of readings is pinned at the
# observed floor or ceiling — the signature of a cheap sensor hitting its rail.
_SATURATION_THRESHOLD = 0.25


def available() -> bool:
    """True when the raw pond CSVs have been fetched (see scripts/fetch_aquaponics_data.py)."""
    return RAW_DIR.exists() and any(RAW_DIR.glob("IoTPond*.csv"))


def load_all() -> pd.DataFrame:
    """Load every pond into one frame with canonical column names and a `pond` id column."""
    files = sorted(RAW_DIR.glob("IoTPond*.csv"))
    if not files:
        raise FileNotFoundError(
            f"No pond CSVs in {RAW_DIR}. Run: python scripts/fetch_aquaponics_data.py"
        )
    frames = []
    for f in files:
        df = pd.read_csv(f).rename(columns=COLUMN_MAP)
        df["pond"] = f.stem.replace("IoTPond", "")
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


def _saturation(s: pd.Series) -> tuple[float, float]:
    """Return (fraction pinned at floor, fraction pinned at ceiling)."""
    s = pd.to_numeric(s, errors="coerce").dropna()
    if s.empty:
        return 0.0, 0.0
    at_floor = float((s <= s.min() + 1e-9).mean())
    at_ceiling = float((s >= s.max() - 1e-9).mean())
    return at_floor, at_ceiling


def empirical_envelope(df: pd.DataFrame | None = None) -> dict:
    """Per-channel distribution (p5/p50/p95, range) with an honest trust flag.

    Channels pinned at a sensor rail are marked low-trust so nobody mistakes a saturated
    reading for a calibrated one.
    """
    if df is None:
        df = load_all()
    out: dict = {}
    for col in NUMERIC_CHANNELS:
        if col not in df.columns:
            continue
        s = pd.to_numeric(df[col], errors="coerce").dropna()
        if s.empty:
            continue
        at_floor, at_ceiling = _saturation(s)
        saturated = at_floor > _SATURATION_THRESHOLD or at_ceiling > _SATURATION_THRESHOLD
        out[col] = {
            "n": int(s.size),
            "min": round(float(s.min()), 3),
            "p5": round(float(s.quantile(0.05)), 3),
            "p50": round(float(s.median()), 3),
            "p95": round(float(s.quantile(0.95)), 3),
            "max": round(float(s.max()), 3),
            "frac_at_floor": round(at_floor, 3),
            "frac_at_ceiling": round(at_ceiling, 3),
            "trust": "low (sensor saturation)" if saturated else "reliable",
        }
    return out


def _band_check(s: pd.Series, target: list, do_not_exceed: list) -> dict:
    s = pd.to_numeric(s, errors="coerce").dropna()
    median = float(s.median())
    if median < target[0]:
        position = "below target band"
    elif median > target[1]:
        position = "above target band"
    else:
        position = "within target band"
    return {
        "median": round(median, 3),
        "target_band": list(target),
        "do_not_exceed_band": list(do_not_exceed),
        "frac_in_target": round(float(((s >= target[0]) & (s <= target[1])).mean()), 3),
        "frac_in_do_not_exceed": round(
            float(((s >= do_not_exceed[0]) & (s <= do_not_exceed[1])).mean()), 3
        ),
        "median_position": position,
    }


def compare_to_model_envelope(
    model_envelope: dict, df: pd.DataFrame | None = None
) -> dict:
    """Cross-check the reliable channels (temperature, pH) of a `DesignOutput.operating_envelope`
    against real pond readings.

    For each channel: where the real median sits relative to the model's target band, and the
    share of readings inside the target and do-not-exceed bands. This is how a seed coefficient
    earns (or loses) trust against reality — e.g. real ponds running below the tilapia optimum,
    or pH above the leafy-crop ceiling, show up here as low `frac_in_target`.
    """
    if df is None:
        df = load_all()
    checks: dict = {}
    if "water_temp_c" in df.columns:
        checks["water_temp_c"] = _band_check(
            df["water_temp_c"],
            model_envelope["temperature_target_c"],
            model_envelope["temperature_do_not_exceed_c"],
        )
    if "ph" in df.columns:
        checks["ph"] = _band_check(
            df["ph"], model_envelope["ph_target"], model_envelope["ph_do_not_exceed"]
        )
    return checks


# Which model-envelope keys map to which reliable real-data channel. Only the trustworthy
# channels appear here — saturated ones (turbidity, ammonia) are deliberately not cross-checked.
_ENVELOPE_CHANNELS = {
    "water_temp_c": ("temperature_target_c", "temperature_do_not_exceed_c"),
    "ph": ("ph_target", "ph_do_not_exceed"),
}


def _summary_check(model_envelope: dict, channels: dict) -> dict:
    """Lighter cross-check from the committed percentile artifact (no raw CSVs needed)."""
    out: dict = {}
    for channel, (target_key, dne_key) in _ENVELOPE_CHANNELS.items():
        c = channels.get(channel)
        if not c:
            continue
        target = model_envelope[target_key]
        median = c["p50"]
        if median < target[0]:
            position = "below target band"
        elif median > target[1]:
            position = "above target band"
        else:
            position = "within target band"
        out[channel] = {
            "median": median,
            "p5": c["p5"],
            "p95": c["p95"],
            "target_band": list(target),
            "do_not_exceed_band": list(model_envelope[dne_key]),
            "median_position": position,
        }
    return out


def envelope_reality_check(model_envelope: dict) -> dict | None:
    """UI-facing reality check that works with OR without the raw CSVs.

    - `mode="full"`  (raw data fetched): exact fraction of real readings inside each band.
    - `mode="summary"` (only the committed artifact): percentile position per channel.
    - `None` if neither the raw data nor the artifact is available.

    Only the reliable channels (temperature, pH) are compared; saturated sensors are excluded.
    """
    if available():
        return {
            "mode": "full",
            "source": DATASET_CITATION,
            "channels": compare_to_model_envelope(model_envelope),
        }
    if ARTIFACT.exists():
        art = load_artifact()
        return {
            "mode": "summary",
            "source": art["source"],
            "n_readings": art["n_readings"],
            "channels": _summary_check(model_envelope, art["channels"]),
        }
    return None


def summarize(df: pd.DataFrame | None = None) -> dict:
    """Build the small, version-controllable summary (the committed reality artifact)."""
    if df is None:
        df = load_all()
    return {
        "source": DATASET_CITATION,
        "license": "CC BY 4.0",
        "schema_version": SCHEMA_VERSION,
        "n_readings": int(len(df)),
        "n_ponds": int(df["pond"].nunique()),
        "date_span": [str(df["timestamp"].min()), str(df["timestamp"].max())],
        "channels": empirical_envelope(df),
    }


def write_artifact(path: Path | str = ARTIFACT) -> dict:
    """Recompute the empirical envelope from raw data and write it to JSON. Returns the dict."""
    artifact = summarize()
    Path(path).write_text(json.dumps(artifact, indent=2) + "\n")
    return artifact


def load_artifact(path: Path | str = ARTIFACT) -> dict:
    """Read the committed empirical envelope without needing the raw CSVs present."""
    return json.loads(Path(path).read_text())
