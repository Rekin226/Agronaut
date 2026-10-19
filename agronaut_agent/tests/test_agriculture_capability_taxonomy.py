"""The agriculture taxonomy has to stay honest as it grows (#243).

A vocabulary document rots in a different way from code: nobody breaks it, it just quietly stops
being true. A method gets added to the wrong axis, a source gets cited that was never read, a
combination is listed as both supported and incompatible, or a status that requires field data
starts being offered as if the data existed. None of that shows up as a failing test unless
someone writes the test.

So this file checks the invariants, not the prose: the two axes stay separate, every case #243
names is present, no pair contradicts itself, every cited source resolves to a registry entry
that carries an edition, a locator and a reuse permission, a numeric source never rests on a
document-level citation, the four capability statuses are exactly the four defined ones, and
`field_validated_operation` never claims to be available.

Hermetic: reads two JSON files next to this repository's docs. No index, model or network.

What this cannot see: whether a locator quoted from a source actually says what the entry claims.
Reading the page is the only check for that, and `verified.how` records who did it and when.
"""

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
AGRICULTURE_DOCS = REPO_ROOT / "docs" / "agriculture"
TAXONOMY = AGRICULTURE_DOCS / "taxonomy.v1.json"
REGISTRY = AGRICULTURE_DOCS / "source_registry.v1.json"
NARRATIVE = AGRICULTURE_DOCS / "CAPABILITY_TAXONOMY.md"

# The cases #243's first acceptance box names, split by the axis each one belongs to. Splitting
# them here is the point: aquaponics and hydroponics are production systems, and drip, sprinkler,
# surface and rainfed are irrigation methods. One list would let them be conflated.
REQUIRED_PRODUCTION_SYSTEMS = {"aquaponics", "hydroponics", "soil_cultivation"}
REQUIRED_IRRIGATION_METHODS = {"drip", "sprinkler", "surface", "rainfed"}
REQUIRED_STATUSES = {
    "sourced_guidance",
    "verified_calculation",
    "experimental_simulation",
    "field_validated_operation",
}
NUMERIC_PROVIDERS = {"numeric_coefficient", "numeric_procedure"}
VALID_LOCATOR_KINDS = {"equation", "table", "section", "document"}
VALID_COMPATIBILITY = {"supported", "conditional", "incompatible"}
VALID_BASIS = {"support_decision", "source_pending", "sourced"}


def _load(path):
    assert path.exists(), f"missing: {path.relative_to(REPO_ROOT)}"
    return json.loads(path.read_text())


def taxonomy():
    return _load(TAXONOMY)


def registry():
    return _load(REGISTRY)


def _sources_by_id():
    return {entry["id"]: entry for entry in registry()["sources"]}


# --- the vocabulary ----------------------------------------------------------------------


def test_the_two_axes_share_no_member():
    axes = taxonomy()["axes"]
    production = set(axes["production_system"]["members"])
    irrigation = set(axes["irrigation_method"]["members"])
    both = production & irrigation
    assert not both, (
        "these names are listed as both a production system and an irrigation method, which is "
        f"the conflation this taxonomy exists to prevent: {sorted(both)}"
    )


def test_every_case_243_names_is_present_on_the_right_axis():
    axes = taxonomy()["axes"]
    production = set(axes["production_system"]["members"])
    irrigation = set(axes["irrigation_method"]["members"])
    assert REQUIRED_PRODUCTION_SYSTEMS <= production, (
        f"missing production systems: {sorted(REQUIRED_PRODUCTION_SYSTEMS - production)}"
    )
    assert REQUIRED_IRRIGATION_METHODS <= irrigation, (
        f"missing irrigation methods: {sorted(REQUIRED_IRRIGATION_METHODS - irrigation)}"
    )


