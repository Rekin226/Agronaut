"""Per-field provenance for the entity tables (#214), machine-checked.

Every row of `crops.py`, `species.py` and `system_types.py` historically carried ONE
`source` string for the whole row, so nothing recorded which numbers the source
actually states, which were placed by reasoning, and which were inherited from a
peer row (#202's review caught exactly that gap by hand). A migrated row answers
with a `provenance` map: {field: "measured:|placed:|inherited: <...>"}.

The rules enforced here, against `provenance_registry.py` (the single source both
this module and the unsourced-constant guard read):

- a migrated row's map covers EVERY numeric field of its table, with no unknown
  keys, and every value carries one of the three labels with content after it;
- the UNMIGRATED_* lists can only shrink: a row not on them must already be
  migrated, a listed row must still be unmigrated, every name must exist, and
  no list grows beyond the frozen pre-regime baseline (#214's third
  acceptance item: new rows ship with a map).
"""

import pytest

from aqua_model.crops import CROPS, Crop
from aqua_model.species import SPECIES, FishSpecies
from aqua_model.system_types import SYSTEM_TYPES, SystemType
from aqua_model.tests.provenance_registry import (
    _BASELINE_CROPS,
    _BASELINE_SPECIES,
    _BASELINE_SYSTEM_TYPES,
    MIGRATED_CROPS,
    MIGRATED_SPECIES,
    MIGRATED_SYSTEM_TYPES,
    PROVENANCE_LABELS,
    TABLES,
    UNMIGRATED_CROPS,
    UNMIGRATED_SPECIES,
    UNMIGRATED_SYSTEM_TYPES,
)

_MIGRATED = {
    "crops": MIGRATED_CROPS,
    "species": MIGRATED_SPECIES,
    "system_types": MIGRATED_SYSTEM_TYPES,
}
_UNMIGRATED = {
    "crops": UNMIGRATED_CROPS,
    "species": UNMIGRATED_SPECIES,
    "system_types": UNMIGRATED_SYSTEM_TYPES,
}
_ROWS = {"crops": CROPS, "species": SPECIES, "system_types": SYSTEM_TYPES}


def _provenance_problems(row, numeric_fields):
    """(field, why) for everything wrong with a row's provenance map.

    Shared by the table tests and the negative-control test below: the map must
    exist, cover exactly the numeric fields, and every value must carry a label
    with something after it.
    """
    problems = []
    prov = getattr(row, "provenance", None)
    if not prov:
        return [("provenance", "missing — every numeric field needs a "
                 "measured:/placed:/inherited: entry")]
    for field in numeric_fields:
        if field not in prov:
            problems.append((field, "no provenance entry"))
    for field, value in prov.items():
        if field not in numeric_fields:
            problems.append((field, "not a numeric field of this table"))
        if not any(value.startswith(label) for label in PROVENANCE_LABELS):
            problems.append((field, f"value must start with one of {PROVENANCE_LABELS}"))
        elif value.strip() in PROVENANCE_LABELS:
            problems.append((field, "label with no source or reasoning after it"))
    return problems


@pytest.mark.parametrize("table", sorted(TABLES))
def test_migrated_rows_have_complete_provenance(table):
    rows, numeric_fields = TABLES[table]
    migrated = sorted(_MIGRATED[table])
    assert migrated, f"no {table} rows migrated yet — migrate at least one per #214"
    for name in migrated:
        problems = _provenance_problems(rows[name], numeric_fields)
        assert not problems, f"{name}: {problems}"


@pytest.mark.parametrize("table", sorted(TABLES))
def test_unmigrated_rows_are_really_unmigrated(table):
    rows, _ = TABLES[table]
    stale = sorted(name for name in _UNMIGRATED[table]
                   if getattr(rows[name], "provenance", None))
    assert not stale, (
        f"{stale} carry a provenance map but are still listed as unmigrated. "
        "Remove them from the UNMIGRATED list in provenance_registry.py — "
        "the list can only shrink."
    )


