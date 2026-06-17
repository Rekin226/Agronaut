"""The sizing coefficients are pinned to sourced empirical ranges — and discrepancies are
surfaced, never silently 'passed'. These tests guard both halves of that contract.
"""

from aqua_model import calibration as cal


def test_every_calibration_is_well_formed():
    for c in cal.all_calibrations():
        assert c.emp_low <= c.emp_high, c.key
        assert c.sources, f"{c.key} must cite a source"
        assert c.unit


def test_load_bearing_coefficients_are_covered():
    keys = {c.key for c in cal.all_calibrations()}
    # FRR is the load-bearing sizing rule; FCR drives biomass. Both must be pinned.
    assert {"tilapia.fcr", "trout.fcr", "lettuce.frr", "basil.frr", "tomato.frr"} <= keys


def test_well_supported_seeds_sit_within_their_sourced_range():
    by = {c.key: c for c in cal.all_calibrations()}
    for key in ("tilapia.fcr", "trout.fcr", "lettuce.frr", "tomato.frr", "tilapia.harvest_weight"):
        assert by[key].within, (
            f"{key}: seed {by[key].seed} fell outside {by[key].emp_low}-{by[key].emp_high}"
        )


def test_basil_frr_discrepancy_is_surfaced():
    # Seed 70 g/m²/day is below the UVI-measured basil band (81–100) — a real undersizing
    # signal that must show up in discrepancies(), not be smoothed over.
    disc = {c.key: c for c in cal.discrepancies()}
    assert "basil.frr" in disc
    assert disc["basil.frr"].verdict == "below empirical range"


def test_ambiguous_catfish_is_flagged_in_its_note():
    # The generic 'catfish' FCR is numerically in-range but biologically ambiguous; the
    # ambiguity must be stated loudly even though it isn't a numeric discrepancy.
    catfish = cal.get("catfish.fcr")
    assert catfish.within
    assert "AMBIGUITY" in catfish.note.upper()


def test_summary_accounting_is_consistent():
    art = cal.summary()
    assert art["n_coefficients"] == len(cal.all_calibrations())
    assert art["n_within_range"] + len(art["discrepancies"]) == art["n_coefficients"]


def test_seeds_are_read_live_from_the_model():
    # The calibration seed must equal what the model actually uses — not a hardcoded copy.
    from aqua_model.crops import get_crop
    from aqua_model.species import get_species

    assert cal.get("tilapia.fcr").seed == get_species("tilapia").fcr
    assert cal.get("basil.frr").seed == get_crop("basil").frr_g_per_m2_day
