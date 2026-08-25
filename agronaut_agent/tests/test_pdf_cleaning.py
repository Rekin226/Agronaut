"""Cleaning publication PDFs before they are chunked.

A book is not a web page. Extracting FAO 589 raw gives 275 pages that include a table of contents,
a list of figures, blank "GENERAL NOTES" pages — and, on every single page, a running header. Left
alone that header lands in 111 of 1034 chunks: about 70 characters of the book's own title
repeated across the corpus, pulling each vector toward the title and away from what the chunk is
actually about. It is the highest-volume noise a PDF contributes and the easiest to remove.

Measured on FAO 589: 13 structural pages dropped, running header present in 4/992 chunks instead
of 111/1034.
"""

import pytest

from srcs import chatbot


class _Doc:
    def __init__(self, text, page=1):
        self.page_content = text
        self.metadata = {"source": "https://example.org/x.pdf", "page": page}


# --- running headers ---------------------------------------------------------

def _book(n=20):
    """A publication whose every page carries the same header with a varying page number."""
    return [_Doc(f"Small-scale aquaponic food production — Integrated fish and plant farming{i}\n"
                 f"Real prose about topic {i} that differs on every page and carries the meaning.",
                 page=i) for i in range(1, n + 1)]


def test_running_header_is_removed_from_every_page():
    out = chatbot._strip_running_headers(_book())
    assert not any("Small-scale aquaponic food production" in d.page_content for d in out)


def test_page_numbers_do_not_defeat_repetition_detection():
    """The header differs by exactly its page number, so counting raw lines finds 20 unique
    strings and removes nothing. Numbers must be normalised away before counting."""
    counts = {chatbot._normalise_for_repetition(d.page_content.splitlines()[0]) for d in _book()}
    assert len(counts) == 1


def test_real_content_survives():
    out = chatbot._strip_running_headers(_book())
    assert all(f"topic {d.metadata['page']}" in d.page_content for d in out)


def test_a_phrase_recurring_in_prose_is_not_treated_as_furniture():
    """"Aquaponics" appears throughout a book about aquaponics. Only text on a large FRACTION of
    pages as its own repeated line is furniture — otherwise the filter eats the subject matter."""
    docs = [_Doc(f"Aquaponics needs careful management of nutrient {i}.", page=i)
            for i in range(1, 21)]
    out = chatbot._strip_running_headers(docs)
    assert all("Aquaponics" in d.page_content for d in out)


def test_short_repeated_lines_are_kept():
    """A very short repeated line is as likely to be data as furniture; require some length."""
    docs = [_Doc(f"pH\nMeasurement number {i} taken at dawn.", page=i) for i in range(1, 21)]
    out = chatbot._strip_running_headers(docs)
    assert all("pH" in d.page_content for d in out)


def test_no_furniture_leaves_documents_untouched():
    docs = [_Doc(f"Wholly distinct page {i} with nothing repeated.", page=i) for i in range(1, 6)]
    before = [d.page_content for d in docs]
    assert [d.page_content for d in chatbot._strip_running_headers(docs)] == before


# --- contents / index pages --------------------------------------------------

TOC = """vi
3.3 Other major components of water quality: algae and parasites 28
3.3.1 Photosynthetic activity of algae 28
3.3.2 Parasites, bacteria and other small organisms living in the water 29
3.4 Sources of aquaponic water  29
3.4.1 Rainwater 30
3.4.2 Cistern or aquifer water 30
3.5 Manipulating pH  31"""

PROSE = """of constant water height. Instead, as the water continues to fall through the standpipe,
the bell, which sits over the standpipe something like a hat, acts as an air tight lock and
produces a siphon effect. This suction within the bell starts the siphon. Once started,
all the water from the bed starts to rapidly flush down the standpipe as the bell keeps
its air tight seal. The draining through the standpipe is faster than the constant inflow
from the fish tank. When the water in the grow bed drains all the way down to bottom."""


def test_contents_page_is_detected():
    assert chatbot._looks_like_contents_page(TOC)


def test_prose_is_not_mistaken_for_contents():
    """The costly false positive: dropping a real page of knowledge because a few lines happen
    to end in a number."""
    assert not chatbot._looks_like_contents_page(PROSE)


