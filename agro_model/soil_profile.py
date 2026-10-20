"""Layer-by-layer root-zone storage accounting, without a transport simulation."""

from copy import deepcopy
from datetime import datetime, timezone
from fractions import Fraction
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .resource_timing import _instant, _number
from .resources import _amount, _object
from .validation import choice, text

SOURCES = (
    "FAO Irrigation and Drainage Paper 56 (1998), Chapter 8, equations 82 and 87: volumetric water storage and depletion. https://www.fao.org/4/X0490E/x0490e0e.htm",
    "University of Minnesota Extension, Basics of irrigation scheduling (reviewed 2026): Renshaw and Hubbard depth-zone available-water examples. https://extension.umn.edu/natural-resources/conservation/agricultural-soil-and-water/basics-of-irrigation-scheduling",
)


def _decimal(value, label, maximum, *, positive=False):
    amount = Fraction(_amount(value), 1_000_000)
    if amount > maximum or (positive and not amount):
        raise ValueError(f"{label} must be {'positive' if positive else 'nonnegative'} and at most {maximum}")
    return amount


def evaluate_soil_profile(payload):
    data = _object(payload, ("schema_version", "site", "profile", "layers"), "soil profile")
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise ValueError("schema_version must be integer 1")
    site = _object(data["site"], ("name", "timezone"), "site")
    text("site name", site["name"])
    text("site timezone", site["timezone"])
    try:
        zone = ZoneInfo(site["timezone"])
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("site timezone must be an IANA timezone") from exc
    profile = _object(data["profile"], ("name", "observed_at", "kind", "root_depth_m", "representation_note", "source"), "profile")
    for key in ("name", "representation_note", "source"):
        text(key, profile[key])
    choice("profile kind", profile["kind"], ("observed", "scenario"))
    observed = _instant(profile["observed_at"], zone)
    if profile["kind"] == "observed" and observed > datetime.now(timezone.utc):
        raise ValueError("an observed soil profile cannot be in the future")
    root = _decimal(profile["root_depth_m"], "root depth", 10, positive=True)
    if not isinstance(data["layers"], list) or not 1 <= len(data["layers"]) <= 100:
        raise ValueError("supply 1–100 contiguous soil layers from the surface through the root depth")
    previous, ids, layers, missing, notes = Fraction(0), set(), [], [], []
    totals = {key: Fraction(0) for key in ("field_capacity_storage_mm", "wilting_point_storage_mm", "total_available_water_mm",
                                         "water_storage_mm", "signed_deficit_mm", "above_field_capacity_mm", "below_wilting_point_mm")}
    complete, in_bounds = True, True
    for item in data["layers"]:
        row = _object(item, ("id", "top_m", "bottom_m", "field_capacity", "wilting_point", "water_content",
                            "soil_source", "water_source", "provenance", "quality"), "soil layer")
        key = text("layer id", row["id"]).strip()
        if key in ids:
            raise ValueError("soil layer IDs must be unique")
        ids.add(key)
        for name in ("soil_source", "water_source"):
            text(name, row[name])
        choice("layer provenance", row["provenance"], ("measured", "reported", "estimated"))
        choice("layer quality", row["quality"], ("unverified", "checked", "rejected"))
        top = _decimal(row["top_m"], "layer top", 10)
        bottom = _decimal(row["bottom_m"], "layer bottom", 10, positive=True)
        if top != previous or bottom <= top:
            raise ValueError("layers must be ordered and contiguous from depth zero, without gaps or overlaps")
        previous = bottom
        fc = _decimal(row["field_capacity"], "field capacity", 1, positive=True)
        wp = _decimal(row["wilting_point"], "wilting point", 1)
        if wp >= fc:
            raise ValueError("wilting point must be strictly below field capacity in each layer")
        theta = None if row["water_content"] is None else _decimal(row["water_content"], "water content", 1)
        depth = max(Fraction(0), min(bottom, root)-top)
        values = dict(field_capacity_storage_mm=1000*depth*fc, wilting_point_storage_mm=1000*depth*wp,
                      total_available_water_mm=1000*depth*(fc-wp))
        usable = theta is not None and row["quality"] != "rejected"
        state = "unknown" if theta is None else "rejected" if row["quality"] == "rejected" else (
            "above_field_capacity" if theta > fc else "below_wilting_point" if theta < wp else "within_bounds")
        if depth:
            if not usable:
                complete = False
                missing.append(f"layers.{key}.water_content: {state}")
            elif state != "within_bounds":
                in_bounds = False
                notes.append(f"Rooted layer {key} is {state}; a bounded root-zone depletion is withheld.")
        if usable:
            values.update(water_storage_mm=1000*depth*theta, signed_deficit_mm=1000*depth*(fc-theta),
                          above_field_capacity_mm=1000*depth*max(Fraction(0), theta-fc),
                          below_wilting_point_mm=1000*depth*max(Fraction(0), wp-theta))
        for name, value in values.items():
            totals[name] += value
        layers.append(dict(id=key, rooted_thickness_m=_number(depth), reading_status=state,
                           contributes_to_root_zone=bool(depth), values={name: _number(value) for name, value in values.items()}))
    if previous < root:
        raise ValueError("soil layers must cover the complete declared root depth")
    known = {"field_capacity_storage_mm", "wilting_point_storage_mm", "total_available_water_mm"}
    summary = {name: _number(value) if complete or name in known else None for name, value in totals.items()}
    summary["bounded_depletion_mm"] = _number(totals["signed_deficit_mm"]) if complete and in_bounds else None
    return dict(model_version="soil-profile-v1", evidence_status="experimental", inputs=deepcopy(payload),
        observed_at_local=observed.astimezone(zone).isoformat(), root_depth_m=_number(root), layers=layers,
        root_zone=summary, complete_root_water_content=complete, within_layer_water_bounds=in_bounds if complete else None,
        units={name: "mm" for name in summary}, missing_inputs=missing, notes=notes, sources=list(SOURCES),
        assumptions=["Layer water contents and field-capacity/wilting-point values are volumetric fractions of the same bulk soil volume (m3/m3), including consistent coarse-fragment treatment.",
                     "The operator declares a common represented time and footprint. Each value is uniform within its layer; a partly rooted layer uses only the thickness above the supplied root depth.",
                     "Layer storage equals 1000 times thickness in meters times volumetric water content. Root-zone totals sum the included thicknesses; this is storage arithmetic, not a root-uptake weighting model.",
                     "Missing or rejected water readings in rooted layers withhold all root water-state totals. Supplied soil capacities remain visible. Unknown readings below the roots do not affect root totals.",
                     "Wet and dry layer departures remain separate. Signed deficits may cancel arithmetically, but bounded depletion is withheld if any rooted layer lies outside its own wilting-point/field-capacity interval."],
        limitations=["No layer-to-layer flow, infiltration, drainage timing, capillary rise, salinity, root uptake or irrigation prescription is simulated.",
                     "A point sensor is not automatically a layer or field average. Sampling coverage, calibration, hydraulic properties and declared quality are not independently verified.",
                     "This assessment does not change daily water logs, observations or existing model states, and does not establish an equivalent homogeneous soil.",
                     "Exact decimal arithmetic preserves comparisons; display values are rounded to six decimals and exact fractions are retained. No independent field validation."])
