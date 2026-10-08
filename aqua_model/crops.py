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

# Heat-tolerant leafy greens. Every other leafy entry above stops at 30 °C or below, so
# a system simulated in the Sahel returned "most limiting factor: temperature" for want of
# a crop rather than for want of a workable climate (#104).
AMARANTH = Crop(
    name="amaranth", category="leafy",
    # No amaranth-specific feeding-rate ratio is published. Placed in the FAO 589 / UVI
    # leafy band (~40-100 g/m2/day) alongside kale, the closest analogue we already carry:
    # a fast cut-and-come-again green with repeated harvests off standing plants.
    frr_g_per_m2_day=65.0, frr_low=45.0, frr_high=90.0,
    n_uptake_g_per_m2_day=0.9,
    # Field leaf yield 11.0-15.3 t/ha across 17 entries over four leaf harvests (World
    # Vegetable Center, Tanzania) = 1.1-1.53 kg/m2 per season. At two to three Sahelian
    # seasons a year that is ~3-4.6 kg/m2/yr in soil; protected raft culture runs several
    # times field for leafy greens, which places this at roughly 14-18. Held at 16 — under
    # lettuce (25) and inside the band its leafy siblings occupy — because the multiplier,
    # not the measurement, is the soft part of that chain.
    yield_kg_per_m2_year=16.0, edible_protein_pct=2.5,
    ph_min=5.5, ph_max=6.5,
    # The reason this crop exists in the database: optimal 21-35 °C, measured across hot
    # (33/27), warm (27/21) and cool (21/15) day/night regimes. temp_max 35 makes amaranth
    # the most heat-tolerant entry we have, ahead of okra at 32.
    temp_min_c=18.0, temp_max_c=35.0,
    source=("WorldVeg Tanzania leaf-harvest trial (17 entries, 11.0-15.3 t/ha over four "
            "harvests) for yield; Water SA, growth-temperature study of Amaranthus leaves "
            "(hot 33/27, warm 27/21, cool 21/15 °C day/night) for the 21-35 °C band; "
            "USDA-style composition for amaranth leaves, raw (2.5 g protein/100 g fresh); "
            "hydroponic amaranth trials run pH 5.5-6.5. FRR placed in the FAO589/UVI "
            "leafy band, not measured for this species"),
)

# WATER SPINACH / KANGKONG (Ipomoea aquatica) — semi-aquatic, ideal for raft culture, heat-tolerant
WATER_SPINACH = Crop(
    name="water_spinach",
    category="leafy",
    frr_g_per_m2_day=65.0,
    frr_low=45.0,
    frr_high=90.0,
    n_uptake_g_per_m2_day=0.9,
    yield_kg_per_m2_year=16.0,
    edible_protein_pct=2.6,
    ph_min=5.5,
    ph_max=7.5,
    temp_min_c=20.0,
    temp_max_c=35.0,
    source=("FAO Ecocrop (https://ecocrop.apps.fao.org/ecocrop/srv/en/dataSheet?id=1264) "
        "for temperature band (15-35°C optimal, 10-40°C absolute); "
        "USDA FoodData Central (https://fdc.nal.usda.gov/) for protein content "
        "(2.6g/100g fresh). FRR placed in FAO 589 / UVI leafy band (not measured "
        "for this species). Yield from tropical field trials (12-18 t/ha) through "
        "protected-culture multiplier."),
)

