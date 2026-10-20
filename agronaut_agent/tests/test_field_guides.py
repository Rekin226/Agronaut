"""Bundled guidance must survive offline installs and retrieval without losing scope."""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from agronaut_agent.domains import get_capability, run_capability
from agronaut_agent.field_guides import guide_catalog, guide_languages, read_field_guide

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("topic", [guide["topic"] for guide in guide_catalog()])
def test_offline_dispatch_carries_sources_and_never_claims_a_calculation(topic):
    result = run_capability("soil", "field_guidance", {"topic": topic})
    assert result["evidence_status"] == result["capability"]["status"] == "guidance"
    assert result["feasible"] is None
    assert result["result"] == read_field_guide(topic)
    expected_source = {"crop-scouting": "https://extension.psu.edu/",
                       "soil-sampling": "https://extension.umd.edu/"}.get(topic, "https://www.fao.org/")
    assert expected_source in result["sources"][0]
    assert result["limitations"] == [result["result"]["applicability"]]
    assert "content_markdown" in result["result"]


@pytest.mark.parametrize("topic", ["../VISION", "/etc/passwd", "aquaponics", "", None, []])
def test_topic_cannot_become_a_path_or_cross_domain_fallback(topic):
    with pytest.raises(ValueError, match="unknown field-guide"):
        read_field_guide(topic)


def test_declared_schema_exposes_the_complete_topic_list():
    cap = get_capability("soil", "field_guidance").describe()
    assert cap["input_schema"]["topic"] == [guide["topic"] for guide in guide_catalog()]
    assert cap["input_schema"]["language"] == list(guide_languages())
    assert cap["input_schema"]["default_language"] == "en"
    assert cap["executable"]
    with pytest.raises(ValueError, match="unsupported"):
        run_capability("hydroponics", "field_guidance", {"topic": "drip"})


def test_standard_library_cli_works_from_another_directory(tmp_path):
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    command = [sys.executable, "-S", "-m", "agronaut_agent.cli", "field-guide"]
    listing = subprocess.run(command + ["--json"], cwd=tmp_path, env=env,
                             text=True, capture_output=True, check=True)
    assert json.loads(listing.stdout) == guide_catalog()
    guide = subprocess.run(command + ["--topic", "surface", "--json"], cwd=tmp_path,
                           env=env, text=True, capture_output=True, check=True)
    assert json.loads(guide.stdout) == read_field_guide("surface")
    bad = subprocess.run(command + ["--topic", "../VISION"], cwd=tmp_path,
                         env=env, text=True, capture_output=True)
    assert bad.returncode == 2


