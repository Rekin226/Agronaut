"""Where the shipped reference tables live — the price book, the calibration record, the
validation verdict.

These are read-only facts that travel *with* the code: a price book someone contributed, a
growth coefficient fitted from a public dataset, the scorecard saying how well the twin
predicted held-out nitrate. They are not state. Nothing writes them at runtime, and a
release is the only thing that changes them.

They used to be found at `aqua_model/../data/`, which is correct in a checkout and wrong
everywhere else. Under `pip install agronaut` that resolves to `site-packages/data/`, a
directory nothing creates, so every one of them silently went missing:

  * the price book vanished, and cost estimates degraded to "no price book on disk yet"
  * `reference_systems.load()` raised outright
  * and the validation record — the artifact the whole honesty story rests on — was absent,
    so the twin reported PREDICTIVE SKILL UNMEASURED to exactly the users who had no way of
    knowing it had in fact been measured

That last one is the reason this module exists. Failing to a stronger caveat is the right
direction to fail, so nobody was misled, but a package that cannot back up its own README
is still broken. The evidence has to ship with the claim.

The resolution mirrors `agronaut_agent.paths.corpus_root()`: prefer the checkout, otherwise
look where a wheel's data files install. It lives in `aqua_model` rather than beside that
function because `aqua_model` is the lower layer — it may not import the agent package —
and because the trust zone must keep importing with no dependency beyond the standard
library.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Shipped as data files by pyproject's [tool.setuptools.data-files]. Kept explicit rather
# than globbed: `data/` in a checkout also holds the memory database, the analytics log and
# the fetched-page cache, none of which belong in a wheel.
SHIPPED = (
    "coefficient_sources.json",
    "dataset_registry.json",
    "empirical_envelope.json",
    "price_book.json",
    "reference_systems.json",
    "tgc_calibration.json",
    "twin_validation.json",
)


def reference_dir() -> Path:
    """Directory holding the shipped reference tables."""
    override = os.environ.get("AGRONAUT_REFERENCE_DIR")
    if override:
        return Path(override)
    checkout = _PROJECT_ROOT / "data"
    if (checkout / "reference_systems.json").is_file():
        return checkout
    return Path(sys.prefix) / "share" / "agronaut" / "data"


def reference_path(name: str) -> Path:
    """Absolute path to one shipped table, whether installed or in a checkout."""
    return reference_dir() / name