# MALABAR SPINACH (Basella alba) — climbing vine grown as a leafy green, the
# most cold-sensitive of the three heat-tolerant leafies (#125: the sixth of the six wanted).
MALABAR_SPINACH = Crop(
    name="malabar_spinach",
    category="leafy",
    # No Basella-specific feeding-rate ratio is published for aquaponics. Placed in
    # the FAO 589 / UVI leafy band (~40-100 g/m2/day) alongside amaranth and water
    # spinach, the closest analogues: fast-growing tropical cut-and-come-again greens.
    frr_g_per_m2_day=65.0,
    frr_low=45.0,
    frr_high=90.0,
    n_uptake_g_per_m2_day=0.9,
    # Field leaf yield from the UVI trial: 344 g/m2 (Basella alba, green) over 57 days,
    # 6.04 g/m2/day (Palada & Crossman 1999, Table 1; 385 g/m2 for B. rubra; Palada
    # et al. 1996 / Palada & Crossman 1998 report comparable season yields at 100 kg/ha N,
    # and Palada, Davis & Crossman 1999 report leaf yields rising to 306.8 g/plant at
    # 40 t/ha manure). Harvested year-round that is about 2.2 kg/m2/yr in the field.
    # The registered 14 assumes roughly a 6x protected-culture multiplier under raft
    # culture, the same kind of step water spinach takes, and sits under water spinach's
    # 16. The multiplier, not the trial, is the soft link in that chain.
    yield_kg_per_m2_year=14.0,
    edible_protein_pct=1.8,
    ph_min=5.5,
    ph_max=7.0,
    # Grows between 10 and 35 °C with ideal 23-27 °C (FAO Ecocrop 2022); disappointing
    # below 27 °C and best above it (UF/IFAS HS1371, Cornell). temp_max 35 ties water
    # spinach and amaranth for the heat ceiling; what sets it apart is the floor —
    # it refuses cool weather rather than merely surviving heat.
    temp_min_c=20.0,
    temp_max_c=35.0,
    source=("FAO Ecocrop (GAEZ v4, 2022; via ECHO EDN #172) for the 10-35 °C growth "
        "band, 23-27 °C optimum and pH 5.5-7 preference; "
        "UF/IFAS 'Florida Cultivation Guide for Malabar Spinach' (HS1371) and "
        "Cornell Home Gardening guide for heat preference and poor cool-season "
        "growth; USDA FoodData Central (fdc 119643, malabar spinach raw, 1.8 g "
        "protein/100 g fresh); UVI leaf-yield trials (Palada & Crossman 1999, "
        "Perspectives on New Crops, Table 1: 344 g/m2 over 57 days, 6.04 g/m2/day, "
        "about 2.2 kg/m2/yr in the field; also Palada et al. 1996; Palada & Crossman "
        "1998; Palada, Davis & Crossman 1999, CFCS 35:178-182) for yield base. "
        "Yield 14 assumes about a 6x protected-culture multiplier under raft "
        "culture; the multiplier, not the trial, is the soft link. FRR placed in "
        "FAO 589 / UVI leafy band (not measured for this species)."),
)

