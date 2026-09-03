"""Serialize a `layout.Layout` into a renderer-neutral 3D scene description.

The contract mirrors `schematic.py`'s split between scene and backend: `layout.py` decides
where things stand, this module states it as plain data, and whatever draws it — the bundled
three.js viewer, some future glTF export — consumes the same JSON. Nothing here knows about
WebGL, and nothing in the viewer re-derives a dimension: if a number is wrong in the picture,
it is wrong here, which is where it can be tested.

Coordinate convention: x across the greenhouse width, y along its length, z up, metres,
origin at one corner. The viewer maps that to its own axes.

The geometry is a DESIGN. The optional `state` and `trajectory` arguments bind a twin to it,
so the same drawing renders three ways: as designed, as it stands today, and as it is
projected to stand on day N. Every appearance the state drives — water colour, fish size and
number, how vigorous the crop looks — is decided HERE, in numbers the tests can read, so the
viewer applies a colour rather than choosing one. A picture that invented its own thresholds
would be a second, uncited opinion about when a pond is in trouble.

Pure and deterministic, like the rest of `aqua_model/`.
"""

from __future__ import annotations

from dataclasses import asdict

from .advisory import (
    NO2_ACT_MG_L,
    NO2_URGENT_MG_L,
    NO3_HIGH_MG_L,
    NO3_LOW_MG_L,
    TAN_ACT_MG_L,
    TAN_URGENT_MG_L,
)
from .cropgrowth import f_nitrogen
from .layout import Layout, Placed
from .types import DesignOutput

SCENE_SCHEMA_VERSION = "1.2.0"

# Fish drawn per rearing tank. The picture is an explanation, not a census: a frame states
# the twin's real count and how many of them are drawn, and the drawn population is scaled
# to the real one so a die-off is still visible as thinning water.
FISH_DRAWN_PER_TANK = 15

# Water colour by nitrogen band. The bands themselves are `advisory.py`'s, which cites
# knowledge/nitrogen_cycle_and_cycling.md; only the colours are new here, and a colour is
# not a claim about a pond. There is deliberately NO "ok" colour: healthy water carries an
# empty override and the viewer keeps its own water blue, so an untroubled system looks
# exactly as it did before any twin was bound, and retuning that blue stays a one-file
# change instead of silently breaking the promise from the other side.
WATER_COLORS = {"act": "#c08b2a", "urgent": "#b23c2e"}

# How pale a nitrogen-starved crop is drawn. Chlorosis on a nitrogen-limited plant is
# ordinary agronomy, but the DEPTH of the tint is a drawing convention, not a measurement,
# which is why the frame also carries the factor itself for the readout to state. The tint
# STARTS where the knowledge base's nitrate floor is crossed (advisory.NO3_LOW_MG_L, cited
# to knowledge/nitrogen_cycle_and_cycling.md) rather than at a number chosen to look right:
# a crop running at 0.8 of its nitrogen potential is an ordinary aquaponic crop, not a sick
# one, and drawing it yellow would be the picture inventing a diagnosis.
CHLOROSIS_COLOR = "#c9c04a"
CHLOROSIS_ONSET = f_nitrogen(NO3_LOW_MG_L)

# Plant size under limitation. A bed at 0 growth is still a bed of small plants, not bare
# water: the twin models a continuous-harvest crop, so it never claims the bed is empty.
PLANT_SCALE_FLOOR = 0.45

# Fish length from mean weight, isometric: L = (100 * W / K)^(1/3), K a nominal Fulton
# condition factor. 1.9 draws a 500 g tilapia at ~30 cm and a 20 g fingerling at ~10 cm.
# This is a DRAWING convention — nothing computes from it, and no output reports a length.
FULTON_K = 1.9

DEFAULT_MAX_FRAMES = 120


def _component(c: Placed) -> dict:
    # Compact form: drop empty fields, then restore the geometry keys whose zeros are meaningful.
    d = {k: v for k, v in asdict(c).items() if v not in (0.0, False, "", None)}
    d["kind"] = c.kind
    d["role"] = c.role
    d["x"] = c.x
    d["y"] = c.y
    if c.kind == "box":
        d["w"], d["l"], d["h"] = c.w, c.l, c.h
    else:
        d["d"], d["h"] = c.d, c.h
    d.pop("plant_spacing_m", None)
    if c.plants:
        d["plant_spacing"] = c.plant_spacing_m
    return d


