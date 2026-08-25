"""Tools wrap the deterministic core, preserve the trust gate, and carry provenance."""

from agronaut_agent.tools import (
    size_aquaponics_system,
    optimize_fish_crop_ratio,
    list_supported_species_and_crops,
    AGRONAUT_TOOLS,
)


def test_tool_registry():
    names = {t.name for t in AGRONAUT_TOOLS}
    assert "size_aquaponics_system" in names
    assert "optimize_fish_crop_ratio" in names
    assert "search_knowledge_base" in names
    assert "remember_about_user" in names
    assert len(AGRONAUT_TOOLS) == 23


def test_size_valid_carries_numbers_and_sources():
    out = size_aquaponics_system.invoke(
        {"fish_species": "tilapia", "crop": "lettuce", "grow_area_m2": 12,
         "temperature_c": 27, "water_budget_lpd": 300}
    )
    assert "FEASIBLE" in out
    assert "fish=" in out and "feed=" in out
    # honesty layer: coefficients with sources and not-modeled caveats are present
    assert "Coefficients used" in out and "source:" in out
    assert "NOT modeled" in out


def test_mixed_bed_tool_sizes_several_crops_and_carries_the_plan():
    from agronaut_agent.tools import size_mixed_bed_aquaponics, AGRONAUT_TOOLS
    assert "size_mixed_bed_aquaponics" in {t.name for t in AGRONAUT_TOOLS}
    out = size_mixed_bed_aquaponics.invoke({
        "fish_species": "tilapia",
        "crop_plan": [{"crop": "lettuce", "area_m2": 10}, {"crop": "basil", "area_m2": 10}],
        "temperature_c": 27, "water_budget_lpd": 8000,
    })
    assert "FEASIBLE" in out
    assert "Mixed beds" in out and "lettuce" in out and "basil" in out
    assert "Coefficients used" in out and "source:" in out


def test_mixed_bed_tool_warns_on_incompatible_ph():
    from agronaut_agent.tools import size_mixed_bed_aquaponics
    out = size_mixed_bed_aquaponics.invoke({
        "fish_species": "tilapia",
        "crop_plan": [{"crop": "watercress", "area_m2": 10}, {"crop": "strawberry", "area_m2": 10}],
        "temperature_c": 26, "water_budget_lpd": 8000,
    })
    assert "pH" in out and "Warnings" in out


def test_mixed_bed_tool_rejects_unknown_crop_through_the_trust_gate():
    from agronaut_agent.tools import size_mixed_bed_aquaponics
    out = size_mixed_bed_aquaponics.invoke({
        "fish_species": "tilapia",
        "crop_plan": [{"crop": "dragonfruit", "area_m2": 10}],
        "temperature_c": 26, "water_budget_lpd": 8000,
    })
    assert "unknown crop" in out.lower() or "VALIDATION" in out.upper()


def test_pilot_proposal_tool_renders_funder_document():
    from agronaut_agent.tools import render_pilot_proposal, AGRONAUT_TOOLS
    assert "render_pilot_proposal" in {t.name for t in AGRONAUT_TOOLS}
    out = render_pilot_proposal.invoke({
        "fish_species": "tilapia", "crop": "lettuce", "grow_area_m2": 20,
        "temperature_c": 27, "water_budget_lpd": 5000,
        "site": "Kaya, Burkina Faso", "organization": "Sahel Coop",
        "ask_amount": 15000, "beneficiaries": "25 households"})
    assert "Pilot Proposal" in out
    assert "15,000 USD" in out
    assert "Projected outcomes" in out and "kg/year" in out
    assert "not model" in out.lower() or "NOT modeled" in out   # honesty layer survives


def test_pilot_proposal_tool_trust_gate_rejects_bad_input():
    from agronaut_agent.tools import render_pilot_proposal
    out = render_pilot_proposal.invoke({
        "fish_species": "dragon", "crop": "lettuce", "grow_area_m2": 20,
        "temperature_c": 27, "water_budget_lpd": 5000,
        "site": "X", "organization": "Y", "ask_amount": 1000})
    assert "VALIDATION_FAILED" in out


