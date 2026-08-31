"""The flowsheet: WHICH components this system needs, chosen from the user's needs — not
a fixed template.

`sizing.py` answers "how big"; this module answers "made of what, and why". A backyard
media-bed unit and a commercial decoupled farm are not the same machine with different
numbers — they have different COMPONENT SETS, and the choices follow rules the literature
states explicitly. Every rule here carries its source; most come from the two references
the corpus now holds in full: FAO 589 (Somerville et al. 2014) and Goddek, Joyce, Kotzen &
Burnell eds. (2019), *Aquaponics Food Production Systems* (Springer Open, CC BY 4.0 —
"the book" below), with the UVI system (Rakocy) as the built-and-run anchor.

The decisions, in the order they cascade:

1. COUPLED or DECOUPLED. One loop shares one water chemistry, so fish and crop must agree
   on temperature and pH; the book's ch. 8 (Goddek et al.) exists because at scale they
   often don't — a decoupled (multi-loop) system runs the fish loop and the hydroponic
   loop each at its own optimum, coupled one-way through nutrient-rich water. We recommend
   decoupling when the species' and crop's cited temperature bands barely overlap, or the
   scale is commercial; otherwise coupled, the small-scale default (FAO 589 throughout).
2. SOLIDS REMOVAL. Media beds double as solids capture and biofilter up to a stocking
   intensity FAO puts near 15 kg/m3 — above that they clog. Past it: a settling tank /
   swirl clarifier at small scale (UVI ran cylindro-conical clarifiers), a radial-flow
   separator at larger flows (better capture per footprint; standard RAS practice per
   Timmons & Ebeling). Decoupled systems always separate solids (the sludge is feedstock).
3. BIOFILTRATION. Media beds provide it (FAO 589; `system_types.provides_biofiltration`);
   raft canals nitrify on raft undersides and tank walls but UVI still ran dedicated
   filter/degassing tanks; NFT and towers hold too little water to nitrify and need a
   dedicated biofilter (FAO 589). Sizing stays `sizing.py`'s media-area rule.
4. DEGASSING. Anaerobic pockets in solids-laden water produce gases fish should not meet;
   UVI's raft line includes a degassing tank after filtration, and the book's decoupled
   chapter treats digestate degassing as standard. Included when stocking is intensive or
   the loop is decoupled.
5. MINERALIZATION. Captured sludge still holds most of its nutrients; a mineralization
   loop digests it and returns them (book ch. 8; Lennard's finding — reported in ch. 5 —
   that remineralization drops the required feed-rate ratio). Offered for decoupled
   systems and whenever the user wants maximum nutrient reuse / minimum discharge.
6. THE LOW END STAYS SIMPLE. FAO 589's core audience runs media beds precisely because
   one component does three jobs; this module must never gold-plate a backyard unit into
   a treatment plant. Components are added only when a stated need triggers them.

Pure and deterministic. The needs arrive as data; the verdict carries WHY per component.
"""

from __future__ import annotations

from dataclasses import dataclass

from .crops import Crop
from .species import FishSpecies
from .system_types import get_system_type
from .types import DesignOutput

NOT_MODELLED = (
    "hydraulic head-loss per component (pump sizing stays sizing.py's system-level rule)",
    "pathogen management components (UV/ozone — commercial add-ons, site-specific)",
    "backup/redundancy engineering (duplicated pumps, generators) beyond a warning",
    "greenhouse climate equipment selection (heaters/fans are the climate model's domain)",
)

# FAO 589: media beds provide adequate solids capture + biofiltration at the manual's
# standard stocking, but clog as intensity rises; the manual's ceiling guidance is ~15 kg/m3
# without added filtration.
MEDIA_BED_SELF_FILTER_MAX_KG_M3 = 15.0
# Above this the system is running like a small RAS: degassing joins the flowsheet
# (UVI's raft line included a degassing step at commercial stocking).
INTENSIVE_KG_M3 = 40.0
# Rough scale threshold where the book's decoupled arguments (ch. 8) start to outweigh
# coupled simplicity: dedicated management, nutrient dosing, climate separation.
COMMERCIAL_AREA_M2 = 50.0
# Bands "barely overlap" when the crop tolerates less than this fraction of the FISH's
# optimal band. The fish's optimum is the loop's set-point in a coupled system (water is
# where the money and the mortality risk live), so the question is whether the crop can
# live at the fish's temperature — not whether two arbitrary ranges intersect. Tilapia
# (27-30) + basil (18-30): fully covered, coupled — the classic UVI pairing. Trout (14-16)
# + basil (18-30): zero coverage, decouple.
MIN_BAND_COVERAGE = 0.5


@dataclass(frozen=True)
class Component:
    """One box in the flowsheet, with the reason it earned its place."""

    role: str          # matches layout/scene roles: fish_tank, settling, biofilter, ...
    name: str
    why: str
    source: str
    count: int = 1
    volume_l: float | None = None


