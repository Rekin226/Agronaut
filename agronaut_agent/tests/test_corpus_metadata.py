"""Citation and routing metadata on every chunk: licence, domain, year, DOI."""

from srcs import chatbot


def test_urls_file_keeps_the_optional_metadata_field(tmp_path):
    f = tmp_path / "urls.txt"
    f.write_text("PAPER|https://x|X (2019)|CC BY 4.0|domain=hydroponics;year=2019;doi=10.1/a;"
                 "bogus=1\nBOOK|https://y|Y|CC BY 4.0\n")
    a, b = chatbot.parse_urls_file(str(f))
    assert a["meta"] == {"domain": "hydroponics", "year": "2019", "doi": "10.1/a"}
    assert b["meta"] == {}                        # old four-field lines still parse


def test_guides_default_to_aquaponics_unless_they_say_otherwise():
    assert chatbot.guide_domain("# pH\nKeep it at 7.") == "aquaponics"
    assert chatbot.guide_domain("# Mixing\n_Domain: hydroponics_\n...") == "hydroponics"


def test_local_guides_carry_a_domain(tmp_path):
    (tmp_path / "a.md").write_text("# A\ntext")
    (tmp_path / "b.md").write_text("# B\n_Domain: ras_\ntext")
    docs = {d.metadata["kb_tag"]: d for d in chatbot.load_local_knowledge_documents(str(tmp_path))}
    assert docs["a"].metadata["domain"] == "aquaponics"
    assert docs["b"].metadata["domain"] == "ras"


def test_metadata_schema_is_part_of_the_cache_key(monkeypatch):
    before = chatbot._corpus_fingerprint()
    monkeypatch.setattr(chatbot, "META_SCHEMA", chatbot.META_SCHEMA + 1)
    assert chatbot._corpus_fingerprint() != before


_JATS = b"""<?xml version="1.0"?><article><front><article-meta>
<title-group><article-title>Lettuce in hydroponics</article-title></title-group>
<contrib-group><contrib>Someone, University of X</contrib></contrib-group>
<abstract><p>We compared water use.</p></abstract>
<permissions><license xlink:href="https://creativecommons.org/licenses/by/4.0/"/></permissions>
</article-meta></front><body>
<sec><title>1. Introduction</title><p>Soil farming uses water<xref>1</xref>.</p></sec>
<sec><title>2. Methods</title><p>We grew lettuce.</p></sec>
</body><back><ref-list><ref>Smith J. (2001) Journal of Things.</ref></ref-list></back></article>"""


def test_jats_keeps_the_body_and_drops_references_and_affiliations():
    probe = {"content_type": "application/xml", "content": _JATS}
    assert chatbot._is_jats(probe)
    docs = chatbot._jats_documents(_JATS, "https://epmc/x")
    text = " ".join(d.page_content for d in docs)
    assert [d.metadata["section"] for d in docs] == ["abstract", "1. Introduction", "2. Methods"]
    assert "compared water use" in text and "grew lettuce" in text
    assert "Smith J." not in text and "University of X" not in text


def test_licence_is_read_from_jats_xml():
    from scripts.corpus_report import detect_licence
    assert detect_licence(_JATS.decode()) == "CC BY 4.0"


def test_new_metadata_keys_are_filterable():
    from agronaut_agent import rag
    assert {"domain", "licence", "year"} <= rag._FILTERABLE
