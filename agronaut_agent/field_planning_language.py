"""Draft question translations matched against their exact English templates."""

import json
import re
from functools import lru_cache
from string import Formatter

from .paths import corpus_root

LANGUAGES = ("en", "fr", "zh-Hant")
_WORDS = {
    "fr": dict(title="Planification du champ", note="Traduction provisoire : langue et agronomie à relire localement. Rapports, détails techniques et erreurs de validation restent en anglais. Utilisez un point décimal.",
        footer="/fieldplan fr reprend ; /back modifie la réponse précédente ; /cancel abandonne le brouillon.",
        saved="Calcul enregistré : {id}. Aucun travail d’irrigation ou d’équipement n’a été enregistré.",
        cancelled="Brouillon de planification annulé pour cette unité. Les calculs et relevés d’eau enregistrés sont conservés.",
        media="Répondez par votre propre texte ; des observations générées à partir d’une image ne peuvent pas remplir ce calcul.",
        select="Sélectionnez une unité de sol avec /units ou créez-en une avec /newunit soil avant /fieldplan.",
        conflict="Terminez ou annulez la création de l’unité avant /fieldplan.", planted="planté", saved_label="enregistré",
        no_budgets="Aucun bilan listé ; saisissez un autre identifiant enregistré ou /back pour choisir manual."),
    "zh-Hant": dict(title="田間規劃", note="翻譯草稿：尚待在地語言與農業專業審閱。計算報告、技術細節及驗證錯誤仍為英文。數值請使用小數點。",
        footer="/fieldplan zh-Hant 繼續；/back 修改上一個回答；/cancel 放棄草稿。",
        saved="已儲存計算：{id}。未記錄任何已完成灌溉或設備作業。",
        cancelled="已取消此單位的規劃草稿。已儲存計算與水量紀錄仍保留。",
        media="請以自己的文字回答；影像生成的觀察不能代填此計算。",
        select="請先以 /units 選擇土耕單位，或用 /newunit soil 建立，再使用 /fieldplan。",
        conflict="請先完成或取消單位建立流程，再使用 /fieldplan。", planted="種植", saved_label="儲存",
        no_budgets="沒有列出的水分收支；可輸入其他已儲存 ID，或用 /back 選 manual。"),
}


def validate_language(language):
    if language is not None and (not isinstance(language, str) or language not in LANGUAGES):
        raise ValueError("Use /fieldplan en, /fieldplan fr or /fieldplan zh-Hant.")
    return language


def words(language):
    return _WORDS[language]


def _parts(template):
    result, fields = [], set()
    for literal, field, spec, conversion in Formatter().parse(template):
        result.append(re.escape(literal))
        if field is not None:
            if not re.fullmatch(r"[a-z_]+", field) or spec or conversion:
                raise ValueError("unsupported translation placeholder")
            result.append(f"(?P={field})" if field in fields else f"(?P<{field}>.+?)")
            fields.add(field)
    return "".join(result), fields


@lru_cache(maxsize=4)
def _catalog(path, mtime, size):
    bundle = json.loads(path.read_text(encoding="utf-8"))
    if type(bundle["schema_version"]) is not int or bundle["schema_version"] != 1 or bundle["translation_status"] != "draft":
        raise ValueError("invalid planning translation contract")
    result, seen = [], set()
    for row in bundle["entries"]:
        if row["english"] in seen:
            raise ValueError("duplicate English template")
        seen.add(row["english"])
        pattern, fields = _parts(row["english"])
        if not set(row["translate_slots"]) <= fields:
            raise ValueError("unknown translated slot")
        for language in LANGUAGES[1:]:
            if not row[language].strip() or _parts(row[language])[1] != fields:
                raise ValueError("translation must preserve every placeholder")
        result.append((re.compile(pattern, re.DOTALL), row))
    # Exact and more specific templates precede general composition rules.
    return sorted(result, key=lambda item: len(re.sub(r"\{[^}]+\}", "", item[1]["english"])), reverse=True)


def translate(question, language, draft=None, depth=0):
    if language == "en":
        return question
    validate_language(language)
    try:
        path = corpus_root() / "field_guides" / "planning_questions.json"
        stamp = path.stat()
        if depth > 5:
            raise ValueError("translation composition too deep")
        for pattern, row in _catalog(path, stamp.st_mtime_ns, stamp.st_size):
            match = pattern.fullmatch(question)
            if match is None:
                continue
            values = match.groupdict()
            for key in row["translate_slots"]:
                values[key] = translate(values[key], language, draft, depth + 1)
            if draft is not None and "cycles" in values:
                values["cycles"] = "\n".join(f"{i}. {c['crop']} · {_WORDS[language]['planted']} {c['planted_on']} · {c['id']}"
                    for i, c in enumerate(draft["cycles"], 1))
            if draft is not None and "budgets" in values:
                budgets = [r for r in draft.get("water_budgets", []) if r["cycle_id"] == draft["data"].get("cycle_id")]
                values["budgets"] = "\n".join(f"{i}. {r['id']} · {r['crop']} · {r['area_m2']:g} m² · {_WORDS[language]['saved_label']} {r['created_at']}"
                    for i, r in enumerate(budgets, 1)) or _WORDS[language]["no_budgets"]
            return row[language].format_map(values)
        raise ValueError("English question has no matching translation")
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ValueError(f"FIELD_TRANSLATION_UNAVAILABLE ({language}): {exc}. Resume with /fieldplan en.") from exc


def normalize_answer(draft, key, answer):
    """Translate only explicit choice tokens; preserve free text and source evidence."""
    if draft.get("language", "en") == "en" or not isinstance(answer, str):
        return answer
    aliases = {}
    if key in ("depletion", "assessment.root_zone_deficit_mm"):
        aliases = {"inconnu": "unknown", "inconnue": "unknown", "未知": "unknown"}
    elif key in ("emitter.pressure_compensating", "event.surface_drained"):
        aliases = {"oui": "yes", "是": "yes"}
    elif key in ("weather_input", "application_input"):
        aliases = {"manuel": "manual", "手動": "manual"}
    elif key == "target_accepted":
        aliases = {"utiliser": "use", "採用": "use"}
    return aliases.get(answer.strip().lower(), answer)
