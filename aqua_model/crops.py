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
    yield_kg_per_m2_year: float  # edible fresh yield per m2 per year — OPTIMIZER objective (seed/LIT)
    edible_protein_pct: float    # % protein of edible fresh mass — for the protein objective
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
    yield_kg_per_m2_year=25.0, edible_protein_pct=1.4,
    ph_min=5.5, ph_max=7.0, temp_min_c=10.0, temp_max_c=26.0, source="FAO589/UVI",
)
# Basil FRR calibrated to UVI-measured values (Rakocy et al. 2004: 81.4 batch, 99.6 staggered
# g/m2/day). Set to 85 — mid the measured band — replacing the earlier 70 g/m2/day stub.
BASIL = Crop(
    name="basil", category="leafy",
    frr_g_per_m2_day=85.0, frr_low=81.0, frr_high=100.0,
    n_uptake_g_per_m2_day=1.0,
    yield_kg_per_m2_year=15.0, edible_protein_pct=3.0,
    ph_min=5.5, ph_max=7.0, temp_min_c=18.0, temp_max_c=30.0, source="Rakocy2004",
)

# Fruiting: higher feed ratio (FAO 589 cites ~100+ g/m2/day for fruiting raft).
TOMATO = Crop(
    name="tomato", category="fruiting",
    frr_g_per_m2_day=110.0, frr_low=80.0, frr_high=140.0,
    n_uptake_g_per_m2_day=1.6,
    yield_kg_per_m2_year=30.0, edible_protein_pct=0.9,
    ph_min=5.5, ph_max=6.5, temp_min_c=18.0, temp_max_c=30.0, source="FAO589",
)

# More leafy greens (UVI/FAO leafy feeding-rate band ~40-100 g/m2/day).
KALE = Crop(
    name="kale", category="leafy",
    frr_g_per_m2_day=65.0, frr_low=45.0, frr_high=90.0,
    n_uptake_g_per_m2_day=0.9,
    yield_kg_per_m2_year=20.0, edible_protein_pct=3.3,
    ph_min=5.5, ph_max=7.0, temp_min_c=7.0, temp_max_c=24.0, source="FAO589/UVI (leafy band)",
)
SWISS_CHARD = Crop(
    name="swiss_chard", category="leafy",
    frr_g_per_m2_day=60.0, frr_low=40.0, frr_high=85.0,
    n_uptake_g_per_m2_day=0.85,
    yield_kg_per_m2_year=22.0, edible_protein_pct=1.8,
    ph_min=5.5, ph_max=7.0, temp_min_c=10.0, temp_max_c=27.0, source="FAO589/UVI (leafy band)",
)
SPINACH = Crop(
    name="spinach", category="leafy",
    frr_g_per_m2_day=55.0, frr_low=40.0, frr_high=80.0,
    n_uptake_g_per_m2_day=0.8,
    yield_kg_per_m2_year=15.0, edible_protein_pct=2.9,
    ph_min=6.0, ph_max=7.0, temp_min_c=7.0, temp_max_c=24.0, source="FAO589/UVI (leafy band)",
)

# More fruiting crops (FAO fruiting feeding-rate band ~80-140 g/m2/day).
CUCUMBER = Crop(
    name="cucumber", category="fruiting",
    frr_g_per_m2_day=100.0, frr_low=80.0, frr_high=130.0,
    n_uptake_g_per_m2_day=1.5,
    yield_kg_per_m2_year=35.0, edible_protein_pct=0.7,
    ph_min=5.5, ph_max=6.5, temp_min_c=18.0, temp_max_c=30.0, source="FAO589 (fruiting band)",
)
PEPPER = Crop(
    name="pepper", category="fruiting",
    frr_g_per_m2_day=100.0, frr_low=80.0, frr_high=130.0,
    n_uptake_g_per_m2_day=1.4,
    yield_kg_per_m2_year=20.0, edible_protein_pct=1.0,
    ph_min=5.5, ph_max=6.5, temp_min_c=18.0, temp_max_c=30.0, source="FAO589 (fruiting band)",
)

