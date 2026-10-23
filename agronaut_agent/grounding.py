"""Does every number in a reply come from somewhere? (#180)

The validation gate guards numbers going INTO the engine. This guards numbers coming OUT:
the reply a grower builds from. It is pure code, no model, so it runs in CI and on every
live turn for the cost of a regex.

A quantity in a reply (a number with a unit) is GROUNDED when the same physical amount
appears in what the model was shown that turn: a tool result, an earlier result replayed as
reference, or something the user said. "Same amount" is judged after converting to one base
unit per dimension, within a rounding tolerance, so these pass:

    tool: pump=6666.7 L/h           reply: "~6,700 L/h"   same flow, rounded
    tool: system_volume=6666.7 L    reply: "6.7 m³"       same volume, converted

and this fails, which is the error a small local model made on 2026-09-30:

    tool: pump=6666.7 L/h           reply: "~6.7 L/h"     right digits, wrong unit, 1000x

Numbers without a unit ("96 fish", "step 2", "pH 6.8") are not checked, and neither are
units too ambiguous to trust alone ("3 m" may be months). Passing means every figure the
reply quotes traces to a source. It does not mean the reply is right.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# unit spelling (lowercase) -> (dimension, factor to that dimension's base unit)
_UNITS: dict[str, tuple[str, float]] = {}


def _add(dim: str, factor: float, *names: str) -> None:
    for n in names:
        _UNITS[n] = (dim, factor)


# Bases: flow L/h, daily volume L/day, volume L, feed g/day, mass kg, area m², power W,
# energy kWh, temperature °C, concentration mg/L, length m, share %.
_add("flow", 1.0, "l/h", "lph", "l/hr", "litres/hour", "liters/hour", "litres per hour",
     "liters per hour")
_add("flow", 1000.0, "m³/h", "m3/h", "m³/hr", "m3/hr")
_add("flow", 60.0, "l/min", "lpm")
_add("daily", 1.0, "l/day", "l/d", "l/jour", "l/j", "litres/day", "liters/day",
     "litres per day", "liters per day", "litres a day", "liters a day")
_add("daily", 1000.0, "m³/day", "m3/day", "m³/d", "m3/d")
_add("volume", 1.0, "l", "litre", "litres", "liter", "liters")
_add("volume", 1000.0, "m³", "m3", "kl")
_add("volume", 0.001, "ml")
_add("feed", 1.0, "g/day", "g/d", "g/jour")
_add("feed", 1000.0, "kg/day", "kg/d", "kg/jour")
_add("mass", 1.0, "kg", "kgs", "kilos", "kilograms")
_add("mass", 0.001, "g", "grams")
_add("area", 1.0, "m²", "m2")
_add("area", 10000.0, "ha", "hectares")
_add("power", 1.0, "w", "watts")
_add("power", 1000.0, "kw")
_add("energy", 1.0, "kwh")
_add("temperature", 1.0, "°c", "°")
_add("concentration", 1.0, "mg/l", "ppm")
_add("length", 0.01, "cm")
_add("length", 0.001, "mm")
_add("percent", 1.0, "%")

# Longest spellings first, so "m³/h" wins over "m³" and "l/day" over "l".
_UNIT_ALT = "|".join(re.escape(u) for u in sorted(_UNITS, key=len, reverse=True))
_NUM = r"\d{1,3}(?:[  ,.]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?"
_QUANTITY = re.compile(rf"(?<![\w.,])({_NUM})\s?({_UNIT_ALT})(?![\w/²³])", re.I)
_BARE = re.compile(rf"(?<![\w.,])({_NUM})(?![\w.,]\d)")


# Amounts that tools write both with and without "/day".
_PER_DAY_TWIN = {"daily": "volume", "volume": "daily", "feed": "mass", "mass": "feed"}


@dataclass(frozen=True)
class Quantity:
    text: str                  # as written, e.g. "6.7 L/h"
    values: tuple[float, ...]  # candidate readings, converted to the dimension's base unit
    dimension: str


def readings(raw: str) -> tuple[float, ...]:
    """Every sensible reading of a written number.

    "6,700" and "2 070" are grouped thousands. "6.700" is ambiguous (6700 in French style,
    6.7 in English) so both readings are returned. "0.052" is never grouped. "1,5" is a
    French decimal.
    """
    s = raw.replace(" ", " ").strip()
    m = re.fullmatch(r"(\d{1,3})((?:[ ,.]\d{3})+)(?:([.,])(\d+))?", s)
    out: list[float] = []
    if m and m.group(1) != "0":
        grouped = float(m.group(1) + re.sub(r"[ ,.]", "", m.group(2))
                        + ("." + m.group(4) if m.group(4) else ""))
        out.append(grouped)
        seps = set(re.findall(r"[ ,.]", m.group(2)))
        if not m.group(3) and seps in ({"."}, {","}) and m.group(2).count(seps.pop()) == 1:
            out.append(float(s.replace(",", ".")))   # a lone "6.700" / "6,700" as a decimal
    else:
        try:
            out.append(float(s.replace(" ", "").replace(",", ".")))
        except ValueError:
            pass
    return tuple(dict.fromkeys(out))


def quantities(text: str) -> list[Quantity]:
    """Every number-with-a-unit in `text`, converted to its dimension's base unit."""
    out = []
    for m in _QUANTITY.finditer(text or ""):
        dim, factor = _UNITS[m.group(2).lower()]
        vals = tuple(v * factor for v in readings(m.group(1)))
        if vals:
            out.append(Quantity(m.group(0).strip(), vals, dim))
    return out


