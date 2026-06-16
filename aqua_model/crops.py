"""Crop database — seed defaults, cited and ranged.

`frr_g_per_m2_day` is the feeding-rate ratio: grams of fish FEED per m2 of this crop's
growing area per day. It is the load-bearing SIZING coefficient (FAO 589 / UVI).
`n_uptake_g_per_m2_day` is used only by the nitrogen CONSISTENCY CHECK, never for sizing.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Crop:
    name: str
    category: str               # "leafy" or "fruiting"
    frr_g_per_m2_day: float      # feeding-rate ratio (g feed / m2 / day) — SIZING rule
    frr_low: float
    frr_high: float
    n_uptake_g_per_m2_day: float # N removed by plants per m2/day — CONSISTENCY CHECK only
    ph_min: float
    ph_max: float
    temp_min_c: float
    temp_max_c: float
    source: str


# Leafy greens: lower feed ratio. FAO 589 / UVI cite ~40-100 g/m2/day for leafy raft.
LETTUCE = Crop(
    name="lettuce", category="leafy",
    frr_g_per_m2_day=60.0, frr_low=40.0, frr_high=80.0,
    n_uptake_g_per_m2_day=0.8,
    ph_min=5.5, ph_max=7.0, temp_min_c=10.0, temp_max_c=26.0, source="FAO589/UVI",
)
BASIL = Crop(
    name="basil", category="leafy",
    frr_g_per_m2_day=70.0, frr_low=50.0, frr_high=90.0,
    n_uptake_g_per_m2_day=1.0,
    ph_min=5.5, ph_max=7.0, temp_min_c=18.0, temp_max_c=30.0, source="LIT",
)

# Fruiting: higher feed ratio (FAO 589 cites ~100+ g/m2/day for fruiting raft).
TOMATO = Crop(
    name="tomato", category="fruiting",
    frr_g_per_m2_day=110.0, frr_low=80.0, frr_high=140.0,
    n_uptake_g_per_m2_day=1.6,
    ph_min=5.5, ph_max=6.5, temp_min_c=18.0, temp_max_c=30.0, source="FAO589",
)

CROPS: dict[str, Crop] = {c.name: c for c in (LETTUCE, BASIL, TOMATO)}


def get_crop(name: str) -> Crop:
    key = (name or "").strip().lower()
    if key not in CROPS:
        raise KeyError(f"Unknown crop {name!r}. Known: {sorted(CROPS)}")
    return CROPS[key]
