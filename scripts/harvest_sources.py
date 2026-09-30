"""Source harvester: find openly licensed literature for the corpus, vet it, propose it.

CORPUS.md records why the corpus is narrow and why scraping alone would not fix it: an ACCEPT
from the gate is a licence-and-reachability check, not a usefulness check, and one IoT paper that
passed every automated test was still the wrong thing to add. So this script does the tedious
half and leaves the judgement half to a person:

  1. For each scope in docs/dpg/corpus_scope.json it asks OpenAlex for works whose title or
     abstract match the query, whose best open-access copy carries an open licence, sorted by
     citations (a crude but honest proxy for "the field found this useful").
  2. It picks the most fetchable copy. Europe PMC's JATS full text comes first when the paper is
     in PMC: publisher PDFs of open papers are routinely behind bot walls (MDPI answers 403),
     and the JATS loader keeps the body while dropping the reference list.
  3. Every candidate goes through the real gate, scripts.corpus_report.vet(): reachable,
     substantial, on topic, licence detected IN THE SOURCE (OpenAlex's licence field is a hint,
     not evidence).
  4. It writes a report, docs/dpg/harvest/candidates.{json,md}. Nothing enters urls.txt until a
     person reads that report and runs --add with the ids they chose; --add refuses anything
     the gate did not ACCEPT.

Run it:
    python -m scripts.harvest_sources --dry-run            # query only, no vetting fetches
    python -m scripts.harvest_sources                      # query + vet, write the report
    python -m scripts.harvest_sources --domain hydroponics
    python -m scripts.harvest_sources --add 10.3390/ijerph120606879,10.3389/fpls.2019.00923

Set OPENALEX_API_KEY or OPENALEX_MAILTO to use OpenAlex's polite pool; anonymous search is
rate-limited when their cluster is busy.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_ROOT = Path(__file__).resolve().parents[1]
SCOPE_FILE = _ROOT / "docs" / "dpg" / "corpus_scope.json"
REPORT_DIR = _ROOT / "docs" / "dpg" / "harvest"
URLS_FILE = _ROOT / "urls.txt"

OPENALEX = "https://api.openalex.org/works"
EUROPE_PMC_SEARCH = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
EUROPE_PMC_FULLTEXT = "https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"

_SELECT = ("id,doi,title,publication_year,cited_by_count,authorships,primary_location,"
           "best_oa_location,type,language")


# --- pure helpers (unit-tested) -----------------------------------------------------------

def normalize_doi(doi: str | None) -> str:
    """'https://doi.org/10.1/ABC' -> '10.1/abc'. DOIs are case-insensitive."""
    d = (doi or "").strip().lower()
    return re.sub(r"^(https?://(dx\.)?doi\.org/|doi:)", "", d)


def citation_label(work: dict) -> str:
    """'Barbosa et al. (2015), Comparison of Land, Water, ... Methods, IJERPH'."""
    authors = work.get("authorships") or []
    names = [((a.get("author") or {}).get("display_name") or "").split() for a in authors]
    first = names[0][-1] if names and names[0] else "Anon."
    who = first if len(authors) == 1 else (f"{first} & {names[1][-1]}" if len(authors) == 2
                                           and names[1] else f"{first} et al.")
    title = " ".join((work.get("title") or "").split()).rstrip(".")
    venue = (((work.get("primary_location") or {}).get("source") or {}).get("display_name")
             or "")
    label = f"{who} ({work.get('publication_year') or 'n.d.'}), {title}"
    label = label + (f", {venue}" if venue else "")
    return label.replace("|", "/")          # the urls.txt field separator


def licence_name(openalex_licence: str | None) -> str:
    """OpenAlex's 'cc-by' -> the corpus's 'CC BY'. The version is filled in by the gate."""
    lic = (openalex_licence or "").lower()
    return {"cc-by": "CC BY", "cc-by-sa": "CC BY-SA", "cc0": "CC0",
            "public-domain": "Public domain"}.get(lic, lic.upper())


def choose_fetch_url(work: dict, pmc: dict | None) -> tuple[str, str]:
    """(url, route) for the most fetchable open copy, or ("", reason) when there is none.

    Europe PMC JATS first when the paper is open in PMC, then the best OA PDF, then the best OA
    landing page (the gate will tell whether it yields text)."""
    if pmc and pmc.get("pmcid") and pmc.get("isOpenAccess") == "Y":
        return EUROPE_PMC_FULLTEXT.format(pmcid=pmc["pmcid"]), "europepmc"
    best = work.get("best_oa_location") or {}
    if best.get("pdf_url"):
        return best["pdf_url"], "pdf"
    if best.get("landing_page_url"):
        return best["landing_page_url"], "landing"
    return "", "no open copy"


def existing_keys(urls_text: str) -> set[str]:
    """DOIs and URLs already in urls.txt (commented-out lines included, so a source someone
    deliberately parked as a comment is not proposed again)."""
    keys = set()
    for line in urls_text.splitlines():
        for m in re.finditer(r"https?://\S+?(?=\||\s|$)", line):
            keys.add(m.group(0).lower())
        for m in re.finditer(r"\b10\.\d{4,9}/[^\s|;]+", line):
            keys.add(normalize_doi(m.group(0)))
    return keys


def urls_line(c: dict) -> str:
    """The urls.txt line for an accepted candidate."""
    meta = ";".join(f"{k}={c[k]}" for k in ("domain", "year", "doi") if c.get(k))
    licence = c.get("vetted_licence") or c.get("licence") or ""
    return f"{c.get('category', 'PAPER')}|{c['url']}|{c['label']}|{licence}|{meta}"


def dedupe(candidates: list[dict], known: set[str]) -> list[dict]:
    """Drop candidates already in the corpus, and repeats across scopes (first scope wins,
    since scopes are listed most-central first)."""
    out, seen = [], set(known)
    for c in candidates:
        keys = {k for k in (c.get("doi"), (c.get("url") or "").lower()) if k}
        if keys & seen:
            continue
        seen |= keys
        out.append(c)
    return out


def to_markdown(candidates: list[dict]) -> str:
    rows = ["| id | domain | verdict | chunks | cited | label |",
            "|---|---|---|---|---|---|"]
    for c in candidates:
        rows.append(f"| `{c['doi'] or c['url']}` | {c['domain']} | {c.get('verdict', '-')} "
                    f"| {c.get('chunks', '-')} | {c.get('cited_by', 0)} | {c['label'][:110]} |")
    accepted = sum(1 for c in candidates if c.get("verdict") == "ACCEPT")
    return ("# Harvested candidates\n\n"
            f"{len(candidates)} candidates, {accepted} accepted by the gate. ACCEPT means "
            "reachable, substantial, on topic and openly licensed; it does not mean useful. Read "
            "the labels, pick what fills a gap, then:\n\n"
            "```bash\npython -m scripts.harvest_sources --add <id>,<id>\n```\n\n"
            + "\n".join(rows) + "\n")


# --- network ----------------------------------------------------------------------------

def _get_json(url: str, params: dict, attempts: int = 4) -> dict:
    import requests
    headers = {"User-Agent": "AgronautHarvester/1.0 (+https://github.com/Rekin226/Agronaut)"}
    for n in range(attempts):
        r = requests.get(url, params=params, headers=headers, timeout=30)
        if r.status_code == 429 or (r.status_code == 503 and n < attempts - 1):
            time.sleep(2 ** n * 3)
            continue
        r.raise_for_status()
        data = r.json()
        if "rate-limited" in str(data.get("message", "")) and n < attempts - 1:
            time.sleep(2 ** n * 3)
            continue
        return data
    raise RuntimeError(f"gave up on {url} after {attempts} attempts (rate limited)")


def query_openalex(scope: dict, licences: list[str]) -> list[dict]:
    flt = [f"title_and_abstract.search:{scope['query']}",
           "best_oa_location.license:" + "|".join(licences),
           "type:article|review|book|book-chapter|report"]
    if scope.get("topic"):
        flt.append(f"primary_topic.id:{scope['topic']}")
    params = {"filter": ",".join(flt), "sort": "cited_by_count:desc",
              "per-page": min(200, int(scope.get("cap", 20)) * 2), "select": _SELECT}
    if os.getenv("OPENALEX_API_KEY"):
        params["api_key"] = os.environ["OPENALEX_API_KEY"]
    if os.getenv("OPENALEX_MAILTO"):
        params["mailto"] = os.environ["OPENALEX_MAILTO"]
    return _get_json(OPENALEX, params).get("results", [])


def lookup_pmc(doi: str) -> dict | None:
    if not doi:
        return None
    try:
        data = _get_json(EUROPE_PMC_SEARCH, {"query": f'DOI:"{doi}"', "format": "json",
                                             "resultType": "lite"})
        hits = (data.get("resultList") or {}).get("result") or []
        return hits[0] if hits else None
    except Exception:
        return None


def harvest(scopes: list[dict], licences: list[str], known: set[str],
            query=query_openalex, pmc=lookup_pmc) -> list[dict]:
    candidates = []
    for scope in scopes:
        print(f"  {scope['domain']}: {scope['query'][:70]}", file=sys.stderr, flush=True)
        works = query(scope, licences)
        picked = 0
        for w in works:
            if picked >= int(scope.get("cap", 20)):
                break
            doi = normalize_doi(w.get("doi"))
            if doi and doi in known:
                continue
            url, route = choose_fetch_url(w, pmc(doi))
            if not url:
                continue
            candidates.append({
                "domain": scope["domain"], "category": "PAPER", "doi": doi,
                "year": w.get("publication_year"), "title": w.get("title"),
                "label": citation_label(w), "url": url, "route": route,
                "licence": licence_name((w.get("best_oa_location") or {}).get("license")),
                "cited_by": w.get("cited_by_count", 0), "language": w.get("language"),
            })
            picked += 1
    return dedupe(candidates, known)


def vet_all(candidates: list[dict], workers: int = 4) -> list[dict]:
    from scripts.corpus_report import vet

    def _one(c):
        try:
            row = vet(c["url"], c["title"] or c["label"])
            c.update(verdict=row["verdict"], chunks=row["chunks"], reasons=row["reasons"],
                     vetted_licence=row["licence"])
        except Exception as exc:  # noqa: BLE001 — one bad source must not end the harvest
            c.update(verdict="REJECT", chunks=0, reasons=[f"vet failed: {exc}"])
        return c

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(_one, candidates))


def add_to_urls(ids: list[str], report: list[dict], urls_path: Path = URLS_FILE) -> list[str]:
    """Append the chosen ACCEPTED candidates to urls.txt. Returns the lines written; raises
    on an id that is unknown or was not accepted, before writing anything."""
    by_id = {c["doi"] or c["url"]: c for c in report}
    chosen = []
    for raw in ids:
        doi = normalize_doi(raw)
        c = by_id.get(doi if doi.startswith("10.") else raw.strip())
        if c is None:
            raise SystemExit(f"{raw!r} is not in the last harvest report")
        if c.get("verdict") != "ACCEPT":
            raise SystemExit(f"{raw!r} was {c.get('verdict', 'not vetted')} by the gate; add it "
                             "by hand only after confirming why")
        chosen.append(c)
    lines = [urls_line(c) for c in chosen]
    with urls_path.open("a", encoding="utf-8") as fh:
        fh.write("\n# Harvested (scripts/harvest_sources.py), chosen by hand\n")
        fh.write("\n".join(lines) + "\n")
    return lines


def main() -> int:  # pragma: no cover - CLI
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--domain", help="harvest only this domain from the scope file")
    ap.add_argument("--dry-run", action="store_true", help="query only; skip the vetting fetches")
    ap.add_argument("--add", help="comma-separated ids (DOI or URL) from the last report to "
                                  "append to urls.txt")
    args = ap.parse_args()

    report_json = REPORT_DIR / "candidates.json"
    if args.add:
        report = json.loads(report_json.read_text())["candidates"]
        for line in add_to_urls([i for i in args.add.split(",") if i.strip()], report):
            print(line)
        print("\nNext: re-run the retrieval sweep and save a new baseline (see CORPUS.md).")
        return 0

    scope = json.loads(SCOPE_FILE.read_text())
    scopes = [s for s in scope["scopes"] if not args.domain or s["domain"] == args.domain]
    known = existing_keys(URLS_FILE.read_text(encoding="utf-8"))
    candidates = harvest(scopes, scope["licences"], known)
    if not args.dry_run:
        print(f"vetting {len(candidates)} candidates through the gate...", file=sys.stderr)
        candidates = vet_all(candidates)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_json.write_text(json.dumps({"candidates": candidates}, indent=1, ensure_ascii=False))
    (REPORT_DIR / "candidates.md").write_text(to_markdown(candidates))
    by = {}
    for c in candidates:
        by.setdefault(c.get("verdict", "unvetted"), 0)
        by[c.get("verdict", "unvetted")] += 1
    print(f"{len(candidates)} candidates: {by}. Report: {REPORT_DIR / 'candidates.md'}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