def test_hydroponic_tool_registered_and_sizes_without_fish():
    from agronaut_agent.tools import size_hydroponic_system_tool, AGRONAUT_TOOLS
    assert "size_hydroponic_system_tool" in {t.name for t in AGRONAUT_TOOLS}
    out = size_hydroponic_system_tool.invoke(
        {"crop": "lettuce", "grow_area_m2": 10, "temperature_c": 22, "water_budget_lpd": 500})
    assert "FEASIBLE hydroponic design" in out
    assert "EC" in out and "reservoir" in out.lower()
    assert "NOT modeled" in out
    # no fish CONCEPT (feed/biofilter/fingerlings) — the only "fish" is the "(no fish)" label
    assert "feed=" not in out and "biofilter" not in out.lower()
    assert "fingerling" not in out.lower()


def test_hydroponic_tool_trust_gate_rejects_unknown_crop():
    from agronaut_agent.tools import size_hydroponic_system_tool
    out = size_hydroponic_system_tool.invoke(
        {"crop": "moonfruit", "grow_area_m2": 10, "temperature_c": 22, "water_budget_lpd": 500})
    assert "VALIDATION_FAILED" in out


def test_size_tool_accepts_growing_method():
    raft = size_aquaponics_system.invoke(
        {"fish_species": "tilapia", "crop": "lettuce", "grow_area_m2": 20,
         "temperature_c": 27, "water_budget_lpd": 5000, "system_type": "nft"})
    assert "NFT" in raft or "nutrient film" in raft.lower()
    # unknown method is rejected at the trust gate
    bad = size_aquaponics_system.invoke(
        {"fish_species": "tilapia", "crop": "lettuce", "grow_area_m2": 20,
         "temperature_c": 27, "water_budget_lpd": 5000, "system_type": "sky-farm"})
    assert "VALIDATION_FAILED" in bad


def test_list_supported_includes_growing_methods():
    out = list_supported_species_and_crops.invoke({})
    assert "Growing methods" in out
    for m in ("raft", "nft", "media_bed"):
        assert m in out


def test_size_trust_gate_rejects_unknown_species():
    out = size_aquaponics_system.invoke(
        {"fish_species": "shark", "crop": "lettuce", "grow_area_m2": 12,
         "temperature_c": 27, "water_budget_lpd": 300}
    )
    assert "VALIDATION_FAILED" in out
    assert "shark" in out
    assert "do NOT guess" in out


def test_optimize_returns_ranked_best():
    out = optimize_fish_crop_ratio.invoke(
        {"grow_area_m2": 10, "temperature_c": 28, "water_budget_lpd": 5000, "objective": "food"}
    )
    assert "Best ratio:" in out
    assert "alternatives" in out


def test_optimize_rejects_bad_objective():
    out = optimize_fish_crop_ratio.invoke(
        {"grow_area_m2": 10, "temperature_c": 28, "water_budget_lpd": 5000, "objective": "money"}
    )
    assert "Unknown objective" in out


def test_list_supported():
    out = list_supported_species_and_crops.invoke({})
    assert "tilapia" in out and "lettuce" in out and "water_efficiency" in out


def test_list_supported_includes_new_species_and_crops():
    from agronaut_agent.tools import list_supported_species_and_crops
    out = list_supported_species_and_crops.invoke({})
    assert "carp" in out
    for c in ("kale", "swiss_chard", "spinach", "cucumber", "pepper"):
        assert c in out


def test_registry_includes_update_profile():
    from agronaut_agent.tools import AGRONAUT_TOOLS
    names = {t.name for t in AGRONAUT_TOOLS}
    assert "update_profile" in names
    assert len(AGRONAUT_TOOLS) == 23


