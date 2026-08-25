"""The business case: what the system costs, what it produces, and whether that clears.

`costing.py` says what a build costs. `production.py` says what it grows. Neither answers
the question every operator actually decides on:

    "If I build this, do I make money — and when do I get my money back?"

This module joins them. Revenue is the twin's projected harvest priced at researched
farm-gate prices (the price book's `revenue_items`); margin is revenue minus the same
year's operating cost; payback is capex over that margin.

Three honesty rules, because a business case is the easiest place in this project to
mislead someone into spending their savings:

1. **LABOUR IS THE VERDICT-FLIPPER.** The knowledge base states the consensus plainly:
   many aquaponic operations are not viable, and the usual reason is that unpaid labour
   hides the loss. So labour is EXCLUDED by default and named as excluded, and when the
   caller supplies hours the case reports the verdict both ways — with and without.
2. **A LOSS IS REPORTED AS A LOSS.** No softening. If the margin does not clear, the
   verdict says the design is a hobby or a food-security project, not a business.
3. **UNPRICED LINES PROPAGATE.** If the cost side could not price the greenhouse, or the
   revenue side has no price for the crop, the case says the number is incomplete and in
   which direction it is wrong.

Pure and deterministic; the price book arrives as data, loaded by the caller.
"""

from __future__ import annotations

from dataclasses import dataclass

from .costing import CostEstimate
from .production import ProductionSummary

NOT_INCLUDED = (
    "labour, unless hours are supplied — pricing your own time usually flips the verdict",
    "land rent or purchase, and site preparation",
    "financing costs and inflation over the payback period",
    "market access: this assumes you can sell everything at the quoted farm-gate price",
    "post-harvest losses, spoilage, and unsold stock",
    "equipment replacement (pumps and media do not last forever)",
    "taxes, permits and insurance",
)

# Below this the harvest is not a business at any price, and a payback number would be
# arithmetic theatre.
_MATERIAL_MARGIN = 1e-6


@dataclass(frozen=True)
class RevenueLine:
    label: str
    qty_kg: float
    unit_price: float | None
    low: float | None
    high: float | None
    source: str = ""

    def subtotal(self) -> tuple[float, float, float] | None:
        if self.unit_price is None:
            return None
        lo = self.low if self.low is not None else self.unit_price
        hi = self.high if self.high is not None else self.unit_price
        return (self.qty_kg * lo, self.qty_kg * self.unit_price, self.qty_kg * hi)


@dataclass(frozen=True)
class BusinessCase:
    region: str
    currency: str
    revenue: tuple[RevenueLine, ...]
    capex: tuple[float, float, float]
    opex_per_year: tuple[float, float, float]
    labour_cost_per_year: float | None      # None => labour not priced into the case
    labour_note: str
    unpriced_cost: tuple[str, ...]
    unpriced_revenue: tuple[str, ...]
    verdict: str
    findings: tuple[str, ...]
    not_included: tuple[str, ...] = NOT_INCLUDED

    def revenue_total(self) -> tuple[float, float, float]:
        lo = mid = hi = 0.0
        for line in self.revenue:
            s = line.subtotal()
            if s:
                lo, mid, hi = lo + s[0], mid + s[1], hi + s[2]
        return lo, mid, hi

    def margin_per_year(self, *, with_labour: bool = False) -> tuple[float, float, float]:
        """Revenue minus operating cost. Ranges are paired worst-to-worst and best-to-best:
        the low margin is the low revenue against the HIGH cost, which is the case an
        operator should plan against."""
        r_lo, r_mid, r_hi = self.revenue_total()
        c_lo, c_mid, c_hi = self.opex_per_year
        labour = (self.labour_cost_per_year or 0.0) if with_labour else 0.0
        return (r_lo - c_hi - labour, r_mid - c_mid - labour, r_hi - c_lo - labour)

    def payback_years(self, *, with_labour: bool = False) -> float | None:
        """Capex over the mid margin. None when the system does not clear its own
        running costs — there is no payback period for a loss."""
        margin = self.margin_per_year(with_labour=with_labour)[1]
        if margin <= _MATERIAL_MARGIN:
            return None
        return self.capex[1] / margin


def _revenue_price(items: dict, key: str, fallback: str | None) -> tuple[dict | None, str]:
    it = items.get(key)
    if it:
        return it, key
    if fallback and items.get(fallback):
        return items[fallback], fallback
    return None, key


