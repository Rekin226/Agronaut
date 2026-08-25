"""Fish growth in time: from "feed becomes biomass" to "a cohort on a growth curve".

The twin (`twin.py`) grows fish by pure FCR arithmetic — every gram of feed becomes 1/FCR
grams of fish, at any temperature, at any size. That is the right simplification for nitrogen
(feed drives excretion either way), and the wrong one for the question an operator plans
around: "when do my fish reach harvest weight, HERE, with MY water temperature?"

This module answers with the Thermal-unit Growth Coefficient (TGC) model
(Iwama & Tautz 1981; Cho 1992; reviewed critically by Jobling 2003):

    W2^(1/3) = W1^(1/3) + (TGC / 1000) * sum(T_c * dt)

The cube-root form captures the empirical regularity that absolute growth accelerates with
size while relative growth slows, and temperature enters as accumulated degree-days. TGC is
species- and system-specific: published values are seeds to CALIBRATE against a logged pond
(the same doctrine as every coefficient in this package), not universal constants.

Feed is the other constraint: growth cannot exceed what the eaten ration supports through the
FCR. The step takes min(thermal potential, feed-limited growth) — an underfed cohort grows
like an underfed cohort, whatever the water temperature says.

Pure and deterministic; the trust-zone rules hold.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .coefficients import Coefficient
from .species import FishSpecies, temperature_feed_factor

NOT_MODELLED = (
    "mortality and disease (the count only changes when you change it)",
    "size dispersion within a cohort (mean weight only; graders exist for a reason)",
    "reproduction (tilapia in a warm tank WILL spawn; this model does not)",
    "dissolved-oxygen limitation on intake (assumes aeration holds DO above limiting)",
    "compensatory growth after a restriction",
)

# TGC seeds by species key, in the x1000 convention above (g^1/3 per degC-day).
# Values are literature-typical for the rearing systems named in the source; calibrate
# against your own harvest records before trusting a date.
# Sanity anchor for the warm-water values: growing Nile tilapia from 1 g to 500 g in ~7
# months at ~28 C is a good outcome, and back-computes to TGC ~= (500^(1/3)-1)/(28*210)*1000
# ~= 1.2. Numbers far above that imply harvests measured in weeks, which nobody achieves.
TGC = {
    "tilapia": Coefficient(
        name="tilapia.tgc", value=1.3, low=0.8, high=1.9, unit="g^1/3/(C·d) x1000",
        source="LIT: back-computed from typical Nile tilapia culture durations "
               "(1 g -> 500 g in 6-9 months at 26-30 C, pond to good RAS; cf. FAO 589 "
               "production timelines); calibrate to your harvest records"),
    "clarias": Coefficient(
        name="clarias.tgc", value=1.7, low=1.1, high=2.3, unit="g^1/3/(C·d) x1000",
        source="LIT: back-computed from African catfish farm performance "
               "(5 g -> 1 kg in ~6 months at 27-30 C; cf. Hogendoorn 1983 growth trials)"),
    "channel_catfish": Coefficient(
        name="channel_catfish.tgc", value=1.0, low=0.6, high=1.5, unit="g^1/3/(C·d) x1000",
        source="LIT: back-computed from pond culture reaching ~0.6 kg over one to two warm "
               "seasons (Tucker & Hargreaves 2004, Biology and Culture of Channel Catfish)"),
    "trout": Coefficient(
        name="trout.tgc", value=1.8, low=1.4, high=2.2, unit="g^1/3/(C·d) x1000",
        source="Cho (1992), Aquaculture 100: rainbow trout TGC 1.4-2.2 in well-run culture "
               "at 8-15 C"),
    "carp": Coefficient(
        name="carp.tgc", value=1.0, low=0.6, high=1.6, unit="g^1/3/(C·d) x1000",
        source="LIT: back-computed from common carp reaching 1-1.5 kg over two pond "
               "seasons; wide range reflects pond productivity"),
}
_TGC_DEFAULT = Coefficient(
    name="generic.tgc", value=1.2, low=0.7, high=1.9, unit="g^1/3/(C·d) x1000",
    source="LIT: mid-range warm-water default for species without a published TGC; "
           "calibrate before trusting")


def tgc_for(species_key: str) -> Coefficient:
    return TGC.get(species_key, _TGC_DEFAULT)


@dataclass(frozen=True)
class Cohort:
    """A batch of fish stocked together and assumed to share a mean weight."""

    count: int
    mean_weight_g: float

    def biomass_kg(self) -> float:
        return self.count * self.mean_weight_g / 1000.0


@dataclass(frozen=True)
class GrowthStep:
    cohort: Cohort
    feed_eaten_g: float          # what the fish actually took, after temperature gating
    feed_offered_g: float
    thermally_limited: bool      # True when temperature (not ration) capped growth


# Juveniles eat a larger fraction of their body weight than harvest-size fish — every feed
# chart says so. The published feeding_rate_pct_bw is anchored at harvest size; we scale it
# with (W_harvest/W)^(1/3), which makes the ration go as W^(2/3) — the same surface-law shape
# the TGC growth curve follows, so ration and growth potential stay consistent across sizes
# instead of starving fingerlings on an adult percentage. Capped at 10% BW/day (feed charts
# rarely exceed it, and beyond it feed goes to waste, not fish).
_MAX_PCT_BW_DAY = 10.0


def ration_g_day(cohort: Cohort, species: FishSpecies, temperature_c: float) -> float:
    """Daily ration the operator would feed: size-scaled %BW/day, gated by temperature.

    At harvest weight this equals the species' published feeding rate — the same number
    sizing uses, so twin and calculator agree where they overlap."""
    harvest_g = species.harvest_weight_kg * 1000.0
    scale = (harvest_g / cohort.mean_weight_g) ** (1.0 / 3.0) if cohort.mean_weight_g > 0 else 1.0
    pct = min(_MAX_PCT_BW_DAY, species.feeding_rate_pct_bw * max(1.0, scale))
    base = cohort.biomass_kg() * 1000.0 * pct / 100.0
    return base * temperature_feed_factor(species, temperature_c)


def grow(cohort: Cohort, species: FishSpecies, species_key: str, *,
         temperature_c: float, dt_days: float = 1.0,
         feed_offered_g: float | None = None,
         tgc_value: float | None = None) -> GrowthStep:
    """One growth step: thermal potential, capped by what the eaten feed supports.

    `feed_offered_g` is the ration for the WHOLE step (defaults to the standard ration);
    fish eat up to their temperature-gated appetite. `tgc_value` overrides the seed —
    that is the calibration hook."""
    if dt_days <= 0:
        raise ValueError("dt_days must be positive")
    if cohort.count <= 0 or cohort.mean_weight_g <= 0:
        return GrowthStep(cohort=cohort, feed_eaten_g=0.0,
                          feed_offered_g=feed_offered_g or 0.0, thermally_limited=False)

    appetite = ration_g_day(cohort, species, temperature_c) * dt_days
    offered = appetite if feed_offered_g is None else feed_offered_g
    eaten = min(offered, appetite)

    # Thermal growth potential. Below the species' minimum the TGC relationship is not
    # credible (intake stops); the temperature factor already zeroed the eaten ration there.
    tgc = (tgc_value if tgc_value is not None else tgc_for(species_key).value) / 1000.0
    w = cohort.mean_weight_g
    if temperature_c > species.temp_min_c:
        w_potential = (w ** (1.0 / 3.0) + tgc * temperature_c * dt_days) ** 3.0
    else:
        w_potential = w
    potential_gain_g = (w_potential - w) * cohort.count

    # Feed-supported gain through the FCR: eating less than the full ration slows growth
    # proportionally. `eaten/appetite` also carries the temperature gate into growth.
    feed_gain_g = (eaten / species.fcr) if species.fcr > 0 else potential_gain_g
    gain_g = max(0.0, min(potential_gain_g, feed_gain_g))
    new_w = w + gain_g / cohort.count

    return GrowthStep(
        cohort=replace(cohort, mean_weight_g=new_w),
        feed_eaten_g=eaten,
        feed_offered_g=offered,
        thermally_limited=potential_gain_g < feed_gain_g,
    )


def _cbrt_rate_per_day(species: FishSpecies, species_key: str, temperature_c: float,
                       tgc_value: float | None = None) -> float:
    """Growth rate of W^(1/3), per day — the quantity that is constant in this model.

    Both constraints scale identically with size (thermal potential and the W^(2/3) feed
    chart), so their minimum is scale-free and the whole trajectory has a closed form.
    This is the single place the binding constraint is decided; `grow` and
    `days_to_weight` both agree with it by construction."""
    tgc = (tgc_value if tgc_value is not None else tgc_for(species_key).value) / 1000.0
    thermal = tgc * temperature_c if temperature_c > species.temp_min_c else 0.0
    harvest_g = species.harvest_weight_kg * 1000.0
    chart = (species.feeding_rate_pct_bw / 100.0 * harvest_g ** (1.0 / 3.0)
             / (3.0 * species.fcr) * temperature_feed_factor(species, temperature_c)
             ) if species.fcr > 0 else thermal
    return max(0.0, min(thermal, chart))


def days_to_weight(start_g: float, target_g: float, species_key: str, temperature_c: float,
                   species: FishSpecies | None = None) -> float:
    """Closed-form planning answer at constant temperature and full ration.

    Uses the same binding rate as the stepped model — min(thermal TGC, what the feed chart
    supports through the FCR) — so simulating and planning give the same date. Real seasons
    are not constant-temperature; treat this as the fair-weather estimate."""
    if target_g <= start_g:
        return 0.0
    if species is None:
        from .species import get_species
        species = get_species(species_key)
    rate = _cbrt_rate_per_day(species, species_key, temperature_c)
    if rate <= 0:
        return float("inf")
    return (target_g ** (1.0 / 3.0) - start_g ** (1.0 / 3.0)) / rate
