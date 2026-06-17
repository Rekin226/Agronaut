"""Pluggable LLM backend for the agent/chat layer.

The deterministic design core (`aqua_model`) uses NO LLM — this only powers fact-collection,
routing, and explanation in the chat/troubleshooting flow. So the backend is freely swappable
via config; correctness of any design is unaffected.

Select with the LLM_PROVIDER env var (or pass `provider=`):
    ollama    -> local open model via Ollama (offline; default)
    nvidia    -> NVIDIA hosted open models (OpenAI-compatible; needs NVIDIA_API_KEY)
    hf        -> Hugging Face Inference (hosted; needs HUGGINGFACEHUB_API_TOKEN)
    hf_local  -> Hugging Face open model run LOCALLY via transformers (no token, offline
                 after the first download). The simplest way to test the assistant with no
                 hosted backend or Ollama install — just `pip install -r requirement.txt`.

Override the model with LLM_MODEL (or pass `model=`).

Provider libraries are imported LAZILY, only when that provider is selected, so importing
this module (or the chat layer) never requires any of them to be installed.
"""

from __future__ import annotations

import os

# Sensible default open model per provider.
# HF default: Qwen2.5-7B-Instruct — Apache-2.0 (clean license for a commercial/B2G venture),
# strong at structured/JSON output (the decision step), multilingual incl. French.
# Alternatives (set via LLM_MODEL):
#   - meta-llama/Llama-3.1-8B-Instruct   (slightly stronger French prose; Llama community license)
#   - Qwen/Qwen2.5-1.5B-Instruct         (Apache-2.0, tiny — for low-resource/edge field use)
#   - microsoft/Phi-4-mini-instruct      (small, multilingual)
DEFAULT_MODELS = {
    "ollama": "llama3",
    "nvidia": "meta/llama-3.1-8b-instruct",
    "hf": "Qwen/Qwen2.5-7B-Instruct",
    # Local default kept small (~3 GB) so it downloads + runs on a laptop CPU/MPS.
    # Bump via LLM_MODEL (e.g. Qwen/Qwen2.5-7B-Instruct) for stronger output.
    "hf_local": "Qwen/Qwen2.5-1.5B-Instruct",
}

SUPPORTED = tuple(DEFAULT_MODELS)


def resolve(provider: str | None = None, model: str | None = None) -> tuple[str, str]:
    """Pure resolution of (provider, model) from args -> env -> defaults. Testable, no I/O."""
    provider = (provider or os.getenv("LLM_PROVIDER") or "ollama").strip().lower()
    if provider not in SUPPORTED:
        raise ValueError(
            f"Unknown LLM_PROVIDER {provider!r}. Supported: {', '.join(SUPPORTED)}."
        )
    model = model or os.getenv("LLM_MODEL") or DEFAULT_MODELS[provider]
    return provider, model


def normalize(output) -> str:
    """Coerce any LangChain result (str from text LLMs, AIMessage from chat models) to str."""
    if output is None:
        return ""
    content = getattr(output, "content", None)
    return content if isinstance(content, str) else str(output)


class StringLLM:
    """Thin adapter so callers always get a string back from .invoke(), whatever the backend."""

    def __init__(self, backend, provider: str, model: str):
        self._backend = backend
        self.provider = provider
        self.model = model

    def invoke(self, prompt) -> str:
        return normalize(self._backend.invoke(prompt))


def _build_backend(provider: str, model: str, temperature: float):
    if provider == "ollama":
        from langchain_ollama.llms import OllamaLLM
        return OllamaLLM(model=model, temperature=temperature)
    if provider == "nvidia":
        # OpenAI-compatible NVIDIA API Catalog / NIM. Reads NVIDIA_API_KEY from env.
        from langchain_nvidia_ai_endpoints import ChatNVIDIA
        # NVIDIA endpoints reject temperature=0; nudge to a tiny positive value.
        temp = temperature if temperature > 0 else 1e-3
        return ChatNVIDIA(model=model, temperature=temp)
    if provider == "hf":
        from langchain_huggingface import ChatHuggingFace, HuggingFaceEndpoint
        endpoint = HuggingFaceEndpoint(
            repo_id=model,
            temperature=max(temperature, 1e-3),
            task="text-generation",
        )
        return ChatHuggingFace(llm=endpoint)
    if provider == "hf_local":
        # Local transformers pipeline. No API token; downloads the model on first use
        # (cached in ~/.cache/huggingface) then runs offline. ChatHuggingFace applies the
        # model's chat template so instruct models behave like chat models.
        from langchain_huggingface import ChatHuggingFace, HuggingFacePipeline
        pipeline_kwargs = {"max_new_tokens": 512, "return_full_text": False}
        if temperature > 0:
            pipeline_kwargs.update(do_sample=True, temperature=temperature)
        else:
            pipeline_kwargs["do_sample"] = False  # greedy; don't pass temperature (transformers warns)
        pipe = HuggingFacePipeline.from_model_id(
            model_id=model,
            task="text-generation",
            pipeline_kwargs=pipeline_kwargs,
        )
        return ChatHuggingFace(llm=pipe)
    raise ValueError(f"Unhandled provider {provider!r}")  # pragma: no cover


def get_llm(provider: str | None = None, model: str | None = None, temperature: float = 0.0) -> StringLLM:
    """Return a normalized LLM client for the configured provider.

    Raises ValueError for an unknown provider, or ImportError if the selected provider's
    library is not installed (install langchain-nvidia-ai-endpoints or langchain-huggingface).
    """
    provider, model = resolve(provider, model)
    backend = _build_backend(provider, model, temperature)
    return StringLLM(backend, provider, model)


class ToolCallingUnsupported(RuntimeError):
    """Raised when the resolved backend cannot bind tools (no .bind_tools())."""


def get_chat_model(provider: str | None = None, model: str | None = None, temperature: float = 0.0):
    """Return the RAW LangChain chat model so callers can `.bind_tools(...)` and read
    `AIMessage.tool_calls`. Unlike get_llm()->StringLLM, this does NOT normalize to str —
    the tool-calling agent loop needs the message object.

    Use a tool-calling-capable provider: `nvidia` (ChatNVIDIA) is the supported default;
    `hf`/`hf_local` chat models technically expose bind_tools but tool-calling reliability
    varies. `ollama`'s text LLM backend does not support tools.
    """
    provider, model = resolve(provider, model)
    backend = _build_backend(provider, model, temperature)
    if not hasattr(backend, "bind_tools"):
        raise ToolCallingUnsupported(
            f"Provider {provider!r} (model {model!r}) has no .bind_tools(); it can't drive the "
            f"tool-calling agent. Use LLM_PROVIDER=nvidia (needs NVIDIA_API_KEY)."
        )
    return backend
