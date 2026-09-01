"""Elevations, pipe routes and pump head, derived from the layout instead of assumed.

`layout.py` decides where things stand in plan. This module decides how high each vessel
sits, how the pipe between two of them actually gets there, and what the pump therefore has
to work against. Those three are one problem: you cannot route a gravity drain without
knowing the fall, you cannot set the fall without knowing the run length, and you cannot
size a pump without either.

Until now none of it existed. Every vessel sat on the floor at z = 0, which put the raft
beds' water surface BELOW the sump's, so the drawing showed water running uphill; and every
pipe was a four-point path that dived to 0.25 m and climbed back, which is a trap rather
than a fall. Pump head came from `system_types.lift_height_m`, a constant per system type,
so the number in the bill of materials had no connection to the drawing beside it.

Three things happen here, in this order, because each needs the one before it:

1. **Route in plan.** A pipe is routed through the floor space the layout left free, on a
   grid, avoiding every vessel by a clearance. This is what stops a run cutting diagonally
   through a bed. The route is also what gives a LENGTH, which the next step needs.

2. **Grade the chain.** Walk the flow order from the rearing tanks down, dropping each
   vessel's water surface below the one before it by a minimum fall plus a slope over the
   routed length. Each vessel's base elevation follows from where its water surface has to
   be. The sump lands lowest, often below floor level, which is exactly where real systems
   put it and why `layout` already labels it "low point; pump lives here".

3. **Total the head.** Static lift is the real elevation the pump must raise water through,
   sump surface to the highest tank inlet. Friction is Darcy-Weisbach over the ROUTED
   length at the design flow, with fittings as equivalent length. The result is a number
   that changes when you move a tank, which is the whole point of deriving it.

Pure and deterministic: the router's neighbour order is fixed, so the same layout always
produces the same routes, byte for byte.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, replace

# --- constants ---------------------------------------------------------------------------

MIN_FALL_M = 0.04
"""Minimum drop across any gravity leg, whatever its length. Below this, level-building
tolerance and water-surface fluctuation eat the whole grade and flow stalls."""

SLOPE = 0.01
"""Fall per metre of routed run (1%), the ordinary rule for a gravity drain line. Shallower
silts up with the solids this water is carrying."""

CLEARANCE_M = 0.12
"""How close a routed pipe may come to a vessel wall."""

GRID_M = 0.10
"""Router resolution. Fine enough to find a 0.8 m aisle, coarse enough to stay fast."""

PIPE_RUN_H_M = 0.25
"""Height the horizontal part of a run sits at where grade does not force it lower."""

TARGET_VELOCITY_MS = 1.2
"""Design velocity for the recirculation main. Below ~0.6 m/s solids settle in the pipe;
above ~1.5 m/s friction and noise climb fast. 1.2 m/s is the ordinary middle."""

STANDARD_OD_MM = (25, 32, 40, 50, 63, 75, 90, 110, 125, 160)
"""Metric PVC pressure pipe, the sizes actually sold. Sizing to a computed diameter and
then not rounding to one of these would specify a pipe nobody stocks."""

DARCY_F = 0.022
"""Darcy friction factor for smooth PVC at the Reynolds numbers of small-system plumbing.
A single value rather than a Colebrook solve: the velocity range here is narrow, and the
fittings allowance below dominates the result anyway."""

FITTING_EQUIV_LENGTHS_M = 1.2
"""Equivalent straight-pipe length added per direction change (elbow). Standard practice
for estimating minor losses without enumerating every fitting."""

G = 9.80665


def pipe_diameter_m(flow_lpm: float) -> float:
    """The smallest standard pipe that carries this flow at or below the design velocity.

    Previously the main was hard-coded at 50 mm regardless of flow, which for a 13 m3/h
    system meant 1.9 m/s and a friction head that swamped the static lift. Sizing it from
    the flow is both the correct engineering and the reason the head number now moves when
    the design does."""
    if flow_lpm <= 0:
        return STANDARD_OD_MM[0] / 1000.0
    q = flow_lpm / 1000.0 / 60.0
    need_m = math.sqrt(4 * q / (math.pi * TARGET_VELOCITY_MS))
    for od in STANDARD_OD_MM:
        if od / 1000.0 >= need_m:
            return od / 1000.0
    return STANDARD_OD_MM[-1] / 1000.0


def velocity_ms(flow_lpm: float, d_m: float) -> float:
    if d_m <= 0:
        return 0.0
    return (flow_lpm / 1000.0 / 60.0) / (math.pi * (d_m / 2) ** 2)


@dataclass(frozen=True)
class HydraulicReport:
    """What the routed, graded layout says about its own plumbing."""

    static_lift_m: float
    friction_head_m: float
    total_head_m: float
    routed_length_m: float
    pumped_length_m: float
    lowest_water_z_m: float
    highest_water_z_m: float
    sump_sunk_m: float
    warnings: tuple[str, ...] = ()

    def summary(self) -> str:
        lines = [
            f"Pump head from the layout: {self.total_head_m:.2f} m "
            f"({self.static_lift_m:.2f} m static lift + {self.friction_head_m:.2f} m friction "
            f"over {self.pumped_length_m:.1f} m of routed return line).",
            f"Gravity side: {self.routed_length_m:.1f} m of routed pipe, water surface falling "
            f"from {self.highest_water_z_m:.2f} m to {self.lowest_water_z_m:.2f} m.",
        ]
        if self.sump_sunk_m > 0.01:
            lines.append(f"The sump sits {self.sump_sunk_m:.2f} m below floor level — the grade "
                         f"line puts it there; budget for excavation or raise everything else.")
        lines += [f"NOTE: {w}" for w in self.warnings]
        return "\n".join(lines)


# --- plan-view routing --------------------------------------------------------------------

def _footprint(c) -> tuple[float, float, float, float]:
    if c.kind == "cyl":
        return (c.x - c.d / 2, c.x + c.d / 2, c.y - c.d / 2, c.y + c.d / 2)
    return (c.x - c.w / 2, c.x + c.w / 2, c.y - c.l / 2, c.y + c.l / 2)


class _Grid:
    """Free/blocked occupancy over the greenhouse floor, at GRID_M."""

    def __init__(self, width: float, length: float, components, clearance: float = CLEARANCE_M):
        self.nx = max(2, int(math.ceil(width / GRID_M)) + 1)
        self.ny = max(2, int(math.ceil(length / GRID_M)) + 1)
        self.blocked = bytearray(self.nx * self.ny)
        self.by_id: dict[str, list[int]] = {}
        for c in components:
            x0, x1, y0, y1 = _footprint(c)
            cells = []
            for ix in range(self._ix(x0 - clearance), self._ix(x1 + clearance) + 1):
                for iy in range(self._iy(y0 - clearance), self._iy(y1 + clearance) + 1):
                    if 0 <= ix < self.nx and 0 <= iy < self.ny:
                        k = iy * self.nx + ix
                        self.blocked[k] = 1
                        cells.append(k)
            self.by_id[c.id] = cells

    def _ix(self, x: float) -> int:
        return max(0, min(self.nx - 1, int(round(x / GRID_M))))

    def _iy(self, y: float) -> int:
        return max(0, min(self.ny - 1, int(round(y / GRID_M))))

    def cell(self, x: float, y: float) -> int:
        return self._iy(y) * self.nx + self._ix(x)

    def xy(self, k: int) -> tuple[float, float]:
        return ((k % self.nx) * GRID_M, (k // self.nx) * GRID_M)

    def free(self, k: int, allow: set[int]) -> bool:
        return not self.blocked[k] or k in allow

    def route(self, a: tuple[float, float], b: tuple[float, float],
              allow: set[int]) -> list[tuple[float, float]] | None:
        """Shortest free path from a to b, breadth-first on a fixed neighbour order.

        `allow` are cells the two endpoints' own vessels occupy: a pipe must be able to
        start and finish inside the thing it connects. Straight lines cost the same as
        turns here, so the simplifier afterwards is what keeps runs orthogonal and tidy.
        """
        start, goal = self.cell(*a), self.cell(*b)
        if start == goal:
            return [a, b]
        prev = {start: -1}
        q = deque([start])
        nx, ny = self.nx, self.ny
        while q:
            k = q.popleft()
            if k == goal:
                break
            ix, iy = k % nx, k // nx
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):     # fixed order = determinism
                jx, jy = ix + dx, iy + dy
                if not (0 <= jx < nx and 0 <= jy < ny):
                    continue
                j = jy * nx + jx
                if j in prev or not self.free(j, allow):
                    continue
                prev[j] = k
                q.append(j)
        if goal not in prev:
            return None
        path = []
        k = goal
        while k != -1:
            path.append(self.xy(k))
            k = prev[k]
        path.reverse()
        path[0], path[-1] = a, b
        return _simplify(self._string_pull(path, allow))

    def _clear(self, a, b, allow: set[int]) -> bool:
        """True when the straight segment a->b crosses no blocked cell."""
        n = max(2, int(math.dist(a, b) / (GRID_M * 0.5)) + 1)
        for i in range(n + 1):
            t = i / n
            k = self.cell(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
            if not self.free(k, allow):
                return False
        return True

    def _string_pull(self, pts, allow: set[int]):
        out = [pts[0]]
        i = 0
        while i < len(pts) - 1:
            j = len(pts) - 1
            while j > i + 1 and not self._clear(pts[i], pts[j], allow):
                j -= 1
            out.append(pts[j])
            i = j
        return out


def _simplify(pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Collapse collinear runs, so a 40-cell staircase becomes two straight legs."""
    if len(pts) < 3:
        return list(pts)
    out = [pts[0]]
    for i in range(1, len(pts) - 1):
        ax, ay = out[-1]
        bx, by = pts[i]
        cx, cy = pts[i + 1]
        if abs((bx - ax) * (cy - ay) - (by - ay) * (cx - ax)) > 1e-9:
            out.append(pts[i])
    out.append(pts[-1])
    return out


