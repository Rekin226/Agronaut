"""Single source of truth for row provenance (#214).

Two halves:

1. The UNMIGRATED_* registries: entity rows that still construct with the row-level
   `source` string only. The lists can only shrink — a row migrates by giving it a
   `provenance` map, and when it does its name must be REMOVED here in the same
   change (the tests enforce both directions). Rows are listed once per table, so
   a new row added without provenance fails `test_unmigrated_rows_exist` until it
   is listed, and listing a row that already has provenance fails
   `test_unmigrated_rows_are_really_unmigrated`.

2. The derived migration sets the narrowed unsourced-constant guard consumes
   (MIGRATED_CROPS / MIGRATED_SPECIES / MIGRATED_SYSTEM_TYPES, and TABLES, which
   binds each table's rows to the numeric fields a provenance map must cover).

Kept as a module (not test-file constants) so the guard scanner and the
provenance tests read the SAME lists and can never drift apart.
"""

from __future__ import annotations

from aqua_model.crops import CROPS
from aqua_model.species import SPECIES
from aqua_model.system_types import SYSTEM_TYPES

# Rows not yet migrated to per-field provenance. Shrink-only: remove a name when
# (and only when) that row carries a complete provenance map.
UNMIGRATED_CROPS: set[str] = {
    "amaranth", "arugula", "basil", "broccoli", "cabbage", "cauliflower",
    "celery", "chives", "cilantro", "collard_greens", "cucumber", "dill",
    "eggplant", "ethiopian_kale", "green_bean", "kale", "lettuce",
    "malabar_spinach", "mint", "mustard_greens", "okra", "oregano",
    "pak_choi", "parsley", "pea", "pepper", "sage", "spinach", "strawberry",
    "swiss_chard", "tomato", "water_spinach", "watercress", "zucchini",
}
UNMIGRATED_SPECIES: set[str] = {
    "barramundi", "carp", "channel_catfish", "clarias", "freshwater_prawn",
    "pangasius", "silver_perch", "tambaqui", "trout",
}
UNMIGRATED_SYSTEM_TYPES: set[str] = {
    "media_bed", "nft", "vertical_tower",
}

# Numeric fields whose values a provenance map must account for, per table.
# `name`, `category`/`key` and `source` are identity/provenance carriers, not
# modelled quantities, so they stay out. `considerations` is a list of strings.
CROP_NUMERIC_FIELDS: tuple[str, ...] = (
    "frr_g_per_m2_day", "frr_low", "frr_high", "n_uptake_g_per_m2_day",
    "yield_kg_per_m2_year", "edible_protein_pct", "ph_min", "ph_max",
    "temp_min_c", "temp_max_c",
)
SPECIES_NUMERIC_FIELDS: tuple[str, ...] = (
    "feeding_rate_pct_bw", "fcr", "feed_protein_pct", "body_protein_pct",
    "harvest_weight_kg", "stocking_density_kg_m3", "temp_min_c",
    "temp_opt_low_c", "temp_opt_high_c", "temp_max_c",
)
SYSTEM_TYPE_NUMERIC_FIELDS: tuple[str, ...] = (
    "water_depth_m", "water_depth_low", "water_depth_high",
    "lift_height_m", "lift_low", "lift_high",
)

# (table name -> (rows, numeric fields)) — the loop driver for both consumers.
TABLES: dict[str, tuple[dict, tuple[str, ...]]] = {
    "crops": (CROPS, CROP_NUMERIC_FIELDS),
    "species": (SPECIES, SPECIES_NUMERIC_FIELDS),
    "system_types": (SYSTEM_TYPES, SYSTEM_TYPE_NUMERIC_FIELDS),
}

MIGRATED_CROPS: frozenset[str] = frozenset(CROPS) - UNMIGRATED_CROPS
MIGRATED_SPECIES: frozenset[str] = frozenset(SPECIES) - UNMIGRATED_SPECIES
MIGRATED_SYSTEM_TYPES: frozenset[str] = frozenset(SYSTEM_TYPES) - UNMIGRATED_SYSTEM_TYPES

# Label prefixes a provenance value must start with (#214's vocabulary).
PROVENANCE_LABELS: tuple[str, ...] = ("measured:", "placed:", "inherited:")
