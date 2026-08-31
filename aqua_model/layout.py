"""Place a sized system in space: from "how big" to "where does it stand".

`sizing.size_system` answers volumes and areas. A build needs one more thing before anyone
digs a footing: an arrangement — how many tanks, which beds, how wide the aisles, and how
large the shelter that holds them all. This module computes that arrangement, deterministically,
from a `DesignOutput` and the system type's geometry.

The layout is a PROPOSAL, not a survey drawing. It uses standard component dimensions
(1.2 m bed widths because plywood and raft sheets come in 1.22 m; 0.8 m aisles because a
wheelbarrow needs one) and packs them on a rectangular floor with the water flowing one way:
fish end -> solids removal -> biofiltration -> grow beds -> sump -> back. Real sites have slopes,
doors and existing walls; the point here is a spatially consistent starting arrangement whose
every dimension traces back to the sizing numbers, so the 3D view the operator walks through
is the same system the calculator sized, not an artist's impression.

Same trust-zone rules as the rest of `aqua_model/`: pure, deterministic, no I/O.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .system_types import SystemType, get_system_type
from .types import DesignOutput

# Construction constants (metres). These are conventions, not physics: raft sheets and plywood
# come in 1.22 m widths, a person pushing a barrow needs ~0.8 m, a hoop bender makes ~2.2 m walls.
AISLE_M = 0.8
MARGIN_M = 0.6            # clearance between any component and the greenhouse wall
WALL_H_M = 2.2
RIDGE_MIN_H_M = 3.0
TANK_DEPTH_M = 1.1        # practical rearing-tank depth: reachable arm + net
TANK_MAX_M3 = 2.5         # above this, split into multiple tanks (handling and redundancy)
TANK_MAX_COUNT = 4
BED_WIDTH_M = 1.22        # standard raft / media bed width (reach from one side is ~0.61 m)
BED_MAX_LENGTH_M = 6.0
NFT_BENCH_H_M = 0.85      # channels on benches at working height
MEDIA_BED_STAND_H_M = 0.55
TOWER_H_M = 2.0
TOWER_ROW_SPACING_M = 1.0
PIPE_RUN_H_M = 0.25       # distribution plumbing runs near the floor


@dataclass(frozen=True)
class Placed:
    """One physical component, positioned. x/y are the centre on the floor plan (metres,
    origin at the greenhouse corner), z is the height of the component's base above ground."""

    id: str
    kind: str                 # "cyl" | "box"
    role: str                 # fish_tank | clarifier | biofilter | sump | dwc_bed | ...
    label: str
    x: float
    y: float
    z: float = 0.0
    # cylinders
    d: float = 0.0
    # boxes. `l` is width/length/height as the 3D viewer reads them: scene3d.py
    # serialises these names straight into the JSON that viewer_template.html
    # consumes as o.w/o.l/o.h, so renaming this field reaches into the JavaScript.
    w: float = 0.0
    l: float = 0.0  # noqa: E741 - geometry, not an ambiguous name
    h: float = 0.0
    water_frac: float = 0.0   # fill level as a fraction of height; 0 = dry component
    plants: bool = False
    plant_spacing_m: float = 0.25
    detail: str = ""          # hover text in the 3D view


@dataclass(frozen=True)
class PipeRun:
    """A plumbing run between two components, as a polyline of (x, y, z) waypoints."""

    from_id: str
    to_id: str
    path: tuple[tuple[float, float, float], ...]
    diameter_m: float = 0.05
    flow_lpm: float = 30.0


@dataclass(frozen=True)
class Greenhouse:
    width_m: float
    length_m: float
    wall_h_m: float = WALL_H_M
    ridge_h_m: float = RIDGE_MIN_H_M


@dataclass(frozen=True)
class Layout:
    greenhouse: Greenhouse
    components: tuple[Placed, ...]
    pipes: tuple[PipeRun, ...]
    assumptions: tuple[str, ...] = field(default_factory=tuple)

    def by_role(self, role: str) -> list[Placed]:
        return [c for c in self.components if c.role == role]


def _split_tanks(volume_l: float) -> list[float]:
    """Split total rearing volume into practical tanks.

    One huge tank is cheaper per litre but loses the whole crop to one failure and cannot
    stagger cohorts; the UVI commercial design uses four. We split above TANK_MAX_M3 and cap
    the count, biased toward equal tanks because unequal ones complicate plumbing."""
    v_m3 = max(0.05, volume_l / 1000.0)
    n = min(TANK_MAX_COUNT, max(1, math.ceil(v_m3 / TANK_MAX_M3)))
    return [v_m3 / n] * n