def _plan_length(pts) -> float:
    return sum(math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))


# --- grading ------------------------------------------------------------------------------

def _water_surface(c) -> float:
    """Elevation of the water surface in a vessel, given its base z."""
    return c.z + c.h * (c.water_frac if c.water_frac else 0.9)


def grade_and_route(components, greenhouse, *, chain, rearing, sump,
                    flow_lpm: float, pipe_d_m: float):
    """Set vessel elevations and route every run. Returns (components, runs, report).

    `chain` is the single-file gravity order downstream of the tanks (treatment, then the
    first grow unit, then the sump). `rearing` are the parallel tanks that all drain into
    `chain[0]` and all receive the pump's return.

    `runs` are (from_id, to_id, path, pumped) with path as (x, y, z) in metres.
    """
    by_id = {c.id: c for c in components}
    warnings: list[str] = []
    grid = _Grid(greenhouse.width_m, greenhouse.length_m, components)

    def attach(a, b) -> tuple[float, float]:
        """A point just outside `a`'s wall, on the side facing `b`."""
        x0, x1, y0, y1 = _footprint(a)
        dx, dy = b.x - a.x, b.y - a.y
        n = math.hypot(dx, dy) or 1.0
        rx = (x1 - x0) / 2 + CLEARANCE_M + GRID_M
        ry = (y1 - y0) / 2 + CLEARANCE_M + GRID_M
        return (a.x + dx / n * rx, a.y + dy / n * ry)

    def plan_route(a, b) -> tuple[list[tuple[float, float]], float]:
        allow = set(grid.by_id.get(a.id, [])) | set(grid.by_id.get(b.id, []))
        pa, pb = attach(a, b), attach(b, a)
        pts = grid.route(pa, pb, allow)
        if pts is None:
            warnings.append(f"no clear route from {a.id} to {b.id}; drawn straight — "
                            f"the aisles around them are too tight for a pipe")
            pts = [pa, pb]
        pts = [(a.x, a.y)] + pts + [(b.x, b.y)]
        return pts, _plan_length(pts)

    # 1. route everything in plan, so the grade has lengths to work with
    legs: list[tuple] = []                      # (from, to, plan_pts, length, pumped)
    for t in rearing:
        if chain:
            pts, ln = plan_route(t, chain[0])
            legs.append((t.id, chain[0].id, pts, ln, False))
    for a, b in zip(chain, chain[1:]):
        pts, ln = plan_route(a, b)
        legs.append((a.id, b.id, pts, ln, False))
    if sump is not None:
        for t in rearing:
            pts, ln = plan_route(sump, t)
            legs.append((sump.id, t.id, pts, ln, True))

    # 2. grade the gravity side: every vessel's water surface below the one feeding it
    length_to = {(f, t): ln for f, t, _p, ln, _pu in legs}
    tank_surface = max((_water_surface(t) for t in rearing), default=1.0)
    surface = {t.id: tank_surface for t in rearing}
    prev_ids = [t.id for t in rearing]
    for c in chain:
        run_len = max((length_to.get((p, c.id), 0.0) for p in prev_ids), default=0.0)
        drop = MIN_FALL_M + SLOPE * run_len
        target = min(surface[p] for p in prev_ids) - drop
        by_id[c.id] = replace(c, z=round(target - c.h * (c.water_frac or 0.9), 3))
        surface[c.id] = target
        prev_ids = [c.id]

    # Parallel grow units share a manifold with the one that stands in the chain, so they
    # share its elevation. Without this only `bed1` was graded and beds 2..n stayed on the
    # floor — below the sump they drain into, which is the same uphill-flow fault the grade
    # line exists to remove, just moved somewhere less obvious.
    chain_roles = {c.role: by_id[c.id].z for c in chain}
    for c in components:
        if c.role in chain_roles and by_id[c.id].z != chain_roles[c.role]:
            by_id[c.id] = replace(by_id[c.id], z=chain_roles[c.role])

    graded = [by_id[c.id] for c in components]
    gmap = {c.id: c for c in graded}
    sump_g = gmap[sump.id] if sump is not None else None

    # 3. hang the 3D profile on the routed plan
    runs = []
    for f, t, pts, ln, pumped in legs:
        a, b = gmap[f], gmap[t]
        z_from = _water_surface(a) if not pumped else _water_surface(a)
        z_to = _water_surface(b)
        if pumped:
            # The return line runs low and rises at the tank, the way a pumped line does.
            low = min(z_from, PIPE_RUN_H_M)
            prof = _profile(pts, z_from, low, z_to, rise_at_end=True)
        else:
            prof = _profile(pts, z_from, None, z_to, rise_at_end=False)
        runs.append((f, t, prof, pumped, round(ln, 2)))

    # 4. head, from the routed geometry rather than a per-system constant
    grav_len = sum(ln for _f, _t, _p, ln, pu in legs if not pu)
    pump_len = sum(ln for _f, _t, _p, ln, pu in legs if pu)
    pump_turns = sum(max(0, len(p) - 2) for _f, _t, p, _ln, pu in legs if pu)
    sump_surface = _water_surface(sump_g) if sump_g is not None else 0.0
    tank_inlet = max((_water_surface(gmap[t.id]) for t in rearing), default=sump_surface)
    static = max(0.0, tank_inlet - sump_surface)
    friction = _friction_head(pump_len + pump_turns * FITTING_EQUIV_LENGTHS_M,
                             flow_lpm, pipe_d_m)

    surfaces = [_water_surface(c) for c in graded if c.water_frac]
    sunk = max(0.0, -min((c.z for c in graded), default=0.0))
    if sunk > 0.6:
        warnings.append(f"the grade line sinks the lowest vessel {sunk:.2f} m below floor; "
                        f"a shallower fall or a raised tank stand would avoid digging")

    report = HydraulicReport(
        static_lift_m=round(static, 3),
        friction_head_m=round(friction, 3),
        total_head_m=round(static + friction, 2),
        routed_length_m=round(grav_len, 2),
        pumped_length_m=round(pump_len, 2),
        lowest_water_z_m=round(min(surfaces, default=0.0), 3),
        highest_water_z_m=round(max(surfaces, default=0.0), 3),
        sump_sunk_m=round(sunk, 3),
        warnings=tuple(warnings),
    )
    return graded, runs, report


