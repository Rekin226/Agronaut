"""Pluggable image-classifier backend — an extra FEATURE source, never a verdict source.

A specialist classifier (a PlantVillage-class CNN, say) beats a general vision model at
fine-grained leaf disease. The temptation is to let its label be the answer. That would
undo the whole point of the vision path: a label like `Tomato___Late_blight` is a diagnosis
with no citation, no discriminating check, and no statement of what it cannot see.

So the classifier is wired in as a **third source of categorical features**, alongside the
prose extractor:

    photo ──▶ VLM ──────────▶ prose ──▶ features ─┐
        └──▶ classifier ──▶ label ──▶ features ───┴─▶ aqua_model.triage ──▶ cited differential

The label is mapped, by a fixed table, onto the SAME `ObservationFeatures` vocabulary the
prose extractor uses. Consequences that matter:

* the differential's candidates stay cited to `knowledge/*.md` — a classifier can never
  introduce an uncited candidate, no matter how confident it is;
* a wrong label can only select a different branch of a differential, exactly like a
  misread adjective;
* the label itself is surfaced to the user as an unverified model suggestion, clearly
  separated from the cited differential.

Provider-agnostic, mirroring `agent/llm.py` and `agent/vision.py`: select with
CLASSIFIER_PROVIDER / CLASSIFIER_MODEL. **No backend ships with Agronaut yet** — this is the
seam a contributor implements. `default_classifier()` returns None until one exists, so the
whole path is inert by default and nothing changes for existing deployments.

Adding a backend: see CONTRIBUTING.md ("Adding an image classifier").
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass

log = logging.getLogger(__name__)

# Below this, a label is discarded rather than fed forward. A low-confidence guess is worse
# than no guess: it adds a branch to the differential while carrying no real information.
MIN_CONFIDENCE = 0.60

DEFAULT_MODELS: dict[str, str] = {
    # No backend is bundled yet. A contributor adding one registers its default here and a
    # builder in _build_classifier_backend below. See issue "Plant-disease classifier".
}
SUPPORTED = tuple(DEFAULT_MODELS)


@dataclass(frozen=True)
class Prediction:
    """One classifier output. `label` is the model's raw class name — kept verbatim so it can
    be shown to the user as what the model actually said."""

    label: str
    confidence: float


# --- label → features -------------------------------------------------------------------
# The mapping is deliberately COARSE. We are not translating a diagnosis; we are extracting
# the visible evidence a diagnosis would have been based on, so the cited table can do the
# reasoning. `late blight` becomes "there are brown spots on the leaves", not "late blight".
#
# Keys are matched as substrings against a normalised label, so the same entry covers
# `Tomato___Late_blight`, `tomato late blight`, and `Late Blight (tomato)`.
_LABEL_FEATURES: tuple[tuple[str, dict], ...] = (
    # nutrient pictures — the ones the cited table discriminates best
    ("iron deficien",      {"leaf_pattern": ["interveinal"], "colour": ["yellow"], "leaf_age": "new"}),
    ("interveinal",        {"leaf_pattern": ["interveinal"], "colour": ["yellow"]}),
    ("nitrogen deficien",  {"leaf_pattern": ["whole_pale"], "colour": ["yellow"], "leaf_age": "old"}),
    ("potassium deficien", {"leaf_pattern": ["margin_scorch"], "colour": ["brown"], "leaf_age": "old"}),
    ("calcium deficien",   {"leaf_pattern": ["tip_burn"], "leaf_age": "new"}),
    ("tip burn",           {"leaf_pattern": ["tip_burn"], "leaf_age": "new"}),
    ("blossom_end",        {"leaf_pattern": ["tip_burn"], "leaf_age": "new"}),
    ("blossom end",        {"leaf_pattern": ["tip_burn"], "leaf_age": "new"}),
    # fungal / bacterial leaf disease → visible evidence only
    ("powdery_mildew",     {"leaf_pattern": ["powder"], "colour": ["white"]}),
    ("powdery mildew",     {"leaf_pattern": ["powder"], "colour": ["white"]}),
    ("downy_mildew",       {"leaf_pattern": ["powder"], "colour": ["white"]}),
    ("downy mildew",       {"leaf_pattern": ["powder"], "colour": ["white"]}),
    ("late_blight",        {"leaf_pattern": ["spots"], "colour": ["brown"]}),
    ("late blight",        {"leaf_pattern": ["spots"], "colour": ["brown"]}),
    ("early_blight",       {"leaf_pattern": ["spots"], "colour": ["brown"]}),
    ("early blight",       {"leaf_pattern": ["spots"], "colour": ["brown"]}),
    ("leaf_spot",          {"leaf_pattern": ["spots"]}),
    ("leaf spot",          {"leaf_pattern": ["spots"]}),
    ("leaf_mold",          {"leaf_pattern": ["spots"], "colour": ["yellow"]}),
    ("leaf mold",          {"leaf_pattern": ["spots"], "colour": ["yellow"]}),
    ("rust",               {"leaf_pattern": ["spots"], "colour": ["brown"]}),
    ("blight",             {"leaf_pattern": ["spots"], "colour": ["brown"]}),
    ("root_rot",           {"root_state": "brown_slimy"}),
    ("root rot",           {"root_state": "brown_slimy"}),
    ("pythium",            {"root_state": "brown_slimy"}),
    # pests
    ("spider_mite",        {"pests_visible": ["mites"], "leaf_pattern": ["stippled"]}),
    ("spider mite",        {"pests_visible": ["mites"], "leaf_pattern": ["stippled"]}),
    ("aphid",              {"pests_visible": ["aphids"]}),
    ("whitefly",           {"pests_visible": ["whiteflies"]}),
    ("whiteflies",         {"pests_visible": ["whiteflies"]}),
    ("thrip",              {"leaf_pattern": ["stippled"]}),
    # fish
    ("ich",                {"fish_body": ["white_spots"], "subject": ["fish"]}),
    ("white_spot",         {"fish_body": ["white_spots"], "subject": ["fish"]}),
    ("fin_rot",            {"fish_body": ["frayed_fins"], "subject": ["fish"]}),
    ("fin rot",            {"fish_body": ["frayed_fins"], "subject": ["fish"]}),
    ("saprolegnia",        {"fish_body": ["cotton_tufts"], "subject": ["fish"]}),
    ("columnaris",         {"fish_body": ["lesion"], "subject": ["fish"]}),
    # explicit healthy: carries no symptom, and must not invent one
    ("healthy",            {}),
)

_LEAF_SUBJECT_HINT = re.compile(r"leaf|leaves|plant|tomato|lettuce|potato|pepper|corn|grape|"
                                r"apple|cherry|peach|strawberr|squash|orange|blueberr|raspberr|"
                                r"soybean", re.IGNORECASE)
_FISH_SUBJECT_HINT = re.compile(r"fish|tilapia|catfish|carp|trout|koi", re.IGNORECASE)


def _normalise(label: str) -> str:
    return re.sub(r"[_\-/]+", " ", str(label or "")).strip().lower()


def features_from_predictions(predictions) -> dict:
    """Merge classifier labels into `ObservationFeatures` keyword arguments.

    Only predictions at or above MIN_CONFIDENCE are considered, and only the coarse visible
    evidence is extracted — never the diagnosis itself. Returns {} when nothing maps, which
    leaves the differential to the prose extractor alone.

    Pure and total: any iterable of Prediction-like objects in, a plain dict out."""
    merged: dict = {}

    def _add_seq(key: str, values) -> None:
        cur = list(merged.get(key, []))
        for v in values:
            if v not in cur:
                cur.append(v)
        merged[key] = cur

    for pred in predictions or ():
        label = _normalise(getattr(pred, "label", ""))
        try:
            confidence = float(getattr(pred, "confidence", 0.0))
        except (TypeError, ValueError):
            continue
        if not label or confidence < MIN_CONFIDENCE:
            continue

        # Subject comes from the label's own words, so a leaf class can't imply a fish.
        if _FISH_SUBJECT_HINT.search(label):
            _add_seq("subject", ["fish"])
        elif _LEAF_SUBJECT_HINT.search(label):
            _add_seq("subject", ["plant"])

        for needle, feats in _LABEL_FEATURES:
            if needle not in label:
                continue
            for key, value in feats.items():
                if isinstance(value, list):
                    _add_seq(key, value)
                else:
                    merged.setdefault(key, value)   # first scalar wins; don't fight the prose
            break                                    # first (most specific) entry only
    return merged


def describe_predictions(predictions) -> str:
    """One line naming what the classifier said, for the agent turn.

    Presented as an unverified suggestion and kept OUT of the cited differential, because a
    classifier label has no source, no discriminating check, and no statement of its limits."""
    kept = [p for p in (predictions or ())
            if float(getattr(p, "confidence", 0.0) or 0.0) >= MIN_CONFIDENCE]
    if not kept:
        return ""
    parts = ", ".join(f"{getattr(p, 'label', '?')} ({float(p.confidence):.0%})" for p in kept[:3])
    return ("[An image classifier suggested: " + parts + ". That is an UNVERIFIED model label "
            "with no source — it is NOT part of the cited differential below. Use it only as a "
            "hint about what to look at, and never repeat it as a diagnosis.]")


def resolve(provider: str | None = None, model: str | None = None) -> tuple[str, str]:
    provider = (provider or os.getenv("CLASSIFIER_PROVIDER") or "").strip().lower()
    if not provider:
        raise ValueError("No CLASSIFIER_PROVIDER set (no classifier backend ships yet).")
    if provider not in SUPPORTED:
        supported = ", ".join(SUPPORTED) or "none yet — see CONTRIBUTING.md"
        raise ValueError(f"Unknown CLASSIFIER_PROVIDER {provider!r}. Supported: {supported}.")
    model = model or os.getenv("CLASSIFIER_MODEL") or DEFAULT_MODELS[provider]
    return provider, model


def _build_classifier_backend(provider: str, model: str):
    """Return a callable(image_bytes) -> list[Prediction]. Lazy imports, so adding a backend
    never adds a hard dependency for anyone not using it."""
    raise ValueError(  # pragma: no cover - no backend bundled yet
        f"No classifier backend implemented for {provider!r}. See CONTRIBUTING.md "
        "('Adding an image classifier') — this is a wanted contribution.")


def make_classifier(backend):
    """Wrap a backend callable into classify(image_bytes) -> list[Prediction]."""
    def _classify(image_bytes: bytes):
        raw = backend(image_bytes) or []
        out = []
        for item in raw:
            if isinstance(item, Prediction):
                out.append(item)
                continue
            try:                                     # accept (label, confidence) tuples too
                label, confidence = item
                out.append(Prediction(str(label), float(confidence)))
            except Exception:
                log.debug("classifier returned an unusable prediction: %r", item)
        return out
    return _classify


def default_classifier():
    """A lazily-built classifier for the configured provider, or None when unavailable —
    which is the normal case today, since no backend is bundled. Never raises."""
    if os.getenv("AGRONAUT_CLASSIFIER", "").lower() in {"off", "0", "false"}:
        return None
    try:
        provider, model = resolve()
        backend = _build_classifier_backend(provider, model)
    except Exception:
        log.debug("classifier backend unavailable", exc_info=True)
        return None
    return make_classifier(backend)
