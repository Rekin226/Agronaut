"""Physical plausibility checks for sensor channels.

A sensor can fail in more ways than one, and each failure mode needs its own test. The envelope
builder originally tested exactly one — a channel pinned at its rail — and labelled everything else
`reliable`. That made "reliable" the answer for every failure nobody had written a check for, and
dissolved oxygen went out at 4.3x saturation wearing it.

Three checks live here, each earned from real data in `data/raw/`:

* **Physical ceilings.** Dissolved oxygen cannot meaningfully exceed saturation, which is a
  function of temperature. pH is bounded by its own scale. Concentrations cannot be negative.
* **Dead-sensor sentinels.** A DS18B20 with no probe attached reports exactly -127 degrees C. That
  is a stable, in-band-looking number that no range check catches, and live aquaponics channels
  are full of it.
* **Channel independence.** Two channels that are supposed to measure different things and instead
  correlate at r > 0.99 are one instrument written to two fields. Measured on public aquaponics
  data, an `Ammonia`/`Nitrite` pair came back at r = 0.9994 — nitrite that was never nitrite.

Pure functions over plain numbers: no I/O, no network, no pandas requirement in the core maths, so
this stays inside the trust zone and is testable without a dataset.
"""

from __future__ import annotations

import math

from .coefficients import DO_SUPERSATURATION_TOLERANCE

# Values a dead or unread sensor reports. These are the dangerous ones: stable, precise-looking,
# and comfortably inside any range wide enough to admit genuine extremes.
SENTINELS: dict[float, str] = {
    -127.0: "DS18B20 temperature probe: no sensor connected",
    -999.0: "generic no-data marker",
    -9999.0: "generic no-data marker",
    85.0: "DS18B20 power-on reset value (only suspicious in bulk)",
    65535.0: "unsigned 16-bit overflow / unread ADC",
    -32768.0: "signed 16-bit underflow / unread ADC",
}

# Channels where a negative reading is physically meaningless.
_NON_NEGATIVE = {
    "dissolved_oxygen_mg_l", "ammonia_mg_l", "nitrite_mg_l", "nitrate_mg_l",
    "turbidity_ntu", "ec_us_cm", "tds_mg_l", "fish_length_cm", "fish_weight_g",
    "water_level_l", "alkalinity_mg_l",
}

_PH_MIN, _PH_MAX = 0.0, 14.0


def do_saturation_mg_l(temp_c: float, elevation_m: float = 0.0) -> float:
    """Dissolved-oxygen saturation in fresh water, mg/L (Benson & Krause).

    The standard closed-form fit used by USGS and APHA; no lookup table required. Pressure is
    corrected for elevation with the barometric approximation, because a highland site genuinely
    saturates lower and would otherwise look supersaturated.
    """
    if not math.isfinite(temp_c) or not (-2.0 <= temp_c <= 60.0):
        raise ValueError(f"temperature {temp_c} out of range for a saturation calculation")
    tk = temp_c + 273.15
    ln_c = (-139.34411
            + 1.575701e5 / tk
            - 6.642308e7 / tk ** 2
            + 1.243800e10 / tk ** 3
            - 8.621949e11 / tk ** 4)
    c = math.exp(ln_c)
    if elevation_m:
        c *= math.exp(-elevation_m / 8434.0)     # barometric scale height, metres
    return c


def implausible_do(do_mg_l: float, temp_c: float, elevation_m: float = 0.0) -> bool:
    """True when a DO reading exceeds what water at this temperature can physically hold.

    A tolerance above 1.0 is deliberate: photosynthesis genuinely supersaturates a planted system
    for part of the day. Sustained multiples of saturation are a broken probe, not a bloom.
    """
    if do_mg_l < 0:
        return True
    return do_mg_l > DO_SUPERSATURATION_TOLERANCE.value * do_saturation_mg_l(temp_c, elevation_m)


def is_sentinel(value: float, tol: float = 1e-6) -> str | None:
    """The dead-sensor meaning of `value`, or None if it is not a known sentinel."""
    for s, meaning in SENTINELS.items():
        if abs(value - s) <= tol:
            return meaning
    return None


def implausible_value(channel: str, value: float) -> str | None:
    """Why `value` is impossible for `channel`, or None if it is physically admissible.

    Channel-agnostic checks only — anything needing a second channel (DO against temperature)
    has its own function, because a per-value check cannot see the rest of the row.
    """
    if not math.isfinite(value):
        return "not a finite number"
    if channel == "ph" and not (_PH_MIN <= value <= _PH_MAX):
        return f"pH {value} outside the 0-14 scale"
    if channel in _NON_NEGATIVE and value < 0:
        return f"negative {channel}"
    return None


def sentinel_fraction(values) -> tuple[float, str]:
    """Fraction of readings that are dead-sensor sentinels, and what the commonest one means."""
    vals = [v for v in values if isinstance(v, (int, float)) and math.isfinite(v)]
    if not vals:
        return 0.0, ""
    counts: dict[str, int] = {}
    for v in vals:
        meaning = is_sentinel(float(v))
        if meaning:
            counts[meaning] = counts.get(meaning, 0) + 1
    if not counts:
        return 0.0, ""
    worst = max(counts, key=counts.get)
    return sum(counts.values()) / len(vals), worst


def channels_are_independent(a, b, threshold: float = 0.99) -> tuple[bool, float]:
    """Whether two channels look like separate instruments, plus their correlation.

    Ammonia and nitrite are sequential stages of nitrification with a lag between them; they
    cannot track each other almost perfectly in a living system. When they do, one probe is being
    written to two fields — which is how a corpus acquires a nitrite column that never measured
    nitrite.
    """
    pairs = [(float(x), float(y)) for x, y in zip(a, b)
             if isinstance(x, (int, float)) and isinstance(y, (int, float))
             and math.isfinite(x) and math.isfinite(y)]
    n = len(pairs)
    if n < 30:
        return True, float("nan")           # too little overlap to accuse anyone
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in pairs)
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return True, float("nan")           # a constant channel has no correlation to speak of
    r = sxy / math.sqrt(sxx * syy)
    return abs(r) < threshold, r
