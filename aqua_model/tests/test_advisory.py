"""A proposal is a claim about what to do. The invariants that keep it honest:

confidence is derived from a declared evidence class and can never exceed it; a serious
action resting only on the model is preceded by a request to measure; no dose ever appears;
every citation points at something real; and the same state always produces the same
proposal.
"""

from dataclasses import replace
from pathlib import Path

import pytest

from aqua_model import advisory as A
from aqua_model.production import ProductionState, start_state
from aqua_model.species import get_species
from aqua_model.twin import TwinState

REPO_ROOT = Path(__file__).resolve().parents[2]
TILAPIA = get_species("tilapia")


def _state(**nitrogen) -> ProductionState:
    """A healthy tilapia system, perturbed only where a test asks."""
    base = start_state(volume_l=3000, fish_count=80, start_weight_g=120.0,
                       water_temp_c=28.0, species=TILAPIA)
    n = replace(base.nitrogen, **({"no3_mg_l": 40.0} | nitrogen))
    return replace(base, nitrogen=n)


def _actions(proposal) -> list[str]:
    return [r.action for r in proposal.recommendations]


# --- the confidence contract ------------------------------------------------------------

def test_confidence_never_exceeds_its_evidence_class():
    """The whole point of the module: a rule cannot assert more certainty than its evidence
    class allows. Enforced in Recommendation.__post_init__, checked here across real runs."""
    for kwargs in ({"tan_mg_l": 2.0, "no2_mg_l": 2.0}, {"tan_mg_l": 0.8},
                   {"no2_mg_l": 0.7}, {"no3_mg_l": 400.0}, {"tan_mg_l": 0.6, "no3_mg_l": 0.0}):
        for measured in (frozenset(), frozenset({"ammonia_mg_l", "nitrite_mg_l",
                                                 "nitrate_mg_l", "water_temp_c"})):
            p = A.recommend(_state(**kwargs), TILAPIA, measured_channels=measured)
            for r in p.recommendations:
                assert 0.0 < r.confidence <= A.EVIDENCE_CONFIDENCE[r.evidence], (
                    f"{r.action} claims {r.confidence} on evidence {r.evidence}")


def test_a_measured_channel_outranks_the_same_reading_modelled():
    """The operator's titration kit beats the twin. If this ever inverts, the module has
    stopped believing its own validation verdict."""
    st = _state(tan_mg_l=0.8)
    modelled = A.recommend(st, TILAPIA)
    measured = A.recommend(st, TILAPIA, measured_channels={"ammonia_mg_l"})
    m_conf = next(r.confidence for r in modelled.recommendations if r.action == "reduce_ration")
    x_conf = next(r.confidence for r in measured.recommendations if r.action == "reduce_ration")
    assert x_conf > m_conf


def test_confidence_is_not_a_free_parameter():
    """Every emitted confidence equals its class ceiling, or a documented fraction of it.
    A number that is neither would mean somebody tuned one by hand."""
    p = A.recommend(_state(tan_mg_l=0.8, no2_mg_l=0.7, no3_mg_l=400.0), TILAPIA)
    for r in p.recommendations:
        ceiling = A.EVIDENCE_CONFIDENCE[r.evidence]
        ratio = r.confidence / ceiling
        assert abs(ratio - round(ratio, 2)) < 1e-9 and 0 < ratio <= 1.0


# --- the measure-first gate -------------------------------------------------------------

def test_an_urgent_modelled_action_is_gated_by_measure_first():
    p = A.recommend(_state(tan_mg_l=2.0), TILAPIA)
    assert _actions(p)[0] == "measure_first", "the model asked to be obeyed without being checked"


def test_measure_first_names_the_channel_to_test():
    p = A.recommend(_state(no2_mg_l=1.5), TILAPIA)
    gate = p.recommendations[0]
    assert gate.action == "measure_first"
    assert "nitrite" in gate.why


def test_no_gate_when_the_operator_already_measured():
    p = A.recommend(_state(tan_mg_l=2.0), TILAPIA,
                    measured_channels={"ammonia_mg_l", "nitrite_mg_l"})
    assert "measure_first" not in _actions(p), "nothing left to check, but it asked anyway"


