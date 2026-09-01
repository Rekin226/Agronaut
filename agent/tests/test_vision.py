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


def test_strips_measurement_numerals_but_keeps_bare_counts():
    text = "3 leaves are yellow, ammonia reads 4 mg/L and the tank is 26 °C."
    cleaned, flags = vision.sanitize_observation(text)
    assert "4 mg/L" not in cleaned
    assert "26 °C" not in cleaned
    assert "[number removed]" in cleaned
    assert "stripped:measurement" in flags
    # a bare count is an observation, not a measurement — it survives
    assert "3 leaves" in cleaned


def test_strips_labelled_readings_but_keeps_the_label():
    cleaned, flags = vision.sanitize_observation("The strip shows pH 6.2 on the sample.")
    assert "6.2" not in cleaned
    assert "pH" in cleaned
    assert "stripped:measurement" in flags


def test_drops_prescriptive_sentences_and_keeps_their_neighbours():
    text = "Older leaves are pale. You should add chelated iron to the sump. New growth is green."
    cleaned, flags = vision.sanitize_observation(text)
    assert "Older leaves are pale" in cleaned
    assert "New growth is green" in cleaned
    assert "chelated iron" not in cleaned
    assert "stripped:prescriptive" in flags


def test_clean_observation_passes_through_untouched():
    text = "Lettuce leaves are uniformly green; the water is clear; fish swim evenly."
    cleaned, flags = vision.sanitize_observation(text)
    assert cleaned == text
    assert flags == []


def test_sanitize_is_total_on_empty_input():
    assert vision.sanitize_observation("") == ("", [])


def test_residual_leaks_reports_what_survived():
    assert vision.residual_leaks("Lettuce leaves are green.") == []
    leaks = vision.residual_leaks("You should dose 5 mg/L of iron.")
    assert "prescriptive" in leaks and "measurement" in leaks


def test_named_conditions_are_flagged_but_left_in_the_text():
    text = "The fish has white spots on its gills; this is ich."
    cleaned, flags = vision.sanitize_observation(text)
    # kept verbatim: redacting the word would not hide the implication of "white spots"
    assert "ich" in cleaned
    assert "white spots" in cleaned
    assert "verdict:ich" in flags


def test_plant_verdict_flagged():
    cleaned, flags = vision.sanitize_observation(
        "Interveinal yellowing on older leaves suggests iron deficiency.")
    assert "verdict:iron deficiency" in flags
    assert "Interveinal yellowing" in cleaned


def test_verdict_inside_a_dropped_prescriptive_sentence_still_flags():
    # The sentence is removed, but the signal that the model rendered a verdict must survive
    # — otherwise the turn looks clean and the agent loses the warning.
    cleaned, flags = vision.sanitize_observation(
        "The gills look inflamed. Treat with salt for ich.")
    assert "Treat with salt" not in cleaned
    assert "verdict:ich" in flags
    assert "stripped:prescriptive" in flags


def test_unclear_short_reply_is_flagged():
    _, flags = vision.sanitize_observation("The image is too blurry to make out.")
    assert "unclear" in flags


def test_hedge_inside_a_rich_observation_is_not_unclear():
    text = ("The lettuce in the front raft shows uniform pale green colour across the older "
            "outer leaves, while the newest inner leaves stay darker. Several leaf tips are "
            "browning and curled. The water surface is slightly cloudy and it is hard to see "
            "the roots below the raft.")
    assert len(text) > 200
    _, flags = vision.sanitize_observation(text)
    assert "unclear" not in flags


def _jpeg_with_gps() -> bytes:
    """A real JPEG carrying an EXIF GPS tag, built in-memory."""
    import io

    from PIL import Image
    im = Image.new("RGB", (32, 32), (10, 120, 40))
    exif = Image.Exif()
    exif[0x8825] = {1: "N", 2: (12.0, 22.0, 0.0)}   # GPSInfo
    buf = io.BytesIO()
    im.save(buf, format="JPEG", exif=exif)
    return buf.getvalue()


def test_strip_exif_removes_embedded_metadata():
    import io

    from PIL import Image
    raw = _jpeg_with_gps()
    assert Image.open(io.BytesIO(raw)).getexif()          # precondition: EXIF is there
    cleaned = vision.strip_exif(raw)
    assert not Image.open(io.BytesIO(cleaned)).getexif()  # and it is gone
    assert Image.open(io.BytesIO(cleaned)).size == (32, 32)


def test_strip_exif_passes_through_undecodable_bytes():
    # Best-effort by design: a failed strip must never cost the user their answer.
    junk = b"\x89PNG\r\n\x1a\n not really an image"
    assert vision.strip_exif(junk) == junk


def test_describer_strips_exif_before_building_the_data_uri():
    import base64
    seen = {}

    def _fake_backend(data_uri, prompt):
        seen["data_uri"] = data_uri
        return "ok"

    raw = _jpeg_with_gps()
    vision.make_describer(backend=_fake_backend)(raw, "what is this?")
    assert base64.b64encode(raw).decode() not in seen["data_uri"]


