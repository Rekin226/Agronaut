"""Farm vocabulary separates production, environment and water delivery.

Identifiers describe relationships; persistence must enforce ownership and referential
integrity. Nothing here infers a farm or field from a chat user's identity.
"""

from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .validation import aware_time, choice, number, text


@dataclass(frozen=True)
class Farm:
    id: str
    owner_id: str
    name: str
    timezone: str

    def __post_init__(self):
        for key in ("id", "owner_id", "name", "timezone"):
            text(key, getattr(self, key))
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("timezone must be an IANA timezone") from exc


@dataclass(frozen=True)
class ProductionUnit:
    id: str
    farm_id: str
    name: str
    production_system: str
    medium: str
    environment: str
    water_strategy: str
    irrigation_method: str | None = None
    parent_unit_id: str | None = None

    def __post_init__(self):
        for key in ("id", "farm_id", "name"):
            text(key, getattr(self, key))
        choice("production_system", self.production_system, ("soil", "aquaponics", "hydroponics"))
        choice("medium", self.medium, ("soil", "water", "substrate"))
        choice("environment", self.environment, ("open_field", "greenhouse", "indoor"))
        choice("water_strategy", self.water_strategy,
               ("rainfed", "supplemental", "fully_irrigated"))
        if self.irrigation_method is not None:
            choice("irrigation_method", self.irrigation_method, ("drip", "sprinkler", "surface"))
        if self.production_system == "soil":
            if self.medium != "soil":
                raise ValueError("soil cultivation requires soil medium")
            if self.water_strategy != "rainfed" and self.irrigation_method is None:
                raise ValueError("irrigated soil cultivation requires an irrigation_method")
        else:
            if self.medium == "soil" or self.water_strategy != "fully_irrigated":
                raise ValueError("aquaponics and hydroponics require water/substrate and full water supply")
            if self.irrigation_method in ("sprinkler", "surface"):
                raise ValueError("sprinkler/surface methods are not supported for aquatic systems")
        if self.water_strategy == "rainfed" and self.irrigation_method is not None:
            raise ValueError("use supplemental water_strategy for rainfed crops with irrigation")
        if self.parent_unit_id is not None:
            text("parent_unit_id", self.parent_unit_id)
            if self.parent_unit_id == self.id:
                raise ValueError("a zone cannot be its own parent")
            if self.production_system != "soil" or self.water_strategy == "rainfed":
                raise ValueError("irrigation zones require irrigated soil cultivation")


@dataclass(frozen=True)
class ProductionCycle:
    id: str
    unit_id: str
    crop: str
    planted_on: str

    def __post_init__(self):
        for key in ("id", "unit_id", "crop", "planted_on"):
            text(key, getattr(self, key))
        parsed = date.fromisoformat(self.planted_on)
        if parsed.isoformat() != self.planted_on:
            raise ValueError("planted_on must use YYYY-MM-DD")


# Deliberately task-specific quantities: a generic float and arbitrary unit would allow
# a rainfall reading in kg to enter a water balance. Extend this registry per workflow.
OBSERVATION_UNITS = {
    "rainfall": "mm", "irrigation_depth": "mm", "irrigation_volume": "L",
    "soil_depletion": "mm", "soil_water_content": "m3/m3", "air_temperature": "degC",
    "flow": "L/h", "pressure": "kPa", "harvest": "kg", "runtime": "h",
}


@dataclass(frozen=True)
class Observation:
    id: str
    owner_id: str
    unit_id: str
    cycle_id: str | None
    quantity: str
    value: float
    unit: str
    observed_at: datetime
    recorded_at: datetime
    provenance: str
    source: str
    quality: str = "unverified"

    def __post_init__(self):
        for key in ("id", "owner_id", "unit_id", "source"):
            text(key, getattr(self, key))
        if self.cycle_id is not None:
            text("cycle_id", self.cycle_id)
        choice("quantity", self.quantity, OBSERVATION_UNITS)
        if self.unit != OBSERVATION_UNITS[self.quantity]:
            raise ValueError(f"{self.quantity} requires {OBSERVATION_UNITS[self.quantity]}")
        number("value", self.value, low=None if self.quantity == "air_temperature" else 0,
               high=1 if self.quantity == "soil_water_content" else None)
        aware_time("observed_at", self.observed_at)
        aware_time("recorded_at", self.recorded_at)
        choice("provenance", self.provenance, ("measured", "reported", "estimated", "forecast"))
        choice("quality", self.quality, ("unverified", "checked", "rejected"))
        if self.observed_at > self.recorded_at and self.provenance != "forecast":
            raise ValueError("a future observation must be labelled forecast")
