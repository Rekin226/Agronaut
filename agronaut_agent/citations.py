"""Native citations on Claude: knowledge passages in, cited sources out.

The knowledge tool returns passages as text, each under a "[source: ...]" label, and the
prompt asks the model to name the source it used. That works on every provider and is still
the path for all of them except Claude. On Claude the same passages can go in as
`search_result` blocks instead; the API then attaches each cited passage to the sentence that
used it, so which source backs which sentence is recorded by the API, not asserted in prose.

The other direction matters as much. A reply's citations live in its content blocks, and a
WhatsApp or Telegram message is plain text, so they would never reach the grower. `with_sources`
appends the cited sources the reply does not already name. For web results that is not
optional: Anthropic requires showing the original source when an answer drawn from web search
is shown to an end user.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

_LABEL = re.compile(r"(?m)^\[source: (.+?)\]\n")
_CJK = re.compile(r"[一-鿿]")


def passages(text: str) -> list[dict]:
    """[{"source", "text"}] from the knowledge tool's labeled output; [] when it holds none
    (the no-match and unavailable notes carry no label)."""
    parts = _LABEL.split(text or "")
    out = []
    for i in range(1, len(parts) - 1, 2):
        body = parts[i + 1].strip()
        if body:
            out.append({"source": parts[i].strip(), "text": body})
    return out


def search_result_blocks(text: str) -> list[dict] | None:
    """The knowledge tool's output as Claude `search_result` blocks, or None when it holds no
    passages (the caller then sends the text unchanged)."""
    found = passages(text)
    if not found:
        return None
    return [{"type": "search_result", "source": p["source"], "title": display_name(p["source"]),
             "content": [{"type": "text", "text": p["text"]}], "citations": {"enabled": True}}
            for p in found]


def display_name(source: str) -> str:
    """What a grower can read: a curated guide's file path becomes its title words."""
    if source.startswith("knowledge/"):
        stem = PurePosixPath(source).stem.replace("_", " ").replace("-", " ").strip()
        return f"Agronaut guide: {stem}"
    return source


def cited_sources(content) -> list[str]:
    """Distinct sources a reply cites, in order of first use, from its content blocks.

    Knowledge results carry their source label; web search results a title and URL (both are
    shown, since the URL is what lets a reader check it); fetched documents a title."""
    if not isinstance(content, list):
        return []
    seen: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        for c in block.get("citations") or []:
            if not isinstance(c, dict):
                continue
            kind = c.get("type")
            if kind == "search_result_location":
                name = display_name(str(c.get("source") or c.get("title") or ""))
            elif kind == "web_search_result_location":
                title, url = (c.get("title") or "").strip(), (c.get("url") or "").strip()
                name = f"{title} ({url})" if title and url else (url or title)
            else:
                name = str(c.get("document_title") or "")
            if name and name not in seen:
                seen.append(name)
    return seen


def _named(source: str, text: str) -> bool:
    """Whether the reply already names the source, read loosely: its first two words, the way
    a reply names one ("per FAO 589", "the dissolved oxygen guide")."""
    words = re.findall(r"\w+", source.split("(")[0].replace("Agronaut guide:", ""))[:2]
    key = " ".join(words).casefold()
    return bool(key) and key in " ".join(re.findall(r"\w+", text)).casefold()


def with_sources(text: str, sources: list[str], limit: int = 3) -> str:
    """`text` with a short sources line for the cited sources it does not already name."""
    missing = [s for s in sources if not _named(s, text)][:limit]
    if not missing or not text.strip():
        return text
    head = "來源：" if _CJK.search(text) else "Sources: "
    return f"{text.rstrip()}\n\n{head}{'; '.join(missing)}"
