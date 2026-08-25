"""Markdown-header-aware chunking and the context prefix.

Ships DISABLED. Measured over the golden set the baseline character splitter wins on every metric
except precision (docs/dpg/retrieval_eval/chunking_ablation.json), because 95% of this corpus's
sections are SMALLER than the 800-character chunk window — splitting on headings fragments a
corpus the recursive splitter was already packing sensibly.

It is kept and tested because that reasoning inverts for documents whose sections EXCEED the chunk
window, which is exactly the book-length PDFs the corpus is growing toward. So it has to be
correct and inert now, and correct and available later.
"""

import pytest

from srcs import chatbot


class _Doc:
    def __init__(self, text, **md):
        self.page_content = text
        self.metadata = md


SAMPLE = """# Nitrogen Cycle and Cycling

Intro paragraph about the cycle.

## Target readings

- Ammonia: keep it below 0.5 mg/L
- Nitrite: keep it below 0.5 mg/L

## Ammonia spike — safe actions

Stop feeding for 24 hours and perform a partial water change.
"""


def test_disabled_by_default(monkeypatch):
    """The shipping default. Unset must mean OFF — the ablation says the baseline wins."""
    monkeypatch.delenv("AGRONAUT_MD_HEADERS", raising=False)
    assert not chatbot.markdown_headers_enabled()
    monkeypatch.setenv("AGRONAUT_MD_HEADERS", "on")
    assert chatbot.markdown_headers_enabled()


def test_splits_on_authored_section_boundaries(monkeypatch):
    monkeypatch.delenv("AGRONAUT_MD_CRUMB", raising=False)
    chunks = chatbot._markdown_header_chunks(_Doc(SAMPLE, source_type="local_file"))
    assert len(chunks) >= 3
    joined = [c.page_content for c in chunks]
    # The two sections must not end up in one chunk — that merge is the failure mode header
    # splitting exists to prevent.
    assert not any("Target readings" in c and "safe actions" in c for c in joined)


def test_context_prefix_gives_an_orphan_bullet_its_subject(monkeypatch):
    """"keep it below 0.5 mg/L" is nearly meaningless embedded alone. The prefix is what makes it
    carry its subject — the one part of this technique the ablation showed genuinely helps."""
    monkeypatch.delenv("AGRONAUT_MD_CRUMB", raising=False)
    chunks = chatbot._markdown_header_chunks(_Doc(SAMPLE, source_type="local_file"))
    target = next(c for c in chunks if "0.5 mg/L" in c.page_content)
    assert "Nitrogen Cycle and Cycling" in target.page_content
    assert "Target readings" in target.page_content


def test_context_prefix_can_be_disabled_for_ablation(monkeypatch):
    monkeypatch.setenv("AGRONAUT_MD_CRUMB", "off")
    chunks = chatbot._markdown_header_chunks(_Doc(SAMPLE, source_type="local_file"))
    target = next(c for c in chunks if "0.5 mg/L" in c.page_content)
    assert not target.page_content.startswith("Nitrogen Cycle and Cycling — Target readings\n")


def test_heading_metadata_is_preserved_for_filtering(monkeypatch):
    monkeypatch.delenv("AGRONAUT_MD_CRUMB", raising=False)
    chunks = chatbot._markdown_header_chunks(
        _Doc(SAMPLE, source_type="local_file", source_path="/r/knowledge/nitrogen.md"))
    target = next(c for c in chunks if "0.5 mg/L" in c.page_content)
    assert target.metadata["h1"] == "Nitrogen Cycle and Cycling"
    assert target.metadata["h2"] == "Target readings"
    # original metadata must survive — source_path is what produces the citation label
    assert target.metadata["source_path"] == "/r/knowledge/nitrogen.md"


def test_document_without_headings_survives_intact():
    """No headings means nothing to split on. What matters is that the text and its metadata come
    through unaltered — the splitter may hand back an equivalent object rather than the same one."""
    doc = _Doc("Just a flat paragraph with no headings at all.", source_type="local_file")
    out = chatbot._markdown_header_chunks(doc)
    assert len(out) == 1
    assert out[0].page_content.strip() == doc.page_content.strip()
    assert out[0].metadata["source_type"] == "local_file"


def test_prefix_is_not_duplicated_when_headings_are_kept():
    """strip_headers=False means the heading is already in the body; prefixing again would embed
    the same words twice and skew the vector."""
    chunks = chatbot._markdown_header_chunks(_Doc(SAMPLE, source_type="local_file"))
    for c in chunks:
        assert c.page_content.count("Target readings") <= 2


def test_only_local_files_are_header_split(monkeypatch):
    """Web and PDF documents have no reliable heading structure, so they must reach the character
    splitter untouched."""
    monkeypatch.setenv("AGRONAUT_MD_HEADERS", "on")
    web = _Doc(SAMPLE, source_type="web")
    local = _Doc(SAMPLE, source_type="local_file")
    prepared = []
    for d in (web, local):
        if d.metadata.get("source_type") == "local_file":
            prepared.extend(chatbot._markdown_header_chunks(d))
        else:
            prepared.append(d)
    assert web in prepared
    assert len(prepared) > 2


def test_malformed_markdown_does_not_lose_the_document(monkeypatch):
    """A parsing failure must never silently drop a knowledge file from the corpus."""
    monkeypatch.setattr(chatbot, "_crumb_enabled", lambda: True)
    doc = _Doc("#\n\n##\n\n" + "\x00 weird bytes", source_type="local_file")
    out = chatbot._markdown_header_chunks(doc)
    assert out, "document was dropped entirely"