def _tank_diameter(v_m3: float) -> float:
    return 2.0 * math.sqrt(v_m3 / (math.pi * TANK_DEPTH_M))


def _bed_units(out: DesignOutput, system: SystemType) -> tuple[str, float, float, float, int, str]:
    """Choose the repeating grow unit for this system type.

    Returns (role, unit_w, unit_l, unit_h, n_units, unit_label). The unit is what gets
    repeated on the floor; its footprint times n_units covers the design's floor area
    (grow area / footprint_ratio, the same arithmetic sizing uses)."""
    floor_area = out.grow_area_m2 / max(system.footprint_ratio, 1e-9)

    if system.key == "vertical_tower":
        # A row of towers shares one drip line; representing the row (not each tower) keeps the
        # component count readable. Each metre of row is ~2.5 towers x ~1.2 m2 grow surface.
        row_len = min(BED_MAX_LENGTH_M, max(2.0, floor_area / TOWER_ROW_SPACING_M))
        n = max(1, math.ceil(floor_area / (TOWER_ROW_SPACING_M * row_len)))
        towers_per_row = max(1, round(row_len / 0.4))
        return ("vertical_tower", 0.2, row_len, TOWER_H_M, n,
                f"tower row ({towers_per_row} towers)")

    if system.key == "nft":
        # Channels grouped on benches; one bench = 1.0 m of channels wide.
        unit_l = min(BED_MAX_LENGTH_M, max(2.0, floor_area))
        n = max(1, math.ceil(floor_area / (1.0 * unit_l)))
        return ("nft_channel", 1.0, unit_l, 0.12, n, "NFT bench")

    if system.key == "media_bed":
        unit_l = 2.4
        n = max(1, math.ceil(floor_area / (BED_WIDTH_M * unit_l)))
        return ("media_bed", BED_WIDTH_M, unit_l,
                system.water_depth_m + 0.18, n, "media bed")

    # raft / DWC default
    unit_l = min(BED_MAX_LENGTH_M, max(2.0, floor_area / BED_WIDTH_M))
    n = max(1, math.ceil(floor_area / (BED_WIDTH_M * unit_l)))
    return ("dwc_bed", BED_WIDTH_M, unit_l, system.water_depth_m + 0.1, n, "DWC raft bed")