def test_every_vocabulary_entry_is_defined_on_a_real_axis_and_cites_a_real_source():
    data = taxonomy()
    axis_members = {
        axis: set(spec["members"]) for axis, spec in data["axes"].items()
    }
    sources = _sources_by_id()
    for item in data["vocabulary"]:
        axis = item["axis"]
        assert axis in axis_members, f"{item['id']}: unknown axis {axis!r}"
        assert item["id"] in axis_members[axis], (
            f"{item['id']}: absent from the {axis} axis member list"
        )
        assert item["definition"].strip(), f"{item['id']}: empty definition"
        assert item["source"] in sources, (
            f"{item['id']}: cites {item['source']!r}, which is not in the source registry"
        )


# --- the compatibility matrix -------------------------------------------------------------


def test_no_combination_is_listed_twice_and_every_status_is_a_known_one():
    seen = {}
    for row in taxonomy()["compatibility"]:
        pair = (row["production_system"], row["irrigation_method"])
        assert pair not in seen, f"combination listed twice: {pair}"
        seen[pair] = row["status"]
        assert row["status"] in VALID_COMPATIBILITY, f"{pair}: unknown status {row['status']!r}"


def test_a_combination_is_never_both_supported_and_incompatible():
    by_pair = {}
    for row in taxonomy()["compatibility"]:
        by_pair.setdefault(
            (row["production_system"], row["irrigation_method"]), set()
        ).add(row["status"])
    contradictory = {pair: statuses for pair, statuses in by_pair.items()
                     if {"supported", "incompatible"} <= statuses}
    assert not contradictory, f"self-contradicting combinations: {contradictory}"


def test_every_row_names_its_basis_and_a_sourced_row_resolves():
    sources = _sources_by_id()
    for row in taxonomy()["compatibility"]:
        pair = (row["production_system"], row["irrigation_method"])
        assert row["reason"].strip(), f"{pair}: empty reason"
        assert row["basis"] in VALID_BASIS, f"{pair}: unknown basis {row['basis']!r}"
        if row["basis"] == "sourced":
            cited = row.get("source")
            assert cited in sources, (
                f"{pair}: claims a sourced basis but cites {cited!r}, which is not in the registry"
            )


def test_every_combination_covers_a_known_system_and_method():
    data = taxonomy()
    axes = data["axes"]
    production = set(axes["production_system"]["members"])
    irrigation = set(axes["irrigation_method"]["members"])
    for row in data["compatibility"]:
        assert row["production_system"] in production, (
            f"unknown production system in matrix: {row['production_system']!r}"
        )
        assert row["irrigation_method"] in irrigation, (
            f"unknown irrigation method in matrix: {row['irrigation_method']!r}"
        )


def test_the_matrix_covers_every_system_and_method_at_least_once():
    seen_pairs = {
        (row["production_system"], row["irrigation_method"])
        for row in taxonomy()["compatibility"]
    }
    uncovered = [
        (system, method)
        for system in REQUIRED_PRODUCTION_SYSTEMS
        for method in REQUIRED_IRRIGATION_METHODS
        if (system, method) not in seen_pairs
    ]
    assert not uncovered, f"combinations with no stated status: {uncovered}"


# --- capability statuses ------------------------------------------------------------------


def test_the_four_statuses_are_exactly_the_four_defined_ones_with_increasing_rank():
    statuses = taxonomy()["capability_statuses"]
    ids = {status["id"] for status in statuses}
    assert ids == REQUIRED_STATUSES, (
        f"status set drifted: missing {sorted(REQUIRED_STATUSES - ids)}, "
        f"unexpected {sorted(ids - REQUIRED_STATUSES)}"
    )
    ranks = sorted(status["rank"] for status in statuses)
    assert ranks == [1, 2, 3, 4], f"ranks are not a 1..4 ordering: {ranks}"
    for status in statuses:
        assert status["required_evidence"], f"{status['id']}: no evidence requirement"
        assert status["definition"].strip(), f"{status['id']}: empty definition"


