"""Cost estimation: the sized design, priced with researched regional prices.

A design conversation ends with "what will this cost me?", and the honest answer has three
parts this module keeps separate:

    the QUANTITY TAKEOFF   — what to buy and how much, derived from the design and layout
                             (deterministic, auditable, no prices involved);
    the PRICE BOOK         — researched regional prices with source and date, loaded from
                             `data/price_book.json` by the CALLER (this module never reads
                             a file — trust-zone rules);
    the ESTIMATE           — takeoff x prices, as RANGES, because a price book is research,
                             not a quote.

Every line carries its unit price's source; missing prices appear as explicit UNPRICED
lines instead of silently shrinking the total — an estimate that omits the greenhouse
because nobody priced greenhouses is worse than no estimate. Totals therefore state both
the priced subtotal and what is missing from it.

The price book's numbers are seeds in the same sense as every coefficient here: researched,
cited, dated, and meant to be replaced by local quotes. The output says so.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .layout import Layout
from .system_types import get_system_type
from .types import DesignOutput

NOT_INCLUDED = (
    "land, site preparation and foundations",
    "labour (a labour_day price may exist in the book, but build hours vary too much to guess)",
    "delivery and transport",
    "permits and water rights",
    "backup power (a battery or generator is strongly advised for NFT and high stocking)",
    "plumbing sundries beyond the fitting allowance (valves, unions, bulkheads vary by build)",
)

# Fitting allowance on straight pipe: elbows, tees and glue typically add ~30% to a run.
_PIPE_FITTING_FACTOR = 1.3
# Air stones scale with tanks and beds (FAO 589: one per 2-4 m2 of raft canal).
_RAFT_M2_PER_AIRSTONE = 3.0


@dataclass(frozen=True)
class TakeoffLine:
    """One thing to buy: a quantity with a unit, before any price is attached."""

    key: str          # canonical price-book key, e.g. "tank_1000l"
    label: str
    qty: float
    unit: str
    note: str = ""


@dataclass(frozen=True)
class CostLine:
    takeoff: TakeoffLine
    unit_price: float | None = None      # None => unpriced in this region's book
    low: float | None = None
    high: float | None = None
    source: str = ""

    def subtotal(self) -> tuple[float, float, float] | None:
        if self.unit_price is None:
            return None
        lo = self.low if self.low is not None else self.unit_price
        hi = self.high if self.high is not None else self.unit_price
        q = self.takeoff.qty
        return (q * lo, q * self.unit_price, q * hi)


@dataclass(frozen=True)
class CostEstimate:
    region: str
    currency: str
    as_of: str
    capex: tuple[CostLine, ...]
    opex_per_year: tuple[CostLine, ...]
    unpriced: tuple[str, ...]            # labels the book could not price
    not_included: tuple[str, ...] = NOT_INCLUDED

    def _total(self, lines: tuple[CostLine, ...]) -> tuple[float, float, float]:
        lo = mid = hi = 0.0
        for line in lines:
            s = line.subtotal()
            if s:
                lo, mid, hi = lo + s[0], mid + s[1], hi + s[2]
        return lo, mid, hi

    def capex_total(self) -> tuple[float, float, float]:
        return self._total(self.capex)

    def opex_total(self) -> tuple[float, float, float]:
        return self._total(self.opex_per_year)


def takeoff(out: DesignOutput, layout: Layout, *, species_key: str = "tilapia",
            greenhouse_mode: str = "poly") -> list[TakeoffLine]:
    """What the design needs bought, in price-book units. Quantities come from the same
    numbers the 3D layout draws, so the estimate prices the system the user saw."""
    system = get_system_type(out.system_type)
    lines: list[TakeoffLine] = []

    tanks = layout.by_role("fish_tank")
    tank_units = sum(max(0.25, (math.pi * (t.d / 2) ** 2 * t.h) ) for t in tanks)
    lines.append(TakeoffLine("tank_1000l", "fish/rearing tanks", round(tank_units, 1),
                             "x 1000 L equivalent", f"{len(tanks)} tank(s) + sump/clarifier below"))
    # clarifier + sump volumes also hold water and cost roughly like tanks per litre
    aux = layout.by_role("clarifier") + layout.by_role("biofilter") + layout.by_role("sump")
    aux_m3 = 0.0
    for c in aux:
        aux_m3 += (math.pi * (c.d / 2) ** 2 * c.h) if c.kind == "cyl" else c.w * c.l * c.h
    if aux_m3:
        lines.append(TakeoffLine("tank_1000l", "filtration vessels (clarifier/biofilter/sump)",
                                 round(aux_m3, 1), "x 1000 L equivalent"))

    if system.key == "raft":
        lines.append(TakeoffLine("raft_foam_m2", "raft boards", round(out.grow_area_m2, 1), "m²"))
        lines.append(TakeoffLine("liner_m2", "canal liner",
                                 round(out.grow_area_m2 * 1.4, 1), "m²",
                                 "bed floor + walls allowance"))
    elif system.key == "media_bed":
        media_m3 = out.grow_area_m2 * system.water_depth_m * 1.5
        lines.append(TakeoffLine("gravel_m3", "grow-bed media (gravel/LECA)",
                                 round(media_m3, 1), "m³", "bed volume x 1.5 settling"))
        lines.append(TakeoffLine("liner_m2", "bed liner", round(out.grow_area_m2 * 1.6, 1), "m²"))
    elif system.key == "nft":
        channel_m = out.grow_area_m2 / 0.15   # one 0.15 m-wide planted strip per channel run
        lines.append(TakeoffLine("nft_channel_m", "NFT channels", round(channel_m, 1), "m"))
    elif system.key == "vertical_tower":
        towers = max(1, round(out.grow_area_m2 / (system.footprint_ratio * 0.4 * 0.4) / 6))
        lines.append(TakeoffLine("vertical_tower_unit", "grow towers", towers, "towers",
                                 "media-filled vertical units incl. drip line"))

    if not system.provides_biofiltration and out.biofilter_media_m2:
        lines.append(TakeoffLine("biofilter_media_m3", "biofilter media",
                                 round(out.biofilter_media_m2 / 200.0 / 0.6, 2), "m³",
                                 "at ~200 m²/m³ specific surface, 60% packing"))

    lines.append(TakeoffLine("pump_small", "water pump",
                             max(1, len(tanks) // 3 + 1), "unit",
                             f"≥{out.pump_turnover_lph:.0f} L/h at ~{out.pump_head_m:.1f} m head"))
    n_stones = len(tanks) + max(1, round(out.grow_area_m2 / _RAFT_M2_PER_AIRSTONE)) \
        if system.key == "raft" else len(tanks) + 2
    lines.append(TakeoffLine("air_pump", "air pump/blower", 1, "unit"))
    lines.append(TakeoffLine("air_stone", "air stones + tubing", n_stones, "unit"))

    pipe_m = sum(
        math.dist(a, b)
        for p in layout.pipes for a, b in zip(p.path, p.path[1:])
    ) * _PIPE_FITTING_FACTOR
    lines.append(TakeoffLine("pvc_pipe_m", "PVC pipe + fitting allowance",
                             round(pipe_m, 1), "m", "layout runs x 1.3 for fittings"))

    gh = layout.greenhouse
    gh_area = gh.width_m * gh.length_m
    if greenhouse_mode == "shade":
        lines.append(TakeoffLine("shade_net_m2", "shade-net structure", round(gh_area, 1), "m²"))
    else:
        lines.append(TakeoffLine("greenhouse_poly_m2", "poly tunnel (structure + film)",
                                 round(gh_area, 1), "m²"))

    fingerling_key = ("fingerling_clarias" if species_key == "clarias"
                      else "fingerling_tilapia")
    lines.append(TakeoffLine(fingerling_key, "fingerlings", out.fish_count, "head",
                             "first stocking"))
    return lines


def opex_takeoff(out: DesignOutput) -> list[TakeoffLine]:
    """Running quantities per year, from the same design numbers."""
    feed_kg_yr = out.feed_g_per_day * 365.0 / 1000.0
    kwh_yr = out.pump_power_w * 24.0 * 365.0 / 1000.0
    water_m3_yr = out.makeup_water_lpd * 365.0 / 1000.0
    return [
        TakeoffLine("feed_kg", "fish feed", round(feed_kg_yr, 1), "kg/yr",
                    "at the design feed rate; a growing cohort averages less in year one"),
        TakeoffLine("electricity_kwh", "electricity (pump, continuous)",
                    round(kwh_yr, 1), "kWh/yr", "aeration adds ~30-60% on top"),
        TakeoffLine("water_m3", "make-up water", round(water_m3_yr, 1), "m³/yr"),
    ]


def _price(book_items: dict, line: TakeoffLine) -> CostLine:
    it = book_items.get(line.key)
    if not it:
        return CostLine(takeoff=line)
    return CostLine(takeoff=line, unit_price=float(it["price"]),
                    low=float(it.get("low", it["price"])),
                    high=float(it.get("high", it["price"])),
                    source=str(it.get("source", "")))


def estimate_cost(out: DesignOutput, layout: Layout, price_book: dict, region: str, *,
                  species_key: str = "tilapia", greenhouse_mode: str = "poly") -> CostEstimate:
    """Price the takeoff with one region's book. Raises KeyError for an unknown region —
    a wrong region must fail loudly, not price Ouagadougou in Taiwan dollars."""
    reg = price_book["regions"][region]
    items = reg.get("items", {})
    capex = tuple(_price(items, t) for t in takeoff(
        out, layout, species_key=species_key, greenhouse_mode=greenhouse_mode))
    opex = tuple(_price(items, t) for t in opex_takeoff(out))
    unpriced = tuple(line.takeoff.label for line in capex + opex if line.unit_price is None)
    return CostEstimate(region=region, currency=str(reg.get("currency", "?")),
                        as_of=str(reg.get("as_of", "?")), capex=capex,
                        opex_per_year=opex, unpriced=unpriced)


def format_estimate(est: CostEstimate) -> str:
    """Operator-facing cost sheet. Ranges, sources, and what is NOT in the number."""
    def money(v: float) -> str:
        return f"{v:,.0f}"

    lines = [f"Cost estimate — {est.region} ({est.currency}, prices as of {est.as_of})", ""]
    lines.append("BUILD (capex):")
    for line in est.capex:
        s = line.subtotal()
        t = line.takeoff
        if s:
            lines.append(f"  {t.label:<38} {t.qty:g} {t.unit:<18} "
                         f"{money(s[0])}–{money(s[2])}")
        else:
            lines.append(f"  {t.label:<38} {t.qty:g} {t.unit:<18} UNPRICED")
    lo, mid, hi = est.capex_total()
    lines.append(f"  {'TOTAL (priced lines)':<38} {'':<20} {money(lo)}–{money(hi)}"
                 f"  (mid {money(mid)})")
    lines.append("")
    lines.append("RUN (opex, per year):")
    for line in est.opex_per_year:
        s = line.subtotal()
        t = line.takeoff
        if s:
            lines.append(f"  {t.label:<38} {t.qty:g} {t.unit:<18} "
                         f"{money(s[0])}–{money(s[2])}")
        else:
            lines.append(f"  {t.label:<38} {t.qty:g} {t.unit:<18} UNPRICED")
    lo, mid, hi = est.opex_total()
    lines.append(f"  {'TOTAL (priced lines)':<38} {'':<20} {money(lo)}–{money(hi)}")
    if est.unpriced:
        lines.append("")
        lines.append("Unpriced in this region's book (total EXCLUDES these): "
                     + ", ".join(dict.fromkeys(est.unpriced)))
    lines += ["", "Not included: " + "; ".join(est.not_included[:4]) + "; ...",
              "",
              "These are researched price ranges with sources and dates "
              "(data/price_book.json), not quotes — verify locally before budgeting."]
    return "\n".join(lines)
