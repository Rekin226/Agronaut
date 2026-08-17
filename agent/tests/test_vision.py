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