def test_no_gate_for_a_merely_this_week_item():
    """There is time to measure before a replanting; a gate there is noise."""
    p = A.recommend(_state(no3_mg_l=400.0), TILAPIA)
    assert _actions(p) == ["increase_plant_uptake"]


# --- the rules themselves ---------------------------------------------------------------

def test_a_healthy_system_gets_no_recommendations():
    p = A.recommend(_state(), TILAPIA)
    assert p.is_empty()
    assert "nothing to do" in A.format_proposal(p)


def test_a_clean_proposal_still_refuses_to_call_the_system_healthy():
    """DO and pH are not modelled; silence must never read as an all-clear."""
    text = A.format_proposal(A.recommend(_state(), TILAPIA))
    assert "not a clean bill of health" in text
    assert "oxygen" in text


def test_both_species_over_the_band_pauses_feeding_and_drops_the_softer_advice():
    p = A.recommend(_state(tan_mg_l=2.0, no2_mg_l=2.0), TILAPIA,
                    measured_channels={"ammonia_mg_l", "nitrite_mg_l"})
    assert "pause_feeding" in _actions(p)
    assert "reduce_ration" not in _actions(p), "told the operator to both halve and stop"


def test_a_held_ration_is_a_different_action_from_a_halved_one():
    """Both are "less feed", but "stop" and "halve" are different instructions and later
    become different questions of the approval record. A single action key with a 0 value
    would render as "cut the ration to 0", which is not what an operator should read."""
    halve = A.recommend(_state(tan_mg_l=0.7), TILAPIA, measured_channels={"ammonia_mg_l"})
    hold = A.recommend(_state(tan_mg_l=1.4), TILAPIA, measured_channels={"ammonia_mg_l"})
    assert "reduce_ration" in _actions(halve) and "hold_ration" not in _actions(halve)
    assert "hold_ration" in _actions(hold) and "reduce_ration" not in _actions(hold)
    assert "no ration today" in A.format_proposal(hold)
    assert "by half" in A.format_proposal(halve)


def test_a_feeding_pause_supersedes_both_softer_feed_actions():
    p = A.recommend(_state(tan_mg_l=2.0, no2_mg_l=2.0), TILAPIA,
                    measured_channels={"ammonia_mg_l", "nitrite_mg_l"})
    assert "pause_feeding" in _actions(p)
    assert not {"reduce_ration", "hold_ration"} & set(_actions(p))


def test_every_action_emitted_has_a_human_label():
    """An unlabelled action would render as its raw key in the one place a person has to
    read it before approving."""
    seen = set()
    for kwargs in ({"tan_mg_l": 2.0, "no2_mg_l": 2.0}, {"tan_mg_l": 0.7}, {"tan_mg_l": 1.4},
                   {"no2_mg_l": 0.7}, {"no3_mg_l": 400.0}, {"tan_mg_l": 0.6, "no3_mg_l": 0.0}):
        seen.update(_actions(A.recommend(_state(**kwargs), TILAPIA)))
    st = _state()
    seen.update(_actions(A.recommend(replace(st, water_temp_c=10.0), TILAPIA)))
    seen.update(_actions(A.recommend(
        replace(st, fish=replace(st.fish, mean_weight_g=600.0)), TILAPIA)))
    missing = seen - set(A.ACTION_LABEL)
    assert not missing, f"actions with no human label: {sorted(missing)}"


def test_ammonia_with_no_nitrate_reads_as_an_uncycled_filter():
    p = A.recommend(_state(tan_mg_l=0.9, no3_mg_l=0.0), TILAPIA)
    assert "inspect_biofilter" in _actions(p)


def test_temperature_rules_use_the_species_own_band():
    """Trout and tilapia must disagree about the same water, or the band is decorative."""
    trout = get_species("trout")
    warm = replace(_state(), water_temp_c=24.0)
    assert "add_heat_or_cover" in _actions(A.recommend(warm, TILAPIA))
    assert "add_shade_or_cooling" in _actions(A.recommend(warm, trout))