def build_case(
    summary: ProductionSummary,
    cost: CostEstimate,
    price_book: dict,
    region: str,
    *,
    crop_key: str,
    species_key: str,
    crop_category: str = "leafy",
    labour_hours_per_week: float | None = None,
    hours_per_labour_day: float = 8.0,
) -> BusinessCase:
    """Join a season's harvest to its cost, at one region's prices.

    `summary` should cover one year; a shorter run is scaled to a year and the scaling is
    reported, because comparing a 6-month harvest to a 12-month cost is the classic way to
    make a losing system look profitable."""
    reg = price_book["regions"][region]
    rev_items = reg.get("revenue_items", {})
    cost_items = reg.get("items", {})
    findings: list[str] = []

    scale = 365.0 / summary.days if summary.days else 1.0
    if abs(scale - 1.0) > 0.02:
        findings.append(
            f"the simulated season is {summary.days} days; harvests scaled x{scale:.2f} to "
            "a year — a partial season carries the stocking transient, so this favours "
            "neither side cleanly")

    fish_kg = (summary.fish_harvested_kg + summary.fish_standing_kg) * scale
    crop_kg = summary.crop_harvested_kg * scale

    fish_price, fish_used = _revenue_price(rev_items, f"fish_{species_key}", "fish_generic")
    crop_price, crop_used = _revenue_price(rev_items, f"crop_{crop_key}",
                                           f"crop_{crop_category}")
    if fish_price and fish_used != f"fish_{species_key}":
        findings.append(f"no {species_key} price in this region's book — priced at the "
                        f"generic fish rate ({fish_used})")
    if crop_price and crop_used != f"crop_{crop_key}":
        findings.append(f"no {crop_key} price in this region's book — priced at the "
                        f"{crop_category} rate ({crop_used})")

    revenue = (
        RevenueLine("fish (harvested + standing)", round(fish_kg, 1),
                    fish_price and float(fish_price["price"]),
                    fish_price and float(fish_price.get("low", fish_price["price"])),
                    fish_price and float(fish_price.get("high", fish_price["price"])),
                    fish_price.get("source", "") if fish_price else ""),
        RevenueLine("crop", round(crop_kg, 1),
                    crop_price and float(crop_price["price"]),
                    crop_price and float(crop_price.get("low", crop_price["price"])),
                    crop_price and float(crop_price.get("high", crop_price["price"])),
                    crop_price.get("source", "") if crop_price else ""),
    )
    unpriced_revenue = tuple(line.label for line in revenue if line.unit_price is None)

    # Labour: only priced when the caller supplies hours AND the book has a day rate.
    labour_cost: float | None = None
    labour_note = ("labour NOT included — pricing your own time is what usually decides "
                   "whether a system like this is a business or a hobby")
    day_rate = cost_items.get("labour_day")
    if labour_hours_per_week is not None:
        if day_rate:
            days_per_year = labour_hours_per_week * 52.0 / hours_per_labour_day
            labour_cost = days_per_year * float(day_rate["price"])
            labour_note = (f"labour priced at {labour_hours_per_week:g} h/week "
                           f"({days_per_year:.0f} labour-days/yr at "
                           f"{float(day_rate['price']):,.0f} {cost.currency}/day)")
        else:
            labour_note = ("labour hours were given, but this region's book has no "
                           "labour_day rate — labour is still excluded")
            findings.append("labour could not be priced in this region")

    # The single most decisive ratio in fish farming: what the feed to grow one kilogram
    # of fish costs, against what that kilogram sells for. If feed alone approaches the
    # farm-gate price, no amount of scale or efficiency elsewhere rescues the business —
    # and this is exactly the squeeze that makes imported feed fatal in West Africa.
    feed_item = cost_items.get("feed_kg")
    if feed_item and fish_price and summary.realized_fcr > 0:
        feed_per_kg_fish = summary.realized_fcr * float(feed_item["price"])
        fish_per_kg = float(fish_price["price"])
        share = feed_per_kg_fish / fish_per_kg if fish_per_kg > 0 else 0.0
        if share >= 1.0:
            findings.append(
                f"FEED COSTS MORE THAN THE FISH SELLS FOR: {feed_per_kg_fish:,.0f} of feed "
                f"per kg of fish (FCR {summary.realized_fcr:.1f} x {float(feed_item['price']):,.0f}"
                f"/kg) against a farm-gate price of {fish_per_kg:,.0f} {cost.currency}/kg. "
                "Cheaper or locally-made feed is the only fix that matters here")
        elif share >= 0.6:
            findings.append(
                f"feed is {share:.0%} of the fish's farm-gate value "
                f"({feed_per_kg_fish:,.0f} vs {fish_per_kg:,.0f} {cost.currency}/kg) — "
                "the margin lives or dies on feed price")

    case = BusinessCase(
        region=region, currency=cost.currency, revenue=revenue,
        capex=cost.capex_total(), opex_per_year=cost.opex_total(),
        labour_cost_per_year=labour_cost, labour_note=labour_note,
        unpriced_cost=cost.unpriced, unpriced_revenue=unpriced_revenue,
        verdict="", findings=tuple(findings))

    verdict, more = _verdict(case, summary)
    return BusinessCase(**{**case.__dict__, "verdict": verdict,
                           "findings": case.findings + tuple(more)})


