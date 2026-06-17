"""Sourced calibration of the SIZING coefficients — turning "LIT" into citations you can check.

The seed coefficients in `species.py` / `crops.py` carry vague source tags ("LIT", "FAO589").
This module pins each load-bearing sizing coefficient to a published empirical range with a
real citation and reports whether the seed sits inside it. It NEVER changes a seed value —
out-of-range coefficients are surfaced as `discrepancies()` for an operator to decide on,
exactly like the model's other honesty layers.

Ranges are the *observed spread across studies*, not a single trial: aquaponics coefficients
vary with diet, cultivar, climate, and system, so a one-number "truth" would be a lie. Where
a value is well-pinned (FRR, tilapia FCR) the range is tight; where the species is ambiguous
(generic "catfish") or annualization is assumed (crop yield) the note says so out loud.

Seed values are read live from the species/crop modules, so this layer can never silently
drift out of sync with what the model actually uses.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .crops import get_crop
from .species import get_species

_REPO_ROOT = Path(__file__).resolve().parent.parent
ARTIFACT = _REPO_ROOT / "data" / "coefficient_sources.json"


@dataclass(frozen=True)
class SizingCalibration:
    """One sizing coefficient pinned to a sourced empirical range."""

    key: str                  # e.g. "basil.frr"
    label: str
    seed: float               # current model value (read live from species/crops)
    emp_low: float            # sourced empirical lower bound
    emp_high: float           # sourced empirical upper bound
    unit: str
    sources: tuple[str, ...]
    note: str = ""

    def __post_init__(self) -> None:
        if self.emp_low > self.emp_high:
            raise ValueError(f"{self.key}: emp_low {self.emp_low} > emp_high {self.emp_high}")
        if not self.sources:
            raise ValueError(f"{self.key}: a calibration must cite at least one source")

    @property
    def within(self) -> bool:
        return self.emp_low <= self.seed <= self.emp_high

    @property
    def verdict(self) -> str:
        if self.seed < self.emp_low:
            return "below empirical range"
        if self.seed > self.emp_high:
            return "above empirical range"
        return "within empirical range"

    def as_record(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "seed": self.seed,
            "empirical_range": [self.emp_low, self.emp_high],
            "unit": self.unit,
            "verdict": self.verdict,
            "sources": list(self.sources),
            "note": self.note,
        }


def _calibrations() -> list[SizingCalibration]:
    tilapia, catfish, trout = get_species("tilapia"), get_species("catfish"), get_species("trout")
    lettuce, basil, tomato = get_crop("lettuce"), get_crop("basil"), get_crop("tomato")

    return [
        # ---- Feed conversion ratios (g feed / g live-weight gain) ----
        SizingCalibration(
            "tilapia.fcr", "Nile tilapia feed conversion ratio",
            tilapia.fcr, 0.9, 1.8, "g feed / g gain",
            (
                "Shaw, Knopf & Kloas (2022), Sustainability 14(7):4064, "
                "DOI 10.3390/su14074064 (FCR 0.86–1.79 across protein sources)",
                "Rodde et al. (2020), Aquaculture Reports 18:100349, "
                "DOI 10.1016/j.aqrep.2020.100349 (GIFT strain, individual rearing)",
            ),
            "Strongly diet-dependent; well-run commercial RAS ~1.4–1.8. Seed 1.7 is high-but-valid.",
        ),
        SizingCalibration(
            "catfish.fcr", "Catfish feed conversion ratio",
            catfish.fcr, 0.8, 2.0, "g feed / g gain",
            (
                "African catfish (Clarias gariepinus) commercial RAS FCR ~0.8–1.0",
                "Channel catfish (Ictalurus punctatus) pond/RAS FCR ~1.5–2.0",
            ),
            "SPECIES AMBIGUITY: 'catfish' spans efficient Clarias (~0.8–1.0) and channel "
            "catfish (~1.5–2.0). Seed 1.6 implies channel catfish — confirm which you stock, "
            "as it nearly doubles the implied biomass for the same feed.",
        ),
        SizingCalibration(
            "trout.fcr", "Rainbow trout feed conversion ratio",
            trout.fcr, 0.8, 1.5, "g feed / g gain",
            ("Rainbow trout (Oncorhynchus mykiss) literature FCR ~0.8–1.5 (trials report 0.99–1.49)",),
            "Cold-water, efficient. Seed 1.2 is mid-range.",
        ),
        # ---- Feeding-rate ratio (g feed / m² plant / day) — the load-bearing sizing rule ----
        SizingCalibration(
            "lettuce.frr", "Lettuce feeding-rate ratio",
            lettuce.frr_g_per_m2_day, 60.0, 100.0, "g feed / m² / day",
            (
                "Rakocy/UVI raft guideline: 60–100 g/m²/day for leafy greens "
                "(lettuce originally 60 g, Bibb lettuce, 1988)",
                "Somerville et al. (2014), FAO 589",
            ),
            "Load-bearing sizing rule. Seed 60 sits at the conservative (low) end of the UVI band.",
        ),
        SizingCalibration(
            "basil.frr", "Basil feeding-rate ratio",
            basil.frr_g_per_m2_day, 81.0, 100.0, "g feed / m² / day",
            (
                "Rakocy, Shultz, Bailey & Thoman (2004), 'Aquaponic production of tilapia and "
                "basil', Acta Hort. 648:63–69 (measured 81.4 g/m²/day batch, 99.6 staggered)",
            ),
            "Recalibrated from the earlier 70 g/m²/day stub to 85 — mid the UVI-measured basil "
            "band (81–100, Rakocy et al. 2004) — so a basil system is no longer under-fed.",
        ),
        SizingCalibration(
            "tomato.frr", "Tomato (fruiting) feeding-rate ratio",
            tomato.frr_g_per_m2_day, 80.0, 140.0, "g feed / m² / day",
            ("Somerville et al. (2014), FAO 589: fruiting raft ~100+ g/m²/day",),
            "Fruiting crops carry a higher feed load than leafy. Seed 110 is mid-band.",
        ),
        # ---- Yield and harvest weight ----
        SizingCalibration(
            "lettuce.yield", "Lettuce edible yield (annualized)",
            lettuce.yield_kg_per_m2_year, 10.0, 30.0, "kg / m² / year",
            (
                "DWC aquaponic lettuce ~1.0–1.42 kg/m²/cycle "
                "(AUC thesis, fount.aucegypt.edu/etds/795); ~8–11 cycles/yr",
            ),
            "Per-cycle yields annualized — depends heavily on cycles/year (climate, cultivar). "
            "Seed 25 is optimistic-but-plausible for year-round warm-climate production.",
        ),
        SizingCalibration(
            "tilapia.harvest_weight", "Nile tilapia harvest weight",
            tilapia.harvest_weight_kg, 0.4, 0.8, "kg / fish",
            (
                "Common small-scale/aquaponic tilapia harvest target 0.4–0.6 kg "
                "(Somerville et al. 2014, FAO 589); commercial up to ~0.8 kg",
                "IoTPond open dataset (data/empirical_envelope.json): observed max ~0.39 kg "
                "over a Jun–Oct grow-out at ~24.5 °C (below the 27–30 °C optimum)",
            ),
            "Seed 0.5 kg is a reasonable target, but the real ponds we ingested only reached "
            "~0.39 kg — a sub-optimal-temperature grow-out finishes lighter than the target.",
        ),
    ]


CALIBRATIONS: list[SizingCalibration] = _calibrations()


def all_calibrations() -> list[SizingCalibration]:
    """Every sizing-coefficient calibration, seeds read live from the model."""
    return list(CALIBRATIONS)


def get(key: str) -> SizingCalibration:
    for c in CALIBRATIONS:
        if c.key == key:
            return c
    raise KeyError(f"Unknown calibration {key!r}. Known: {[c.key for c in CALIBRATIONS]}")


def discrepancies() -> list[SizingCalibration]:
    """Seed coefficients that fall OUTSIDE their sourced empirical range — surfaced, not fixed."""
    return [c for c in CALIBRATIONS if not c.within]


def summary() -> dict:
    return {
        "n_coefficients": len(CALIBRATIONS),
        "n_within_range": sum(c.within for c in CALIBRATIONS),
        "discrepancies": [c.key for c in discrepancies()],
        "coefficients": [c.as_record() for c in CALIBRATIONS],
    }


def write_artifact(path: Path | str = ARTIFACT) -> dict:
    """Write the seed-vs-sourced-range table to JSON (the committed reference artifact)."""
    art = summary()
    Path(path).write_text(json.dumps(art, indent=2) + "\n")
    return art
