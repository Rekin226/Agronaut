"""The harvester's decisions, checked without the network."""

import pytest

from scripts import harvest_sources as hs

_WORK = {
    "doi": "https://doi.org/10.3390/IJERPH120606879",
    "title": "Comparison of Land, Water, and Energy Requirements of Lettuce",
    "publication_year": 2015, "cited_by_count": 681,
    "authorships": [{"author": {"display_name": "Guilherme Lages Barbosa"}},
                    {"author": {"display_name": "Francisca Gadelha"}},
                    {"author": {"display_name": "Rolf Halden"}}],
    "primary_location": {"source": {"display_name": "IJERPH"}},
    "best_oa_location": {"license": "cc-by", "pdf_url": "https://www.mdpi.com/x.pdf",
                         "landing_page_url": "https://www.mdpi.com/x"},
}


def test_doi_normalisation():
    assert hs.normalize_doi("https://doi.org/10.3390/IJERPH1") == "10.3390/ijerph1"
    assert hs.normalize_doi("doi:10.1/A") == "10.1/a"
    assert hs.normalize_doi(None) == ""


def test_citation_label_reads_like_a_reference():
    assert hs.citation_label(_WORK) == ("Barbosa et al. (2015), Comparison of Land, Water, and "
                                        "Energy Requirements of Lettuce, IJERPH")
    two = {**_WORK, "authorships": _WORK["authorships"][:2]}
    assert hs.citation_label(two).startswith("Barbosa & Gadelha (2015)")
    assert "|" not in hs.citation_label({**_WORK, "title": "A | B"})


def test_europe_pmc_beats_a_publisher_pdf():
    url, route = hs.choose_fetch_url(_WORK, {"pmcid": "PMC4483736", "isOpenAccess": "Y"})
    assert route == "europepmc" and url.endswith("/PMC4483736/fullTextXML")
    url, route = hs.choose_fetch_url(_WORK, {"pmcid": "PMC1", "isOpenAccess": "N"})
    assert route == "pdf"
    assert hs.choose_fetch_url({"best_oa_location": None}, None) == ("", "no open copy")


def test_known_sources_are_never_proposed_again():
    urls = ("BOOK|https://library.oapen.org/x/retrieve|Goddek|CC BY 4.0\n"
            "# PAYWALLED|https://doi.org/10.1016/J.AQUACULTURE.2019.1|parked\n")
    known = hs.existing_keys(urls)
    assert "https://library.oapen.org/x/retrieve" in known
    assert "10.1016/j.aquaculture.2019.1" in known     # commented lines count too
    cands = [{"doi": "10.1016/j.aquaculture.2019.1", "url": "u1"},
             {"doi": "10.9/new", "url": "u2"}, {"doi": "10.9/new", "url": "u3"}]
    assert [c["url"] for c in hs.dedupe(cands, known)] == ["u2"]


def test_urls_line_carries_metadata_and_the_vetted_licence():
    c = {"url": "https://e/x", "label": "Barbosa et al. (2015), T", "licence": "CC BY",
         "vetted_licence": "CC BY 4.0", "domain": "hydroponics", "year": 2015,
         "doi": "10.3390/x"}
    line = hs.urls_line(c)
    assert line == ("PAPER|https://e/x|Barbosa et al. (2015), T|CC BY 4.0|"
                    "domain=hydroponics;year=2015;doi=10.3390/x")
    from srcs.chatbot import parse_url_meta
    assert parse_url_meta(line.split("|")[4]) == {"domain": "hydroponics", "year": "2015",
                                                  "doi": "10.3390/x"}


def test_harvest_respects_caps_and_skips_known(monkeypatch):
    works = [{**_WORK, "doi": f"https://doi.org/10.1/{i}",
              "best_oa_location": {"license": "cc-by", "pdf_url": f"https://p/{i}.pdf"}}
             for i in range(10)]
    got = hs.harvest([{"domain": "aquaponics", "query": "q", "cap": 3}], ["cc-by"],
                     known={"10.1/0"}, query=lambda s, lic: works, pmc=lambda doi: None)
    assert [c["doi"] for c in got] == ["10.1/1", "10.1/2", "10.1/3"]
    assert all(c["domain"] == "aquaponics" and c["route"] == "pdf" for c in got)


def test_add_refuses_anything_the_gate_did_not_accept(tmp_path):
    urls = tmp_path / "urls.txt"
    urls.write_text("BOOK|https://a|A|CC BY 4.0\n")
    report = [{"doi": "10.1/ok", "url": "https://ok", "label": "Ok", "verdict": "ACCEPT",
               "vetted_licence": "CC BY 4.0", "domain": "ras", "year": 2020},
              {"doi": "10.1/rev", "url": "https://rev", "label": "Rev", "verdict": "REVIEW"}]
    with pytest.raises(SystemExit):
        hs.add_to_urls(["10.1/ok", "10.1/rev"], report, urls)
    assert urls.read_text() == "BOOK|https://a|A|CC BY 4.0\n"      # nothing written
    lines = hs.add_to_urls(["https://doi.org/10.1/OK"], report, urls)
    assert lines == ["PAPER|https://ok|Ok|CC BY 4.0|domain=ras;year=2020;doi=10.1/ok"]
    assert urls.read_text().endswith(lines[0] + "\n")


def test_scope_file_is_well_formed():
    import json
    scope = json.loads(hs.SCOPE_FILE.read_text())
    assert set(scope["licences"]) <= {"cc-by", "cc-by-sa", "cc0", "public-domain"}
    assert all(s["domain"] and s["query"] and s["cap"] > 0 for s in scope["scopes"])
