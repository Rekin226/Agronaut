"""Pluggable vision layer: turn a photo into a plain-language visual observation that feeds
the normal agent turn. The describer is provider-agnostic and lazily built; these tests
inject a fake so there's no model or network.
"""

import base64

from agent import vision


def test_resolve_defaults_and_env(monkeypatch):
    monkeypatch.delenv("VLM_PROVIDER", raising=False)
    monkeypatch.delenv("VLM_MODEL", raising=False)
    assert vision.resolve()[0] == "nvidia"          # sensible hosted default
    monkeypatch.setenv("VLM_PROVIDER", "nvidia")
    monkeypatch.setenv("VLM_MODEL", "some/vlm")
    assert vision.resolve() == ("nvidia", "some/vlm")


def test_unknown_provider_rejected(monkeypatch):
    monkeypatch.setenv("VLM_PROVIDER", "does-not-exist")
    import pytest
    with pytest.raises(ValueError):
        vision.resolve()


def test_describe_uses_backend_and_passes_data_uri():
    seen = {}

    def _fake_backend(data_uri, prompt):
        seen["data_uri"] = data_uri
        seen["prompt"] = prompt
        return "Older leaves show interveinal yellowing; fish look normal."

    describe = vision.make_describer(backend=_fake_backend)
    out = describe(b"\x89PNG\r\n\x1a\n fake image bytes", "what's wrong with my plant?")
    assert "interveinal yellowing" in out
    assert seen["data_uri"].startswith("data:image/")
    # the raw image bytes are base64-encoded into the data URI (never sent as-is)
    assert base64.b64encode(b"\x89PNG\r\n\x1a\n fake image bytes").decode() in seen["data_uri"]
    assert "what's wrong" in seen["prompt"]


def test_describer_none_when_unavailable(monkeypatch):
    # No provider library installed / build fails -> None, so callers degrade gracefully.
    monkeypatch.setattr(vision, "_build_vlm_backend", lambda *a, **k: (_ for _ in ()).throw(ImportError("no vlm")))
    assert vision.default_describer() is None
