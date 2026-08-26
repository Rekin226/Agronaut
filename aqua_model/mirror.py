"""The live mirror: one user's twin state, persisted, advanced through real time, and
pulled toward what they actually measure.

"Live" here does not mean a daemon. It means the twin state carries a calendar date, and
every conversation advances it through the weather that actually happened since — then
forward through the forecast. Between conversations nothing runs and nothing needs to;
the arithmetic of the missed days is done on arrival. That is the right shape for an
operator on a phone: the twin is always current when spoken to, and costs nothing when not.

The second half of "live" is ASSIMILATION: when the operator reports a reading, the state
is pulled toward it. Deliberately bounded nudging, not a Kalman filter — the epic (#85)
says why: we have no defensible covariances yet, and a filter dressed in guessed noise
matrices is confidence theatre. A nudge with a stated weight is honest about being a
compromise, and the INNOVATION — how far the model was from the measurement — is reported
to the operator every time, because a drifting model should say it is drifting.

Pure and deterministic: serialization, nudging, and the advance contract. Weather arrives
as data; persistence lives in the agent layer.
"""

from __future__ import annotations

from dataclasses import asdict

from .fishgrowth import Cohort
from .production import ProductionState
from .twin import TwinState

# How hard a single reading pulls the state, per channel. 0 = ignore the operator,
# 1 = discard the model. 0.6 says: a hand titration kit beats an unvalidated model, but
# one reading doesn't erase the trajectory. Fish observations are near-authoritative —
# the operator weighed real fish.
NUDGE_WEIGHTS = {
    "water_temp_c": 0.8,
    "tan_mg_l": 0.6,
    "no2_mg_l": 0.6,
    "no3_mg_l": 0.6,
    "fish_avg_weight_g": 0.9,
    "fish_count": 1.0,           # a count is a count; mortality is not negotiable
}

MIRROR_SCHEMA_VERSION = "1.0.0"


def to_dict(state: ProductionState, *, as_of: str) -> dict:
    """Serialize with its calendar anchor. `as_of` is the ISO date the state describes."""
    return {"schema": MIRROR_SCHEMA_VERSION, "as_of": as_of,
            "nitrogen": asdict(state.nitrogen), "fish": asdict(state.fish),
            "scalars": {k: getattr(state, k) for k in (
                "water_temp_c", "day", "harvested_fish_kg", "restocked_fish_kg",
                "harvested_crop_kg", "feed_used_kg", "heat_deficit_c_days")}}


def from_dict(d: dict) -> tuple[ProductionState, str]:
    if d.get("schema") != MIRROR_SCHEMA_VERSION:
        raise ValueError(f"mirror state schema {d.get('schema')!r} is not "
                         f"{MIRROR_SCHEMA_VERSION} — refuse to guess at a migration")
    state = ProductionState(nitrogen=TwinState(**d["nitrogen"]),
                            fish=Cohort(**d["fish"]), **d["scalars"])
    return state, str(d["as_of"])


def nudge(state: ProductionState, readings: dict) -> tuple[ProductionState, tuple[str, ...]]:
    """Pull the state toward the operator's readings; report every innovation.

    `readings` uses the keys of NUDGE_WEIGHTS (missing/None = not measured). Returns the
    nudged state and human-readable notes — the notes are the product as much as the
    state, because "the model was 40% low on nitrate" is what tells an operator (and us)
    where the model needs work."""
    from dataclasses import replace

    notes: list[str] = []
    nit = state.nitrogen
    fish = state.fish
    water = state.water_temp_c

    def blend(model: float, obs: float, w: float) -> float:
        return model + (obs - model) * w

    def note(label: str, model: float, obs: float, unit: str) -> None:
        if obs > 1e-9:
            off = (model - obs) / obs
            direction = "high" if off > 0 else "low"
            if abs(off) >= 0.15:
                notes.append(f"{label}: model {model:.2f} vs your {obs:.2f} {unit} — "
                             f"model was {abs(off):.0%} {direction}; pulled toward yours")
                return
        notes.append(f"{label}: model {model:.2f}, yours {obs:.2f} {unit} — close; blended")

    for key, attr in (("tan_mg_l", "tan_mg_l"), ("no2_mg_l", "no2_mg_l"),
                      ("no3_mg_l", "no3_mg_l")):
        obs = readings.get(key)
        if obs is not None:
            obs = max(0.0, float(obs))
            model = getattr(nit, attr)
            note({"tan_mg_l": "ammonia", "no2_mg_l": "nitrite",
                  "no3_mg_l": "nitrate"}[key], model, obs, "mg/L")
            nit = replace(nit, **{attr: blend(model, obs, NUDGE_WEIGHTS[key])})

    if readings.get("water_temp_c") is not None:
        obs = float(readings["water_temp_c"])
        note("water temperature", water, obs, "C")
        water = blend(water, obs, NUDGE_WEIGHTS["water_temp_c"])

    if readings.get("fish_avg_weight_g") is not None:
        obs = max(0.1, float(readings["fish_avg_weight_g"]))
        note("mean fish weight", fish.mean_weight_g, obs, "g")
        fish = Cohort(count=fish.count,
                      mean_weight_g=blend(fish.mean_weight_g, obs,
                                          NUDGE_WEIGHTS["fish_avg_weight_g"]))
    if readings.get("fish_count") is not None:
        obs = max(0, int(readings["fish_count"]))
        if obs != fish.count:
            notes.append(f"fish count set to {obs} (was {fish.count})")
        fish = Cohort(count=obs, mean_weight_g=fish.mean_weight_g)

    nit = replace(nit, fish_biomass_kg=fish.biomass_kg())
    new = ProductionState(
        nitrogen=nit, fish=fish, water_temp_c=water, day=state.day,
        harvested_fish_kg=state.harvested_fish_kg,
        restocked_fish_kg=state.restocked_fish_kg,
        harvested_crop_kg=state.harvested_crop_kg,
        feed_used_kg=state.feed_used_kg,
        heat_deficit_c_days=state.heat_deficit_c_days)
    if not notes:
        notes.append("no readings supplied — state unchanged")
    return new, tuple(notes)


def snapshot_line(state: ProductionState) -> str:
    """One phone-sized line of where the mirror stands."""
    n = state.nitrogen
    return (f"{state.fish.count} fish @ {state.fish.mean_weight_g:.0f} g "
            f"({state.fish.biomass_kg():.1f} kg) · water {state.water_temp_c:.1f} C · "
            f"NH3 {n.tan_mg_l:.2f} / NO2 {n.no2_mg_l:.2f} / NO3 {n.no3_mg_l:.0f} mg/L · "
            f"harvested so far {state.harvested_fish_kg:.1f} kg fish, "
            f"{state.harvested_crop_kg:.1f} kg crop")
