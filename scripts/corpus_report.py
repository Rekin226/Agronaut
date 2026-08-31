"""Corpus verification gate — what does each declared knowledge source ACTUALLY contribute?

A RAG system is only as good as the text it can retrieve, and a source can fail silently in
three different ways: it can refuse the fetch (403/timeout), it can return a page that is all
navigation chrome and no substance (a JS redirect shim is ~11 characters of visible text), or
— the dangerous one — it can return plenty of healthy-looking text about the wrong subject,
because a publication ID silently resolved to a different article.

This script catches all three. For every entry in urls.txt and every knowledge/*.md it reports
the fetch status, the extracted characters, the chunks that survive boilerplate filtering, and
a topic-drift check against the curated LABEL. It re-uses the real loader and the real splitter
from srcs.chatbot, so the chunk counts are the ones the index would actually get — but it stops
short of embedding, so it needs no model and no GPU.

Run it:
    python -m scripts.corpus_report              # table; exits non-zero if a source is dead
    python -m scripts.corpus_report --json       # machine-readable, for CI
    python -m scripts.corpus_report --offline    # local knowledge only, no network
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_text_splitters import RecursiveCharacterTextSplitter  # noqa: E402

from srcs.chatbot import (  # noqa: E402
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    KNOWLEDGE_DIR,
    URL_FILE,
    WEB_LOAD_TIMEOUT,
    _is_boilerplate_text,
    _pdf_documents,
    _probe_url,
    load_web_page,
    parse_urls_file,
)

# Words that carry no topic signal, so their presence in a LABEL proves nothing about whether
# the page we fetched is the page we meant to cite.
_STOPWORDS = {
    "the", "and", "for", "with", "from", "into", "a", "an", "of", "in", "on", "to", "by",
    "guide", "manual", "handbook", "publication", "report", "paper", "study", "edition",
    "volume", "part", "vol", "no", "pp", "university", "extension", "press", "journal",
}


def _tokens(text: str) -> set[str]:
    """Topic-bearing words: lowercase, alphanumeric, >3 chars, not a stopword."""
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower())
            if len(w) > 3 and w not in _STOPWORDS}


_CC_RE = re.compile(r"creativecommons\.org/licenses/([a-z\-]+)/([0-9.]+)")


_LICENCE_PHRASES = [
    (re.compile(r"CC[ \-]?BY[\- ]NC[\- ]SA[\- ]?([0-9.]+)?\s*(IGO)?", re.I), "CC BY-NC-SA"),
    (re.compile(r"CC[ \-]?BY[\- ]NC[\- ]?([0-9.]+)?\s*(IGO)?", re.I), "CC BY-NC"),
    (re.compile(r"CC[ \-]?BY[\- ]SA[\- ]?([0-9.]+)?", re.I), "CC BY-SA"),
    (re.compile(r"CC[ \-]?BY[\- ]?([0-9.]+)", re.I), "CC BY"),
    (re.compile(r"creative\s+commons", re.I), "Creative Commons (unspecified)"),
    (re.compile(r"public\s+domain", re.I), "Public domain"),
    # FAO publications from before their Creative Commons adoption carry a bespoke permissive
    # grant instead of a CC licence. It is genuinely open for educational/non-commercial reuse
    # with attribution, so treating it as "unlicensed" would wrongly exclude FAO 589 — the single
    # most authoritative small-scale aquaponics reference there is.
    (re.compile(r"reproduction and dissemination of material in this information product",
                re.I), "FAO permissive (educational/non-commercial, attribution)"),
]


def detect_licence_in_text(text: str) -> str:
    """Licence declared in a PDF's own text. A PDF carries no HTML licence link, so without this
    every PDF looks unlicensed — which would falsely reject FAO's entire catalogue, exactly the
    open-licence material the corpus most wants."""
    # Whitespace is normalised first: PDF text extraction wraps lines mid-sentence, so a licence
    # phrase is routinely split by a newline and no amount of widening the window will match it.
    # Wide enough to reach the imprint page of a full-length publication too — in FAO 589 the
    # rights statement sits on page 4, past 6 000 characters of front matter.
    head = " ".join((text or "")[:30000].split())
    for rx, name in _LICENCE_PHRASES:
        m = rx.search(head)
        if m:
            ver = next((g for g in (m.groups() or ()) if g and g[0].isdigit()), "")
            igo = " IGO" if "igo" in m.group(0).lower() else ""
            return f"{name} {ver}{igo}".strip()
    return ""


def detect_licence(html: str) -> str:
    """The Creative Commons licence a page declares, or "" if it declares none.

    Agronaut is a Digital Public Good, so the corpus can only carry openly licensed text. In
    practice this doubles as a retrievability signal: every source measured so far that returned
    usable full text was CC BY, and every paywalled one returned nothing.
    """
    found = sorted(set(_CC_RE.findall(html or "")))
    return ", ".join(f"CC {a.upper()} {b}" for a, b in found)


def _fetch_http_metadata(url: str) -> dict:
    """Status, final URL and <title> — the signals load_web_page throws away but that we need
    to tell 'fetched the wrong article' apart from 'fetched the right one'."""
    try:
        import requests
        r = requests.get(url, timeout=WEB_LOAD_TIMEOUT, allow_redirects=True,
                         headers={"User-Agent": "Mozilla/5.0 (compatible; AgronautCorpusReport/1.0)"})
        title = ""
        ctype = r.headers.get("Content-Type", "")
        if "html" in ctype.lower():
            m = re.search(r"<title[^>]*>(.*?)</title>", r.text, re.S | re.I)
            if m:
                title = re.sub(r"\s+", " ", m.group(1)).strip()[:200]
        licence = detect_licence(r.text) if "html" in ctype.lower() else ""
        return {"status": str(r.status_code), "final_url": r.url, "title": title,
                "content_type": ctype.split(";")[0], "licence": licence}
    except Exception as exc:
        return {"status": f"{type(exc).__name__}", "final_url": "", "title": "",
                "content_type": "", "licence": ""}


def _pdf_title(content: bytes) -> str:
    """The /Title (or /Subject) a PDF declares in its metadata."""
    try:
        import io

        from pypdf import PdfReader
        info = PdfReader(io.BytesIO(content)).metadata or {}
        return str(info.get("/Title") or info.get("/Subject") or "")[:200]
    except Exception:  # noqa: BLE001
        return ""


def _split(documents) -> int:
    """Chunks that survive the boilerplate filter — mirrors build_vector_store's pipeline
    minus the embedding step."""
    if not documents:
        return 0
    splitter = RecursiveCharacterTextSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)
    return sum(1 for d in splitter.split_documents(documents)
               if not _is_boilerplate_text(getattr(d, "page_content", "")))


def _drift_in_text(label: str, text: str) -> str:
    """Topic check for a PDF, run against its opening text instead of an HTML <title>."""
    want = _tokens(label)
    if not want:
        return "unlabeled"
    have = _tokens((text or "")[:4000])
    if not have:
        return ""
    return "" if (want & have) else "DRIFT"


# Bot-protection interstitials. Their <title> describes the challenge, not the document, so
# treating one as the page's identity produces a spurious "wrong topic" verdict on a source whose
# content fetch actually succeeded.
_CHALLENGE_TITLES = ("client challenge", "just a moment", "attention required",
                     "access denied", "are you a robot", "checking your browser")


def _is_challenge_title(title: str) -> bool:
    t = (title or "").strip().lower()
    return any(c in t for c in _CHALLENGE_TITLES)


def _drift(label: str, meta: dict, body: str = "") -> str:
    """'' when the fetched page plausibly IS the cited work, else why we doubt it.

    An unlabeled URL cannot be checked — that is itself worth reporting, because it means
    nothing would catch the page silently becoming a different article.
    """
    want = _tokens(label)
    if not want:
        return "unlabeled"
    title = meta.get("title", "")
    haystack = _tokens(meta.get("final_url", ""))
    if not _is_challenge_title(title):
        haystack |= _tokens(title)
    # The retrieved BODY is the strongest evidence of what was actually indexed. A title fetched
    # in a separate request can be a bot challenge while the content request succeeded, so judging
    # on the title alone flags a healthy source as drifted.
    haystack |= _tokens(body[:4000])
    if not haystack:
        return ""            # nothing to compare against; the chunk count is the real verdict
    return "" if (want & haystack) else "DRIFT"


def _audit_url(entry: dict) -> dict:
    url, label = entry["url"], entry.get("label", "")
    # HTML metadata (title/licence) is only meaningful for HTML, and fetching it for a PDF means
    # downloading a 262-page publication a second time purely to look for a <title> it does not
    # have. Probe once, then only ask for metadata when it can exist.
    probe = _probe_url(url)
    if probe is not None and probe["is_pdf"]:
        docs = _pdf_documents(probe["content"], url)
        opening = " ".join(d.page_content for d in docs[:12])
        meta = {"status": str(probe["status"]), "final_url": url,
                "title": _pdf_title(probe["content"]),
                "content_type": probe["content_type"],
                "licence": detect_licence_in_text(opening)}
        chars = sum(len(d.page_content) for d in docs)
        return {
            "kind": "url", "source": url, "label": label,
            "category": entry.get("category", ""), "status": meta["status"],
            "final_url": url, "title": meta["title"], "content_type": meta["content_type"],
            "chars": chars, "chunks": _split(docs),
            "drift": _drift_in_text(label, opening) if label else "unlabeled",
            "licence": meta["licence"] or entry.get("licence", ""),
        }
    else:
        meta = _fetch_http_metadata(url)
        docs = load_web_page(url)
    chars = sum(len(getattr(d, "page_content", "")) for d in docs)
    chunks = _split(docs)
    body = " ".join(getattr(d, "page_content", "") for d in docs[:3])
    return {
        "kind": "url", "source": url, "label": label, "category": entry.get("category", ""),
        "status": meta["status"], "final_url": meta["final_url"], "title": meta["title"],
        "content_type": meta["content_type"], "chars": chars, "chunks": chunks,
        "drift": _drift(label, meta, body), "licence": meta.get("licence", "") or entry.get("licence", ""),
    }


def _audit_local(path: Path) -> dict:
    from langchain_community.document_loaders import TextLoader
    docs = TextLoader(str(path), encoding="utf-8").load()
    return {
        "kind": "local", "source": str(path.relative_to(Path(KNOWLEDGE_DIR).parent)),
        "label": "", "category": "LOCAL", "status": "200", "final_url": "", "title": "",
        "content_type": "text/markdown", "licence": "first-party",
        "chars": sum(len(d.page_content) for d in docs), "chunks": _split(docs), "drift": "",
    }


def run(offline: bool = False) -> dict:
    rows = [_audit_local(p) for p in sorted(Path(KNOWLEDGE_DIR).glob("*.md"))]
    entries = parse_urls_file(URL_FILE)
    if offline:
        entries = []
    if entries:
        with ThreadPoolExecutor(max_workers=6) as pool:
            rows.extend(pool.map(_audit_url, entries))

    dead = [r for r in rows if r["chunks"] == 0]
    drifted = [r for r in rows if r["drift"] == "DRIFT"]
    return {
        "rows": rows,
        "totals": {
            "sources": len(rows),
            "local_files": sum(1 for r in rows if r["kind"] == "local"),
            "urls": sum(1 for r in rows if r["kind"] == "url"),
            "chunks": sum(r["chunks"] for r in rows),
            "chunks_local": sum(r["chunks"] for r in rows if r["kind"] == "local"),
            "chunks_web": sum(r["chunks"] for r in rows if r["kind"] == "url"),
        },
        "dead": [r["source"] for r in dead],
        "drifted": [r["source"] for r in drifted],
        "ok": not dead and not drifted,
    }


def _print_table(report: dict) -> None:
    rows, tot = report["rows"], report["totals"]
    width = min(58, max((len(r["source"]) for r in rows), default=20))
    print(f"{'SOURCE':<{width}}  {'STATUS':>10}  {'CHARS':>8}  {'CHUNKS':>6}  NOTE")
    print("-" * (width + 40))
    for r in sorted(rows, key=lambda r: (r["kind"], r["source"])):
        src = r["source"]
        if len(src) > width:
            src = "…" + src[-(width - 1):]
        note = ""
        if r["drift"] == "DRIFT":
            note = f"WRONG TOPIC? title={r['title'][:44]!r}"
        elif r["drift"] == "unlabeled":
            note = "no LABEL — drift undetectable"
        elif r["chunks"] == 0:
            note = "contributes nothing"
        print(f"{src:<{width}}  {r['status']:>10}  {r['chars']:>8}  {r['chunks']:>6}  {note}")

    print("-" * (width + 40))
    print(f"{tot['local_files']} local files -> {tot['chunks_local']} chunks   "
          f"{tot['urls']} URLs -> {tot['chunks_web']} chunks   "
          f"TOTAL {tot['chunks']} chunks")
    if report["dead"]:
        print(f"\nDEAD ({len(report['dead'])}) — declared but contribute 0 chunks:")
        for s in report["dead"]:
            print(f"  - {s}")
    if report["drifted"]:
        print(f"\nTOPIC DRIFT ({len(report['drifted'])}) — fetched page may not be the cited work:")
        for s in report["drifted"]:
            print(f"  - {s}")


def vet(url: str, label: str = "") -> dict:
    """Vet ONE proposed source before it is allowed into urls.txt.

    Adding sources by hand is how a corpus rots: a publication ID silently resolves to a different
    article, a repository turns out to be JS-driven and serves 300 characters of navigation, or the
    text is real but not openly licensed. This answers all four questions at once — reachable,
    substantial, on-topic, openly licensed — so a source is only added on evidence.
    """
    row = _audit_url({"url": url, "label": label, "category": "CANDIDATE", "licence": ""})
    verdict, reasons = "ACCEPT", []
    if not row["status"].isdigit() or not (200 <= int(row["status"]) < 300):
        verdict, reasons = "REJECT", reasons + [f"not reachable (status {row['status']})"]
    if row["chunks"] == 0:
        verdict, reasons = "REJECT", reasons + ["contributes 0 chunks after filtering"]
    elif row["chunks"] < 3:
        verdict = "REVIEW" if verdict == "ACCEPT" else verdict
        reasons.append(f"only {row['chunks']} chunks - likely a landing page, not full text")
    if row["drift"] == "DRIFT":
        verdict, reasons = "REJECT", reasons + [
            f"topic drift: fetched title is {row['title'][:60]!r}, not {label!r}"]
    lic = row["licence"]
    if not lic:
        verdict = "REVIEW" if verdict == "ACCEPT" else verdict
        reasons.append("no open licence detected - confirm terms before indexing")
    elif not lic.startswith("CC BY") and "Public domain" not in lic:
        verdict = "REVIEW" if verdict == "ACCEPT" else verdict
        reasons.append(f"licence is {lic!r} - permissive but not Creative Commons; confirm it is "
                       "compatible with redistributing Agronaut as a Digital Public Good")
    row.update(verdict=verdict, reasons=reasons)
    return row


def _print_vet(row: dict) -> None:
    print(f"\nCANDIDATE  {row['source']}")
    print(f"  status      {row['status']}   content-type {row['content_type'] or 'n/a'}")
    print(f"  resolves to {row['final_url'][:100] or 'n/a'}")
    print(f"  title       {row['title'][:100] or 'n/a'}")
    print(f"  extracted   {row['chars']} chars -> {row['chunks']} chunks")
    print(f"  licence     {row['licence'] or 'NOT DETECTED'}")
    print(f"\n  VERDICT: {row['verdict']}")
    for r in row["reasons"]:
        print(f"    - {r}")
    if row["verdict"] == "ACCEPT":
        lic = row["licence"] or "CONFIRM-LICENCE"
        print(f"\n  add to urls.txt as:\n    CATEGORY|{row['source']}|{row['label'] or 'LABEL'}|{lic}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="machine-readable output for CI")
    ap.add_argument("--offline", action="store_true", help="skip URLs; audit knowledge/ only")
    ap.add_argument("--candidate", metavar="URL",
                    help="vet a PROPOSED source without adding it to the corpus")
    ap.add_argument("--label", default="", help="expected topic of --candidate, for drift checking")
    args = ap.parse_args()

    if args.candidate:
        row = vet(args.candidate, args.label)
        if args.json:
            print(json.dumps(row, indent=2))
        else:
            _print_vet(row)
        return 0 if row["verdict"] == "ACCEPT" else 1

    report = run(offline=args.offline)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        _print_table(report)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
