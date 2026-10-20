"""Bundled field guidance, readable without a model, index, account or network."""

from __future__ import annotations

import json
import re
from hashlib import sha256

from .paths import corpus_root

# Explicit topic IDs keep a caller's input out of filesystem paths.
_TOPICS = {
    "water-needs": ("field_water_needs", "Understanding crop water needs"),
    "choose-method": ("field_choose_method", "Choosing an irrigation method"),
    "drip": ("field_drip", "Checking a drip system"),
    "sprinkler": ("field_sprinkler", "Checking sprinkler distribution"),
    "surface": ("field_surface", "Observing a surface irrigation event"),
    "crop-scouting": ("field_crop_scouting", "Investigating crop symptoms"),
    "soil-sampling": ("field_soil_sampling", "Preparing a soil sample and laboratory record"),
}
_HEADERS = {"Evidence": "evidence_status", "Applicability": "applicability",
            "Sources": "source_references"}
_LANGUAGES = {"en": "English", "fr": "Français", "zh-Hant": "繁體中文"}
_TRANSLATION_TEXT = {
    "fr": ("Traduction provisoire du guide Agronaut en anglais. La langue et les termes agronomiques "
           "restent à relire localement ; ce texte ne constitue pas une validation pour une région.",
           "Champ d’application", "Sources du guide anglais (liens en anglais)", "Guides de terrain", "Lire un guide"),
    "zh-Hant": ("本指南為 Agronaut 英文指南的翻譯草稿，尚待在地語言及農業專業審閱；不代表已在特定地區驗證。",
                "適用範圍", "英文指南的參考來源（英文連結）", "田間指南", "閱讀指南"),
}


def guide_languages() -> dict[str, str]:
    return dict(_LANGUAGES)


def _language(language):
    if not isinstance(language, str) or language not in _LANGUAGES:
        raise ValueError("unknown field-guide language; choose " + ", ".join(_LANGUAGES))
    return language


def guide_metadata(text: str) -> dict[str, str]:
    """Optional single-line guide context, inherited by every retrieval chunk."""
    result = {}
    for label, key in _HEADERS.items():
        match = re.search(rf"^_{label}: (.+)_$", text, re.MULTILINE)
        if match:
            result[key] = match.group(1).strip()
    return result


def guide_catalog(language="en") -> list[dict]:
    _language(language)
    if language != "en":
        return [{"topic": topic, "title": _translation(topic, language)["title"],
                 "evidence_status": "guidance", "language": language, "translation_status": "draft"}
                for topic in _TOPICS]
    return [{"topic": topic, "title": title, "evidence_status": "guidance"}
            for topic, (_, title) in _TOPICS.items()]


def _translation(topic, language):
    # Translations are read directly, outside the English retrieval corpus. These
    # code-owned names cannot be changed into paths by a topic or language argument.
    path = corpus_root() / "field_guides" / f"{language}.json"
    try:
        bundle = json.loads(path.read_text(encoding="utf-8"))
        translated = bundle["guides"][topic]
        valid = (type(bundle["schema_version"]) is int and bundle["schema_version"] == 1
                 and bundle["language"] == language and bundle["translation_status"] == "draft"
                 and all(isinstance(translated[key], str) and translated[key].strip()
                         for key in ("title", "applicability", "body_markdown", "source_sha256")))
        if not valid:
            raise ValueError("invalid translation contract")
    except (OSError, UnicodeError, ValueError, KeyError, TypeError) as exc:
        raise ValueError(f"bundled {language} guide {topic!r} is unavailable or incomplete; check the corpus installation") from exc
    return translated


def read_field_guide(topic: str, language: str = "en") -> dict:
    _language(language)
    if not isinstance(topic, str) or topic not in _TOPICS:
        raise ValueError("unknown field-guide topic; choose " + ", ".join(_TOPICS))
    filename, title = _TOPICS[topic]
    path = corpus_root() / "knowledge" / f"{filename}.md"
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"bundled guide {topic!r} is unavailable; check the corpus installation") from exc
    metadata = guide_metadata(content)
    if metadata.get("evidence_status") != "guidance" or not all(
        metadata.get(key) for key in _HEADERS.values()
    ):
        raise ValueError(f"bundled guide {topic!r} is missing its evidence or source context")
    digest = sha256(content.encode("utf-8")).hexdigest()
    result = {"schema_version": 1, "topic": topic, "title": title,
              **metadata, "content_markdown": content, "language": language,
              "source_language": "en", "source_sha256": digest,
              "translation_status": "original", "translation_note": None}
    if language == "en":
        return result
    translated = _translation(topic, language)
    if translated["source_sha256"] != digest:
        raise ValueError(f"bundled {language} guide {topic!r} is out of date; review its translation against the current English source")
    note, scope_label, sources_label, _, _ = _TRANSLATION_TEXT[language]
    content = (f"# {translated['title']}\n\n{note}\n\n"
               f"**{scope_label}**: {translated['applicability']}\n\n"
               f"{translated['body_markdown'].strip()}\n\n"
               f"## {sources_label}\n\n{metadata['source_references']}\n")
    result.update(title=translated["title"], applicability=translated["applicability"],
                  content_markdown=content, translation_status="draft", translation_note=note)
    return result


def _command(args) -> int:
    try:
        result = read_field_guide(args.topic, args.language) if args.topic else guide_catalog(args.language)
    except ValueError as exc:
        error = {"status": "GUIDE_UNAVAILABLE", "error": str(exc)}
        print(json.dumps(error) if args.json else f"GUIDE_UNAVAILABLE: {exc}")
        return 2
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    elif args.topic:
        print(result["content_markdown"])
    else:
        print("Field guides — general guidance; no local field validation" if args.language == "en"
              else _TRANSLATION_TEXT[args.language][3]+" — "+_TRANSLATION_TEXT[args.language][0])
        for guide in result:
            print(f"  {guide['topic']}: {guide['title']}")
        label = "Read one with" if args.language == "en" else _TRANSLATION_TEXT[args.language][4]
        print(f"{label}: agronaut field-guide --language {args.language} --topic TOPIC")
    return 0


def add_guide_command(subparsers):
    parser = subparsers.add_parser("field-guide", help="read bundled field guidance offline")
    parser.add_argument("--topic", choices=list(_TOPICS), help="omit to list topics")
    parser.add_argument("--language", choices=list(_LANGUAGES), default="en", help="guide language; default en")
    parser.add_argument("--json", action="store_true", help="return structured guidance")
    parser.set_defaults(func=_command)