def plan_layout(out: DesignOutput, *, crop_label: str = "", species_label: str = "",
                flowsheet=None) -> Layout:
    """Arrange the sized system on a floor and size the greenhouse around it.

    Flow order fixes the zones: fish tanks at one end, filtration between, grow beds filling
    the rest, sump beside the filters. The greenhouse is the bounding envelope plus margins —
    which means its size is DERIVED from the design, and changing the crop area visibly
    changes the building. That coupling is the point of drawing it.

    `flowsheet` (a `flowsheet.Flowsheet`) overrides the default filtration row with the
    component set the needs actually selected — settling vs radial-flow, dedicated
    biofilter, degasser, mineralization tank, hydroponic reservoir — so the drawing shows
    the machine that was chosen, not a template."""
    system = get_system_type(out.system_type)
    assumptions: list[str] = []

    # --- fish zone ---
    tank_vols = _split_tanks(out.rearing_tank_volume_l)
    tank_ds = [_tank_diameter(v) for v in tank_vols]
    fish_span = sum(tank_ds) + AISLE_M * (len(tank_ds) - 1)

    # --- filtration zone ---
    filters: list[tuple[str, str, float, str]] = []
    sump_v = max(0.2, out.system_volume_l * 0.10 / 1000.0)
    sump_label = "Sump"
    if flowsheet is not None:
        _VESSELS = ("settling", "biofilter", "degasser", "mineraliser")
        for i, c in enumerate(v for v in flowsheet.components if v.role in _VESSELS):
            v_m3 = max(0.1, (c.volume_l or 150.0) / 1000.0)
            filters.append((c.role, f"{c.role}{i + 1}" if c.role != "biofilter" else "biofilter",
                            _tank_diameter(v_m3) * 0.9, c.name))
        fs_sump = next((c for c in flowsheet.components if c.role == "sump"), None)
        if fs_sump is not None:
            sump_v = max(0.2, (fs_sump.volume_l or sump_v * 1000.0) / 1000.0)
            sump_label = fs_sump.name.split("(")[0].strip().title()
        assumptions.append(f"architecture: {flowsheet.architecture} "
                           f"({len(filters)} treatment vessel(s) from the flowsheet)")
        if not filters:
            assumptions.append("flowsheet: media beds handle solids and biofiltration — "
                               "no separate treatment vessels")
    else:
        # Default chain: clarifier ~15% of rearing volume; biofilter vessel from media area
        # at ~200 m2/m3, 60% packing. Media-bed systems biofilter in the beds and skip it.
        clar_v = max(0.15, out.rearing_tank_volume_l * 0.15 / 1000.0)
        filters.append(("clarifier", "clarifier", _tank_diameter(clar_v) * 0.9,
                        "settles solids before the biofilter"))
        if not system.provides_biofiltration and out.biofilter_media_m2:
            media_m3 = out.biofilter_media_m2 / 200.0
            bf_v = max(0.1, media_m3 / 0.6)
            filters.append(("biofilter", "biofilter", _tank_diameter(bf_v),
                            f"{out.biofilter_media_m2:.0f} m² media surface"))
        elif system.provides_biofiltration:
            assumptions.append("media beds provide biofiltration; no separate biofilter vessel")

    sump_w = max(0.8, math.sqrt(sump_v / 0.8))
    filters.append(("sump", "sump", sump_w, "low point; pump lives here"))
    filter_span = sum(d for _r, _i, d, _n in filters) + AISLE_M * (len(filters) - 1)

    # --- grow zone ---
    role, bw, bl, bh, n_units, unit_label = _bed_units(out, system)
    # Squarish envelope: columns span x at (bw + aisle) each, rows span y at (bl + aisle).
    # Equal spans want n_cols ~ sqrt(n * (bl+aisle)/(bw+aisle)) — long beds need MORE
    # columns, not fewer, or four 6 m raft beds stack into one 27 m tunnel.
    n_cols = max(1, math.ceil(math.sqrt(n_units * (bl + AISLE_M) / (bw + AISLE_M))))
    n_cols = min(n_cols, n_units)
    n_rows = math.ceil(n_units / n_cols)
    beds_span_x = n_cols * bw + (n_cols - 1) * AISLE_M
    beds_span_y = n_rows * bl + (n_rows - 1) * AISLE_M

    # --- greenhouse envelope ---
    width = max(fish_span, filter_span, beds_span_x) + 2 * MARGIN_M
    tank_zone_y = max(max(tank_ds, default=0.0), 0.0) + AISLE_M
    filter_zone_y = max((d for _r, _i, d, _n in filters), default=0.0) + AISLE_M
    length = MARGIN_M + tank_zone_y + filter_zone_y + beds_span_y + MARGIN_M
    ridge = max(RIDGE_MIN_H_M, WALL_H_M + width * 0.18)
    if role == "vertical_tower":
        ridge = max(ridge, TOWER_H_M + 0.8)
    gh = Greenhouse(width_m=round(width, 2), length_m=round(length, 2),
                    wall_h_m=WALL_H_M, ridge_h_m=round(ridge, 2))

    comps: list[Placed] = []

    # fish tanks, centred across the width at the near end
    x0 = (width - fish_span) / 2.0
    y_t = MARGIN_M + max(tank_ds, default=0.0) / 2.0
    x = x0
    for i, (v, d) in enumerate(zip(tank_vols, tank_ds), 1):
        cx = x + d / 2.0
        comps.append(Placed(
            id=f"tank{i}", kind="cyl", role="fish_tank",
            label=f"Fish tank {i} ({v:.1f} m³{', ' + species_label if species_label else ''})",
            x=round(cx, 2), y=round(y_t, 2), d=round(d, 2), h=TANK_DEPTH_M, water_frac=0.85,
            detail=f"{v * 1000:.0f} L rearing volume"))
        x += d + AISLE_M

    # filtration row
    y_f = MARGIN_M + tank_zone_y + max((d for _r, _i, d, _n in filters), default=0.0) / 2.0
    x = (width - filter_span) / 2.0
    for role_f, cid, d, note in filters:
        cx = x + d / 2.0
        if role_f == "sump":
            comps.append(Placed(id=cid, kind="box", role="sump",
                                label=f"{sump_label} ({sump_v:.1f} m³)",
                                x=round(cx, 2), y=round(y_f, 2), w=round(d, 2), l=round(d, 2),
                                h=0.8, water_frac=0.6, detail=note))
        else:
            comps.append(Placed(id=cid, kind="cyl", role=role_f,
                                label=note if flowsheet is not None else role_f.capitalize(),
                                x=round(cx, 2), y=round(y_f, 2),
                                d=round(d, 2), h=1.0, water_frac=0.8,
                                detail=note if flowsheet is None else role_f))
        x += d + AISLE_M

    # grow units, gridded
    z0 = {"nft_channel": NFT_BENCH_H_M, "media_bed": MEDIA_BED_STAND_H_M}.get(role, 0.0)
    y_beds0 = MARGIN_M + tank_zone_y + filter_zone_y
    gx0 = (width - beds_span_x) / 2.0
    unit_area = (out.grow_area_m2 / n_units) if n_units else 0.0
    idx = 0
    for r in range(n_rows):
        for c in range(n_cols):
            if idx >= n_units:
                break
            cx = gx0 + c * (bw + AISLE_M) + bw / 2.0
            cy = y_beds0 + r * (bl + AISLE_M) + bl / 2.0
            comps.append(Placed(
                id=f"bed{idx + 1}", kind="box", role=role,
                label=f"{unit_label} {idx + 1}"
                      f"{' — ' + crop_label if crop_label else ''}",
                x=round(cx, 2), y=round(cy, 2), z=z0,
                w=round(bw, 2), l=round(bl, 2), h=round(bh, 2),
                water_frac=0.8 if role in ("dwc_bed", "media_bed") else 0.0,
                plants=role in ("dwc_bed", "media_bed", "nft_channel"),
                detail=f"{unit_area:.1f} m² grow area"))
            idx += 1

    # --- plumbing: one loop, in flow order ---
    # Main flow order: fish -> solids removal -> biofilter -> degasser -> beds -> sump.
    # The mineraliser is a SPUR off the solids stream (sludge goes there, not the main flow).
    order = ([c for c in comps if c.role == "fish_tank"]
             + [c for c in comps if c.role in ("clarifier", "settling")]
             + [c for c in comps if c.role == "biofilter"]
             + [c for c in comps if c.role == "degasser"])
    first_bed = next((c for c in comps if c.role == role), None)
    sump = next((c for c in comps if c.role == "sump"), None)
    flow_lpm = out.pump_turnover_lph / 60.0 if out.pump_turnover_lph else 30.0

    def rim(c: Placed) -> tuple[float, float, float]:
        top = c.z + (c.h if c.kind == "box" else c.h)
        return (c.x, c.y, round(top * (c.water_frac or 0.9), 2))

    pipes: list[PipeRun] = []

    def run(a: Placed, b: Placed) -> None:
        ax, ay, az = rim(a)
        bx, by, bz = rim(b)
        pipes.append(PipeRun(
            from_id=a.id, to_id=b.id,
            path=((ax, ay, az), (ax, ay, PIPE_RUN_H_M), (bx, by, PIPE_RUN_H_M), (bx, by, bz)),
            flow_lpm=round(flow_lpm, 1)))

    chain: list[Placed] = []
    chain += order
    if first_bed is not None:
        chain.append(first_bed)
    if sump is not None:
        chain.append(sump)
    for a, b in zip(chain, chain[1:]):
        run(a, b)
    mineraliser = next((c for c in comps if c.role == "mineraliser"), None)
    solids = next((c for c in comps if c.role in ("settling", "clarifier")), None)
    if mineraliser is not None and solids is not None:
        run(solids, mineraliser)          # sludge spur, off the main loop
    if sump is not None and order:
        run(sump, order[0])  # the pump's return line closes the loop

    assumptions += [
        f"greenhouse sized from the layout: {gh.width_m} x {gh.length_m} m "
        f"({gh.width_m * gh.length_m:.0f} m² floor for {out.grow_area_m2:.0f} m² grow area)",
        f"{len(tank_vols)} rearing tank(s) of {tank_vols[0]:.1f} m³ at {TANK_DEPTH_M} m depth",
        f"aisles {AISLE_M} m, wall margin {MARGIN_M} m",
        "positions are a deterministic proposal, not a site plan — doors, slope and services move things",
    ]
    return Layout(greenhouse=gh, components=tuple(comps), pipes=tuple(pipes),
                  assumptions=tuple(assumptions))
