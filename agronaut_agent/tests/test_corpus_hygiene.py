"""Guards on what is allowed INTO the index.

Measured on the real corpus: three MDPI sources returned HTTP 403, and the "Access Denied - You
don't have permission" body was indexed as a retrievable, citable passage. It is 231 characters,
so it clears the 200-character boilerplate floor, and it contains none of the boilerplate
keywords. Only the HTTP status distinguishes it from content.

The vetting tests cover the other way a corpus rots: a source that is reachable, substantial and
well-formed, but is the wrong document or is not openly licensed.

All hermetic: no network, no index, no embedding model.
"""

import pytest


# --- what gets in: HTTP status must gate indexing ----------------------------

class _Resp:
    def __init__(self, status=200, content=b"hello", ctype="text/html"):
        self.status_code = status
        self.content = content
        self.headers = {"Content-Type": ctype}


@pytest.fixture
def fake_requests(monkeypatch):
    """Swap the `requests` module srcs.chatbot imports inside _probe_url."""
    import sys, types

    def _install(resp=None, raises=None):
        mod = types.ModuleType("requests")

        def get(url, **kw):
            if raises:
                raise raises
            return resp
        mod.get = get
        monkeypatch.setitem(sys.modules, "requests", mod)
    return _install


def test_error_page_body_is_never_indexed(fake_requests):
    """A 403 page must yield NO documents — this is the bug that put 'Access Denied' text
    into the knowledge base as a citable passage."""
    from srcs.chatbot import _probe_url
    fake_requests(_Resp(status=403, content=b"Access Denied. You don't have permission."))
    assert _probe_url("https://example.org/paywalled") is None


@pytest.mark.parametrize("status", [400, 401, 404, 429, 500, 503])
def test_all_non_2xx_statuses_are_rejected(fake_requests, status):
    from srcs.chatbot import _probe_url
    fake_requests(_Resp(status=status))
    assert _probe_url("https://example.org/x") is None


@pytest.mark.parametrize("status", [200, 201, 204])
def test_2xx_is_accepted(fake_requests, status):
    from srcs.chatbot import _probe_url
    fake_requests(_Resp(status=status))
    assert _probe_url("https://example.org/x") is not None


def test_transport_failure_defers_rather_than_dropping_the_source(fake_requests):
    """One flaky request must not silently remove a healthy source from the corpus: the probe
    yields to the loader's own error handling instead of returning None."""
    from srcs.chatbot import _probe_url
    fake_requests(raises=OSError("connection reset"))
    probe = _probe_url("https://example.org/x")
    assert probe is not None and probe["content"] == b""


def test_pdf_detected_by_magic_bytes_not_url_suffix(fake_requests):
    """FAO serves publications from extension-less bitstream endpoints, so a `.pdf` suffix
    check would miss them entirely."""
    from srcs.chatbot import _probe_url
    fake_requests(_Resp(content=b"%PDF-1.7 ...", ctype="application/octet-stream"))
    assert _probe_url("https://openknowledge.fao.org/server/api/core/bitstreams/x/content")["is_pdf"]


def test_pdf_detected_by_content_type(fake_requests):
    from srcs.chatbot import _probe_url
    fake_requests(_Resp(content=b"whatever", ctype="application/pdf"))
    assert _probe_url("https://example.org/download")["is_pdf"]


def test_html_is_not_mistaken_for_pdf(fake_requests):
    from srcs.chatbot import _probe_url
    fake_requests(_Resp(content=b"<html><body>hi</body></html>", ctype="text/html"))
    assert not _probe_url("https://example.org/page")["is_pdf"]


def test_unreadable_pdf_bytes_degrade_to_empty_not_crash():
    from srcs.chatbot import _pdf_documents
    assert _pdf_documents(b"not actually a pdf", "https://example.org/x") == []
# --- vetting a proposed source before it enters the corpus -------------------

from scripts.corpus_report import (  # noqa: E402
    _drift, _drift_in_text, _tokens, detect_licence, detect_licence_in_text,
)


def test_cc_licence_detected_in_html():
    html = '<a href="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0</a>'
    assert detect_licence(html) == "CC BY 4.0"


def test_no_licence_in_plain_html():
    assert detect_licence("<html><body>nothing here</body></html>") == ""


def test_licence_matched_across_pdf_line_breaks():
    """PDF extraction wraps lines mid-sentence. A licence phrase split by a newline must still
    match — this silently rejected FAO 589 until whitespace was normalised first."""
    text = ("© FAO, 2014 FAO encourages the use, reproduction and dissemination\nof material in "
            "this\ninformation product. Except where otherwise indicated...")
    assert detect_licence_in_text(text).startswith("FAO permissive")


def test_cc_by_detected_in_pdf_text():
    assert detect_licence_in_text("Licensed under CC BY 4.0 by the authors.") == "CC BY 4.0"