@dataclass(frozen=True)
class Needs:
    """What the user told us that changes the machine. All optional — defaults describe
    the FAO small-scale case, and every default is visible in the verdict."""

    stocking_kg_m3: float | None = None      # None => derived from the design
    reliable_power: bool = True
    wants_max_nutrient_reuse: bool = False
    operator_experience: str = "beginner"    # beginner | intermediate | expert
    force_architecture: str | None = None    # "coupled" | "decoupled" | None = decide


@dataclass(frozen=True)
class Flowsheet:
    architecture: str                        # coupled | decoupled
    components: tuple[Component, ...]
    decisions: tuple[str, ...]               # the WHY trail, in decision order
    warnings: tuple[str, ...]
    not_modelled: tuple[str, ...] = NOT_MODELLED

    def roles(self) -> list[str]:
        return [c.role for c in self.components]


def _band_coverage(species: FishSpecies, crop: Crop) -> float:
    """Fraction of the fish's OPTIMAL temperature band the crop tolerates (0..1)."""
    width = species.temp_opt_high_c - species.temp_opt_low_c
    if width <= 0:
        return 1.0
    lo = max(species.temp_opt_low_c, crop.temp_min_c)
    hi = min(species.temp_opt_high_c, crop.temp_max_c)
    return max(0.0, hi - lo) / width