# MORINGA (Moringa oleifera) — the drumstick tree grown as a cut-and-come-again leaf
# crop, the seventh wanted heat-tolerant leafy (#125). What sets it apart from the
# other three is drought: it carries hot AND dry, where amaranth, water spinach and
# malabar spinach still want irrigation.
MORINGA = Crop(
    name="moringa",
    category="leafy",
    # No Moringa-specific feeding-rate ratio is published for aquaponics. Placed in
    # the FAO 589 / UVI leafy band (~40-100 g/m2/day) alongside amaranth, water
    # spinach and malabar spinach: fast regrowth after cutting, repeated harvests.
    frr_g_per_m2_day=65.0,
    frr_low=45.0,
    frr_high=90.0,
    n_uptake_g_per_m2_day=0.9,
    # Field yield from the only multi-harvest density trial we could reach: aboveground
    # dry biomass 527-2867 kg/ha per cutting, across four harvests in ~14 months
    # (Mabapa et al. 2017, Int. J. Agronomy 2941432, northern South Africa; no
    # fertilizer, dryland — smallholder conditions; per-harvest ranges in Fig. 2,
    # e.g. Ofcolaco harvest 1: 1185-2867 kg/ha, harvest 3: 527-1035 kg/ha).
    # Annualized: 4 cuttings / (14/12 yr) ≈ 3.4 cuts/yr → ~1.8-9.8 t/ha/yr of dry
    # shoot. Leaf is a fraction of the shoot — take ~40% for cut green-matter
    # culture — and dry leaf is ~20-25% of fresh mass, so the field fresh-leaf
    # equivalent sits around ~2.9-19.7 t/ha/yr. The registered 14 (=140 t/ha/yr)
    # therefore sits roughly 7-48x above the field trial — a step of the same
    # order as the intensification steps its leafy siblings take, and it lands
    # under water spinach's 16. The multiplier, not the trial, is the soft link
    # in that chain.
    yield_kg_per_m2_year=14.0,
    # Highest-protein leafy entry in the database: USDA FDC "Drumstick leaves, raw"
    # carries 9.4 g protein/100 g fresh — about triple the leafy greens around it.
    edible_protein_pct=9.4,
    ph_min=5.5,
    ph_max=7.0,
    # The measured part is the optimum: 25-35 °C, with survival to 48 °C for limited
    # periods (Trigo et al. 2021). What sets it apart from its heat-tolerant siblings
    # is the floor — 20 °C is the warmest here, because a chilling-sensitive tropical
    # species grows poorly well below its optimum. Light-frost survival is real but
    # belongs to the tree, not to a leaf-growth band, so it is not modelled.
    temp_min_c=20.0,
    temp_max_c=35.0,
    source=("Mabapa et al. 2017 (Mabapa, Ayisi & Mariga), Int. J. Agronomy 2941432 "
        "(northern South Africa, four harvests in ~14 months): aboveground dry biomass "
        "527-2867 kg/ha per cutting (per-harvest ranges in the paper's Fig. 2), "
        "no fertilizer, dryland, for the yield base; "
        "Trigo et al. 2021, Foods 10(1):31: 'The optimum temperature range is 25-35 °C "
        "and it can even withstand 48 °C for a limited period of time', 3-5 leaf cuts "
        "per season; USDA FoodData Central 'Drumstick leaves, raw' (9.4 g protein/100 g "
        "fresh) for protein. FRR placed in FAO 589 / UVI leafy band (not measured for "
        "this species). Yield 14: the trial's ~2.9-19.7 t/ha/yr field fresh-leaf "
        "equivalent (4 cuttings in ~14 months, ~40% leaf share, 20-25% dry-to-fresh) "
        "times a ~7-48x protected-culture multiplier; the "
        "multiplier, not the trial, is the soft link. pH 5.5-7.0 placed: the cited "
        "trial ran on soils of pH(KCl) 5.1-7.0 across its two sites and produced "
        "throughout; no species-level pH requirement was reached in the sources above. "
        "temp_min 20 °C placed: chilling-sensitive tropical species, growth poor below "
        "~20 °C."),)

