"""Pluggable vision (VLM) backend — turns a photo into a plain-language visual observation.

Design rule (see docs/PLAN.md 1.1): the VLM only OBSERVES. Its text description feeds the
normal agent turn as user-provided context; it never calls tools or emits sizing numbers.
Diagnosis stays with the agent + cited knowledge base, so the honesty/citation guarantees
hold for anything derived from an image.

Provider-agnostic, mirroring agent/llm.py: select with VLM_PROVIDER / VLM_MODEL. The
backend library is imported lazily, so importing this module needs nothing installed.
"""

from __future__ import annotations

import base64
import logging
import os
import re

log = logging.getLogger(__name__)

DEFAULT_MODELS = {
    # NVIDIA hosts OpenAI-compatible vision models on the same free tier as the chat brain.
    "nvidia": "meta/llama-3.2-11b-vision-instruct",
}
SUPPORTED = tuple(DEFAULT_MODELS)

_OBSERVE_PROMPT = (
    "You are helping an aquaponics assistant. Describe ONLY what you can see in this photo "
    "that is relevant to fish or plant health — leaf colour and patterning, visible pests or "
    "algae, water clarity, fish appearance or behaviour, equipment. Be specific and concise. "
    "Do NOT diagnose, prescribe, or state any numbers; another system does that. If the image "
    "is unclear or unrelated to aquaponics, say so."
)

# --- Observation guard --------------------------------------------------------------
# _OBSERVE_PROMPT ASKS the model not to diagnose, prescribe, or state numbers. An
# instruction is not a guarantee — the same gap PLAN 1.3 closed for citations. These
# functions enforce the mechanically enforceable part, so a hallucinated dose or reading
# can never reach the agent turn dressed as something the user said.
#
# Pure and total: no I/O, no model, never raises. That purity is load-bearing — it is what
# lets scripts/safety_eval.py score this guard in CI without breaking its no-network charter.

_NUMBER_PLACEHOLDER = "[number removed]"

# A numeral carrying a unit is a MEASUREMENT — the kind of value that could travel toward a
# tool argument. A bare count ("3 leaves are yellow") is an observation and is left alone.
# Alternation runs longest-first; the trailing lookahead (not \b) is required so units ending
# in a non-word character — %, °C, m² — still match at end of string.
_MEASUREMENT_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*"
    r"(?:mg/?L|µg/?L|g/?L|ppm|ppt|kilograms?|kilos?|kg|grams?|pounds?|lbs?|"
    r"millilit(?:re|er)s?|litres?|liters?|gallons?|gal|ml|"
    r"centimet(?:re|er)s?|cm|millimet(?:re|er)s?|mm|met(?:re|er)s?|m²|m2|"
    r"degrees?|°\s*[CF]|%|g|L|m)"
    r"(?![a-z0-9])",
    re.IGNORECASE,
)

# "pH 6.2", "DO of 4", "nitrate: 40" — a reading whose label carries the unit. The label is
# kept (that the model mentioned pH is itself an observation); only the figure is redacted.
_LABELLED_READING_RE = re.compile(
    r"\b(pH|DO|EC|TDS|ammonia|nitrite|nitrate)\s*(?:of|is|at|=|:)?\s*\d+(?:\.\d+)?",
    re.IGNORECASE,
)

# Prescriptions are the highest-harm output a VLM can produce here, and it was told not to.
# Removed at SENTENCE granularity — a clause cut mid-sentence leaves mangled text, and the
# sentence is the unit of advice.
_PRESCRIPTIVE_RE = re.compile(
    r"\b(?:you should|you need to|you'?ll need to|you will need to|treat with|treatment|"
    r"dose|dosing|apply|administer|medicate|i recommend|recommend(?:ed)? (?:that|you)|"
    r"increase the|reduce the|lower the|raise the)\b",
    re.IGNORECASE,
)

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _strip_prescriptive(text: str) -> tuple[str, bool]:
    sentences = _SENTENCE_SPLIT_RE.split(text)
    kept = [s for s in sentences if not _PRESCRIPTIVE_RE.search(s)]
    return " ".join(kept).strip(), len(kept) != len(sentences)