def test_prose_with_incidental_trailing_numbers_survives():
    text = ("Keep dissolved oxygen above 5\n"
            "Target pH sits near 6.8\n"
            "The biofilter needs surface area, and the media provides it in proportion to the\n"
            "volume of the bed, which is why media choice matters more than bed depth alone.\n"
            "Feeding rate drives the whole system and should be adjusted slowly over days.")
    assert not chatbot._looks_like_contents_page(text)


def test_short_page_is_not_a_contents_page():
    assert not chatbot._looks_like_contents_page("GENERAL NOTES")


# --- the combined pass -------------------------------------------------------

def test_cleaning_drops_structure_and_keeps_knowledge():
    docs = _book(10) + [_Doc(TOC, page=11), _Doc(PROSE, page=12)]
    out = chatbot._clean_pdf_documents(docs)
    pages = {d.metadata["page"] for d in out}
    assert 11 not in pages, "contents page survived"
    assert 12 in pages, "prose page was dropped"
    assert not any("Small-scale aquaponic food production" in d.page_content for d in out)


def test_cleaning_can_be_disabled_for_ablation(monkeypatch):
    monkeypatch.setenv("AGRONAUT_PDF_CLEAN", "off")
    docs = _book(10) + [_Doc(TOC, page=11)]
    assert len(chatbot._clean_pdf_documents(docs)) == 11


def test_cleaning_is_on_by_default(monkeypatch):
    """Unlike hybrid and header chunking, this defaults ON: a table of contents is not knowledge
    under any corpus conditions, so there is nothing to trade off."""
    monkeypatch.delenv("AGRONAUT_PDF_CLEAN", raising=False)
    assert chatbot.pdf_cleaning_enabled()


def test_empty_input_is_safe():
    assert chatbot._clean_pdf_documents([]) == []


# --- chapter labelling -------------------------------------------------------

def test_chapter_is_read_from_the_running_header():
    """A PDF has no headings to split on — extraction flattens the typography away. What survives
    is the running header: "51Design of aquaponic units" carries the chapter and page as one
    string."""
    docs = [_Doc("51Design of aquaponic units\nProse about media beds.", page=51)]
    out = chatbot._label_pdf_chapters(docs)
    assert out[0].metadata["chapter"] == "Design of aquaponic units"


def test_chapter_forward_fills_across_following_pages():
    """Detection alone reached 76 of 262 pages, because even pages carry the book title instead.
    A chapter continues until the next one starts — forward-filling took coverage to 95%."""
    docs = [_Doc("51Design of aquaponic units\nFirst page.", page=51),
            _Doc("Continuation prose with no header of its own.", page=52),
            _Doc("More continuation prose.", page=53),
            _Doc("75Plants in aquaponics\nA new chapter starts.", page=75),
            _Doc("Prose belonging to the new chapter.", page=76)]
    out = chatbot._label_pdf_chapters(docs)
    assert [d.metadata["chapter"] for d in out] == [
        "Design of aquaponic units", "Design of aquaponic units", "Design of aquaponic units",
        "Plants in aquaponics", "Plants in aquaponics"]


def test_header_line_is_removed_once_captured():
    """The header is furniture once its content is metadata — leaving it would embed the page
    number alongside the chapter name."""
    docs = chatbot._label_pdf_chapters([_Doc("51Design of aquaponic units\nReal prose.", page=51)])
    assert "51Design" not in docs[0].page_content


def test_pages_before_any_chapter_are_left_alone():
    docs = chatbot._label_pdf_chapters([_Doc("Front matter with no chapter yet.", page=1)])
    assert "chapter" not in docs[0].metadata


def test_prose_is_not_mistaken_for_a_chapter_header():
    """A body line beginning with a number must not hijack the chapter for every page after it."""
    docs = chatbot._label_pdf_chapters([_Doc("5 percent of body weight is a typical ration.", page=9)])
    assert "chapter" not in docs[0].metadata


def test_chapter_labelling_ships_disabled(monkeypatch):
    """Measured worse on every metric: prefixing chunks with a generic chapter name adds words
    that appear in every chunk about that topic, which dilutes rather than disambiguates."""
    monkeypatch.delenv("AGRONAUT_PDF_SECTIONS", raising=False)
    assert not chatbot.pdf_sections_enabled()
    monkeypatch.setenv("AGRONAUT_PDF_SECTIONS", "on")
    assert chatbot.pdf_sections_enabled()
