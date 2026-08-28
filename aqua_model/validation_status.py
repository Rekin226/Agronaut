"""What the twin's predictions have actually been shown to do — read from the evidence.

The projection footer used to say the model was "calibrated on literature seeds, not on your
system". True, and weaker than what is now known: it reads as *not yet tuned to you*, when the
model has since been scored against seven real ponds and **did not beat a flat-line null on six
of them** (`data/twin_validation.json`, produced by `scripts/validate_twin.py`).

Those are different admissions. "Not tuned to you" invites an operator to trust the shape and
discount the precision. "Tested, and level prediction is not demonstrated" tells them not to act
on the numbers at all. The second is the true one, and it is the one that matters to someone with
fish in the water.

The statement is DERIVED from the artifact rather than written next to it, so it cannot drift
from the evidence: improve the validation and the wording follows on its own. If the artifact is
missing the caveat gets stronger, never weaker — an unmeasured model is not a validated one.
"""

from __future__ import annotations

import json
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
ARTIFACT = _REPO_ROOT / "data" / "twin_validation.json"

_UNKNOWN = (
    "PREDICTIVE SKILL UNMEASURED: no validation record was found, so nothing here has been "
    "checked against a real system. Treat every number as illustrative.",
)


def _load(path: Path | str | None = None) -> dict | None:
    try:
        return json.loads(Path(path or ARTIFACT).read_text())
    except Exception:      # a missing or unreadable record must never break a projection
        return None


def validation_lines(path: Path | str | None = None) -> tuple[str, ...]:
    """Two or three lines stating, from the record, what the twin has been shown to do.

    Deliberately blunt about the failure. A projection that prints kilograms and mg/L reads as a
    forecast whatever hedging surrounds it, so the hedge has to be specific enough to overcome
    that: not "treat with caution" but "on six of seven real ponds this was worse than assuming
    nothing changes".
    """
    data = _load(path)
    if not data:
        return _UNKNOWN
    s = data.get("summary") or {}
    n = int(s.get("n_scored") or 0)
    both = int(s.get("n_beats_both_nulls") or 0)
    shape = int(s.get("n_positive_shape_correlation") or 0)
    r_med = s.get("holdout_r_median")
    if not n:
        return _UNKNOWN

    beaten = n - both
    # "both nulls" is the honest phrasing: the record scores against a flat baseline AND a
    # linear-trend baseline, and this counts ponds that beat BOTH. Saying "a no-change baseline"
    # would overstate the failure — one pond does beat the flat null while losing to the trend —
    # and being more negative than the evidence is as inaccurate as being less.
    head = (
        f"VALIDATION: scored against {n} real ponds on held-out data. It beat both a flat and a "
        f"trend baseline on {both} of {n}"
    )
    head += (
        f" — on the other {beaten} a simple baseline predicted the level as well or better."
        if beaten else "."
    )
    mid = (
        f"It tracked the DIRECTION of change on {shape} of {n}"
        + (f" (median correlation {float(r_med):.2f})" if isinstance(r_med, (int, float)) else "")
        + ". So use it to compare options, never to predict a level."
    )
    return (head, mid, f"Evidence: {ARTIFACT.relative_to(_REPO_ROOT)} "
                       f"(regenerate with scripts/validate_twin.py).")


def level_prediction_demonstrated(path: Path | str | None = None) -> bool:
    """True only if the twin beat both null models on a majority of scored ponds.

    The gate for whether a forward number may be presented as a prediction at all. It is
    currently False, and that is the honest state, not a bug to route around.
    """
    data = _load(path)
    if not data:
        return False
    s = data.get("summary") or {}
    n = int(s.get("n_scored") or 0)
    both = int(s.get("n_beats_both_nulls") or 0)
    return n > 0 and both > n / 2
