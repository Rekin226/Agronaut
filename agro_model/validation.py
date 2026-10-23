"""Strict boundaries for physical values and provenance; no coercion of missing facts."""

from datetime import datetime
from math import isfinite


def number(name: str, value: float, low=None, high=None, *, positive=False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    try:
        finite = isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError(f"{name} must be a finite number")
    if positive and value <= 0:
        raise ValueError(f"{name} must be positive")
    if low is not None and value < low or high is not None and value > high:
        raise ValueError(f"{name} must be within {low} to {high}")
    return float(value)


def text(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be nonempty text")
    return value


def choice(name: str, value: str, choices) -> str:
    if not isinstance(value, str) or value not in choices:
        raise ValueError(f"{name} must be one of {', '.join(choices)}")
    return value


def aware_time(name: str, value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include a timezone")
    return value
