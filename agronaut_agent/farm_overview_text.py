"""Bounded, paginated messages from the owned overview; no model or saved cursor."""

import re

LANGUAGES = ("en", "fr", "zh-Hant")
USAGE = "Use /overview [en|fr|zh-Hant] [page], for example /overview fr 2."
_WORDS = {
    "en": dict(title="Farm overview", page="Page {page}/{pages}",
        note="Recorded crop work and equipment reservations, not recommendations. Elapsed reservations do not prove completed work. No tasks does not mean no work is needed. Pages reflect current records.",
        empty="No farms recorded.", no_units="No production units recorded.", no_cycles="No crop cycles recorded; current growing activity is unknown.",
        unknown="unknown", soil="Soil cultivation", hydroponics="Hydroponics", aquaponics="Aquaponics",
        timezone="Timezone missing or invalid; task timing is unknown.",
        zone="Zone of", cycle="Crop", source="Source", window="Window", review="Review required",
        trigger="Record prompting the task", trigger_reason="Operator's reason",
        counts="Planned {planned}; due {due}; overdue {overdue}; review flags {needs_review}.",
        unclassified="{count} tasks cannot be classified without the farm timezone.",
        details="Full details: Farm overview / Crop records / Farm resources in the app, or agronaut farm overview.",
        equipment="Equipment reservations", equipment_counts="Active {active}; today {today}; review flags {needs_review}.",
        ongoing="interval in progress", elapsed="interval elapsed", booking="Booking", interval="Reserved interval",
        equipment_changed="Equipment record changed", unavailable_equipment="Equipment unavailable",
        timezone_changed="Booking timezone changed", plan_changed="Plan evidence changed",
        range="Choose a page from 1 to {pages}.", next="Next: {command}",
        open="open", closed="closed", future_dated="future-dated", date_unknown="date unknown",
        due="due", overdue="overdue", upcoming="upcoming", completed="completed", cancelled="cancelled", planned="planned"),
    "fr": dict(title="Vue des exploitations", page="Page {page}/{pages}",
        note="Travaux culturaux et réservations de matériel enregistrés, pas des recommandations. Une réservation écoulée ne prouve pas un travail effectué. Aucun travail enregistré ne signifie pas qu’il n’y a rien à faire. Chaque page utilise les données actuelles.",
        empty="Aucune exploitation enregistrée.", no_units="Aucune unité de production enregistrée.", no_cycles="Aucun cycle cultural enregistré ; l’activité actuelle est inconnue.",
        unknown="inconnu", soil="Culture en sol", hydroponics="Hydroponie", aquaponics="Aquaponie",
        timezone="Fuseau horaire absent ou invalide ; échéances inconnues.",
        zone="Zone de", cycle="Culture", source="Source", window="Période", review="À vérifier",
        trigger="Enregistrement ayant motivé la tâche", trigger_reason="Motif de l’opérateur",
        counts="Prévus {planned} ; à faire {due} ; en retard {overdue} ; à vérifier {needs_review}.",
        unclassified="{count} travaux ne peuvent pas être classés sans le fuseau horaire.",
        details="Détails : Farm overview / Crop records / Farm resources dans l’application, ou agronaut farm overview.",
        equipment="Réservations de matériel", equipment_counts="Actives {active} ; aujourd’hui {today} ; à vérifier {needs_review}.",
        ongoing="créneau en cours", elapsed="créneau écoulé", booking="Réservation", interval="Créneau réservé",
        equipment_changed="Fiche du matériel modifiée", unavailable_equipment="Matériel indisponible",
        timezone_changed="Fuseau horaire de la réservation modifié", plan_changed="Données du plan modifiées",
        range="Choisissez une page de 1 à {pages}.", next="Suite : {command}",
        open="ouvert", closed="clos", future_dated="date future", date_unknown="date inconnue",
        due="à faire", overdue="en retard", upcoming="à venir", completed="terminé", cancelled="annulé", planned="prévu"),
    "zh-Hant": dict(title="農場總覽", page="第 {page}/{pages} 頁",
        note="列出已記錄的作物工作與設備預約，不是農業建議。預約時段已過不代表工作已完成。沒有記錄不代表無事可做。每頁反映目前記錄。",
        empty="尚無農場記錄。", no_units="尚無生產單位記錄。", no_cycles="尚無作物週期記錄；目前種植情況不明。",
        unknown="未知", soil="土耕", hydroponics="水耕", aquaponics="魚菜共生",
        timezone="農場時區缺失或無效；無法判定工作期限。",
        zone="所屬田區", cycle="作物", source="來源", window="工作期間", review="需要複核",
        trigger="促使安排此工作的記錄", trigger_reason="操作人員的理由",
        counts="計畫 {planned}；到期 {due}；逾期 {overdue}；需複核 {needs_review}。",
        unclassified="缺少農場時區，無法判定 {count} 項工作的時間狀態。",
        details="完整資料：應用程式的 Farm overview / Crop records / Farm resources，或 agronaut farm overview。",
        equipment="設備預約", equipment_counts="有效預約 {active}；今日 {today}；需複核 {needs_review}。",
        ongoing="預約時段進行中", elapsed="預約時段已過", booking="預約", interval="預約時段",
        equipment_changed="設備記錄已變更", unavailable_equipment="設備不可用",
        timezone_changed="預約時區已變更", plan_changed="計畫依據已變更",
        range="請選擇第 1 至 {pages} 頁。", next="下一頁：{command}",
        open="進行中", closed="已結束", future_dated="未來日期", date_unknown="日期未知",
        due="到期", overdue="逾期", upcoming="尚未到期", completed="已完成", cancelled="已取消", planned="已計畫"),
}