def test_licence_found_past_the_front_matter():
    """The rights statement sits on page 4 of a full publication, behind thousands of characters
    of title pages — a narrow scan window misses it."""
    text = ("front matter " * 900) + " reproduction and dissemination of material in this information product"
    assert detect_licence_in_text(text).startswith("FAO permissive")


def test_topic_drift_catches_the_wrong_publication():
    """A publication ID that silently resolves to a different article is the failure mode that
    passes every other check: reachable, substantial, well-formed — and about sharks."""
    meta = {"title": "Sharks for the Aquarium and Considerations for Their Selection",
            "final_url": "https://ask.ifas.ufl.edu/publication/FA179"}
    assert _drift("aquaponics water quality guide", meta) == "DRIFT"


def test_on_topic_page_is_not_flagged():
    meta = {"title": "Important Water Quality Parameters in Aquaponics Systems",
            "final_url": "https://pubs.nmsu.edu/_circulars/CR680/"}
    assert _drift("aquaponics water quality parameters", meta) == ""


def test_unlabelled_source_is_reported_not_silently_passed():
    """An unlabelled URL cannot be drift-checked at all, which is itself the risk worth naming."""
    assert _drift("", {"title": "anything"}) == "unlabeled"


def test_pdf_drift_uses_document_text_not_html_title():
    """PDFs have no <title>, so drift must be judged on their opening text — otherwise every
    PDF is falsely rejected."""
    body = "Small-scale aquaponic food production. Integrated fish and plant farming. FAO 2014."
    assert _drift_in_text("aquaponic food production", body) == ""
    assert _drift_in_text("bicycle maintenance", body) == "DRIFT"


def test_stopwords_cannot_manufacture_a_topic_match():
    """'guide'/'university'/'the' appear in half the titles on the internet; matching on them
    would let any page pass the topic check."""
    assert not (_tokens("the university guide") & {"aquaponics", "tilapia"})


def test_bot_challenge_title_does_not_fake_topic_drift():
    """Cloudflare served 'Client Challenge' to the metadata request while the content request
    succeeded with 215 real chunks. Judging identity on the title alone flagged a healthy source
    as the wrong document."""
    from scripts.corpus_report import _drift
    meta = {"title": "Client Challenge", "final_url": "https://doi.org/10.1007/S10462-024-11003-X"}
    body = "Smart aquaponics systems are gaining popularity as they contribute to food production"
    assert _drift("smart aquaponics systematic literature review", meta, body) == ""


def test_body_evidence_cannot_excuse_genuine_drift():
    """The body is the strongest evidence of identity — which means it must still catch a source
    that really is the wrong document."""
    from scripts.corpus_report import _drift
    meta = {"title": "Sharks for the Aquarium", "final_url": "https://ask.ifas.ufl.edu/x"}
    body = "Selecting sharks for home aquaria requires attention to tank volume and temperament."
    assert _drift("aquaponics water quality", meta, body) == "DRIFT"


def test_challenge_title_detection():
    from scripts.corpus_report import _is_challenge_title
    for t in ["Just a moment...", "Client Challenge", "Attention Required! | Cloudflare",
              "Access Denied", "Checking your browser before accessing"]:
        assert _is_challenge_title(t), t
    assert not _is_challenge_title("Important Water Quality Parameters in Aquaponics Systems")


# --- dependency contract -----------------------------------------------------

def test_lazily_imported_dependencies_are_actually_installed():
    """rank_bm25 and pypdf are imported inside try/except so that a missing one degrades
    gracefully rather than killing a live turn. That is right at runtime and dangerous in CI:
    with both absent the whole suite still passed, because every test only asserted the graceful
    degradation. PDF ingestion returned no chunks and BM25 fell back to dense-only, silently, and
    nothing failed.

    This asserts the dependency contract directly, so a fresh install missing them breaks loudly
    here instead of quietly shipping two inert features.
    """
    import pypdf  # noqa: F401
    import rank_bm25  # noqa: F401


def test_lazy_dependencies_are_declared_in_requirements():
    """Installed on this machine is not the same as declared. requirement.txt is what CI, Docker
    and `pip install -e .` actually install (pyproject reads it as the single source of truth)."""
    import pathlib
    req = (pathlib.Path(__file__).resolve().parents[2] / "requirement.txt").read_text().lower()
    for dep in ("rank_bm25", "pypdf"):
        assert dep in req, f"{dep} is imported by the code but not declared in requirement.txt"


def test_pdf_extraction_actually_works():
    """A functional round-trip, not a graceful-degradation check: build a real PDF and read its
    text back. Without pypdf this fails instead of silently yielding zero chunks."""
    import io
    from pypdf import PdfWriter

    from srcs.chatbot import _pdf_documents

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    writer.write(buf)
    docs = _pdf_documents(buf.getvalue(), "https://example.org/doc.pdf")
    # A blank page yields no text, but parsing must succeed and metadata must be well formed.
    assert isinstance(docs, list)
    for d in docs:
        assert d.metadata["source"] == "https://example.org/doc.pdf"
        assert isinstance(d.metadata["page"], int)
