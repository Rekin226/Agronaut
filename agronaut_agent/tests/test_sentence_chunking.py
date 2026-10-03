"""Extracted sources are cut at sentence ends, not wherever a printed line or a space falls.

pypdf ends every printed line with a newline and the default splitter prefers newline, then
space, so a book chunk usually began and ended mid-sentence (72% / 59% on FAO 589). These pin the
two halves of the fix: rejoining printed lines, and preferring sentence ends when splitting. The
curated knowledge/ files must come out byte-identical, because nothing about them was broken.
"""

import pytest
from langchain_core.documents import Document

from srcs import chatbot

PRINTED = """Ammonia is toxic to fish at high concentrations and must be
converted by nitrifying bacteria before it accumulates. The bio-
filter is where this happens.
Keep total ammonia below 1 mg/L.

FIGURE 2.8
Nitrification in the biofilter"""


def test_printed_lines_are_rejoined_into_sentences():
    out = chatbot._rejoin_printed_lines(PRINTED)
    assert "must be converted by nitrifying bacteria" in out


def test_a_typesetting_hyphen_is_rejoined():
    assert "The biofilter is where" in chatbot._rejoin_printed_lines(PRINTED)


def test_a_line_break_survives_only_at_a_sentence_end():
    out = chatbot._rejoin_printed_lines(PRINTED)
    assert "accumulates. The biofilter" in out            # mid-paragraph sentence end: joined
    assert "where this happens.\nKeep total" in out        # line ended a sentence: kept
    assert "\n\nFIGURE 2.8 Nitrification" in out           # blank line: paragraph kept


def _pages(text, n=6):
    return [Document(page_content=text, metadata={"source": "book.pdf", "page": i,
                                                   "source_type": "web"}) for i in range(n)]


SENTENCES = " ".join(f"Sentence number {i} says something about water quality in tanks."
                     for i in range(60))


def test_extracted_chunks_end_at_a_sentence(monkeypatch):
    monkeypatch.setenv("AGRONAUT_SENTENCE_CHUNKS", "on")
    chunks = chatbot.split_into_chunks(_pages(SENTENCES, 1))
    assert len(chunks) > 1
    assert all(c.page_content.rstrip().endswith(".") for c in chunks)
    assert all(c.page_content[0].isupper() for c in chunks)


def test_with_the_flag_off_the_old_splitter_cuts_mid_sentence(monkeypatch):
    """The baseline this replaces, so the test above is known to be testing something."""
    monkeypatch.setenv("AGRONAUT_SENTENCE_CHUNKS", "off")
    chunks = chatbot.split_into_chunks(_pages(SENTENCES, 1))
    assert not all(c.page_content.rstrip().endswith(".") for c in chunks)


@pytest.mark.parametrize("flag", ["on", "off"])
def test_curated_files_are_untouched_by_the_flag(monkeypatch, flag):
    text = "## Heading\n\n" + SENTENCES.replace(". ", ".\n")
    doc = Document(page_content=text, metadata={"source": "k.md", "source_type": "local_file"})
    monkeypatch.setenv("AGRONAUT_SENTENCE_CHUNKS", "off")
    before = [c.page_content for c in chatbot.split_into_chunks([doc])]
    monkeypatch.setenv("AGRONAUT_SENTENCE_CHUNKS", flag)
    assert [c.page_content for c in chatbot.split_into_chunks([doc])] == before


def test_chunks_keep_document_order(monkeypatch):
    monkeypatch.setenv("AGRONAUT_SENTENCE_CHUNKS", "on")
    local = Document(page_content="local guide text.", metadata={"source": "k.md",
                                                                 "source_type": "local_file"})
    book = _pages("A page of the book.", 1)[0]
    chunks = chatbot.split_into_chunks([book, local])
    assert [c.metadata["source"] for c in chunks] == ["book.pdf", "k.md"]