# ── Culinary herbs (leafy band; UVI/FAO leafy feeding-rate band ~40-100 g/m2/day) ──────────
# FRR is placed within the published leafy band by nutrient demand relative to peers; yield &
# protein are horticultural seed values (fresh edible mass), calibratable per system.
MINT = Crop(
    name="mint", category="leafy",
    frr_g_per_m2_day=70.0, frr_low=55.0, frr_high=90.0,
    n_uptake_g_per_m2_day=1.0,
    yield_kg_per_m2_year=12.0, edible_protein_pct=3.8,
    ph_min=5.5, ph_max=7.0, temp_min_c=15.0, temp_max_c=30.0, source="FAO589/UVI (leafy band)",
)
CILANTRO = Crop(
    name="cilantro", category="leafy",
    frr_g_per_m2_day=50.0, frr_low=40.0, frr_high=75.0,
    n_uptake_g_per_m2_day=0.8,
    yield_kg_per_m2_year=10.0, edible_protein_pct=2.1,
    ph_min=6.0, ph_max=6.8, temp_min_c=10.0, temp_max_c=24.0, source="FAO589/UVI (leafy band)",
)
PARSLEY = Crop(
    name="parsley", category="leafy",
    frr_g_per_m2_day=55.0, frr_low=40.0, frr_high=80.0,
    n_uptake_g_per_m2_day=0.85,
    yield_kg_per_m2_year=15.0, edible_protein_pct=3.0,
    ph_min=5.5, ph_max=7.0, temp_min_c=7.0, temp_max_c=26.0, source="FAO589/UVI (leafy band)",
)
CHIVES = Crop(
    name="chives", category="leafy",
    frr_g_per_m2_day=55.0, frr_low=40.0, frr_high=80.0,
    n_uptake_g_per_m2_day=0.85,
    yield_kg_per_m2_year=12.0, edible_protein_pct=3.3,
    ph_min=6.0, ph_max=7.0, temp_min_c=7.0, temp_max_c=26.0, source="FAO589/UVI (leafy band)",
)
DILL = Crop(
    name="dill", category="leafy",
    frr_g_per_m2_day=55.0, frr_low=40.0, frr_high=80.0,
    n_uptake_g_per_m2_day=0.85,
    yield_kg_per_m2_year=10.0, edible_protein_pct=3.5,
    ph_min=5.5, ph_max=6.5, temp_min_c=10.0, temp_max_c=25.0, source="FAO589/UVI (leafy band)",
)
OREGANO = Crop(
    name="oregano", category="leafy",
    frr_g_per_m2_day=60.0, frr_low=45.0, frr_high=85.0,
    n_uptake_g_per_m2_day=0.9,
    yield_kg_per_m2_year=8.0, edible_protein_pct=3.4,
    ph_min=6.0, ph_max=7.0, temp_min_c=15.0, temp_max_c=28.0, source="FAO589/UVI (leafy band)",
)
SAGE = Crop(
    name="sage", category="leafy",
    frr_g_per_m2_day=55.0, frr_low=40.0, frr_high=80.0,
    n_uptake_g_per_m2_day=0.85,
    yield_kg_per_m2_year=6.0, edible_protein_pct=3.5,
    ph_min=6.0, ph_max=6.5, temp_min_c=15.0, temp_max_c=28.0, source="FAO589/UVI (leafy band)",
)