def test_update_profile_writes_canonical_drops_unknown():
    from agronaut_agent.store import _Db, MemoryStore
    from agronaut_agent import runtime
    from agronaut_agent.tools import update_profile

    mem = MemoryStore(_Db(":memory:"))
    runtime.set_current(mem, "cli:p")
    try:
        out = update_profile.invoke({"updates": {
            "goal": "optimize", "objective": "protein", "grow_area_m2": 10,
            "bogus_key": "x", "ph": "",
        }})
    finally:
        runtime.clear_current()

    facts = mem.get_facts("cli:p")
    assert facts["goal"] == "optimize"
    assert facts["objective"] == "protein"
    assert facts["grow_area_m2"] == "10"
    assert "bogus_key" not in facts   # unknown key ignored
    assert "ph" not in facts          # empty value skipped
    assert "optimize" in out


def test_registry_includes_schedule_followup():
    from agronaut_agent.tools import AGRONAUT_TOOLS
    names = {t.name for t in AGRONAUT_TOOLS}
    assert "schedule_followup" in names
    assert len(AGRONAUT_TOOLS) == 23


def test_schedule_followup_writes_a_row_and_guards_duplicates():
    from agronaut_agent.store import _Db, MemoryStore, FollowupStore
    from agronaut_agent import runtime
    from agronaut_agent.tools import schedule_followup

    db = _Db(":memory:")
    mem, fs = MemoryStore(db), FollowupStore(db)
    runtime.set_current(mem, "telegram:7", fs)
    try:
        out = schedule_followup.invoke({"question": "did the water change help?",
                                        "hours": 24, "about": "ammonia spike"})
        assert "check back" in out.lower()
        assert fs.open_for("telegram:7")["question"] == "did the water change help?"
        # second while one is open is refused
        again = schedule_followup.invoke({"question": "still ok?", "hours": 24, "about": "x"})
        assert "pending" in again.lower()
    finally:
        runtime.clear_current()


def test_schedule_followup_rejects_out_of_range_hours():
    from agronaut_agent.store import _Db, MemoryStore, FollowupStore
    from agronaut_agent import runtime
    from agronaut_agent.tools import schedule_followup

    db = _Db(":memory:")
    runtime.set_current(MemoryStore(db), "telegram:8", FollowupStore(db))
    try:
        assert "between" in schedule_followup.invoke(
            {"question": "q", "hours": 0.5, "about": "x"}).lower()
        assert "between" in schedule_followup.invoke(
            {"question": "q", "hours": 999, "about": "x"}).lower()
    finally:
        runtime.clear_current()


def test_registry_includes_community_tools():
    from agronaut_agent.tools import AGRONAUT_TOOLS
    names = {t.name for t in AGRONAUT_TOOLS}
    assert "nominate_shared_insight" in names
    assert "search_community_knowledge" in names
    assert len(AGRONAUT_TOOLS) == 23


def test_nominate_writes_pending_and_rejects_blank():
    from agronaut_agent.store import _Db, MemoryStore, CommunityStore
    from agronaut_agent import runtime
    from agronaut_agent.tools import nominate_shared_insight

    db = _Db(":memory:")
    mem, cs = MemoryStore(db), CommunityStore(db)
    mem.add_memory("telegram:1", "30% water change fixed my ammonia", "learning")
    runtime.set_current(mem, "telegram:1", None, cs)
    try:
        out = nominate_shared_insight.invoke(
            {"insight": "a partial water change commonly clears an acute ammonia spike",
             "topic": "ammonia"})
        assert "nominated" in out.lower()
        pend = cs.pending()
        assert len(pend) == 1
        assert pend[0]["insight"].startswith("a partial water change")
        assert pend[0]["original"] == "30% water change fixed my ammonia"   # review context
        # blank is rejected
        assert "nothing" in nominate_shared_insight.invoke({"insight": "  ", "topic": "x"}).lower()
    finally:
        runtime.clear_current()


