"""Install-logging standard (CEO-accepted expansion B) — the dataset moat.

A versioned, documented schema for what every installed system logs. The point: every
install — the founder's and any partner's — logs the SAME fields, the SAME units, the SAME
cadence, so the aggregate is research-grade from the first row instead of a pile of
inconsistent CSVs. This is also the raw material that, once collected across sites, lets the
M4 digital twin calibrate against reality.

Versioned on purpose: when the schema changes, the version bumps, and old rows stay
interpretable. `validate_row` rejects bad data loudly (same philosophy as the trust gate).
"""

from __future__ import annotations

from dataclasses import dataclass

SCHEMA_VERSION = "1.0.0"


@dataclass(frozen=True)
class Field:
    name: str
    unit: str
    required: bool
    cadence: str          # "per-reading" | "daily" | "weekly"
    description: str
    min_value: float | None = None
    max_value: float | None = None


# The standard. Add fields by appending and bumping SCHEMA_VERSION (never silently reuse).
FIELDS: tuple[Field, ...] = (
    Field("system_id", "", True, "per-reading", "Stable unique id for the installed system."),
    Field("timestamp", "ISO-8601", True, "per-reading", "When the reading was taken."),
    Field("water_temp_c", "°C", True, "daily", "Water temperature.", 0.0, 45.0),
    Field("ph", "", True, "weekly", "Water pH.", 3.0, 10.0),
    Field("ammonia_mg_l", "mg/L", True, "weekly", "Total ammonia nitrogen.", 0.0, 20.0),
    Field("nitrite_mg_l", "mg/L", True, "weekly", "Nitrite.", 0.0, 20.0),
    Field("nitrate_mg_l", "mg/L", True, "weekly", "Nitrate.", 0.0, 500.0),
    Field("dissolved_oxygen_mg_l", "mg/L", False, "daily", "Dissolved oxygen.", 0.0, 20.0),
    Field("feed_g", "g", True, "daily", "Feed given that day.", 0.0, 1_000_000.0),
    Field("makeup_water_l", "L", True, "daily", "Water added that day.", 0.0, 1_000_000.0),
    Field("fish_mortality_count", "count", True, "daily", "Fish deaths that day.", 0.0, 1_000_000.0),
    Field("harvest_fish_kg", "kg", False, "per-reading", "Fish harvested.", 0.0, 1_000_000.0),
    Field("harvest_crop_kg", "kg", False, "per-reading", "Crop harvested.", 0.0, 1_000_000.0),
    Field("note", "", False, "per-reading", "Free-text observation."),
)

_BY_NAME = {f.name: f for f in FIELDS}


def csv_header() -> list[str]:
    """Column order for a logging CSV. Stable for a given SCHEMA_VERSION."""
    return [f.name for f in FIELDS]


def required_fields() -> list[str]:
    return [f.name for f in FIELDS if f.required]


def validate_row(row: dict) -> list[str]:
    """Return a list of problems with a logged row (empty list = valid). Never raises."""
    problems: list[str] = []

    for name in required_fields():
        if row.get(name) in (None, ""):
            problems.append(f"missing required field: {name}")

    for name, value in row.items():
        field = _BY_NAME.get(name)
        if field is None:
            problems.append(f"unknown field not in schema v{SCHEMA_VERSION}: {name}")
            continue
        if value in (None, ""):
            continue
        if field.min_value is not None or field.max_value is not None:
            try:
                v = float(value)
            except (TypeError, ValueError):
                problems.append(f"{name} must be numeric, got {value!r}")
                continue
            lo = field.min_value if field.min_value is not None else float("-inf")
            hi = field.max_value if field.max_value is not None else float("inf")
            if not (lo <= v <= hi):
                problems.append(f"{name}={v} out of range [{lo}, {hi}]")

    return problems


def schema_doc() -> str:
    """Human-readable schema documentation (ship this with every install)."""
    lines = [f"# Aquaponics install-logging standard v{SCHEMA_VERSION}\n",
             "| Field | Unit | Required | Cadence | Description |",
             "|---|---|---|---|---|"]
    for f in FIELDS:
        req = "yes" if f.required else "no"
        lines.append(f"| {f.name} | {f.unit or '—'} | {req} | {f.cadence} | {f.description} |")
    return "\n".join(lines)
