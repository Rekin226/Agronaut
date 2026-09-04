"""The shipped `data/price_book.json` — checked as data, not exercised as code.

Every other costing and business test runs against a synthetic fixture, so until this
file existed nothing looked at the book we actually ship. That gap has a specific shape:
a region can carry a key the code never reads, and nothing complains.

That is the failure this module is really for. A MISSING price is safe — the estimator
prints `UNPRICED` and excludes it loudly, which the contributing guide names as the
honest outcome. A MISTYPED price is not: `tank_1000L` or `fish_tilapa` reads as present
to a human reviewing the JSON, is invisible to `book_items.get(...)`, and produces
exactly the same `UNPRICED` line as a deliberate omission. The contributor believes they
priced it, the reviewer sees a plausible entry, and the estimate is quietly short.

So the rule here is: you may leave an item out, but you may not misspell one.
"""

import json
import pathlib

import pytest

from aqua_model.crops import CROPS
from aqua_model.species import SPECIES

_BOOK_PATH = pathlib.Path(__file__).resolve().parents[2] / "data" / "price_book.json"

# Keys `costing.takeoff`/`opex_takeoff` can ask for. Kept as a literal rather than
# scraped from the source: a test that derives its expectations from the code under
# test agrees with that code by construction, including when the code is wrong.
_COST_KEYS = frozenset({
    "air_pump", "biofilter_media_m3", "electricity_kwh", "feed_kg", "gravel_m3",
    "greenhouse_poly_m2", "liner_m2", "nft_channel_m", "pump_small", "pvc_pipe_m",
    "raft_foam_m2", "shade_net_m2", "tank_1000l", "vertical_tower_unit", "water_m3",
    # costing.py picks the fingerling key by species; only these two are ever built.
    "fingerling_clarias", "fingerling_tilapia",
    # Read by business.py, not costing.py — labour is excluded from a case unless the
    # caller supplies hours, and this is the rate it uses when they do.
    "labour_day",
})

# business.py looks up `fish_{species}` falling back to `fish_generic`, and
# `crop_{crop}` falling back to `crop_{category}`.
_CROP_CATEGORIES = frozenset({"leafy", "fruiting"})
_REVENUE_KEYS = (
    {f"fish_{s}" for s in SPECIES} | {"fish_generic"}
    | {f"crop_{c}" for c in CROPS} | {f"crop_{c}" for c in _CROP_CATEGORIES}
)

# Priced ahead of the catalogue: a real local market price for something `aqua_model`
# cannot grow yet, so the number is ready on the day the crop lands. Deliberate, and
# listed here so it stays deliberate — an entry that drops off this list without its
# crop appearing in CROPS is a typo, which is exactly what this module exists to catch.
#
# Empty, and that is the resting state. `crop_amaranth` (Burkina Faso) lived here until
# the amaranth crop entry landed in #104; the test below is what noticed, and the entry
# is now checked as a normal revenue key. Add to this set only alongside an issue number.
_PRICED_AHEAD_OF_CATALOGUE: frozenset[str] = frozenset()

# Documented in the contributing guide and in issue #105.
_BASIS_VALUES = frozenset({"official", "retail_snapshot", "guide", "derived", "quote"})


@pytest.fixture(scope="module")
def book():
    return json.loads(_BOOK_PATH.read_text(encoding="utf-8"))


def _entries(book):
    """(region, group, key, entry) for every priced line in the book."""
    for region, reg in book["regions"].items():
        for group in ("items", "revenue_items"):
            for key, entry in reg.get(group, {}).items():
                yield region, group, key, entry


def test_the_shipped_book_is_valid_json_with_regions(book):
    assert book["regions"], "price book has no regions"


def test_every_key_is_one_the_code_can_actually_read(book):
    """A key nothing looks up is a typo wearing a plausible name.

    This is the whole point of the module: a misspelled key is indistinguishable from
    an omitted one at runtime, so it has to be caught here instead.
    """
    legal = {
        "items": _COST_KEYS,
        "revenue_items": _REVENUE_KEYS | _PRICED_AHEAD_OF_CATALOGUE,
    }
    unreadable = [
        f"{region}.{group}.{key}"
        for region, group, key, _ in _entries(book)
        if key not in legal[group]
    ]
    assert not unreadable, (
        "these keys are in the book but no code path requests them, so they are priced "
        "in the file and UNPRICED in every estimate: " + ", ".join(sorted(unreadable))
    )


def test_nothing_priced_ahead_of_the_catalogue_has_quietly_landed(book):
    """When the crop arrives, its price stops being an exception and becomes a normal one.

    Without this, `_PRICED_AHEAD_OF_CATALOGUE` only ever grows, and the typo-catching
    above weakens by exactly one key each time someone adds to it.
    """
    landed = sorted(k for k in _PRICED_AHEAD_OF_CATALOGUE if k in _REVENUE_KEYS)
    assert not landed, (
        "these are now real catalogue entries — drop them from "
        "_PRICED_AHEAD_OF_CATALOGUE so they are checked normally: " + ", ".join(landed)
    )


def test_price_bounds_are_ordered(book):
    """`low <= price <= high`, or the range says the opposite of what it means."""
    bad = []
    for region, group, key, e in _entries(book):
        price, low, high = e.get("price"), e.get("low"), e.get("high")
        if low is not None and price is not None and low > price:
            bad.append(f"{region}.{group}.{key}: low {low} > price {price}")
        if high is not None and price is not None and high < price:
            bad.append(f"{region}.{group}.{key}: high {high} < price {price}")
    assert not bad, "; ".join(bad)


def test_every_entry_has_a_price_a_unit_and_a_source(book):
    """A number with no source is the one thing this book must never carry."""
    bad = []
    for region, group, key, e in _entries(book):
        where = f"{region}.{group}.{key}"
        if not isinstance(e.get("price"), (int, float)):
            bad.append(f"{where}: price is not a number")
        if not str(e.get("source", "")).strip():
            bad.append(f"{where}: no source")
        if not str(e.get("unit", "")).strip():
            bad.append(f"{where}: no unit")
    assert not bad, "; ".join(bad)


def test_basis_tags_come_from_the_documented_vocabulary(book):
    """`basis` is how a reader weighs a number; an unknown tag silently means nothing."""
    bad = [
        f"{region}.{group}.{key}={e.get('basis')!r}"
        for region, group, key, e in _entries(book)
        if e.get("basis") not in _BASIS_VALUES
    ]
    assert not bad, (
        "unknown basis tags (expected one of "
        f"{sorted(_BASIS_VALUES)}): " + ", ".join(sorted(bad))
    )


def test_every_region_declares_its_currency_and_vintage(book):
    """Prices are in local currency, so an undeclared currency makes them unreadable."""
    bad = []
    for region, reg in book["regions"].items():
        if not str(reg.get("currency", "")).strip():
            bad.append(f"{region}: no currency")
        if not str(reg.get("as_of", "")).strip():
            bad.append(f"{region}: no as_of")
        if not reg.get("items"):
            bad.append(f"{region}: no items")
    assert not bad, "; ".join(bad)