def test_search_community_knowledge_labels_and_filters():
    from agronaut_agent.store import _Db, MemoryStore, CommunityStore
    from agronaut_agent import runtime
    from agronaut_agent.tools import search_community_knowledge

    db = _Db(":memory:")
    cs = CommunityStore(db)
    cs.nominate("telegram:9", "raw", "aerate at dawn to avoid DO crashes", "dissolved oxygen")
    cs.approve(cs.pending()[0]["id"])
    runtime.set_current(MemoryStore(db), "telegram:2", None, cs)
    try:
        out = search_community_knowledge.invoke({"query": "dissolved oxygen"})
        assert "other operators" in out.lower()          # peer-reported label
        assert "aerate at dawn" in out
        # no match -> clean fallback
        assert "no community" in search_community_knowledge.invoke({"query": "zzzzz"}).lower()
    finally:
        runtime.clear_current()


def test_registry_includes_record_measurement():
    from agronaut_agent.tools import AGRONAUT_TOOLS
    names = {t.name for t in AGRONAUT_TOOLS}
    assert "record_measurement" in names
    assert len(AGRONAUT_TOOLS) == 23


def test_record_measurement_maps_metric_to_qualified_key():
    from agronaut_agent.store import _Db, MemoryStore, CalibrationStore
    from agronaut_agent import runtime
    from agronaut_agent.tools import record_measurement

    db = _Db(":memory:")
    mem, cal = MemoryStore(db), CalibrationStore(db)
    mem.set_facts("telegram:1", {"fish_species": "tilapia", "crop": "lettuce"})
    runtime.set_current(mem, "telegram:1", None, None, cal)
    try:
        out = record_measurement.invoke({"metric": "harvest_weight", "value": 0.45})
        assert "recorded" in out.lower()
        assert cal._by_coefficient("telegram:1") == {"tilapia.harvest_weight": [0.45]}
        # yield maps to the crop
        record_measurement.invoke({"metric": "yield", "value": 12.0})
        assert "lettuce.yield" in cal._by_coefficient("telegram:1")
    finally:
        runtime.clear_current()


def test_record_measurement_is_honest_when_no_calibration_coverage():
    # trout.harvest_weight has no published range in aqua_model.calibration: the value must
    # still be stored (for when coverage lands), but the reply must say it can't calibrate —
    # never the silent "I'll use your measurements to calibrate" lie.
    from agronaut_agent.store import _Db, MemoryStore, CalibrationStore
    from agronaut_agent import runtime
    from agronaut_agent.tools import record_measurement

    db = _Db(":memory:")
    mem, cal = MemoryStore(db), CalibrationStore(db)
    mem.set_facts("telegram:2", {"fish_species": "trout", "crop": "kale"})
    runtime.set_current(mem, "telegram:2", None, None, cal)
    try:
        out = record_measurement.invoke({"metric": "harvest_weight", "value": 1.1})
        assert "can't calibrate" in out.lower()
        assert "no published range" in out.lower()
        assert cal._by_coefficient("telegram:2") == {"trout.harvest_weight": [1.1]}  # kept
        # covered metrics keep the confident wording
        out2 = record_measurement.invoke({"metric": "fcr", "value": 1.6})
        assert "calibrate future sizings" in out2.lower()
    finally:
        runtime.clear_current()


def test_record_measurement_rejects_unknown_metric_and_bad_value():
    from agronaut_agent.store import _Db, MemoryStore, CalibrationStore
    from agronaut_agent import runtime
    from agronaut_agent.tools import record_measurement

    db = _Db(":memory:")
    mem, cal = MemoryStore(db), CalibrationStore(db)
    mem.set_facts("telegram:1", {"fish_species": "tilapia", "crop": "lettuce"})
    runtime.set_current(mem, "telegram:1", None, None, cal)
    try:
        assert "metric" in record_measurement.invoke({"metric": "weight", "value": 1}).lower()
        assert "number" in record_measurement.invoke({"metric": "fcr", "value": -1}).lower()
    finally:
        runtime.clear_current()