def _bare_numbers(text: str) -> list[float]:
    """Numbers in `text` that carry no unit. Numbers inside a quantity are excluded, so a
    source's "6666.7 L/h" can never ground a reply's "6666.7 kg"."""
    spans = [m.span() for m in _QUANTITY.finditer(text or "")]
    out: list[float] = []
    for m in _BARE.finditer(text or ""):
        if any(a <= m.start() < b for a, b in spans):
            continue
        out.extend(readings(m.group(1)))
    return out


def _close(a: float, b: float, tol: float) -> bool:
    # Relative tolerance covers rounding ("6666.7" -> "6,700" is 0.5%); the absolute floor
    # covers one-decimal rounding of small values ("0.52" -> "0.5").
    return abs(a - b) <= max(tol * max(abs(a), abs(b)), 0.051)


def ungrounded(reply: str, sources: list[str], tol: float = 0.05) -> list[str]:
    """Quantities in `reply` that nothing in `sources` supports, as written in the reply.

    Supported means: a source quantity of the SAME dimension and a close value after unit
    conversion, or a unit-less source number equal to the value as written. Zero is never
    flagged ("0 W", "0%").
    """
    by_dim: dict[str, list[float]] = {}
    written_by_dim: dict[str, list[str]] = {}
    bare: list[float] = []
    for t in sources:
        for q in quantities(t):
            by_dim.setdefault(q.dimension, []).extend(q.values)
            written_by_dim.setdefault(q.dimension, []).append(
                re.match(rf"({_NUM})", q.text).group(1))
        bare.extend(_bare_numbers(t))
    flagged = []
    for q in quantities(_without_examples(reply)):
        if all(v == 0 for v in q.values):
            continue
        same_dim = by_dim.get(q.dimension, [])
        if any(_close(v, s, tol) for v in q.values for s in same_dim):
            continue
        raw = re.match(rf"({_NUM})", q.text).group(1)
        if any(_rounds_to(s, raw) for s in bare):
            continue
        # Tools sometimes leave "/day" implicit ("makeup_water=58 L"); the reply rightly says
        # "58 L/day". A plain amount grounds its per-day twin, and back, but only when the
        # written number is the same, and never for flow, where L vs L/h is the 1000x trap.
        twin = _PER_DAY_TWIN.get(q.dimension)
        if twin and any(_rounds_to(v, raw) for w in written_by_dim.get(twin, [])
                        for v in readings(w)):
            continue
        # Tools report shares as fractions ("disagreement_fraction=0.084"); replies say "8.4%".
        if q.dimension == "percent" and any(
                0 < s <= 1 and _rounds_to(s * 100, raw) for s in bare):
            continue
        flagged.append(q.text)
    return flagged


# "(a rough estimate is fine, e.g. 100L, 300L, 500L)": sample answers inside a question are
# not claims. Found on the maintainer's own conversation history: they were four of the seven
# replies the first version flagged. The example runs to the closing bracket, the question
# mark or the end of the line.
_EXAMPLE = re.compile(r"(?i)\b(?:e\.g\.?|eg\.|for example|for instance|such as)[^)?\n]*")


def _without_examples(text: str) -> str:
    return _EXAMPLE.sub(" ", text or "")


def _rounds_to(source: float, written: str) -> bool:
    """Whether `source`, rounded to the precision `written` shows, IS `written`.

    Used for unit-less source numbers, which say nothing about what they measure, so only
    exact rounding may match them. The loose 5% used within a dimension would let a pH of 7.0
    ground a flow of "6.7 L/h", which is how the first version of this check missed the
    1000x error it was written to catch.
    """
    for value in readings(written):
        s = written.replace(" ", " ")
        decimals = len(re.split(r"[.,]", s)[-1]) if re.search(r"[.,]\d{1,2}$", s) else 0
        if abs(source - value) <= 0.5 * 10 ** (-decimals) + 1e-9:
            return True
    return False