def plan_flowsheet(out: DesignOutput, species: FishSpecies, crop: Crop,
                   needs: Needs | None = None) -> Flowsheet:
    """Choose the component set for THIS design and THESE needs, with the why per choice."""
    needs = needs or Needs()
    system = get_system_type(out.system_type)
    decisions: list[str] = []
    warnings: list[str] = []
    comps: list[Component] = []

    density = needs.stocking_kg_m3
    if density is None and out.rearing_tank_volume_l > 0:
        density = out.fish_biomass_kg / (out.rearing_tank_volume_l / 1000.0)
    density = density or 0.0

    # --- 1. architecture ---
    coverage = _band_coverage(species, crop)
    if needs.force_architecture in ("coupled", "decoupled"):
        arch = needs.force_architecture
        decisions.append(f"architecture {arch}: fixed by the user")
    elif coverage < MIN_BAND_COVERAGE or out.grow_area_m2 >= COMMERCIAL_AREA_M2:
        arch = "decoupled"
        why = (f"{crop.name} tolerates only {coverage:.0%} of {species.name}'s optimal "
               f"water-temperature band" if coverage < MIN_BAND_COVERAGE else
               f"{out.grow_area_m2:.0f} m² is commercial scale")
        decisions.append(
            f"architecture DECOUPLED: {why} — separate loops let each run its own optimum, "
            "coupled one-way through nutrient water (Goddek et al. 2019, ch. 8)")
        if needs.operator_experience == "beginner":
            warnings.append(
                "a decoupled system is two systems to run, plus dosing — as a beginner, "
                "consider starting coupled and smaller, or budgeting for training")
    else:
        arch = "coupled"
        decisions.append(
            f"architecture COUPLED: {crop.name} tolerates {coverage:.0%} of {species.name}'s "
            f"optimal band and {out.grow_area_m2:.0f} m² is small-scale — one loop, one "
            "chemistry, the FAO 589 default; simplest to run")

    # --- fish tanks (always; count follows layout's split rule) ---
    comps.append(Component(
        role="fish_tank", name="rearing tank(s)",
        why=f"{out.fish_count} fish, {out.rearing_tank_volume_l:,.0f} L total "
            f"(~{density:.0f} kg/m³ at standing biomass)",
        source="sizing.py (FAO 589 stocking rules)",
        volume_l=out.rearing_tank_volume_l))

    # --- 2. solids removal ---
    media_can_self_filter = (system.provides_biofiltration
                             and density <= MEDIA_BED_SELF_FILTER_MAX_KG_M3)
    if media_can_self_filter:
        decisions.append(
            f"solids: NONE SEPARATE — media beds capture solids and biofilter at "
            f"{density:.0f} kg/m³ (≤ {MEDIA_BED_SELF_FILTER_MAX_KG_M3:.0f}; FAO 589 — one "
            "component, three jobs, which is why the manual builds on media beds)")
    else:
        big = out.pump_turnover_lph >= 10_000 or out.grow_area_m2 >= COMMERCIAL_AREA_M2
        if big:
            comps.append(Component(
                role="settling", name="radial-flow separator",
                why="at this flow a radial-flow separator captures more solids per footprint "
                    "than a settling cone",
                source="standard RAS practice (Timmons & Ebeling); UVI ran clarifier cones "
                       "at smaller flow",
                volume_l=max(200.0, out.rearing_tank_volume_l * 0.10)))
        else:
            comps.append(Component(
                role="settling", name="settling tank / swirl clarifier",
                why="captures settleable solids before they reach the biofilter or beds",
                source="UVI cylindro-conical clarifiers (Rakocy); FAO 589 solids guidance",
                volume_l=max(150.0, out.rearing_tank_volume_l * 0.15)))
        if system.provides_biofiltration:
            decisions.append(
                f"solids: SEPARATE clarifier added even with media beds — {density:.0f} kg/m³ "
                f"exceeds the ~{MEDIA_BED_SELF_FILTER_MAX_KG_M3:.0f} kg/m³ the beds can "
                "handle alone without clogging (FAO 589)")
        else:
            decisions.append(
                f"solids: SEPARATE removal — {system.name} beds do not capture solids")

    # --- 3. biofiltration ---
    if system.provides_biofiltration:
        decisions.append("biofilter: the media beds ARE the biofilter (FAO 589; "
                         "system_types.provides_biofiltration)")
    elif out.biofilter_media_m2:
        comps.append(Component(
            role="biofilter", name="biofilter (moving-bed / packed media)",
            why=f"{out.biofilter_media_m2:.0f} m² of media surface for the ammonia load; "
                "NFT/towers/raft hold too little biofilm surface on their own",
            source="sizing.py media-area rule (FAO 589 0.57 g/m²/d conservative; "
                   "Rusten et al. 2006 MBBR design rates)",
            volume_l=max(100.0, out.biofilter_media_m2 / 200.0 / 0.6 * 1000.0)))
        decisions.append("biofilter: DEDICATED vessel — this growing method does not "
                         "provide one (FAO 589)")

    # --- 4. degassing ---
    if arch == "decoupled" or density > INTENSIVE_KG_M3:
        comps.append(Component(
            role="degasser", name="degassing tank",
            why=("solids-laden water at intensive stocking develops anaerobic pockets; "
                 "strip the gases before water returns to fish"),
            source="UVI raft line includes degassing (Rakocy); Goddek et al. 2019 ch. 8",
            volume_l=max(100.0, out.rearing_tank_volume_l * 0.05)))
        decisions.append(
            f"degassing: INCLUDED ({'decoupled loop' if arch == 'decoupled' else f'{density:.0f} kg/m³ is intensive'})")

    # --- 5. mineralization ---
    if arch == "decoupled" or needs.wants_max_nutrient_reuse:
        comps.append(Component(
            role="mineraliser", name="mineralization tank",
            why="digests captured sludge and returns its nutrients to the plants instead "
                "of discharging them; lowers the feed the plants need",
            source="Goddek et al. 2019 ch. 8; Lennard (in ch. 5): remineralization lowers "
                   "the required feed-rate ratio",
            volume_l=max(150.0, out.rearing_tank_volume_l * 0.10)))
        decisions.append("mineralization: INCLUDED "
                         + ("(decoupled standard practice)" if arch == "decoupled"
                            else "(user wants maximum nutrient reuse)"))

    # --- grow beds (always) + 6. sump / hydroponic reservoir ---
    comps.append(Component(
        role={"raft": "dwc_bed", "nft": "nft_channel", "media_bed": "media_bed",
              "vertical_tower": "vertical_tower"}.get(system.key, "dwc_bed"),
        name=system.grow_bed_label,
        why=f"{out.grow_area_m2:.0f} m² planted area (the sizing anchor)",
        source="sizing.py (feed-rate ratio, FAO 589 / Rakocy)"))
    if arch == "decoupled":
        comps.append(Component(
            role="sump", name="hydroponic reservoir (own loop)",
            why="the plant loop runs its own temperature, pH and EC; sized to buffer "
                "1-2 days of crop water use",
            source="Goddek et al. 2019 ch. 8 (decoupled sizing is ET-driven)",
            volume_l=max(300.0, out.makeup_water_lpd * 1.5)))
        decisions.append("reservoir: separate hydroponic reservoir replaces the shared sump")
    else:
        comps.append(Component(
            role="sump", name="sump",
            why="the loop's low point: pump, level control, top-up",
            source="FAO 589; sizing.py SUMP_FRACTION",
            volume_l=max(200.0, out.system_volume_l * 0.10)))

    comps.append(Component(
        role="aeration", name="aeration (air pump + stones)",
        why="DO is the fastest way to kill fish; every tank and raft canal gets air",
        source="FAO 589 (~4-8 L/min small systems; stones every 2-4 m² of raft)"))

    if not needs.reliable_power and system.key in ("nft", "vertical_tower"):
        warnings.append(
            f"{system.name} + unreliable power is a fish-kill waiting to happen — roots dry "
            "in minutes when a pump stops (FAO 589). Prefer media beds or raft, or budget "
            "battery backup before anything else")
    if not needs.reliable_power:
        warnings.append("with unreliable power, aeration backup (battery air pump) is the "
                        "single highest-value resilience purchase")

    return Flowsheet(architecture=arch, components=tuple(comps),
                     decisions=tuple(decisions), warnings=tuple(warnings))


def format_flowsheet(fs: Flowsheet) -> str:
    """Operator-facing architecture summary: the machine, then the reasons."""
    lines = [f"Architecture: {fs.architecture.upper()}", "", "Components:"]
    for c in fs.components:
        vol = f" (~{c.volume_l:,.0f} L)" if c.volume_l else ""
        lines.append(f"  • {c.name}{vol} — {c.why} [{c.source}]")
    lines += ["", "Why this shape:"]
    lines += [f"  - {d}" for d in fs.decisions]
    if fs.warnings:
        lines.append("")
        lines += [f"  ! {w}" for w in fs.warnings]
    lines += ["", "Not decided here: " + "; ".join(fs.not_modelled[:2]) + "; ..."]
    return "\n".join(lines)
