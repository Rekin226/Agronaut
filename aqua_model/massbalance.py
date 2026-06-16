"""Mass balance: nitrogen CONSISTENCY CHECK, water balance, biofilter sizing.

  FEED  ──FRR sizes──▶  the system (see sizing.py)
    │
    │  nitrogen flows (THIS module — a CHECK, not a sizing path):
    ▼
  N_fed = feed * protein% * 0.16
    │
    ├─ N_retained = growth * body_protein% * 0.16   (growth = feed / FCR)
    │
    └─ N_excreted = N_fed - N_retained
          │
          ├─ plants take up ~30-50% (PLANT_N_UPTAKE_FRACTION)   ◀── checked vs FRR area
          └─ the rest leaves via solids / water-exchange / denitrification
                                  (estimated INDEPENDENTLY, not as a residual)

The check never RESIZES anything. It asks: does the FRR-sized grow area roughly match the
area the nitrogen flow implies? If they disagree beyond tolerance, it raises a flag instead
of silently reconciling. (This is what makes the test non-vacuous: sinks are independent.)
"""

from __future__ import annotations

from . import coefficients as C
from .crops import Crop
from .species import FishSpecies


# Independent estimates of where excreted N goes (NOT a residual). These are coarse
# literature fractions; they must sum to ~1.0 with the plant-uptake fraction so the
# balance is a real check, not a tautology.
SOLIDS_REMOVAL_FRACTION = 0.35   # settleable solids / sludge removal
WATER_EXCHANGE_FRACTION = 0.20   # leaves with purge / overflow water
DENITRIFICATION_FRACTION = 0.05  # anoxic loss


def nitrogen_check(
    feed_g_per_day: float,
    species: FishSpecies,
    crop: Crop,
    frr_grow_area_m2: float,
) -> dict:
    """Compare the FRR-sized grow area against the area implied by nitrogen flow.

    Returns a dict with the full balance plus an `agreement` verdict. Never mutates sizing.
    """
    n_frac = C.N_FRACTION_OF_PROTEIN.value
    uptake_frac = C.PLANT_N_UPTAKE_FRACTION.value

    n_fed = feed_g_per_day * (species.feed_protein_pct / 100.0) * n_frac
    growth_g_per_day = feed_g_per_day / species.fcr
    n_retained = growth_g_per_day * (species.body_protein_pct / 100.0) * n_frac
    n_excreted = max(0.0, n_fed - n_retained)

    # Independent sink estimates (NOT residual).
    n_plant = n_excreted * uptake_frac
    n_solids = n_excreted * SOLIDS_REMOVAL_FRACTION
    n_water = n_excreted * WATER_EXCHANGE_FRACTION
    n_denitri = n_excreted * DENITRIFICATION_FRACTION
    n_sinks_total = n_plant + n_solids + n_water + n_denitri

    # Area the nitrogen flow implies, given the crop's uptake rate.
    if crop.n_uptake_g_per_m2_day > 0:
        n_implied_area = n_plant / crop.n_uptake_g_per_m2_day
    else:
        n_implied_area = float("inf")

    # Do the two independent methods agree? (FRR area vs N-implied area.)
    if frr_grow_area_m2 > 0 and n_implied_area not in (0.0, float("inf")):
        disagreement = abs(n_implied_area - frr_grow_area_m2) / frr_grow_area_m2
    else:
        disagreement = float("inf")
    agrees = disagreement <= 0.35  # within 35% is "consistent" for a seed model

    # How close do the independently-estimated sinks come to closing the excreted N?
    # (A real check: if this is ~0, our sink fractions are self-consistent; large means
    # our independent estimates don't add up and the model needs calibration.)
    closure_residual = n_excreted - n_sinks_total

    return {
        "n_fed_g_day": round(n_fed, 2),
        "n_retained_g_day": round(n_retained, 2),
        "n_excreted_g_day": round(n_excreted, 2),
        "n_plant_uptake_g_day": round(n_plant, 2),
        "n_solids_g_day": round(n_solids, 2),
        "n_water_exchange_g_day": round(n_water, 2),
        "n_denitrification_g_day": round(n_denitri, 2),
        "closure_residual_g_day": round(closure_residual, 2),
        "frr_grow_area_m2": round(frr_grow_area_m2, 2),
        "n_implied_area_m2": round(n_implied_area, 2) if n_implied_area != float("inf") else None,
        "disagreement_fraction": round(disagreement, 3) if disagreement != float("inf") else None,
        "agrees": agrees,
        "flag": None if agrees else (
            "FRR sizing and nitrogen balance disagree by "
            f"{round(disagreement * 100)}% — calibrate coefficients or check inputs."
        ),
    }


def water_balance(grow_area_m2: float, tank_surface_m2: float, rainfall_lpd: float = 0.0) -> dict:
    """Daily makeup water = evapotranspiration + tank evaporation + sludge loss - rainfall.

    Rainfall defaults to 0 (covered/controlled system) — counting uncaptured rain would be
    fantasy accounting. The result drives the water-budget feasibility check.
    """
    et = grow_area_m2 * C.EVAPOTRANSPIRATION_RATE.value
    evap = tank_surface_m2 * C.TANK_EVAPORATION_RATE.value
    sludge = 0.05 * (et + evap)  # small loss with solids removal
    makeup = max(0.0, et + evap + sludge - rainfall_lpd)
    return {
        "evapotranspiration_lpd": round(et, 1),
        "tank_evaporation_lpd": round(evap, 1),
        "sludge_loss_lpd": round(sludge, 1),
        "rainfall_lpd": round(rainfall_lpd, 1),
        "makeup_water_lpd": round(makeup, 1),
    }


def biofilter_media_m2(feed_g_per_day: float, species: FishSpecies) -> float:
    """Required nitrifying media area, sized to TAN production with a safety factor.

    Conservative by decision: tank/raft surfaces also nitrify but are NOT counted here, so
    we never undersize. TAN production is approximated as the excreted-N rate.
    """
    n_frac = C.N_FRACTION_OF_PROTEIN.value
    n_fed = feed_g_per_day * (species.feed_protein_pct / 100.0) * n_frac
    n_retained = (feed_g_per_day / species.fcr) * (species.body_protein_pct / 100.0) * n_frac
    tan_g_day = max(0.0, n_fed - n_retained)
    media = tan_g_day / C.NITRIFICATION_RATE.value
    return round(media * C.SAFETY_FACTOR.value, 2)
