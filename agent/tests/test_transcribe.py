"""Pluggable speech-to-text (ASR) layer: a voice note becomes text that feeds the normal
agent turn. The backend is provider-agnostic and lazily built; tests inject a fake.
"""

from agent import transcribe


def test_resolve_defaults_and_env(monkeypatch):
    monkeypatch.delenv("ASR_PROVIDER", raising=False)
    monkeypatch.delenv("ASR_MODEL", raising=False)
    prov, _model = transcribe.resolve()
    assert prov in transcribe.SUPPORTED
    monkeypatch.setenv("ASR_PROVIDER", prov)
    monkeypatch.setenv("ASR_MODEL", "some/asr")
    assert transcribe.resolve() == (prov, "some/asr")


def test_unknown_provider_rejected(monkeypatch):
    monkeypatch.setenv("ASR_PROVIDER", "does-not-exist")
    import pytest
    with pytest.raises(ValueError):
        transcribe.resolve()


def test_transcribe_uses_backend():
    seen = {}

    def _fake_backend(audio_bytes, mime):
        seen["audio"] = audio_bytes
        seen["mime"] = mime
        return "  mes tilapias sont malades  "

    tx = transcribe.make_transcriber(backend=_fake_backend)
    out = tx(b"oggbytes", "audio/ogg")
    assert out == "mes tilapias sont malades"   # trimmed
    assert seen["audio"] == b"oggbytes"
    assert seen["mime"] == "audio/ogg"


def test_transcriber_none_when_unavailable(monkeypatch):
    monkeypatch.setattr(transcribe, "_build_asr_backend",
                        lambda *a, **k: (_ for _ in ()).throw(ImportError("no asr")))
    assert transcribe.default_transcriber() is None


def test_disabled_by_env(monkeypatch):
    monkeypatch.setenv("AGRONAUT_VOICE", "off")
    assert transcribe.default_transcriber() is None