# ETHIOPIAN KALE (Brassica carinata) — the leafy brassica that carries real heat. The
# database already has kale, collards and mustard greens, and every one of them stops at
# 24-27 °C; this is the same slot filled for amaranth in #104, one genus over (#125).
ETHIOPIAN_KALE = Crop(
    name="ethiopian_kale",
    category="leafy",
    # No B. carinata-specific feeding-rate ratio is published for aquaponics. Placed in
    # the FAO 589 / UVI leafy band (~40-100 g/m2/day) at the collard-greens seed point —
    # the closest analogue we carry: a heavy-feeding leafy brassica harvested by repeated
    # defoliation (PROTA: leaf picking every ~2 weeks at 50% defoliation).
    frr_g_per_m2_day=72.0,
    frr_low=50.0,
    frr_high=95.0,
    n_uptake_g_per_m2_day=0.95,
    # Field yield is measured, not placed: PROTA (Mnzava & Schippers 2007) gives an
    # average farmer leaf-and-shoot yield of 35 t/ha per crop, with 50-55 t/ha at
    # research stations. 35 t/ha is 3.5 kg/m2; 14 is the chain 3.5 kg/m2 x 4x — the
    # same ~4x protected-culture multiplier the leafy siblings use (amaranth ~4-5x,
    # malabar spinach ~6x). Held at 14 — under water spinach (16), level with malabar
    # spinach. The multiplier is the soft link; the trial is measured.
    yield_kg_per_m2_year=14.0,
    # PROTA states explicitly that leaf nutritional composition for B. carinata is not
    # published and is "probably comparable to Brassica juncea" — so the protein number
    # is inherited from our mustard-greens entry (2.7), with that caveat recorded here.
    edible_protein_pct=2.7,
    ph_min=5.5,
    ph_max=7.0,
    # Heat tolerance is the reason this entry exists, and the band is built from sourced
    # endpoints, not one source: ECHO's Mutarda carinata production profile states
    # "Temperature range: 15-20° C" for the leafy use, and OMAFRA's Specialty Cropportunities
    # (Carinata) gives "Optimal Temperature Range: 20-30˚C" with "Frost Tolerant, heat
    # tolerant" — that 30 ceiling is the species-level optimum (measured for oilseed
    # agronomy), placed on the leafy use here. PROTA describes the ecology (highland to
    # 2600 m, lowland warm-and-dry, daylength neutral, no vernalization) but states no
    # numbers. temp_max 30 therefore claims no amaranth-class 35 °C headroom; the OMAFRA
    # 30 ceiling and ECHO's 15 floor are what the sources actually support. Daylength
    # neutrality and the lack of a vernalization requirement are what fit it to year-round
    # equatorial raft culture.
    temp_min_c=15.0,
    temp_max_c=30.0,
    source=("PROTA4U, Mnzava & Schippers 2007 ('Brassica carinata A.Braun', PROTA4U "
        "record, van der Vossen & Mkamilo eds): farmer leaf+shoot yield ~35 t/ha "
        "(research stations 50-55 t/ha) for the yield base, highland-to-lowland "
        "versatility and daylength neutrality for the climate description, and the "
        "explicit statement that leaf nutritional composition is unpublished ('probably "
        "comparable to Brassica juncea') — protein 2.7 inherited from mustard_greens on "
        "that basis. Temperature: ECHO production notes (Mutarda carinata, "
        "echocommunity.org) state 'Temperature range: 15-20° C' (floor placed from it); "
        "OMAFRA Specialty Cropportunities (Carinata) states 'Optimal Temperature Range: "
        "20-30˚C' for the species — the 30 ceiling placed on the leafy use from it, the "
        "union of the two sourced endpoints. pH 5.5-7.0 is the FAO 589 / UVI leafy-band "
        "default, not species-measured. Leaf harvest every ~2 weeks at 50% defoliation "
        "per PROTA agronomy section. Yield 14 = 3.5 kg/m2 (35 t/ha per crop) x 4x: "
        "the same ~4x protected-culture multiplier the leafy siblings use (amaranth "
        "~4-5x, malabar spinach ~6x); the multiplier is the soft link, the trial is "
        "measured. FRR "
        "placed at the collard-greens seed point in the FAO 589 / UVI leafy band (not "
        "measured for this species)."),
)