def _verdict(case: BusinessCase, summary: ProductionSummary) -> tuple[str, list[str]]:
    findings: list[str] = []
    margin = case.margin_per_year()[1]
    r_mid = case.revenue_total()[1]
    c_mid = case.opex_per_year[1]

    if case.unpriced_revenue:
        findings.append(
            "revenue is INCOMPLETE — no price for: " + ", ".join(case.unpriced_revenue) +
            "; the real revenue is higher than this figure")
    if case.unpriced_cost:
        findings.append(
            "cost is INCOMPLETE — unpriced: " + ", ".join(dict.fromkeys(case.unpriced_cost)) +
            "; the real cost is higher than this figure")

    if margin <= 0:
        verdict = (f"This does not clear its own running costs: revenue {r_mid:,.0f} vs "
                   f"operating cost {c_mid:,.0f} {case.currency}/year. As designed it is a "
                   "food-security or hobby system, not a business — which is a fine goal, "
                   "but it should be chosen deliberately.")
        return verdict, findings

    payback = case.payback_years()
    verdict = (f"Gross margin about {margin:,.0f} {case.currency}/year before labour; "
               f"simple payback on the build in roughly {payback:.1f} years.")
    if case.labour_cost_per_year:
        m_lab = case.margin_per_year(with_labour=True)[1]
        p_lab = case.payback_years(with_labour=True)
        if m_lab <= 0:
            verdict += (f" WITH labour priced in it loses {abs(m_lab):,.0f} "
                        f"{case.currency}/year — the labour is the business case.")
        else:
            verdict += (f" With labour priced in: {m_lab:,.0f} {case.currency}/year, "
                        f"payback about {p_lab:.1f} years.")
    if payback and payback > 10:
        findings.append("a payback beyond ten years is longer than most of this equipment "
                        "lasts — treat the build as the product, not the investment")
    if summary.limiting_factor:
        findings.append(f"the crop was limited by {summary.limiting_factor} — fixing that "
                        "is usually cheaper than expanding the system")
    return verdict, findings


def format_case(case: BusinessCase) -> str:
    def money(v: float) -> str:
        return f"{v:,.0f}"

    lines = [f"Business case — {case.region} ({case.currency}/year)", ""]
    lines.append("REVENUE (projected harvest at farm-gate prices):")
    for line in case.revenue:
        s = line.subtotal()
        if s:
            lines.append(f"  {line.label:<34} {line.qty_kg:>9,.1f} kg   "
                         f"{money(s[0])}–{money(s[2])}")
        else:
            lines.append(f"  {line.label:<34} {line.qty_kg:>9,.1f} kg   UNPRICED")
    r = case.revenue_total()
    lines.append(f"  {'TOTAL':<34} {'':>12}   {money(r[0])}–{money(r[2])}")
    lines += ["", "COSTS:",
              f"  {'build (one-time)':<34} {'':>12}   "
              f"{money(case.capex[0])}–{money(case.capex[2])}",
              f"  {'running (per year)':<34} {'':>12}   "
              f"{money(case.opex_per_year[0])}–{money(case.opex_per_year[2])}"]
    if case.labour_cost_per_year:
        lines.append(f"  {'labour (per year)':<34} {'':>12}   "
                     f"{money(case.labour_cost_per_year)}")
    m = case.margin_per_year()
    lines += ["", f"MARGIN before labour: {money(m[0])}–{money(m[2])} {case.currency}/year",
              "", case.verdict]
    if case.findings:
        lines.append("")
        lines += [f"  - {f}" for f in case.findings]
    lines += ["", f"  ! {case.labour_note}",
              "", "Not included: " + "; ".join(case.not_included[:4]) + "; ...",
              "",
              "Prices are researched farm-gate figures with sources and dates, not offers "
              "you have; yields come from an unvalidated model. Treat this as a structured "
              "argument to check locally, not a forecast."]
    return "\n".join(lines)
