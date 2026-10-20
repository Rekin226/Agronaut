"""Growers can read and switch guides without starting a calculation."""

import pytest
from streamlit.testing.v1 import AppTest

from agronaut_agent.tests.test_field_guides import copy_corpus


def test_native_guide_selection_and_missing_corpus(monkeypatch, tmp_path):
    at = AppTest.from_string(
        "from agent.field_guides_ui import render_field_guides\nrender_field_guides()"
    ).run()
    assert not at.exception
    assert "Understanding crop water needs" in at.markdown[0].value
    at.selectbox(key="field_guide_topic").set_value("surface").run()
    assert not at.exception
    assert "no predictive cutoff timing" in at.markdown[0].value
    assert "https://www.fao.org/4/s8684e/s8684e0c.htm" in at.markdown[0].value
    monkeypatch.setenv("AGRONAUT_CORPUS_DIR", str(tmp_path))
    at.run()
    assert not at.exception
    assert "unavailable" in at.error[0].value


def test_language_switching_preserves_topic_and_renders_review_note():
    from agronaut_agent.field_guides import read_field_guide

    at = AppTest.from_string(
        "from agent.field_guides_ui import render_field_guides\nrender_field_guides()"
    ).run()
    at.selectbox(key="field_guide_topic").set_value("drip").run()
    for language in ("fr", "zh-Hant", "en"):
        at.selectbox(key="field_guide_language").set_value(language).run()
        assert not at.exception and not at.error
        assert at.selectbox(key="field_guide_topic").value == "drip"
        assert at.markdown[0].value == read_field_guide("drip", language)["content_markdown"].strip()
        assert len(at.get("download_button")) == 1


def test_stale_translation_removes_old_content_and_download_without_english_fallback(tmp_path, monkeypatch):
    copy_corpus(tmp_path, monkeypatch)
    at = AppTest.from_string(
        "from agent.field_guides_ui import render_field_guides\nrender_field_guides()"
    ).run()
    at.selectbox(key="field_guide_language").set_value("fr").run()
    at.selectbox(key="field_guide_topic").set_value("drip").run()
    path = tmp_path/"knowledge/field_drip.md"
    path.write_text(path.read_text()+"\nChanged applicability\n")
    at.run()
    assert not at.exception and "out of date" in at.error[0].value
    assert len(at.markdown) == len(at.get("download_button")) == 0
    at.selectbox(key="field_guide_topic").set_value("surface").run()
    assert not at.error and len(at.markdown) == 1


def test_translated_guides_work_inside_field_records_without_model_or_record_changes(tmp_path):
    from agent.tests.test_field_ui import _SCRIPT, setup

    brain, _, _ = setup(tmp_path)
    before = brain.field_workflow("web", "operator", "list", {})
    at = AppTest.from_string(_SCRIPT).run(timeout=15)
    at.selectbox(key="field_guide_language").set_value("fr").run(timeout=15)
    at.selectbox(key="field_guide_topic").set_value("sprinkler").run(timeout=15)
    assert not at.exception and not at.error
    assert any("Contrôler la répartition de l’aspersion" in block.value for block in at.markdown)
    at.selectbox(key="field_guide_language").set_value("zh-Hant").run(timeout=15)
    assert not at.exception and not at.error
    assert any("檢查噴灌分布" in block.value for block in at.markdown)
    assert brain.field_workflow("web", "operator", "list", {}) == before


@pytest.mark.parametrize('topic', ['crop-scouting', 'soil-sampling'])
def test_observation_guide_is_readable_in_each_language_without_record_changes(tmp_path, topic):
    from agent.tests.test_field_ui import _SCRIPT, setup
    from agronaut_agent.field_guides import read_field_guide

    brain, _, _ = setup(tmp_path)
    before = brain.field_workflow('web','operator','list',{})
    at = AppTest.from_string(_SCRIPT).run(timeout=15)
    at.selectbox(key='field_guide_topic').set_value(topic).run(timeout=15)
    for language in ('fr','zh-Hant','en'):
        at.selectbox(key='field_guide_language').set_value(language).run(timeout=15)
        assert not at.exception and not at.error
        assert at.selectbox(key='field_guide_topic').value == topic
        guide = read_field_guide(topic,language)
        assert any(block.value == guide['content_markdown'].strip() for block in at.markdown)
    assert brain.field_workflow('web','operator','list',{}) == before
