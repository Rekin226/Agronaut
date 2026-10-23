"""Read bundled guides in bounded messages, without models or account state."""

import re

from .field_guides import guide_catalog, guide_languages, read_field_guide

MESSAGE_LIMIT = 3500  # UTF-16 code units; below Telegram's 4096-unit limit.
USAGE = "Use /guide [topic] [en|fr|zh-Hant] [page], e.g. /guide drip fr 2; /guide fr lists topics."
_WORDS = {
    "en": dict(title="Field guides", note="General guidance; no local field validation.",
        scope="Applicability", sources="Sources", page="Page {page}/{pages}",
        next="Next", catalog="Topics", range="Choose a page from 1 to {pages}.",
        version="English source", unavailable="GUIDE_UNAVAILABLE: guide context exceeds the message limit; use agronaut field-guide."),
    "fr": dict(title="Guides de terrain", note="Conseils généraux ; aucune validation locale sur le terrain.",
        scope="Champ d’application", sources="Sources", page="Page {page}/{pages}",
        next="Suite", catalog="Sujets", range="Choisissez une page de 1 à {pages}.",
        version="Source anglaise", unavailable="GUIDE_UNAVAILABLE : contexte trop long ; utilisez agronaut field-guide."),
    "zh-Hant": dict(title="田間指南", note="一般指引；尚未經在地田間驗證。",
        scope="適用範圍", sources="來源", page="第 {page}/{pages} 頁",
        next="下一頁", catalog="主題", range="請選擇第 1 至 {pages} 頁。",
        version="英文來源", unavailable="GUIDE_UNAVAILABLE：指南背景超過訊息長度限制；請使用 agronaut field-guide。"),
}


def parse_guide_args(arguments):
    parts = arguments.split()
    topic, language, page = None, "en", "1"
    topics = {entry["topic"] for entry in guide_catalog()}
    if parts and parts[0] in topics:
        topic = parts.pop(0)
    if parts and parts[0] in guide_languages():
        language = parts.pop(0)
    if parts and topic:
        page = parts.pop(0)
    if parts or not re.fullmatch(r"[1-9][0-9]{0,5}", page, flags=re.ASCII):
        raise ValueError(USAGE)
    return topic, language, int(page)


def _size(text):
    return len(text.encode("utf-16-le")) // 2


def _chunks(text, budget):
    """Lossless splitting at whitespace where possible, never within a code point."""
    chunks = []
    while text:
        used, end = 0, 0
        for char in text:
            units = 2 if ord(char) > 0xFFFF else 1
            if used + units > budget:
                break
            used += units
            end += 1
        if end < len(text):
            # Prefer paragraph, then line/word boundaries, without tiny fragments.
            for separator in ("\n\n", "\n", " "):
                boundary = text.rfind(separator, 0, end)
                if boundary >= end // 2:
                    end = boundary + len(separator)
                    break
        chunks.append(text[:end])
        text = text[end:]
    return chunks or [""]


def render_guide(arguments=""):
    topic, language, page = parse_guide_args(arguments)
    w = _WORDS[language]
    if topic is None:
        rows = [w["title"], w["note"]]
        if language != "en":
            # Reuse the bundled translation's draft notice; no account or unit needed.
            rows.append(read_field_guide("drip", language)["translation_note"])
        rows.extend(f"{entry['title']}\n/guide {entry['topic']} {language}"
                    for entry in guide_catalog(language))
        result = "\n\n".join(rows)
        if _size(result) > MESSAGE_LIMIT:
            raise ValueError(w["unavailable"])
        return result

    guide = read_field_guide(topic, language)
    context = [guide["title"], w["note"]]
    if guide["translation_note"]:
        context.append(guide["translation_note"])
    context.extend((f"{w['scope']}: {guide['applicability']}",
                    f"{w['sources']}: {guide['source_references']}",
                    f"{w['version']}: {guide['source_sha256'][:12]}"))
    prefix = "\n".join(context) + "\n\n"
    def footer(current, total):
        text = "\n\n" + w["page"].format(page=current, pages=total)
        if current < total:
            text += f"\n{w['next']}: /guide {topic} {language} {current + 1}"
        return text + f"\n{w['catalog']}: /guide {language}"

    budget = MESSAGE_LIMIT - _size(prefix + footer(999998, 999999))
    if budget < 128:
        raise ValueError(w["unavailable"])
    pages = _chunks(guide["content_markdown"], budget)
    if len(pages) > 999999:
        raise ValueError(w["unavailable"])
    if page > len(pages):
        return w["range"].format(pages=len(pages)) + f"\n/guide {topic} {language} 1"
    return prefix + pages[page - 1] + footer(page, len(pages))