def fish_length_m(mean_weight_g: float) -> float:
    """Drawn length of one fish at this mean weight (see FULTON_K)."""
    w = max(0.1, float(mean_weight_g))
    return round(((100.0 * w / FULTON_K) ** (1.0 / 3.0)) / 100.0, 4)


def water_band(nitrogen) -> dict:
    """Which nitrogen band this water is in, and therefore what colour it is drawn.

    The thresholds are `advisory.py`'s, unchanged and unrounded, so the water in the picture
    turns amber on exactly the number that makes the advisor propose an action. The DRIVER is
    named because "the water is orange" is not actionable and "nitrite is over the band" is.
    """
    tan, no2, no3 = nitrogen.tan_mg_l, nitrogen.no2_mg_l, nitrogen.no3_mg_l
    if tan >= TAN_URGENT_MG_L or no2 >= NO2_URGENT_MG_L:
        driver, value, limit = (("ammonia", tan, TAN_URGENT_MG_L)
                                if tan >= TAN_URGENT_MG_L else ("nitrite", no2, NO2_URGENT_MG_L))
        return {"band": "urgent", "color": WATER_COLORS["urgent"], "driver": driver,
                "why": f"{driver} {value:.2f} mg/L is at or past {limit:.1f} mg/L"}
    if tan >= TAN_ACT_MG_L or no2 >= NO2_ACT_MG_L:
        driver, value, limit = (("ammonia", tan, TAN_ACT_MG_L)
                                if tan >= TAN_ACT_MG_L else ("nitrite", no2, NO2_ACT_MG_L))
        why = f"{driver} {value:.2f} mg/L is over the {limit:.1f} mg/L action band"
        if no3 < NO3_LOW_MG_L:
            why += " while nitrate is still low — the cycle is not established yet"
        return {"band": "act", "color": WATER_COLORS["act"], "driver": driver, "why": why}
    if no3 >= NO3_HIGH_MG_L:
        return {"band": "act", "color": WATER_COLORS["act"], "driver": "nitrate",
                "why": (f"nitrate {no3:.0f} mg/L is over {NO3_HIGH_MG_L:.0f} mg/L — plant "
                        "uptake is not keeping up")}
    return {"band": "ok", "color": "", "driver": "",
            "why": f"ammonia {tan:.2f} / nitrite {no2:.2f} / nitrate {no3:.0f} mg/L"}


def _crop_block(fac) -> dict:
    """How the crop is drawn today, and the three factors that say why."""
    growth = fac.combined()
    limiting = min((("light", fac.f_light), ("temperature", fac.f_temp),
                    ("nitrogen", fac.f_nitrogen)), key=lambda kv: kv[1])[0]
    pale = 0.0
    if fac.f_nitrogen < CHLOROSIS_ONSET:
        pale = round((CHLOROSIS_ONSET - fac.f_nitrogen) / CHLOROSIS_ONSET, 3)
    return {
        "f_light": round(fac.f_light, 3),
        "f_temp": round(fac.f_temp, 3),
        "f_nitrogen": round(fac.f_nitrogen, 3),
        "growth": round(growth, 3),
        "limiting": limiting,
        "scale": round(PLANT_SCALE_FLOOR + (1.0 - PLANT_SCALE_FLOOR) * min(1.0, growth), 3),
        "chlorosis": pale,
        "chlorosis_color": CHLOROSIS_COLOR,
    }


def _fish_block(state, *, roster: int, peak_count: int) -> dict:
    count = int(state.fish.count)
    drawn = 0
    if count > 0 and peak_count > 0:
        drawn = max(1, min(roster, round(roster * count / peak_count)))
    return {
        "count": count,
        "mean_weight_g": round(state.fish.mean_weight_g, 1),
        "biomass_kg": round(state.fish.biomass_kg(), 2),
        "length_m": fish_length_m(state.fish.mean_weight_g),
        "drawn": drawn,
    }