# --- Finding 1: widened lexicons -----------------------------------------------------

def test_strips_hedged_ph_reading():
    cleaned, flags = vision.sanitize_observation("The test strip suggests pH is about 6.4.")
    assert "6.4" not in cleaned
    assert "[number removed]" in cleaned
    assert "stripped:measurement" in flags


def test_strips_bare_temperature_reading():
    cleaned, flags = vision.sanitize_observation("Water temperature is 26 and the fish are active.")
    assert "26" not in cleaned
    assert "[number removed]" in cleaned
    assert "stripped:measurement" in flags


def test_strips_uppercase_do_reading_but_not_lowercase_do():
    cleaned, flags = vision.sanitize_observation("DO is around 4 in the morning.")
    assert "around 4" not in cleaned
    assert "[number removed]" in cleaned
    assert "stripped:measurement" in flags

    # the ordinary English word "do" must never be mistaken for dissolved oxygen
    cleaned2, flags2 = vision.sanitize_observation("The fish do swim near 3 outlets.")
    assert "3 outlets" in cleaned2
    assert "[number removed]" not in cleaned2
    assert "stripped:measurement" not in flags2


def test_strips_bare_imperative_prescriptions():
    cleaned, flags = vision.sanitize_observation(
        "Older leaves are pale. Add chelated iron to the sump.")
    assert "Older leaves are pale" in cleaned
    assert "chelated iron" not in cleaned
    assert "stripped:prescriptive" in flags


def test_flags_plural_verdict_terms():
    cleaned, flags = vision.sanitize_observation(
        "The undersides of the leaves are covered in aphids.")
    assert "verdict:aphid" in flags
    assert "undersides" in cleaned


# --- Critical regression fix: bare imperatives must not erase ordinary observations --------

def test_lower_leaves_observation_survives_the_imperative_widening():
    # "lower" is a prescriptive verb ONLY when it opens a sentence ("Lower the pH"); here it's
    # an adjective describing which leaves, and the sentence must not be dropped.
    text = "The lower leaves are yellow while the new growth is green."
    cleaned, flags = vision.sanitize_observation(text)
    assert cleaned == text
    assert "stripped:prescriptive" not in flags


def test_fish_stop_at_surface_observation_survives_the_imperative_widening():
    # "stop" mid-sentence describing fish behaviour (the safety-critical gasping-fish case)
    # must not be mistaken for the imperative "Stop feeding".
    text = "The fish stop at the surface when the light comes on."
    cleaned, flags = vision.sanitize_observation(text)
    assert cleaned == text
    assert "stripped:prescriptive" not in flags


def test_incidental_bare_verbs_mid_sentence_survive():
    text = "The nets add shade over the raft and the roots use the full depth."
    cleaned, flags = vision.sanitize_observation(text)
    assert cleaned == text
    assert "stripped:prescriptive" not in flags


def test_labelled_reading_gap_preserves_intervening_words():
    cleaned, flags = vision.sanitize_observation("The pH meter has 2 buttons.")
    assert "meter has" in cleaned
    assert "2 buttons" not in cleaned
    assert "[number removed]" in cleaned


def test_labelled_reading_gap_does_not_cross_a_clause_boundary():
    cleaned, flags = vision.sanitize_observation("Temperature feels warm; 5 plants wilted.")
    assert "5 plants" in cleaned
    assert "[number removed]" not in cleaned


def test_multi_sentence_observation_survives_intact():
    text = ("The lower leaves of the lettuce are pale yellow between the veins. "
            "A thin film of algae has started on the raft edge. "
            "The water is clear and the fish use the whole tank.")
    cleaned, flags = vision.sanitize_observation(text)
    assert cleaned == text
    assert "stripped:prescriptive" not in flags


# --- Finding 2: importable-with-nothing-installed promise ----------------------------

def _real_jpeg_bytes() -> bytes:
    import io

    from PIL import Image
    im = Image.new("RGB", (10, 10), (200, 50, 50))
    buf = io.BytesIO()
    im.save(buf, format="JPEG")
    return buf.getvalue()


def test_strip_exif_falls_back_to_passthrough_when_pillow_is_unavailable(monkeypatch):
    import sys

    raw = _real_jpeg_bytes()  # decodable — so passthrough can ONLY be due to missing Pillow
    # Setting a module to None in sys.modules makes `import <name>` raise ImportError, the
    # same trick test_llm.py uses (via setitem) to control what a lazy import inside the
    # module under test resolves to, without needing Pillow to actually be uninstalled.
    monkeypatch.setitem(sys.modules, "PIL", None)

    assert vision.strip_exif(raw) == raw


# --- Finding 4: EXIF Orientation is applied, not merely discarded --------------------

def _jpeg_rotated(width=40, height=20, orientation=6) -> bytes:
    """A landscape JPEG carrying EXIF Orientation=6 (rotate 90° CW to display correctly),
    i.e. the pixel data is landscape but should be DISPLAYED as portrait."""
    import io

    from PIL import Image
    im = Image.new("RGB", (width, height), (10, 120, 40))
    exif = Image.Exif()
    exif[0x0112] = orientation  # Orientation tag
    buf = io.BytesIO()
    im.save(buf, format="JPEG", exif=exif)
    return buf.getvalue()