def test_size_tool_applies_calibration_and_labels_it():
    from agronaut_agent.store import _Db, MemoryStore, CalibrationStore
    from agronaut_agent import runtime
    from agronaut_agent.tools import size_aquaponics_system

    db = _Db(":memory:")
    mem, cal = MemoryStore(db), CalibrationStore(db)
    cal.record("telegram:1", "tilapia.harvest_weight", 0.4)
    cal.record("telegram:1", "tilapia.harvest_weight", 0.4)   # mean 0.4, in range -> applied
    runtime.set_current(mem, "telegram:1", None, None, cal)
    try:
        out = size_aquaponics_system.invoke(
            {"fish_species": "tilapia", "crop": "lettuce", "grow_area_m2": 20,
             "temperature_c": 27, "water_budget_lpd": 500})
        assert "FEASIBLE" in out
        assert "calibrat" in out.lower()             # honest calibration note present
        assert "tilapia.harvest_weight" in out
    finally:
        runtime.clear_current()


def test_size_tool_without_calibration_is_unchanged():
    from agronaut_agent.tools import size_aquaponics_system
    # no runtime context -> no overrides, plain design
    out = size_aquaponics_system.invoke(
        {"fish_species": "tilapia", "crop": "lettuce", "grow_area_m2": 20,
         "temperature_c": 27, "water_budget_lpd": 500})
    assert "FEASIBLE" in out
    assert "calibrated from your" not in out.lower()


def test_size_tool_surfaces_override_validation_error():
    """Prove that a ValidationError raised by an override is caught and surfaced,
    not crashed."""
    from agronaut_agent.store import _Db, MemoryStore
    from agronaut_agent import runtime
    from agronaut_agent.tools import size_aquaponics_system

    class _BadCal:
        def overrides_for(self, user_id):
            return {"tilapia.fcr": 5.0}          # far above the empirical range
        def calibration_report(self, user_id):
            return []
    db = _Db(":memory:")
    runtime.set_current(MemoryStore(db), "telegram:1", None, None, _BadCal())
    try:
        out = size_aquaponics_system.invoke(
            {"fish_species": "tilapia", "crop": "lettuce", "grow_area_m2": 20,
             "temperature_c": 27, "water_budget_lpd": 500})
        assert "VALIDATION_FAILED" in out          # surfaced, not crashed
    finally:
        runtime.clear_current()


def test_size_note_not_claimed_for_mismatched_species():
    from agronaut_agent.store import _Db, MemoryStore, CalibrationStore
    from agronaut_agent import runtime
    from agronaut_agent.tools import size_aquaponics_system
    db = _Db(":memory:")
    mem, cal = MemoryStore(db), CalibrationStore(db)
    cal.record("telegram:1", "tilapia.harvest_weight", 0.4)
    cal.record("telegram:1", "tilapia.harvest_weight", 0.4)   # applied for TILAPIA only
    runtime.set_current(mem, "telegram:1", None, None, cal)
    try:
        out = size_aquaponics_system.invoke(
            {"fish_species": "trout", "crop": "lettuce", "grow_area_m2": 20,
             "temperature_c": 15, "water_budget_lpd": 500})   # trout, not tilapia
        assert "calibrated from your" not in out.lower()      # no false claim on trout
    finally:
        runtime.clear_current()


def test_optimize_tool_labels_calibration():
    from agronaut_agent.store import _Db, MemoryStore, CalibrationStore
    from agronaut_agent import runtime
    from agronaut_agent.tools import optimize_fish_crop_ratio
    db = _Db(":memory:")
    mem, cal = MemoryStore(db), CalibrationStore(db)
    cal.record("telegram:1", "tilapia.fcr", 1.4)
    cal.record("telegram:1", "tilapia.fcr", 1.6)              # applied
    runtime.set_current(mem, "telegram:1", None, None, cal)
    try:
        out = optimize_fish_crop_ratio.invoke(
            {"grow_area_m2": 10, "temperature_c": 27, "water_budget_lpd": 5000, "objective": "food"})
        assert "calibrated from your" in out.lower()          # optimize now labels it
    finally:
        runtime.clear_current()