@pytest.mark.parametrize("table", sorted(TABLES))
def test_every_row_is_migrated_or_listed(table):
    rows, numeric_fields = TABLES[table]
    accounted = _MIGRATED[table] | _UNMIGRATED[table]
    assert accounted == set(rows), (
        f"rows not accounted for: {sorted(set(rows) - accounted)}. "
        "Give the row a provenance map (and it is migrated) — listing it as "
        "unmigrated is only available to rows inside the frozen baseline."
    )
    assert not (_MIGRATED[table] & _UNMIGRATED[table]), "a row cannot be both"


@pytest.mark.parametrize("table", sorted(TABLES))
def test_unmigrated_lists_stay_within_baseline(table):
    """The shrink-only rule, mechanically: each UNMIGRATED_* list is pinned to
    the baseline frozen when the provenance regime landed — the rows that
    predate it. A row that arrives later must ship with a provenance map;
    growing an unmigrated list to absorb it defeats the registry's purpose.
    """
    baselines = {
        "crops": _BASELINE_CROPS,
        "species": _BASELINE_SPECIES,
        "system_types": _BASELINE_SYSTEM_TYPES,
    }
    grown = _UNMIGRATED[table] - baselines[table]
    assert not grown, (
        f"{sorted(grown)} joined the UNMIGRATED_{table} registry beyond the "
        "frozen baseline. New rows ship with a provenance map (#214's third "
        "acceptance item); extend the baseline only for rows that genuinely "
        "predate the regime."
    )


@pytest.mark.parametrize("table", sorted(TABLES))
def test_unmigrated_lists_only_name_real_rows(table):
    unknown = _UNMIGRATED[table] - set(_ROWS[table])
    assert not unknown, f"UNMIGRATED list names rows that do not exist: {sorted(unknown)}"


def test_rows_without_provenance_still_construct():
    # Acceptance #1: the provenance field is optional — pre-#214 call sites keep
    # working unchanged (seed defaults, overrides, tests all rely on this).
    c = Crop(name="test_crop", category="leafy", frr_g_per_m2_day=60.0,
             frr_low=40.0, frr_high=80.0, n_uptake_g_per_m2_day=0.8,
             yield_kg_per_m2_year=25.0, edible_protein_pct=1.4,
             ph_min=5.5, ph_max=7.0, temp_min_c=10.0, temp_max_c=26.0,
             source="LIT")
    s = FishSpecies(name="test_fish", feeding_rate_pct_bw=1.5, fcr=1.7,
                    feed_protein_pct=32.0, body_protein_pct=16.0,
                    harvest_weight_kg=0.5, stocking_density_kg_m3=20.0,
                    temp_min_c=14.0, temp_opt_low_c=27.0, temp_opt_high_c=30.0,
                    temp_max_c=36.0, source="LIT")
    st = SystemType(key="test_system", name="test system", water_depth_m=0.3,
                    water_depth_low=0.2, water_depth_high=0.4,
                    provides_biofiltration=False, grow_bed_label="beds",
                    grow_bed_item="bed", considerations=[], source="LIT")
    assert c.provenance is None and s.provenance is None and st.provenance is None


def test_provenance_check_fails_on_a_missing_entry():
    # Negative control: a migrated row with one field missing from its map is
    # named, with the field, by the checker the table tests run.
    class _Row:
        provenance = {"frr_g_per_m2_day": "measured: somewhere"}

    problems = _provenance_problems(_Row(), ("frr_g_per_m2_day", "ph_min"))
    assert ("ph_min", "no provenance entry") in problems, problems


def test_provenance_check_fails_on_an_unlabelled_value():
    class _Row:
        provenance = {"frr_g_per_m2_day": "FAO 589 says 60"}

    problems = _provenance_problems(_Row(), ("frr_g_per_m2_day",))
    assert ("frr_g_per_m2_day", f"value must start with one of {PROVENANCE_LABELS}") in problems