def test_strip_exif_applies_orientation_before_discarding_it():
    import io

    from PIL import Image
    raw = _jpeg_rotated(width=40, height=20, orientation=6)
    assert Image.open(io.BytesIO(raw)).size == (40, 20)  # precondition: raw pixels are landscape

    cleaned = vision.strip_exif(raw)
    out = Image.open(io.BytesIO(cleaned))
    assert out.size == (20, 40)  # orientation applied: now portrait
    assert not out.getexif()     # and the tag itself is gone


# --- the local (offline) provider ---------------------------------------------------------

def test_ollama_is_a_supported_provider(monkeypatch):
    """Vision was the last subsystem that still needed an API key (#121)."""
    monkeypatch.setenv("VLM_PROVIDER", "ollama")
    monkeypatch.delenv("VLM_MODEL", raising=False)
    provider, model = vision.resolve()
    assert provider == "ollama"
    assert model == vision.DEFAULT_MODELS["ollama"]


def test_the_hosted_provider_stays_the_default(monkeypatch):
    """Adding the local path must not silently change what an existing install uses:
    switching a working NVIDIA deployment to a model nobody has pulled would decline every
    photo with no error the operator can see."""
    monkeypatch.delenv("VLM_PROVIDER", raising=False)
    assert vision.resolve()[0] == "nvidia"


def _fake_ollama(monkeypatch, capabilities):
    """Make the capability probe answer without a daemon.

    Patches `show` on the REAL ollama module rather than substituting a fake one in
    sys.modules: langchain_ollama imports `AsyncClient, Client, Message` from it, so a stub
    module breaks the very backend these tests are about.
    """
    import ollama
    monkeypatch.setattr(ollama, "show", lambda model: {"capabilities": capabilities})


def test_a_text_only_tag_is_refused_loudly(monkeypatch):
    """A text-only model accepts an image-bearing message, ignores the image and answers
    from the prompt alone. The reply reads like an observation and is invention. Better to
    refuse to build than to describe a photo nobody looked at."""
    _fake_ollama(monkeypatch, ["completion", "tools"])
    assert vision._ollama_lacks_vision("qwen2.5") is True
    import pytest
    with pytest.raises(ValueError, match="no vision capability"):
        vision._build_vlm_backend("ollama", "qwen2.5")


def test_a_vision_tag_passes_the_probe(monkeypatch):
    _fake_ollama(monkeypatch, ["completion", "vision"])
    assert vision._ollama_lacks_vision("llama3.2-vision") is False


def test_an_unreachable_daemon_does_not_block_a_working_install(monkeypatch):
    """The probe is one-sided on purpose: unknown must never mean refuse."""
    import ollama

    def _boom(model):
        raise ConnectionError("no daemon")
    monkeypatch.setattr(ollama, "show", _boom)
    assert vision._ollama_lacks_vision("llama3.2-vision") is False


def test_an_old_client_reporting_no_capabilities_does_not_block(monkeypatch):
    _fake_ollama(monkeypatch, [])
    assert vision._ollama_lacks_vision("llama3.2-vision") is False


def test_the_local_backend_sends_the_image_in_the_shape_ollama_parses(monkeypatch):
    """langchain_ollama reads {"type": "image_url", "image_url": {"url": ...}} and splits
    the base64 payload off the data URI itself. If this shape drifts, images are dropped
    silently and the model describes nothing."""
    _fake_ollama(monkeypatch, ["vision"])
    sent = {}

    class _FakeChat:
        def __init__(self, **kw):
            sent["init"] = kw

        def invoke(self, messages):
            sent["content"] = messages[0].content
            class _Out:
                content = "Leaves are pale at the margins."
            return _Out()

    import langchain_ollama
    monkeypatch.setattr(langchain_ollama, "ChatOllama", _FakeChat)
    backend = vision._build_vlm_backend("ollama", "llama3.2-vision")
    out = backend("data:image/jpeg;base64,QUJD", "describe this")

    assert out == "Leaves are pale at the margins."
    kinds = [part["type"] for part in sent["content"]]
    assert kinds == ["text", "image_url"]
    assert sent["content"][1]["image_url"] == {"url": "data:image/jpeg;base64,QUJD"}
    assert sent["init"]["model"] == "llama3.2-vision"


def test_the_local_path_still_goes_through_the_sanitizer():
    """A local model is not a trusted one. Whatever it says is still stripped of numbers
    and prescriptions before anyone reads it."""
    cleaned, flags = vision.sanitize_observation(
        "The water is cloudy and ammonia reads 4 mg/L. Add chelated iron to the sump.")
    assert "4 mg/L" not in cleaned
    assert "chelated iron" not in cleaned
    assert "stripped:measurement" in flags and "stripped:prescriptive" in flags