# ── More leafy greens (leafy band ~40-100 g/m2/day) ─────────────────────────────────────────
ARUGULA = Crop(
    name="arugula", category="leafy",
    frr_g_per_m2_day=50.0, frr_low=40.0, frr_high=75.0,
    n_uptake_g_per_m2_day=0.8,
    yield_kg_per_m2_year=18.0, edible_protein_pct=2.6,
    ph_min=6.0, ph_max=7.0, temp_min_c=10.0, temp_max_c=24.0, source="FAO589/UVI (leafy band)",
)
WATERCRESS = Crop(
    name="watercress", category="leafy",
    frr_g_per_m2_day=50.0, frr_low=40.0, frr_high=75.0,
    n_uptake_g_per_m2_day=0.8,
    yield_kg_per_m2_year=20.0, edible_protein_pct=2.3,
    ph_min=6.5, ph_max=7.5, temp_min_c=10.0, temp_max_c=22.0, source="FAO589/UVI (leafy band)",
)
PAK_CHOI = Crop(
    name="pak_choi", category="leafy",
    frr_g_per_m2_day=60.0, frr_low=45.0, frr_high=85.0,
    n_uptake_g_per_m2_day=0.85,
    yield_kg_per_m2_year=22.0, edible_protein_pct=1.5,
    ph_min=6.0, ph_max=7.0, temp_min_c=13.0, temp_max_c=24.0, source="FAO589/UVI (leafy band)",
)
MUSTARD_GREENS = Crop(
    name="mustard_greens", category="leafy",
    frr_g_per_m2_day=60.0, frr_low=45.0, frr_high=85.0,
    n_uptake_g_per_m2_day=0.85,
    yield_kg_per_m2_year=18.0, edible_protein_pct=2.7,
    ph_min=5.5, ph_max=6.8, temp_min_c=10.0, temp_max_c=24.0, source="FAO589/UVI (leafy band)",
)
COLLARD_GREENS = Crop(
    name="collard_greens", category="leafy",
    frr_g_per_m2_day=72.0, frr_low=50.0, frr_high=95.0,
    n_uptake_g_per_m2_day=0.95,
    yield_kg_per_m2_year=20.0, edible_protein_pct=3.0,
    ph_min=6.0, ph_max=7.0, temp_min_c=10.0, temp_max_c=27.0,
    source="FAO589/UVI (leafy band, high — heavy-feeding brassica)",
)
CELERY = Crop(
    name="celery", category="leafy",
    frr_g_per_m2_day=70.0, frr_low=50.0, frr_high=95.0,
    n_uptake_g_per_m2_day=0.9,
    # Yield moderated 25→18: long-season (~120 d) head crop, ~4-6 kg/m2/cycle × ~2-3 cycles/yr.
    yield_kg_per_m2_year=18.0, edible_protein_pct=0.7,
    ph_min=6.0, ph_max=6.8, temp_min_c=15.0, temp_max_c=24.0, source="FAO589/UVI (leafy band)",
)
CABBAGE = Crop(
    name="cabbage", category="leafy",
    frr_g_per_m2_day=80.0, frr_low=55.0, frr_high=100.0,
    n_uptake_g_per_m2_day=1.1,
    # Yield moderated 30→20: field top ~75 t/ha = 7.5 kg/m2/head-crop, ~2-3 cycles/yr (FAO cabbage).
    yield_kg_per_m2_year=20.0, edible_protein_pct=1.3,
    ph_min=6.0, ph_max=7.2, temp_min_c=7.0, temp_max_c=24.0,
    source="FAO589/UVI (leafy band, high — heavy-feeding brassica)",
)

