"""Pluggable speech-to-text (ASR) backend — turns a voice note into text.

The transcript feeds the normal agent turn, so a voice note gets the full consultation
(memory, trust-gated tools, cited knowledge) and — via the system prompt's "reply in the
user's language" rule — an answer in the language the note was spoken in.

Provider-agnostic, mirroring agent/llm.py and agent/vision.py: select with ASR_PROVIDER /
ASR_MODEL. Backends are imported lazily, so importing this module needs nothing installed.

Default is a LOCAL faster-whisper model — voice is most valuable exactly where connectivity
is worst (last-mile field use), and a local model keeps it working offline. Set
ASR_PROVIDER=nvidia to use a hosted endpoint instead.
"""

from __future__ import annotations

import io
import logging
import os

log = logging.getLogger(__name__)

DEFAULT_MODELS = {
    "local": "base",                       # faster-whisper size: tiny/base/small/medium/large-v3
    "nvidia": "openai/whisper-large-v3",   # hosted, OpenAI-compatible ASR
}
SUPPORTED = tuple(DEFAULT_MODELS)


def resolve(provider: str | None = None, model: str | None = None) -> tuple[str, str]:
    provider = (provider or os.getenv("ASR_PROVIDER") or "local").strip().lower()
    if provider not in SUPPORTED:
        raise ValueError(f"Unknown ASR_PROVIDER {provider!r}. Supported: {', '.join(SUPPORTED)}.")
    model = model or os.getenv("ASR_MODEL") or DEFAULT_MODELS[provider]
    return provider, model


def _build_asr_backend(provider: str, model: str):
    """Return a callable(audio_bytes, mime) -> transcript str for the provider. Lazy imports."""
    if provider == "local":
        from faster_whisper import WhisperModel  # heavy, optional
        wm = WhisperModel(model, device="auto", compute_type="int8")

        def _call(audio_bytes: bytes, mime: str) -> str:
            segments, _info = wm.transcribe(io.BytesIO(audio_bytes))
            return " ".join(seg.text for seg in segments)

        return _call
    if provider == "nvidia":
        # Hosted OpenAI-compatible ASR. Kept minimal; needs NVIDIA_API_KEY.
        from openai import OpenAI
        client = OpenAI(base_url=os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"),
                        api_key=os.environ["NVIDIA_API_KEY"])

        def _call(audio_bytes: bytes, mime: str) -> str:
            buf = io.BytesIO(audio_bytes)
            buf.name = "note.ogg"
            out = client.audio.transcriptions.create(model=model, file=buf)
            return getattr(out, "text", "") or ""

        return _call
    raise ValueError(f"Unhandled ASR provider {provider!r}")  # pragma: no cover


def make_transcriber(backend):
    """Wrap a backend callable into transcribe(audio_bytes, mime) -> trimmed transcript."""
    def _transcribe(audio_bytes: bytes, mime: str | None = None) -> str:
        return (backend(audio_bytes, mime or "audio/ogg") or "").strip()
    return _transcribe


def default_transcriber():
    """A lazily-built transcriber for the configured provider, or None when unavailable
    (no library installed, build fails, or voice disabled). Never raises."""
    if os.getenv("AGRONAUT_VOICE", "").lower() in {"off", "0", "false"}:
        return None
    try:
        provider, model = resolve()
        backend = _build_asr_backend(provider, model)
    except Exception:
        log.debug("ASR backend unavailable", exc_info=True)
        return None
    return make_transcriber(backend)
