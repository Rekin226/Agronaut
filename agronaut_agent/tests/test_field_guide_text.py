"""Message paging must preserve complete guidance and every page's context."""

import re
import subprocess
import sys

import pytest

from agronaut_agent.channels.commands import dispatch
from agronaut_agent.field_guide_text import (
    MESSAGE_LIMIT,
    _chunks,
    parse_guide_args,
    render_guide,
)
from agronaut_agent.field_guides import guide_catalog, read_field_guide


@pytest.mark.parametrize("language", ["en", "fr", "zh-Hant"])
@pytest.mark.parametrize("topic", [entry["topic"] for entry in guide_catalog()])
def test_every_page_keeps_context_and_body_is_lossless(topic, language):
    guide = read_field_guide(topic, language)
    first = render_guide(f"{topic} {language}")
    prefix = first[:first.index(guide["content_markdown"][:40])]
    bodies = []
    page = 1
    while True:
        result = render_guide(f"{topic} {language} {page}")
        assert result.startswith(prefix)
        assert len(result.encode("utf-16-le")) // 2 <= MESSAGE_LIMIT
        assert guide["applicability"] in prefix
        assert guide["source_references"] in prefix
        assert guide["source_sha256"][:12] in prefix
        if language != "en":
            assert guide["translation_note"] in prefix
        match = re.search(r"\n\n(?:Page |第 )(\d+)/(\d+)(?: 頁)?\n", result[len(prefix):])
        assert match and int(match[1]) == page
        bodies.append(result[len(prefix):len(prefix) + match.start()])
        total = int(match[2])
        if page == total:
            break
        assert f"/guide {topic} {language} {page + 1}" in result
        page += 1
    assert "".join(bodies) == guide["content_markdown"]
    beyond = render_guide(f"{topic} {language} {total + 1}")
    assert f"/guide {topic} {language} 1" in beyond


@pytest.mark.parametrize("language", ["en", "fr", "zh-Hant"])
def test_catalog_includes_every_action(language):
    text = render_guide(language)
    for entry in guide_catalog(language):
        assert entry["title"] in text
        assert f"/guide {entry['topic']} {language}" in text
    assert len(text.encode("utf-16-le")) // 2 <= MESSAGE_LIMIT


@pytest.mark.parametrize("args", ["fr 2", "2", "drip fr 0", "drip fr 01", "drip fr -1",
    "drip fr ２", "drip fr 1.0", "drip fr 1000000", "drip fr 1 extra", "../VISION", "drip de"])
def test_bad_arguments_do_not_access_an_agent(args):
    assert "Use /guide" in dispatch(object(), "cli", "unregistered", "/guide@bot " + args).text


@pytest.mark.parametrize("args,expected", [("", (None, "en", 1)), ("fr", (None, "fr", 1)),
    ("drip", ("drip", "en", 1)), ("drip 2", ("drip", "en", 2)),
    ("soil-sampling zh-Hant 999999", ("soil-sampling", "zh-Hant", 999999))])
def test_explicit_language_and_page(args, expected):
    assert parse_guide_args(args) == expected


@pytest.mark.parametrize("text", ["🌱" * 5000, ("Long soil sample 地區 🌱\n\n" * 500), "x" * 10000])
def test_long_unicode_or_unbroken_paragraphs_remain_complete(text):
    chunks = _chunks(text, 129)
    assert "".join(chunks) == text
    assert all(0 < len(chunk.encode("utf-16-le")) // 2 <= 129 for chunk in chunks)


def test_oversized_context_fails_explicitly_without_truncating_sources(monkeypatch):
    guide = read_field_guide("drip")
    guide["source_references"] = "https://example.invalid/" + "x" * 4000
    monkeypatch.setattr("agronaut_agent.field_guide_text.read_field_guide", lambda *args: guide)
    assert "GUIDE_UNAVAILABLE" in dispatch(object(), "cli", "17", "/guide drip").text


def test_missing_bundle_is_an_explicit_command_error(tmp_path, monkeypatch):
    monkeypatch.setenv("AGRONAUT_CORPUS_DIR", str(tmp_path))
    assert "unavailable" in dispatch(object(), "cli", "17", "/guide drip fr").text


def test_renderer_uses_only_standard_library():
    result = subprocess.run([sys.executable, "-S", "-c",
        "from agronaut_agent.field_guide_text import render_guide; print(render_guide('soil-sampling fr'))"],
        capture_output=True, text=True, check=True)
    assert "Traduction provisoire" in result.stdout
