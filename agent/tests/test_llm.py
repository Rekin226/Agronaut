"""The pluggable LLM factory: resolution, normalization, error handling. No network."""

import pytest

from agent import llm as L


def test_default_provider_and_model(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    provider, model = L.resolve()
    assert provider == "ollama"
    assert model == "llama3"


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
