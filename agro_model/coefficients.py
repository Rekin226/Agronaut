"""Version-pinned equation constants, not fitted agronomic parameters.

FAO Irrigation and Drainage Paper 56, Allen et al. (1998). Exact equation numbers
are attached to each constant. A future edition must get a distinct method version.
"""

from dataclasses import dataclass

FAO_METEOROLOGY = "https://www.fao.org/4/X0490E/x0490e07.htm"
FAO_ET = "https://www.fao.org/4/X0490E/x0490e06.htm"
FAO_SOIL = "https://www.fao.org/4/X0490E/x0490e0e.htm"
FAO_CROP = "https://www.fao.org/4/X0490E/x0490e0a.htm"


@dataclass(frozen=True)
class Constant:
    value: float
    unit: str
    equation: str
    source: str


CONSTANTS = {
    "svp_base": Constant(0.6108, "kPa", "11", FAO_METEOROLOGY),
    "svp_multiplier": Constant(17.27, "1", "11", FAO_METEOROLOGY),
    "svp_offset": Constant(237.3, "degC", "11", FAO_METEOROLOGY),
    "slope_multiplier": Constant(4098, "degC", "13", FAO_METEOROLOGY),
    "pressure_base": Constant(101.3, "kPa", "7", FAO_METEOROLOGY),
    "pressure_temp": Constant(293, "K", "7", FAO_METEOROLOGY),
    "pressure_lapse": Constant(0.0065, "K/m", "7", FAO_METEOROLOGY),
    "pressure_exponent": Constant(5.26, "1", "7", FAO_METEOROLOGY),
    "psychrometric": Constant(0.000665, "1/degC", "8", FAO_METEOROLOGY),
    "wind_numerator": Constant(4.87, "1", "47", FAO_METEOROLOGY),
    "wind_height_multiplier": Constant(67.8, "1/m", "47", FAO_METEOROLOGY),
    "wind_height_offset": Constant(5.42, "1", "47", FAO_METEOROLOGY),
    "energy_to_water": Constant(0.408, "mm/(MJ/m2)", "6", FAO_ET),
    "aerodynamic": Constant(900, "K s/m", "6", FAO_ET),
    "temperature_offset": Constant(273, "K", "6", FAO_ET),
    "wind_denominator": Constant(0.34, "s/m", "6", FAO_ET),
}


def c(key: str) -> float:
    return CONSTANTS[key].value