def test_harvest_is_flagged_irreversible():
    st = _state()
    st = replace(st, fish=replace(st.fish, mean_weight_g=600.0))
    p = A.recommend(st, TILAPIA)
    harvest = next(r for r in p.recommendations if r.action == "harvest_fish")
    assert harvest.reversible is False
    assert "cannot be undone" in A.format_proposal(p)


def test_a_forecast_peak_is_capped_at_the_direction_class():
    """A trend the twin sees coming may speak, but only at the confidence validation earned.

    The scenario is a newly stocked, UNCYCLED system, because that is the case where the
    twin genuinely predicts a spike days before a test kit would show one: the biofilter is
    a population that has to grow into the load. A cycled system absorbs the same feed with
    no excursion at all, which is what makes this rule worth having.
    """
    from aqua_model.climate import DailyClimate, GreenhouseParams
    from aqua_model.crops import get_crop
    from aqua_model.production import ProductionParams, simulate_production

    st = start_state(volume_l=3000, fish_count=80, start_weight_g=120.0,
                     water_temp_c=28.0, species=TILAPIA, cycled=False)
    weather = tuple(DailyClimate(t_mean_c=29.0, t_min_c=24.0, t_max_c=34.0,
                                 solar_mj_m2=22.0) for _ in range(12))
    run = simulate_production(st, weather, TILAPIA, "tilapia", get_crop("lettuce"), 24.0,
                              params=ProductionParams(greenhouse=GreenhouseParams()))
    p = A.recommend(st, TILAPIA, trajectory=run.trajectory)

    forecast = [r for r in p.recommendations if r.action == "plan_ration_reduction"]
    assert forecast, "the twin saw an ammonia spike coming and said nothing"
    for r in forecast:
        assert r.evidence == A.MODELLED_DIRECTION
        assert r.confidence < A.EVIDENCE_CONFIDENCE[A.MODELLED_DIRECTION], (
            "a forecast trend must sit BELOW the direction ceiling, not at it")
        assert r.urgency == "this_week"


def test_a_cycled_system_gets_no_forecast_warning_on_the_same_weather():
    """The other half of the rule: it must stay quiet when there is nothing coming."""
    from aqua_model.climate import DailyClimate, GreenhouseParams
    from aqua_model.crops import get_crop
    from aqua_model.production import ProductionParams, simulate_production

    st = _state(tan_mg_l=0.0, no2_mg_l=0.0, no3_mg_l=10.0)
    weather = tuple(DailyClimate(t_mean_c=29.0, t_min_c=24.0, t_max_c=34.0,
                                 solar_mj_m2=22.0) for _ in range(12))
    run = simulate_production(st, weather, TILAPIA, "tilapia", get_crop("lettuce"), 24.0,
                              params=ProductionParams(greenhouse=GreenhouseParams()))
    p = A.recommend(st, TILAPIA, trajectory=run.trajectory)
    assert "plan_ration_reduction" not in _actions(p)


# --- the safety contract ----------------------------------------------------------------

@pytest.mark.parametrize("kwargs", [
    {"tan_mg_l": 2.0, "no2_mg_l": 2.0}, {"tan_mg_l": 0.8}, {"no2_mg_l": 0.7},
    {"no3_mg_l": 400.0}, {"tan_mg_l": 0.6, "no3_mg_l": 0.0},
])
def test_no_dose_is_ever_stated(kwargs):
    """The same discipline triage.py keeps. Reference bands (mg/L of a MEASURED channel)
    are fine; an amount to ADD is not."""
    text = A.format_proposal(A.recommend(_state(**kwargs), TILAPIA)).lower()
    for banned in ("teaspoon", "tablespoon", "grams of salt", "g of salt", "add salt",
                   "per litre of water", "dose"):
        assert banned not in text, f"a dose leaked into the proposal: {banned!r}"


def test_the_proposal_says_it_cannot_actuate_anything():
    """The gap between 'recommends' and 'does' must be stated on every proposal that has
    something in it, because that gap is the safety property."""
    text = A.format_proposal(A.recommend(_state(tan_mg_l=2.0), TILAPIA))
    assert "NO connection to your equipment" in text
    assert "does not move a valve" in text