def _profile(pts, z_start: float, z_low: float | None, z_end: float,
             *, rise_at_end: bool) -> tuple[tuple[float, float, float], ...]:
    """Hang elevations on a plan route.

    A gravity leg falls continuously from start to end, so every point is lower than the
    one before it — that is what makes it a drain rather than a trap. A pumped leg drops to
    the run height, travels level, and rises at the far end.
    """
    total = _plan_length(pts) or 1.0
    out = []
    acc = 0.0
    for i, (x, y) in enumerate(pts):
        if i:
            acc += math.dist(pts[i - 1], pts[i])
        u = acc / total
        if rise_at_end:
            low = z_low if z_low is not None else min(z_start, z_end)
            z = z_start if i == 0 else (z_end if i == len(pts) - 1 else low)
        else:
            z = z_start + (z_end - z_start) * u
        out.append((round(x, 3), round(y, 3), round(z, 3)))
    return tuple(out)


def _friction_head(length_m: float, flow_lpm: float, d_m: float) -> float:
    """Darcy-Weisbach head loss, h = f (L/D) v² / 2g."""
    if length_m <= 0 or d_m <= 0 or flow_lpm <= 0:
        return 0.0
    q = flow_lpm / 1000.0 / 60.0                     # m³/s
    area = math.pi * (d_m / 2) ** 2
    v = q / area
    return DARCY_F * (length_m / d_m) * v * v / (2 * G)