# ROSELLE (Hibiscus sabdariffa) — the hibiscus of karkade tea, grown as a leafy vegetable
# across the Sahel. The last of #125's wanted crops that fits raft culture without special
# pleading: a heat-tolerant leafy harvested by repeated branch picking, leaf harvest from
# 6-8 weeks after sowing.
ROSELLE = Crop(
    name="roselle",
    category="leafy",
    # No H. sabdariffa-specific feeding-rate ratio is published for aquaponics. Placed in
    # the FAO 589 / UVI leafy band (~40-100 g/m2/day) at the water-spinach seed point —
    # the closest analogue we carry: a fast tropical leafy harvested by repeated picking
    # (PROTA: branches ~50 cm picked 2-3 times per vegetative period, which stimulates
    # branching and raises leaf production).
    frr_g_per_m2_day=65.0,
    frr_low=45.0,
    frr_high=90.0,
    n_uptake_g_per_m2_day=0.9,
    # Field yield is measured, not placed: PROTA reports leafy-branch yields of up to
    # 20 t/ha from three cuttings. 20 t/ha is 2.0 kg/m2 per cycle; roselle is a 4-6 month
    # annual, so ~1.5 cycles/yr is assumed; 2.0 x 1.5 x 4x protected-culture multiplier
    # (the same ~4x the leafy siblings use) = 12. Held at 12 — below amaranth and water
    # spinach (16), below malabar spinach and moringa (14). PROTA's "much lower and
    # variable" African averages belong to its calyx section (Sudan 93 kg/ha and Senegal
    # 500 kg/ha are dry calyces), so they do not bear on leaf yield; 12 stands on the
    # reported-maximum reason alone — the 20 t/ha is a maximum, not an average. The
    # multiplier and the cycle count are the soft links; the trial figure is measured.
    yield_kg_per_m2_year=12.0,
    # PROTA (Leung, Busson & Jardin 1968 composition table) gives leaf protein 3.3 g per
    # 100 g edible portion — above every heat-tolerant leafy-green sibling (amaranth 2.5,
    # water spinach 2.6, malabar spinach 1.8, Ethiopian kale 2.7); only moringa (9.4,
    # its own leafy-legume class) sits higher.
    edible_protein_pct=3.3,
    ph_min=5.5,
    ph_max=7.0,
    # The band is measured, not placed — PROTA states it directly: "temperature
    # requirements ranging between 18°C and 35°C", with growth stopping at 14°C. The 18
    # floor is why roselle is a heat crop; 35 matches amaranth's ceiling (the only sibling
    # that reaches it), but here it is the source's own upper endpoint, not headroom.
    # Daylength sensitivity touches the leaf harvest too: roselle is a short-day plant
    # and PROTA states it "requires 13 hours/day light during vegetative growth to
    # prevent premature flowering". Across the Sahel and the tropics days run roughly
    # 11-13 h, so in the short-day months it flowers early and the picking window
    # closes. The model does not account for photoperiod — the 1.5 cycles/yr in the
    # yield chain assumes the plant stays vegetative, which the field does not
    # guarantee year-round.
    temp_min_c=18.0,
    temp_max_c=35.0,
    source=("PROTA4U, McClintock & El Tahir 2011 ('Hibiscus sabdariffa L.', PROTA4U "
        "record, Brink & Achigan-Dako eds): 'temperature requirements ranging between "
        "18°C and 35°C' with growth stopping at 14°C for the band; leafy-branch yields "
        "'up to 20 t/ha from three cuttings' for the yield base; leaf protein 3.3 g/100 g "
        "from the Leung, Busson & Jardin 1968 composition table it cites. Leaf harvest "
        "6-8 weeks after sowing, branches ~50 cm picked 2-3 times per vegetative period. "
        "PROTA also states roselle 'requires 13 hours/day light during vegetative growth "
        "to prevent premature flowering' (short-day plant, flowers best under 12 h days): "
        "across the Sahel and the tropics days run roughly 11-13 h, so in short-day "
        "months it flowers early and the picking window closes — the model does not "
        "account for photoperiod, and the 1.5 cycles/yr assumes the plant stays "
        "vegetative. Yield 12 = 2.0 kg/m2 (20 t/ha per cycle) x 1.5 cycles/yr x 4x: the "
        "same protected-culture multiplier the leafy siblings use, with the cycle count "
        "as a second soft link; the trial figure is measured and held below the 14-16 "
        "siblings because the 20 t/ha is a reported maximum, not an average (PROTA's "
        "'much lower and variable' African averages sit in its calyx section — Sudan 93 "
        "kg/ha and Senegal 500 kg/ha are dry calyces — not its leaf-yield sentence). FRR "
        "placed at the water-spinach seed point in the FAO 589 / UVI leafy band (not "
        "measured for this species). pH 5.5-7.0 is the FAO 589 / UVI leafy-band default, "
        "not species-measured."),
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
        # heat-tolerant
        AMARANTH,
        WATER_SPINACH,
        MALABAR_SPINACH,
        MORINGA,
        ETHIOPIAN_KALE,
        ROSELLE,
    )
}


def get_crop(name: str) -> Crop:
    key = (name or "").strip().lower()
    if key not in CROPS:
        raise KeyError(f"Unknown crop {name!r}. Known: {sorted(CROPS)}")
    return CROPS[key]
