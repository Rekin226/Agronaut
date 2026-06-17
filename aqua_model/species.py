"""Fish species database — seed defaults, cited and ranged.

v1 palette is intentionally small. Tilapia is the best-characterized aquaponics fish
(FAO 589 uses it as the reference species); the others are coarser stubs to be
calibrated. Every value should eventually be replaced by measured data per system.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FishSpecies:
    name: str
    feeding_rate_pct_bw: float    # % of body weight fed per day at grow-out (temp-adjusted at use)
    fcr: float                    # feed conversion ratio (g feed / g growth)
    feed_protein_pct: float       # % protein in the feed used for this species
    body_protein_pct: float       # % protein of fish wet body mass (for N-retention)
    harvest_weight_kg: float      # target individual weight at harvest
    stocking_density_kg_m3: float # rearing-tank biomass density
    temp_min_c: float             # viable water-temperature window
    temp_opt_low_c: float
    temp_opt_high_c: float
    temp_max_c: float
    source: str


# Tilapia: the FAO 589 / UVI reference species. Best-supported numbers.
TILAPIA = FishSpecies(
    name="tilapia",
    feeding_rate_pct_bw=1.5, fcr=1.7, feed_protein_pct=32.0,
    body_protein_pct=16.0, harvest_weight_kg=0.5, stocking_density_kg_m3=20.0,
    temp_min_c=14.0, temp_opt_low_c=27.0, temp_opt_high_c=30.0, temp_max_c=36.0,
    source="FAO589/UVI",
)

# African catfish (Clarias gariepinus): air-breather, very feed-efficient, tolerates high
# density. Split out from the old generic "catfish" stub (FCR ~0.8-1.0, not 1.6).
CLARIAS = FishSpecies(
    name="clarias",
    feeding_rate_pct_bw=1.5, fcr=0.9, feed_protein_pct=35.0,
    body_protein_pct=16.0, harvest_weight_kg=0.6, stocking_density_kg_m3=60.0,
    temp_min_c=15.0, temp_opt_low_c=26.0, temp_opt_high_c=30.0, temp_max_c=35.0,
    source="LIT",
)

# Channel catfish (Ictalurus punctatus): the US pond-aquaculture standard. Less efficient than
# Clarias (FCR ~1.5-2.0) and stocked at lower density.
CHANNEL_CATFISH = FishSpecies(
    name="channel_catfish",
    feeding_rate_pct_bw=1.5, fcr=1.7, feed_protein_pct=32.0,
    body_protein_pct=16.0, harvest_weight_kg=0.7, stocking_density_kg_m3=25.0,
    temp_min_c=10.0, temp_opt_low_c=26.0, temp_opt_high_c=30.0, temp_max_c=34.0,
    source="LIT",
)

# Trout: cold-water. Included to make the temperature logic meaningful. Coarse stub.
TROUT = FishSpecies(
    name="trout",
    feeding_rate_pct_bw=1.2, fcr=1.2, feed_protein_pct=42.0,
    body_protein_pct=18.0, harvest_weight_kg=0.4, stocking_density_kg_m3=20.0,
    temp_min_c=5.0, temp_opt_low_c=12.0, temp_opt_high_c=18.0, temp_max_c=21.0,
    source="LIT",
)

SPECIES: dict[str, FishSpecies] = {
    s.name: s for s in (TILAPIA, CLARIAS, CHANNEL_CATFISH, TROUT)
}


def get_species(name: str) -> FishSpecies:
    key = (name or "").strip().lower()
    if key not in SPECIES:
        raise KeyError(
            f"Unknown fish species {name!r}. Known: {sorted(SPECIES)}"
        )
    return SPECIES[key]


def temperature_feed_factor(species: FishSpecies, temperature_c: float) -> float:
    """Scale feeding rate by how close water temp is to the species optimum.

    Fish eat most in their optimal band and progressively less toward their limits.
    Outside the viable window the factor floors at a small positive value (they barely
    eat) rather than zero, so sizing degrades gracefully instead of dividing by zero.
    Returns a multiplier in [0.25, 1.0]. This is a coarse model (see not_modeled).
    """
    t = temperature_c
    if species.temp_opt_low_c <= t <= species.temp_opt_high_c:
        return 1.0
    if t <= species.temp_min_c or t >= species.temp_max_c:
        return 0.25
    if t < species.temp_opt_low_c:
        span = species.temp_opt_low_c - species.temp_min_c
        frac = (t - species.temp_min_c) / span if span > 0 else 1.0
    else:
        span = species.temp_max_c - species.temp_opt_high_c
        frac = (species.temp_max_c - t) / span if span > 0 else 1.0
    return round(0.25 + 0.75 * max(0.0, min(1.0, frac)), 3)