# ── Fruiting & heavy-feeding brassicas (fruiting band ~80-140 g/m2/day) ──────────────────────
BROCCOLI = Crop(
    name="broccoli", category="fruiting",
    frr_g_per_m2_day=90.0, frr_low=80.0, frr_high=120.0,
    n_uptake_g_per_m2_day=1.3,
    yield_kg_per_m2_year=12.0, edible_protein_pct=2.8,
    ph_min=6.0, ph_max=7.0, temp_min_c=10.0, temp_max_c=24.0, source="FAO589 (fruiting band)",
)
CAULIFLOWER = Crop(
    name="cauliflower", category="fruiting",
    frr_g_per_m2_day=90.0, frr_low=80.0, frr_high=120.0,
    n_uptake_g_per_m2_day=1.3,
    yield_kg_per_m2_year=15.0, edible_protein_pct=1.9,
    ph_min=6.0, ph_max=7.0, temp_min_c=10.0, temp_max_c=24.0, source="FAO589 (fruiting band)",
)
STRAWBERRY = Crop(
    name="strawberry", category="fruiting",
    frr_g_per_m2_day=60.0, frr_low=45.0, frr_high=85.0,
    n_uptake_g_per_m2_day=0.9,
    yield_kg_per_m2_year=6.0, edible_protein_pct=0.7,
    ph_min=5.5, ph_max=6.5, temp_min_c=10.0, temp_max_c=27.0,
    source="FAO589 (fruiting band, low — light/moderate feeder; excess N favours runners over fruit)",
)
EGGPLANT = Crop(
    name="eggplant", category="fruiting",
    frr_g_per_m2_day=110.0, frr_low=80.0, frr_high=140.0,
    n_uptake_g_per_m2_day=1.5,
    # Yield moderated 25→20 (greenhouse ~6.6 kg/m2/crop; aquaponics lower). temp_min 20→18:
    # tolerates ~16 °C night / ~18 °C day, warm-season but not as heat-strict as okra.
    yield_kg_per_m2_year=20.0, edible_protein_pct=1.0,
    ph_min=5.5, ph_max=6.5, temp_min_c=18.0, temp_max_c=30.0, source="FAO589 (fruiting band)",
)
GREEN_BEAN = Crop(
    name="green_bean", category="fruiting",
    frr_g_per_m2_day=65.0, frr_low=50.0, frr_high=90.0,
    n_uptake_g_per_m2_day=0.85,
    yield_kg_per_m2_year=15.0, edible_protein_pct=1.8,
    ph_min=6.0, ph_max=7.0, temp_min_c=18.0, temp_max_c=28.0,
    source="FAO589 (fruiting band, low — legume: partial N-fixation lowers demand on fish-derived N)",
)
OKRA = Crop(
    name="okra", category="fruiting",
    frr_g_per_m2_day=95.0, frr_low=80.0, frr_high=125.0,
    n_uptake_g_per_m2_day=1.3,
    # temp_min 22→20: okra will not tolerate below ~18 °C (65 °F); 20 is the productive floor.
    # Yield 12 confirmed conservative vs UVI (2.9 t okra/yr ≈ 14 kg/m2 on raft area).
    yield_kg_per_m2_year=12.0, edible_protein_pct=1.9,
    ph_min=6.0, ph_max=6.8, temp_min_c=20.0, temp_max_c=32.0, source="FAO589/UVI (fruiting band)",
)
ZUCCHINI = Crop(
    name="zucchini", category="fruiting",
    frr_g_per_m2_day=100.0, frr_low=80.0, frr_high=130.0,
    n_uptake_g_per_m2_day=1.4,
    # Yield moderated 35→28: protected continuous zucchini is high-yielding but 35 was aggressive.
    yield_kg_per_m2_year=28.0, edible_protein_pct=1.2,
    ph_min=5.5, ph_max=6.8, temp_min_c=18.0, temp_max_c=30.0, source="FAO589 (fruiting band)",
)
PEA = Crop(
    name="pea", category="fruiting",
    frr_g_per_m2_day=60.0, frr_low=45.0, frr_high=85.0,
    n_uptake_g_per_m2_day=0.8,
    yield_kg_per_m2_year=10.0, edible_protein_pct=5.4,
    ph_min=6.0, ph_max=7.0, temp_min_c=7.0, temp_max_c=24.0,
    source="FAO589 (fruiting band, low — legume: partial N-fixation lowers demand on fish-derived N)",
)

CROPS: dict[str, Crop] = {
    c.name: c for c in (
        LETTUCE, BASIL, TOMATO, KALE, SWISS_CHARD, SPINACH, CUCUMBER, PEPPER,
        # herbs
        MINT, CILANTRO, PARSLEY, CHIVES, DILL, OREGANO, SAGE,
        # leafy greens
        ARUGULA, WATERCRESS, PAK_CHOI, MUSTARD_GREENS, COLLARD_GREENS, CELERY, CABBAGE,
        # fruiting & heavy brassicas
        BROCCOLI, CAULIFLOWER, STRAWBERRY, EGGPLANT, GREEN_BEAN, OKRA, ZUCCHINI, PEA,
    )
}


def get_crop(name: str) -> Crop:
    key = (name or "").strip().lower()
    if key not in CROPS:
        raise KeyError(f"Unknown crop {name!r}. Known: {sorted(CROPS)}")
    return CROPS[key]