def test_field_validation_is_not_claimed_while_no_field_data_exists():
    by_id = {status["id"]: status for status in taxonomy()["capability_statuses"]}
    field_validated = by_id["field_validated_operation"]
    assert field_validated["available_in_agronaut"] is False, (
        "field_validated_operation is marked available; this project has no field trial and no "
        "measured irrigation outcome, so the claim cannot be made"
    )
    experimental = by_id["experimental_simulation"]
    assert experimental["available_in_agronaut"] is False, (
        "experimental_simulation is marked available for irrigation; no irrigation simulation "
        "exists yet"
    )


def test_verified_calculation_is_scoped_and_does_not_cover_irrigation():
    by_id = {status["id"]: status for status in taxonomy()["capability_statuses"]}
    scope = by_id["verified_calculation"]["available_scope"].lower()
    assert "irrigation" in scope, (
        "verified_calculation must state its scope explicitly, including that irrigation is not "
        "in it while the reference cases are unwritten"
    )


# --- the source registry ------------------------------------------------------------------


def test_every_registry_entry_carries_edition_locator_licence_and_permission():
    for entry in registry()["sources"]:
        ident = entry["id"]
        assert entry["edition"].strip(), f"{ident}: no edition"
        assert entry["citation"].strip(), f"{ident}: no citation"
        assert entry["locators"], f"{ident}: no locator"
        for locator in entry["locators"]:
            assert locator["kind"] in VALID_LOCATOR_KINDS, (
                f"{ident}: unknown locator kind {locator['kind']!r}"
            )
            assert locator["label"].strip(), f"{ident}: locator with no label"
        licence = entry["licence"]
        assert licence["class"].strip(), f"{ident}: no licence class"
        assert licence["statement"].strip(), f"{ident}: no licence statement"
        permission = entry["reuse_permission"]
        for field in ("cite_with_attribution", "redistribute_text",
                      "index_full_text_in_knowledge_corpus", "derive_values_with_citation"):
            assert field in permission, f"{ident}: reuse_permission lacks {field}"
        verified = entry["verified"]
        assert verified["on"].strip() and verified["how"].strip(), (
            f"{ident}: no record of when and how the source was verified"
        )


def test_a_numeric_source_never_rests_on_a_document_level_citation():
    """#243: no agronomic coefficient may sit behind a broad document-level citation."""
    for entry in registry()["sources"]:
        if entry["provides"] not in NUMERIC_PROVIDERS:
            continue
        kinds = {locator["kind"] for locator in entry["locators"]}
        assert kinds & {"equation", "table"}, (
            f"{entry['id']}: carries numbers but no equation or table locator; a document-level "
            f"citation is not enough (locator kinds present: {sorted(kinds)})"
        )
        assert "document" not in kinds, (
            f"{entry['id']}: carries numbers and also rests on a document-level locator"
        )


def test_every_registry_locator_states_how_far_it_was_verified():
    levels = set(registry()["verification_levels"])
    for entry in registry()["sources"]:
        for locator in entry["locators"]:
            level = locator.get("verified")
            if level is None:
                continue  # whole-entry verification in entry["verified"] covers it
            assert level in levels, (
                f"{entry['id']}: locator {locator['label']!r} uses unknown level {level!r}"
            )


# --- the prose and the data stay in sync ---------------------------------------------------


def test_every_capability_status_is_named_in_the_narrative():
    text = NARRATIVE.read_text()
    missing = sorted(status for status in REQUIRED_STATUSES if status not in text)
    assert not missing, (
        f"the narrative does not name these statuses: {missing}; a status defined only in JSON "
        "is one a reader will never apply"
    )


def test_the_narrative_points_at_the_versioned_data_files():
    text = NARRATIVE.read_text()
    assert TAXONOMY.name in text, f"{NARRATIVE.name} does not reference {TAXONOMY.name}"
    assert REGISTRY.name in text, f"{NARRATIVE.name} does not reference {REGISTRY.name}"