def test_registry_includes_the_twin_tools():
    """The twin is user-visible: season simulation, what-if forking, and the 3D design."""
    from agronaut_agent.tools import AGRONAUT_TOOLS
    names = {t.name for t in AGRONAUT_TOOLS}
    assert "simulate_season" in names
    assert "what_if_nitrogen" in names
    assert "design_system_3d" in names


def test_simulate_season_teaches_when_the_site_is_missing():
    """A missing climate file must return the fetch command, not a stack trace."""
    from agronaut_agent.tools import simulate_season
    text = simulate_season.func(fish_species="tilapia", crop="basil",
                                grow_area_m2=10.0, site="atlantis",
                                water_budget_lpd=300.0)
    assert "fetch_climate.py" in text
    assert "atlantis" in text


def test_what_if_nitrogen_reports_relative_not_absolute():
    from agronaut_agent.tools import what_if_nitrogen
    text = what_if_nitrogen.func(fish_species="tilapia", volume_l=2000.0,
                                 feed_g_per_day=150.0, temperature_c=27.0,
                                 change="double feed", new_feed_g_per_day=300.0)
    assert "x higher" in text or "x lower" in text or "No material change" in text
    assert "unvalidated" in text


def test_simulate_season_design_mode_stocks_the_designed_system():
    """Path 1 of the intake procedure: the agreed design flows into the twin without
    anyone retyping numbers — the summary label carries the design's own fish count."""
    from agronaut_agent.tools import simulate_season
    text = simulate_season.func(fish_species="tilapia", crop="basil", grow_area_m2=24.0,
                                site="taichung_2025", water_budget_lpd=500.0,
                                system_type="raft", days=60)
    assert "designed:" in text and "fish" in text
    assert "Season projection" in text


def test_simulate_season_without_a_system_teaches_instead_of_guessing():
    from agronaut_agent.tools import simulate_season
    text = simulate_season.func(fish_species="tilapia", crop="basil",
                                grow_area_m2=10.0, site="taichung_2025")
    assert "water_budget_lpd" in text


def test_simulate_my_system_asks_for_what_the_mirror_is_missing():
    """Path 2: an incomplete profile returns the exact list to collect, not a guess."""
    from agronaut_agent.store import _Db, MemoryStore
    from agronaut_agent import runtime
    from agronaut_agent.tools import simulate_my_system

    mem = MemoryStore(_Db(":memory:"))
    runtime.set_current(mem, "cli:t")
    try:
        mem.set_facts("cli:t", {"fish_species": "tilapia", "crop": "lettuce"},
                      source="user_stated")
        text = simulate_my_system.func()
    finally:
        runtime.clear_current()
    assert "tank_volume_l" in text and "fish_count" in text and "climate_site" in text
    assert "update_profile" in text


def test_simulate_my_system_mirrors_a_complete_profile():
    from agronaut_agent.store import _Db, MemoryStore
    from agronaut_agent import runtime
    from agronaut_agent.tools import simulate_my_system

    mem = MemoryStore(_Db(":memory:"))
    runtime.set_current(mem, "cli:t")
    try:
        mem.set_facts("cli:t", {
            "fish_species": "tilapia", "crop": "basil", "grow_area_m2": 15,
            "tank_volume_l": 2000, "fish_count": 60, "fish_avg_weight_g": 200,
            "climate_site": "taichung_2025"}, source="user_stated")
        text = simulate_my_system.func(days=60)
    finally:
        runtime.clear_current()
    assert "your system" in text
    assert "Season projection" in text


def test_estimate_system_cost_degrades_without_a_price_book_or_region():
    from agronaut_agent.tools import estimate_system_cost
    text = estimate_system_cost.func(fish_species="tilapia", crop="basil",
                                     grow_area_m2=24.0, temperature_c=27.0,
                                     water_budget_lpd=500.0, region="list")
    assert "price book" in text.lower() or "region" in text.lower()
