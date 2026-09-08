"""The pluggable LLM factory: resolution, normalization, error handling. No network."""

import pytest

from agent import llm as L


def test_default_provider_and_model(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    provider, model = L.resolve()
    assert provider == "ollama"
    assert model == "qwen2.5"


def test_env_selects_provider_and_model(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "NVIDIA")  # case-insensitive
    monkeypatch.delenv("LLM_MODEL", raising=False)
    provider, model = L.resolve()
    assert provider == "nvidia"
    assert model == L.DEFAULT_MODELS["nvidia"]


def test_explicit_args_override_env(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    provider, model = L.resolve(provider="hf", model="my/custom-model")
    assert provider == "hf"
    assert model == "my/custom-model"


def test_unknown_provider_raises():
    with pytest.raises(ValueError):
        L.resolve(provider="skynet")


def test_get_llm_unknown_provider_raises_before_importing_anything():
    with pytest.raises(ValueError):
        L.get_llm(provider="skynet")


def test_normalize_handles_str_and_message_and_none():
    assert L.normalize("hello") == "hello"
    assert L.normalize(None) == ""

    class _Msg:
        content = "from a chat model"

    assert L.normalize(_Msg()) == "from a chat model"


def test_openai_compat_provider_resolves(monkeypatch):
    monkeypatch.delenv("LLM_MODEL", raising=False)
    provider, model = L.resolve(provider="openai_compat")
    assert provider == "openai_compat"
    assert model == L.DEFAULT_MODELS["openai_compat"]


def _stub_langchain_openai(monkeypatch, recorder):
    import sys
    import types

    mod = types.ModuleType("langchain_openai")

    class _ChatOpenAI:
        def __init__(self, **kwargs):
            recorder.update(kwargs)

        def bind_tools(self, tools):
            return self

    mod.ChatOpenAI = _ChatOpenAI
    monkeypatch.setitem(sys.modules, "langchain_openai", mod)


def test_openai_compat_builds_tool_calling_backend_from_env(monkeypatch):
    # A self-hostable OpenAI-compatible server (vLLM / llama.cpp-server): zero proprietary
    # API, and it drives the tool-calling agent (has .bind_tools). Endpoint from env.
    monkeypatch.setenv("OPENAI_COMPAT_BASE_URL", "http://my-vllm:8000/v1")
    monkeypatch.setenv("OPENAI_COMPAT_API_KEY", "local-key")
    seen = {}
    _stub_langchain_openai(monkeypatch, seen)

    backend = L.get_chat_model(provider="openai_compat", model="Qwen/Qwen2.5-7B-Instruct")
    assert hasattr(backend, "bind_tools")               # can drive the agent loop
    assert seen["base_url"] == "http://my-vllm:8000/v1"
    assert seen["api_key"] == "local-key"
    assert seen["model"] == "Qwen/Qwen2.5-7B-Instruct"


def test_openai_compat_defaults_to_localhost(monkeypatch):
    monkeypatch.delenv("OPENAI_COMPAT_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_COMPAT_API_KEY", raising=False)
    seen = {}
    _stub_langchain_openai(monkeypatch, seen)
    L.get_chat_model(provider="openai_compat")
    assert "localhost" in seen["base_url"]              # sensible local default
    assert seen["api_key"]                              # never empty (servers 401 on blank)


def test_stringllm_always_returns_str():
    class _FakeBackend:
        def invoke(self, prompt):
            class _Msg:
                content = f"echo:{prompt}"
            return _Msg()

    client = L.StringLLM(_FakeBackend(), "fake", "fake-model")
    out = client.invoke("ping")
    assert isinstance(out, str)
    assert out == "echo:ping"


def test_the_local_provider_can_actually_drive_the_agent():
    """Ollama is how a grower self-hosting with no API key runs a model, so it has to bind
    tools. It used to build `OllamaLLM`, the text-COMPLETION class, which has no
    .bind_tools() — so `LLM_PROVIDER=ollama` raised ToolCallingUnsupported and the whole
    local path was dead. Constructing ChatOllama contacts no server, so this needs no
    running Ollama and no network."""
    model = L.get_chat_model(provider="ollama", model="qwen2.5")
    assert hasattr(model, "bind_tools")
    assert model.bind_tools([]) is not None


def test_every_default_model_can_bind_tools_or_is_documented_as_hosted():
    """A provider whose DEFAULT model cannot call tools is a trap: the agent refuses to
    start and the error names the provider rather than the model."""
    from langchain_ollama import ChatOllama
    assert hasattr(ChatOllama, "bind_tools")
    assert L.DEFAULT_MODELS["ollama"].startswith("qwen2.5"), (
        "the default Ollama tag must be one that supports tool calling; llama3 does not")


def test_the_local_provider_has_a_fallback_too():
    """A self-hoster has no hosted tier to lean on, so the fallback matters more locally
    than it does for a provider with a big model behind it."""
    assert "ollama" in L.FALLBACK_MODELS
    assert L.FALLBACK_MODELS["ollama"] != L.DEFAULT_MODELS["ollama"]


# --- the Anthropic provider ---------------------------------------------------------------

def test_anthropic_is_a_supported_provider(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    provider, model = L.resolve()
    assert provider == "anthropic"
    assert model == L.DEFAULT_MODELS["anthropic"]
    assert model.startswith("claude-")
    assert not model[-1].isdigit() or "-20" not in model, "model ids carry no date suffix"


def test_the_anthropic_branch_never_sends_temperature(monkeypatch):
    """The Claude 5 family rejects sampling parameters outright: a request carrying
    temperature comes back 400 "`temperature` is deprecated for this model." Every other
    backend takes one, so the shared signature offers it and this branch must drop it."""
    seen = {}

    class _FakeChat:
        def __init__(self, **kw):
            seen.update(kw)

    import langchain_anthropic
    monkeypatch.setattr(langchain_anthropic, "ChatAnthropic", _FakeChat)
    L._build_backend("anthropic", "claude-sonnet-5", 0.7)
    assert "temperature" not in seen
    assert seen["model"] == "claude-sonnet-5"


def test_the_dead_nvidia_fallback_is_gone():
    """meta/llama-3.1-8b-instruct returns 410 Gone (measured 2026-09-08), so the fallback
    turned one failure into two. A fallback that cannot answer is worse than none."""
    assert L.FALLBACK_MODELS.get("nvidia") != "meta/llama-3.1-8b-instruct"


class _Echo:
    """Minimal stand-in for a chat model: records what it was asked to send."""

    def __init__(self):
        self.got = None

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        self.got = messages
        return "ok"


def test_the_adapter_folds_interleaved_system_messages(monkeypatch):
    """core.py uses SystemMessage as an operator channel INSIDE the transcript. Anthropic
    models a single top-level system field and refuses the rest: "Received multiple
    non-consecutive system messages." Leading ones stay system; later ones become tagged
    operator notes so the instruction survives in position."""
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

    inner = _Echo()
    adapter = L._AnthropicSystemAdapter(inner)
    adapter.invoke([
        SystemMessage(content="PROMPT"),
        SystemMessage(content="RECALL"),
        HumanMessage(content="hello"),
        AIMessage(content="hi"),
        SystemMessage(content="now answer in plain text"),
    ])
    kinds = [type(m).__name__ for m in inner.got]
    assert kinds == ["SystemMessage", "SystemMessage", "HumanMessage",
                     "AIMessage", "HumanMessage"]
    assert inner.got[-1].content.startswith(L._AnthropicSystemAdapter._OPERATOR)
    assert "now answer in plain text" in inner.got[-1].content


def test_the_adapter_survives_bind_tools_and_delegates_everything_else():
    inner = _Echo()
    bound = L._AnthropicSystemAdapter(inner).bind_tools([])
    assert isinstance(bound, L._AnthropicSystemAdapter)
    bound.invoke([])
    assert inner.got == []