def test_missing_or_incomplete_override_is_an_explicit_error(tmp_path, monkeypatch, capsys):
    from agronaut_agent.cli import main

    monkeypatch.setenv("AGRONAUT_CORPUS_DIR", str(tmp_path))
    assert main(["field-guide", "--topic", "drip", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "GUIDE_UNAVAILABLE"
    (tmp_path / "knowledge").mkdir()
    (tmp_path / "knowledge/field_drip.md").write_text("# Incomplete replacement")
    with pytest.raises(ValueError, match="missing its evidence"):
        read_field_guide("drip")


@pytest.mark.parametrize("headers", [False, True])
def test_middle_chunks_keep_citations_and_scope_in_filtered_retrieval(monkeypatch, headers):
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    from agronaut_agent import rag
    from srcs import chatbot

    docs = chatbot.load_local_knowledge_documents(str(ROOT / "knowledge"))
    drip = next(doc for doc in docs if doc.metadata["kb_tag"] == "field_drip")
    aquatic = next(doc for doc in docs if doc.metadata["domain"] == "aquaponics")
    prepared = chatbot._markdown_header_chunks(drip) if headers else [drip]
    chunks = RecursiveCharacterTextSplitter(chunk_size=700, chunk_overlap=80).split_documents(prepared)
    middle = next(doc for doc in chunks if "Keep emitter identifiers" in doc.page_content)
    assert "https://www.fao.org/" not in middle.page_content
    assert middle.metadata["source_references"] == drip.metadata["source_references"]

    class Index:
        def similarity_search_with_score(self, query, k, filter=None, **kwargs):
            return [(doc, 0.2) for doc in [aquatic, middle]
                    if filter is None or filter(doc.metadata)][:k]

    monkeypatch.setattr(rag, "_get_index", lambda: Index())
    monkeypatch.setattr(rag, "hybrid_enabled", lambda: False)
    monkeypatch.setattr(rag, "rerank_enabled", lambda: False)
    hits = rag.retrieve("uneven emitter flow", filters={"domain": ["soil", "irrigation"]})
    assert len(hits) == 1
    assert "Evidence: guidance" in hits[0]["text"]
    assert "no automatic fault diagnosis" in hits[0]["text"]
    assert "https://www.fao.org/4/s8684e/s8684e07.htm" in hits[0]["text"]
    assert "field_drip" in hits[0]["source"]
    assert rag._passage_text(aquatic) == aquatic.page_content.strip()


@pytest.mark.parametrize("topic", ["drip", "crop-scouting", "soil-sampling"])
@pytest.mark.parametrize("language", ["en", "fr", "zh-Hant"])
def test_soil_chat_can_read_the_guide_without_an_index(tmp_path, monkeypatch, language, topic):
    from langchain_core.messages import AIMessage

    from agronaut_agent import rag
    from agronaut_agent.tests.test_unit_agent import Model, agent_and_units

    def no_retrieval(*args, **kwargs):
        raise AssertionError("reading a bundled guide must not initialize retrieval")

    monkeypatch.setattr(rag, "_get_index", no_retrieval)
    calls = [{"name": "calculate_agriculture", "id": "guide", "args": {
        "production_system": "soil", "task": "field_guidance", "inputs": {"topic": topic, "language": language}}}]
    agent, _ = agent_and_units(tmp_path, Model([AIMessage(content="", tool_calls=calls),
                                               AIMessage(content="Here is the guide.")]))
    agent.select_unit("cli", "one", "field")
    agent.handle_message("cli", "one", "Read the selected field guide")
    result = json.loads(agent.tool_results("cli", "one", "calculate_agriculture")[-1])
    assert result["result"]["topic"] == topic
    assert result["capability"]["status"] == "guidance"
    assert result["result"]["language"] == language
    assert result["result"] == read_field_guide(topic, language)


@pytest.mark.parametrize("language", ["fr", "zh-Hant"])
@pytest.mark.parametrize("topic", [guide["topic"] for guide in guide_catalog()])
def test_translations_preserve_sources_commands_and_visible_review_limits(topic, language):
    original = read_field_guide(topic)
    result = run_capability("soil", "field_guidance", {"topic": topic, "language": language})
    translated = result["result"]
    assert translated["source_sha256"] == original["source_sha256"]
    assert translated["source_references"] == original["source_references"]
    assert translated["source_language"] == "en" and translated["language"] == language
    assert translated["translation_status"] == "draft"
    assert translated["translation_note"] in translated["content_markdown"]
    assert translated["translation_note"] in result["limitations"]
    assert set(re.findall(r"`([^`]+)`", original["content_markdown"])) <= set(
        re.findall(r"`([^`]+)`", translated["content_markdown"]))
    assert result["feasible"] is None and result["evidence_status"] == "guidance"
    assert translated["title"] != original["title"]


@pytest.mark.parametrize("language", [None, [], "", "../knowledge", "zh", "zh-CN", "fr-BF", "FR"])
def test_language_is_explicit_and_cannot_become_a_path(language):
    with pytest.raises(ValueError, match="unknown field-guide language"):
        read_field_guide("drip", language)
    with pytest.raises(ValueError, match="unknown field-guide language"):
        guide_catalog(language)


def copy_corpus(tmp_path, monkeypatch):
    shutil.copytree(ROOT/"knowledge", tmp_path/"knowledge")
    shutil.copytree(ROOT/"field_guides", tmp_path/"field_guides")
    monkeypatch.setenv("AGRONAUT_CORPUS_DIR", str(tmp_path))


def test_changed_original_blocks_only_its_outdated_translation(tmp_path, monkeypatch):
    copy_corpus(tmp_path, monkeypatch)
    path = tmp_path/"knowledge/field_drip.md"
    path.write_text(path.read_text()+"\nChanged scope requiring a new translation review.\n")
    for language in ("fr", "zh-Hant"):
        with pytest.raises(ValueError, match="out of date"):
            read_field_guide("drip", language)
        assert len(guide_catalog(language)) == len(guide_catalog())
        assert read_field_guide("surface", language)["language"] == language
    assert "Changed scope" in read_field_guide("drip")["content_markdown"]


@pytest.mark.parametrize("change", [
    lambda bundle: bundle.update(schema_version=True),
    lambda bundle: bundle.update(language="en"),
    lambda bundle: bundle.update(translation_status="reviewed"),
    lambda bundle: bundle["guides"].pop("drip"),
    lambda bundle: bundle["guides"]["drip"].update(body_markdown=""),
    lambda bundle: bundle["guides"]["drip"].update(applicability=None),
])
def test_incomplete_translations_never_fall_back_or_claim_review(tmp_path, monkeypatch, change):
    copy_corpus(tmp_path, monkeypatch)
    path = tmp_path/"field_guides/fr.json"
    bundle = json.loads(path.read_text())
    change(bundle)
    path.write_text(json.dumps(bundle))
    with pytest.raises(ValueError, match="unavailable or incomplete"):
        read_field_guide("drip", "fr")


def test_missing_translation_is_an_explicit_cli_error(tmp_path, monkeypatch, capsys):
    from agronaut_agent.cli import main

    copy_corpus(tmp_path, monkeypatch)
    (tmp_path/"field_guides/fr.json").unlink()
    assert main(["field-guide", "--language", "fr", "--topic", "drip", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "GUIDE_UNAVAILABLE"
    assert read_field_guide("drip")["language"] == "en"


@pytest.mark.parametrize("language", ["fr", "zh-Hant"])
def test_translated_cli_works_without_dependencies_from_another_directory(tmp_path, language):
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    command = [sys.executable, "-S", "-m", "agronaut_agent.cli", "field-guide", "--language", language]
    listing = subprocess.run(command+["--json"], cwd=tmp_path, env=env, text=True, capture_output=True, check=True)
    assert json.loads(listing.stdout) == guide_catalog(language)
    guide = subprocess.run(command+["--topic", "water-needs"], cwd=tmp_path, env=env,
                           text=True, capture_output=True, check=True)
    assert guide.stdout.strip() == read_field_guide("water-needs", language)["content_markdown"].strip()


@pytest.mark.parametrize('language',['en','fr','zh-Hant'])
@pytest.mark.parametrize('topic,source', [
    ('crop-scouting', 'https://www.udel.edu/'),
    ('soil-sampling', 'https://soiltest.cfans.umn.edu/'),
])
def test_observation_guide_cli_is_available_offline_with_current_source_and_limits(tmp_path,language,topic,source):
    result = subprocess.run([sys.executable,'-S','-m','agronaut_agent.cli','field-guide',
        '--topic',topic,'--language',language,'--json'], cwd=tmp_path,
        env=dict(os.environ,PYTHONPATH=str(ROOT)), text=True,capture_output=True,check=True,timeout=30)
    guide = json.loads(result.stdout)
    assert guide == read_field_guide(topic,language)
    assert guide['evidence_status'] == 'guidance'
    assert source in guide['source_references']
    assert guide['translation_status'] == ('original' if language=='en' else 'draft')


@pytest.mark.parametrize('tag,needle,scope,source', [
    ('field_crop_scouting', 'optional counts', 'no confirmed pest or disease diagnosis', 'https://extension.psu.edu/'),
    ('field_soil_sampling', 'Copy each reported result', 'no universal sampling depth', 'https://extension.umd.edu/'),
])
def test_observation_retrieval_chunks_keep_scope_and_primary_sources(tag,needle,scope,source):
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    from agronaut_agent import rag
    from srcs import chatbot

    docs = chatbot.load_local_knowledge_documents(str(ROOT/'knowledge'))
    doc = next(d for d in docs if d.metadata['kb_tag']==tag)
    assert doc.metadata['domain']=='soil'
    chunks = RecursiveCharacterTextSplitter(chunk_size=700,chunk_overlap=80).split_documents([doc])
    middle = next(d for d in chunks if needle in d.page_content)
    passage = rag._passage_text(middle)
    assert scope in passage
    assert source in passage
    assert 'Evidence: guidance' in passage
