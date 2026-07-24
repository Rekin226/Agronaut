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