def residual_leaks(text: str) -> list[str]:
    """Which guarded categories are still present in a string. Used by the Tier-2 eval to
    assert the end-to-end guarantee against real model output, rather than re-listing the
    lexicon in a second place where it would drift."""
    leaks = []
    if _PRESCRIPTIVE_RE.search(text or ""):
        leaks.append("prescriptive")
    if _MEASUREMENT_RE.search(text or "") or _LABELLED_READING_RE.search(text or ""):
        leaks.append("measurement")
    return leaks


def sanitize_observation(text: str) -> tuple[str, list[str]]:
    """Enforce the observe-only contract on a VLM observation.

    Returns (cleaned_text, flags). Flags are category-prefixed strings:
    'stripped:measurement', 'stripped:prescriptive' (and, from Task 2, 'verdict:<term>'
    and 'unclear')."""
    if not text:
        return "", []
    flags: list[str] = []

    cleaned, dropped = _strip_prescriptive(text)
    if dropped:
        flags.append("stripped:prescriptive")

    # Measurements before labelled readings: "DO 4 mg/L" becomes "DO [number removed]"
    # rather than leaving a dangling unit behind.
    cleaned, n_units = _MEASUREMENT_RE.subn(_NUMBER_PLACEHOLDER, cleaned)
    cleaned, n_labelled = _LABELLED_READING_RE.subn(r"\1 " + _NUMBER_PLACEHOLDER, cleaned)
    if n_units or n_labelled:
        flags.append("stripped:measurement")

    return cleaned.strip(), flags


def resolve(provider: str | None = None, model: str | None = None) -> tuple[str, str]:
    provider = (provider or os.getenv("VLM_PROVIDER") or "nvidia").strip().lower()
    if provider not in SUPPORTED:
        raise ValueError(f"Unknown VLM_PROVIDER {provider!r}. Supported: {', '.join(SUPPORTED)}.")
    model = model or os.getenv("VLM_MODEL") or DEFAULT_MODELS[provider]
    return provider, model


def _data_uri(image_bytes: bytes, mime: str = "image/jpeg") -> str:
    return f"data:{mime};base64,{base64.b64encode(image_bytes).decode()}"


def _build_vlm_backend(provider: str, model: str):
    """Return a callable(data_uri, prompt) -> str for the resolved provider. Lazy imports."""
    if provider == "nvidia":
        from langchain_nvidia_ai_endpoints import ChatNVIDIA
        from langchain_core.messages import HumanMessage
        client = ChatNVIDIA(model=model, temperature=1e-3)

        def _call(data_uri: str, prompt: str) -> str:
            msg = HumanMessage(content=[
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": data_uri}},
            ])
            out = client.invoke([msg])
            return (getattr(out, "content", "") or "").strip()

        return _call
    raise ValueError(f"Unhandled VLM provider {provider!r}")  # pragma: no cover


def make_describer(backend):
    """Wrap a backend callable into describe(image_bytes, user_prompt) -> observation str."""
    def _describe(image_bytes: bytes, user_prompt: str | None = None) -> str:
        prompt = _OBSERVE_PROMPT
        if user_prompt:
            prompt += f"\n\nThe user asked: {user_prompt}"
        return backend(_data_uri(image_bytes), prompt)
    return _describe


def default_describer():
    """A lazily-built describer for the configured provider, or None when unavailable
    (no library installed, build fails, or vision disabled). Never raises."""
    if os.getenv("AGRONAUT_VISION", "").lower() in {"off", "0", "false"}:
        return None
    try:
        provider, model = resolve()
        backend = _build_vlm_backend(provider, model)
    except Exception:
        log.debug("vision backend unavailable", exc_info=True)
        return None
    return make_describer(backend)