def parse_overview_args(arguments):
    parts = arguments.split()
    language, page = "en", "1"
    if parts and parts[0] in LANGUAGES:
        language = parts.pop(0)
    if parts:
        page = parts.pop(0)
    if parts or not re.fullmatch(r"[1-9][0-9]{0,5}", page, flags=re.ASCII):
        raise ValueError(USAGE)
    return language, int(page)


def _short(value, limit=90):
    # Keep user-entered names in one line; don't let them manufacture a new command line.
    value = " ".join(str(value).encode("utf-8", errors="replace").decode("utf-8").split())
    return value if len(value) <= limit else value[:limit-1] + "…"


def _size(value):
    return len(value.encode("utf-16-le")) // 2


def _equipment_blocks(farm, context, w):
    equipment = farm.get('equipment')
    if not equipment or not equipment['equipment_count'] and not equipment['counts']['active']:
        return []
    counts = dict(equipment['counts'])
    if counts['today'] is None:
        counts['today'] = w['unknown']
    heading = context + '\n' + w['equipment']
    blocks = [heading + '\n' + w['equipment_counts'].format(**counts)]
    for booking in equipment['attention']:
        label = f"{w['booking']}: {_short(booking['title'], 160)} [{_short(booking['booking_id'], 64)}]"
        review = booking['review']
        reasons = [w[key] for key in ('equipment_changed', 'unavailable_equipment', 'timezone_changed') if review[key]]
        if review['plan'] and review['plan']['needs_review']:
            reasons.append(w['plan_changed'])
        if reasons:
            label += '\n' + w['review'] + ': ' + '; '.join(reasons)
        for interval in booking['intervals']:
            start, end = (interval['local_'+key] or interval[key] for key in ('start', 'end'))
            blocks.append(heading + '\n' + label + '\n' + _short(interval['equipment_name'], 90) +
                f" [{_short(interval['equipment_key'], 80)}] · {w[interval['timing']]}" +
                f"\n{w['interval']}: {start} – {end}\n{w['source']}: {_short(booking['source'], 120)}")
    return blocks


def render_overview(report, language="en", page=1):
    if language not in LANGUAGES or type(page) is not int or page < 1:
        raise ValueError(USAGE)
    w = _WORDS[language]
    blocks = []
    for farm in report["farms"]:
        context = f"{_short(farm['farm']['name'])} · {farm['local_date'] or w['unknown']} ({_short(farm['farm']['timezone'] or w['unknown'])})"
        if not farm["units"]:
            blocks.append(context + "\n" + w["no_units"])
        for unit in farm["units"]:
            info = unit["unit"]
            heading = context + "\n" + _short(info["name"]) + f" [{_short(info['id'], 64)}] · " + w.get(info["production_system"], w["unknown"])
            if info.get("parent_unit_id"):
                heading += f" · {w['zone']} {_short(info['parent_name'])}"
            if farm["issue"]:
                heading += "\n" + w["timezone"]
            if not unit["cycles"]:
                blocks.append(heading + "\n" + w["no_cycles"])
            for cycle in unit["cycles"]:
                label = f"{w['cycle']}: {_short(cycle['cycle']['crop'])} [{_short(cycle['cycle']['id'], 64)}] · {cycle['cycle']['planted_on']} · {w[cycle['status']]}"
                summary = w["counts"].format(**cycle["task_counts"]) if cycle["task_counts"] is not None else (
                    w["unclassified"].format(count=cycle["unclassified_task_count"]))
                blocks.append(heading + "\n" + label + "\n" + summary)
                for task in cycle["attention"]:
                    window = task["effective_window"]
                    timing = w.get(task["timing"], w["unknown"])
                    if task["needs_review"]:
                        timing += " · " + w["review"]
                    trigger_text = ''
                    if trigger := task.get('trigger'):
                        trigger_text = f"\n{w['trigger']}: {_short(trigger['kind'])}:{_short(trigger['id'], 64)}"
                        if task['trigger_review']['needs_review']:
                            trigger_text += ' · '+w['review']
                        trigger_text += f"\n{w['trigger_reason']}: {_short(trigger['reason'], 160)}"
                    blocks.append(heading + "\n" + label + "\n" + _short(task["title"], 160) + f" [{_short(task['record_id'], 64)}] · " + timing +
                        f"\n{w['window']}: " + (f"{window['start']} – {window['end']}" if window else w["unknown"]) +
                        f"\n{w['source']}: {_short(task['source'], 120)}" + trigger_text)
        blocks.extend(_equipment_blocks(farm, context, w))
    if not blocks:
        blocks = [w["empty"]]
    pages, current = [], ""
    for block in blocks:
        # The fields above are individually bounded; never split a task from its context.
        if current and _size(current + "\n\n" + block) > 2400:
            pages.append(current)
            current = ""
        current = current + "\n\n" + block if current else block
    pages.append(current)
    if page > len(pages):
        return w["range"].format(pages=len(pages)) + f"\n/overview {language} 1"
    result = w["title"] + " · " + w["page"].format(page=page, pages=len(pages)) + "\n" + w["note"]
    result += "\n\n" + pages[page-1] + "\n\n" + w["details"]
    if page < len(pages):
        result += "\n" + w["next"].format(command=f"/overview {language} {page+1}")
    return result