def test_every_source_points_at_something_real():
    """A citation that rots into a dead reference is worse than no citation."""
    seen = set()
    for kwargs in ({"tan_mg_l": 2.0, "no2_mg_l": 2.0}, {"tan_mg_l": 0.8}, {"no2_mg_l": 0.7},
                   {"no3_mg_l": 400.0}, {"tan_mg_l": 0.6, "no3_mg_l": 0.0}):
        for r in A.recommend(_state(**kwargs), TILAPIA).recommendations:
            seen.add(r.source)
    st = _state()
    seen.update(r.source for r in A.recommend(
        replace(st, fish=replace(st.fish, mean_weight_g=600.0)), TILAPIA).recommendations)
    seen.update(r.source for r in A.recommend(
        replace(st, water_temp_c=10.0), TILAPIA).recommendations)
    assert seen, "no sources were exercised at all"
    for source in seen:
        path = source.split(" ")[0].split("(")[0].strip()
        assert (REPO_ROOT / path).exists(), f"dead citation: {source}"


def test_the_not_modelled_list_travels_with_every_proposal():
    p = A.recommend(_state(tan_mg_l=2.0), TILAPIA)
    assert p.not_modelled
    assert any("oxygen" in item for item in p.not_modelled)
    assert any("pH" in item for item in p.not_modelled)


# --- structure --------------------------------------------------------------------------

def test_recommend_is_deterministic():
    st = _state(tan_mg_l=0.8, no2_mg_l=0.7, no3_mg_l=400.0)
    a = A.to_dict(A.recommend(st, TILAPIA, as_of="2026-09-01"))
    b = A.to_dict(A.recommend(st, TILAPIA, as_of="2026-09-01"))
    assert a == b


def test_urgency_orders_the_list_and_numbering_follows_it():
    """`/approve 1` must mean the first thing rendered, so ordering is part of the contract."""
    st = _state(tan_mg_l=2.0, no3_mg_l=400.0)
    p = A.recommend(st, TILAPIA, measured_channels={"ammonia_mg_l", "nitrate_mg_l"})
    order = [A.URGENCY_ORDER.index(r.urgency) for r in p.recommendations]
    assert order == sorted(order)
    text = A.format_proposal(p)
    for i, r in enumerate(p.recommendations, 1):
        assert f"{i}. [" in text
        assert A.ACTION_LABEL[r.action] in text


def test_a_confidence_floor_can_filter_but_the_default_hides_nothing():
    st = _state(tan_mg_l=0.8)
    p = A.recommend(st, TILAPIA)
    assert p.recommendations, "the default must surface weak items, saying they are weak"
    strict = A.with_confidence_floor(p, 0.85)
    assert len(strict.recommendations) < len(p.recommendations)


def test_an_unknown_evidence_class_or_urgency_is_refused():
    with pytest.raises(ValueError):
        A.Recommendation(action="x", value=None, unit="", why="", evidence="vibes",
                         confidence=0.5, urgency="now", verify="", source="")
    with pytest.raises(ValueError):
        A.Recommendation(action="x", value=None, unit="", why="", evidence=A.MEASURED,
                         confidence=0.5, urgency="eventually", verify="", source="")
    with pytest.raises(ValueError):
        A.Recommendation(action="x", value=None, unit="", why="", evidence=A.MODELLED_LEVEL,
                         confidence=0.99, urgency="now", verify="", source="")


def test_to_dict_round_trips_the_fields_a_store_needs():
    p = A.recommend(_state(tan_mg_l=0.8), TILAPIA, as_of="2026-09-01", horizon_days=7)
    d = A.to_dict(p)
    assert d["schema_version"] == A.ADVISORY_SCHEMA_VERSION
    assert d["as_of"] == "2026-09-01" and d["horizon_days"] == 7
    for r in d["recommendations"]:
        assert set(r) == {"action", "value", "unit", "why", "evidence", "confidence",
                          "urgency", "verify", "source", "reversible"}


def test_state_is_never_mutated():
    st = _state(tan_mg_l=0.8)
    before = TwinState(**vars(st.nitrogen))
    A.recommend(st, TILAPIA)
    assert st.nitrogen == before