def _frame(state, *, kind: str, roster: int, peak_count: int,
           fac=None, date: str = "", note: str = "") -> dict:
    n = state.nitrogen
    frame = {
        "day": int(state.day),
        "date": date,
        "kind": kind,
        "fish": _fish_block(state, roster=roster, peak_count=peak_count),
        "water": dict(water_band(n),
                      temp_c=round(state.water_temp_c, 1),
                      tan_mg_l=round(n.tan_mg_l, 3),
                      no2_mg_l=round(n.no2_mg_l, 3),
                      no3_mg_l=round(n.no3_mg_l, 1)),
        "totals": {
            "fish_harvested_kg": round(state.harvested_fish_kg, 2),
            "crop_harvested_kg": round(state.harvested_crop_kg, 2),
            "feed_used_kg": round(state.feed_used_kg, 2),
        },
    }
    if fac is not None:
        frame["crop"] = _crop_block(fac)
    if note:
        frame["note"] = note
    return frame


def _keyframe_indices(trajectory, max_frames: int, today_index: int | None) -> list[int]:
    """Which days to embed. Even stride, plus the days it would be dishonest to skip.

    A downsample that lands either side of the nitrite spike shows a season in which the
    spike never happened — the one event this view exists to make visible. So the peaks of
    each nitrogen channel, every harvest, today, the first day and the last are pinned in
    regardless of stride.
    """
    n = len(trajectory)
    if n == 0:
        return []
    keep = {0, n - 1}
    if today_index is not None and 0 <= today_index < n:
        keep.add(today_index)
    for attr in ("tan_mg_l", "no2_mg_l", "no3_mg_l"):
        keep.add(max(range(n), key=lambda i: getattr(trajectory[i].state.nitrogen, attr)))
    keep |= {i for i, d in enumerate(trajectory) if d.fish_harvested_today_kg > 0}
    budget = max(2, int(max_frames))
    stride = max(1, -(-n // max(1, budget - len(keep))))
    keep |= set(range(0, n, stride))
    return sorted(keep)


def build_frames(trajectory, *, today_index: int | None = None, dates=(),
                 max_frames: int = DEFAULT_MAX_FRAMES, roster: int = 0,
                 peak_count: int = 0) -> list[dict]:
    """Turn a `ProductionRun.trajectory` into embeddable per-day frames.

    `today_index` marks the day that is NOW: earlier days are what already happened, later
    ones are projection. Pass None when the run is not anchored to a calendar at all (a
    season simulated from a design), and every frame is labelled a projection rather than
    borrowing the authority of "today". Pass a NEGATIVE index when now precedes the run —
    a live mirror whose forecast starts tonight — and every day in it is a forecast.
    """
    days = list(trajectory)
    if not days:
        return []
    peak = peak_count or max(int(d.state.fish.count) for d in days) or 1
    roster = roster or FISH_DRAWN_PER_TANK
    frames = []
    for i in _keyframe_indices(days, max_frames, today_index):
        d = days[i]
        if today_index is None:
            kind = "projected"
        elif i < today_index:
            kind = "past"
        elif i == today_index:
            kind = "today"
        else:
            kind = "forecast"
        note = ""
        if d.fish_harvested_today_kg > 0:
            note = f"harvested {d.fish_harvested_today_kg:.0f} kg of fish and restocked"
        frames.append(_frame(d.state, kind=kind, roster=roster, peak_count=peak,
                             fac=d.crop_factors, note=note,
                             date=str(dates[i]) if i < len(dates) else ""))
    return frames


def to_scene(layout: Layout, out: DesignOutput, *,
             name: str = "Aquaponic system", subtitle: str = "",
             crop: str = "", species: str = "",
             state=None, trajectory=(), dates=(), today_index: int | None = None,
             as_of: str = "", max_frames: int = DEFAULT_MAX_FRAMES) -> dict:
    """Build the scene dict the 3D viewer renders.

    Fish are shown in proportion to the design's stocking (capped for legibility — the
    picture is an explanation, not a census), split evenly across rearing tanks.

    `crop` and `species` are the plain keys ("lettuce", "tilapia"), not labels. The viewer
    picks a plant form and a fish form from them, so a bed of lettuce and a bed of tomatoes
    stop looking like the same green spheres. Parsing them out of the label instead would
    make the picture depend on prose, which is the coupling `scene3d` exists to avoid.

    Binding a twin (all optional, and omitting them leaves the design view untouched):

    - `state`: one `ProductionState` — the live mirror, which is what NOW means everywhere
      else in this project: `/forecast` prints it as "Now" and `advisory.recommend` reasons
      about it. So when it is given it becomes the TODAY frame, and the trajectory behind it
      is entirely FORECAST, exactly as `production.format_summary` labels it. Letting the
      simulated first day stand in for today would put a different pond in the picture from
      the one the bot is talking about, which is the disagreement `twin_view` exists to
      prevent. Today's crop appearance comes from that first simulated day, because a stored
      state carries no crop factors and today's conditions are precisely what it evaluates.
    - `trajectory`: a `ProductionRun.trajectory`, embedded as frames the viewer scrubs
      through. With `today_index` set and no `state` it is a run anchored mid-way; with it
      None the run is a projection from the design and is labelled as one.
    - `dates`: ISO dates aligned to the trajectory, so the scrubber can say a real date
      instead of only a day number.

    The frames are embedded, never fetched: the file has to work from a double-click on a
    laptop with no connection (#79).
    """
    tanks = layout.by_role("fish_tank")
    roster = FISH_DRAWN_PER_TANK * max(1, len(tanks))
    days = list(trajectory)
    counts = [int(d.state.fish.count) for d in days]
    if state is not None:
        counts.append(int(state.fish.count))
    peak = max(counts, default=0) or 1

    frames: list[dict] = []
    if state is not None:
        frames.append(_frame(state, kind="today", roster=roster, peak_count=peak,
                             fac=days[0].crop_factors if days else None, date=as_of))
        today_index = -1          # now precedes the run: every day in it is a forecast
    frames += build_frames(days, today_index=today_index, dates=dates,
                           max_frames=max_frames, roster=roster, peak_count=peak)

    # The fish roster: how many meshes the viewer builds. In design mode it is the design's
    # stocking, capped; with a twin bound it is sized to the busiest frame, and each frame
    # then says how many of them are visible on that day. A live twin with no fish in it
    # must NOT fall through to the design's stocking, so the fallback keys on whether a twin
    # is bound at all rather than on a count that is legitimately zero.
    fish: list[dict] = []
    peak_drawn = max((f["fish"]["drawn"] for f in frames), default=0)
    shown = peak_drawn if frames else (min(out.fish_count, roster)
                                       if tanks and out.fish_count else 0)
    if tanks and shown:
        per = max(1, -(-shown // len(tanks)))     # ceil: the roster must cover the busiest day
        length = frames[0]["fish"]["length_m"] if frames else 0.15
        for t in tanks:
            fish.append({"tank": t.id, "count": per, "length_m": length})

    mode = "design"
    if frames:
        mode = "live" if any(f["kind"] in ("today", "past", "forecast") for f in frames) \
            else "projection"

    return {
        "schema_version": SCENE_SCHEMA_VERSION,
        "name": name,
        "subtitle": subtitle,
        "crop": crop,
        "species": species,
        "units": "m",
        "greenhouse": {
            "width": layout.greenhouse.width_m,
            "length": layout.greenhouse.length_m,
            "wall_h": layout.greenhouse.wall_h_m,
            "ridge_h": layout.greenhouse.ridge_h_m,
        },
        "objects": [_component(c) for c in layout.components],
        "pipes": [
            {"from": p.from_id, "to": p.to_id, "path": [list(q) for q in p.path],
             "d": p.diameter_m, "flow_lpm": p.flow_lpm,
             "pumped": p.pumped, "length_m": p.length_m}
            for p in layout.pipes
        ],
        "fish": fish,
        "assumptions": list(layout.assumptions),
        "hydraulics": (asdict(layout.hydraulics)
                       if layout.hydraulics is not None else None),
        "twin": {
            "mode": mode,
            "as_of": as_of,
            "frames": frames,
            # The geometry is a proposal even when the state is the operator's own. Saying so
            # on screen is the difference between a twin and a picture that flatters itself.
            "geometry_note": ("layout is a proposed arrangement; the state shown is this "
                              "system's" if mode == "live" else ""),
        },
    }
