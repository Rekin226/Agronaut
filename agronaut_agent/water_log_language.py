"""Draft localized prompts for recording completed daily water totals."""

from .scouting_language import LANGUAGES, scouting_error
from .scouting_language import TEXT as SCOUTING_TEXT

TEXT = {
    'en': dict(title='Record daily water totals',
        cycle_id='Choose a crop cycle number or ID below, or none for a unit record without a crop cycle.',
        day='Which completed day? Use YYYY-MM-DD in {timezone}; today is not complete.',
        rainfall_mm='Total rainfall in mm for that day? Enter an explicit number; unknown is not zero.',
        net_irrigation_mm='Net irrigation reaching the root zone, in mm for that day? Do not enter pump volume, runtime, or gross application depth. If unknown, /cancel and use /measure to keep the original readings.',
        runoff_mm='Rainfall runoff in mm for that day (not irrigation runoff)? Enter an explicit number from 0 through the rainfall total; unknown is not zero.',
        source='Give the source and how these whole-day totals were measured, reported or estimated, including the basis for net irrigation.',
        provenance='Choose measured, reported or estimated for these totals. Use estimated if any total is estimated, otherwise reported if any total is reported; source notes should distinguish the components.',
        correction='Daily totals already exist, shown below. Reply replace only to correct the whole record, or /cancel to keep it.',
        reason='Explain why you are correcting the existing daily totals.',
        ready='Review the whole-day totals below. Reply save to record them as unverified, /back to edit, or /cancel.',
        footer='/waterlog resumes; /back edits; /cancel discards this draft.',
        saved='Saved daily water record {id}. Quality is unverified. No calculation, soil state or equipment action was changed.',
        cancelled='Daily water draft cancelled.', media='Reply with your own text; image-generated facts cannot fill water totals.',
        stale='Draft changed or ended; resume /waterlog.', changed='Daily totals changed; resume /waterlog and review the latest record.',
        context='Farm timezone or crop details changed; /cancel and restart /waterlog.',
        conflict='Finish or cancel unit setup or another intake for this unit before /waterlog.',
        select='Select a soil production unit before /waterlog.',
        invalid='Use a finite nonnegative number in mm, with a dot for decimals and no thousands separator. Unknown is not zero; /cancel if the total is unavailable.',
        incomplete='Finish the questions and review before saving.',
        date_error='Use a completed local day, within the crop cycle if selected.',
        labels=('Crop cycle','Day','Rainfall (mm)','Net irrigation (mm)','Rainfall runoff (mm)','Source','Provenance','Correction reason'),
        prior='Existing record to replace:', none='No crop cycle', quality='Quality: unverified'),
    'fr': dict(title='Enregistrer les totaux d’eau journaliers',
        cycle_id='Choisissez un numéro ou identifiant de cycle ci-dessous, ou aucun pour une unité sans cycle associé.',
        day='Quelle journée terminée ? Format AAAA-MM-JJ, fuseau {timezone} ; aujourd’hui n’est pas terminé.',
        rainfall_mm='Pluie totale de cette journée en mm ? Saisissez un nombre explicite ; inconnu ne signifie pas zéro.',
        net_irrigation_mm='Irrigation nette atteignant la zone racinaire, en mm pour cette journée ? Ne saisissez ni volume pompé, ni durée, ni hauteur brute appliquée. Si elle est inconnue, /cancel puis /measure pour conserver les relevés originaux.',
        runoff_mm='Ruissellement de la pluie en mm (hors ruissellement d’irrigation) ? Nombre entre zéro et la pluie totale ; inconnu ne signifie pas zéro.',
        source='Indiquez la source et la méthode de mesure, de déclaration ou d’estimation des totaux journaliers, notamment de l’irrigation nette.',
        provenance='Choisissez mesuré, rapporté ou estimé. Choisissez estimé si un total est estimé, sinon rapporté si un total est rapporté ; distinguez les composantes dans la source.',
        correction='Des totaux existent déjà ci-dessous. Répondez remplacer uniquement pour corriger l’ensemble, ou /cancel pour les conserver.',
        reason='Pourquoi corrigez-vous les totaux existants ?',
        ready='Vérifiez les totaux journaliers. Répondez enregistrer pour les conserver comme non vérifiés, /back pour modifier, ou /cancel.',
        footer='/waterlog reprend ; /back modifie ; /cancel abandonne ce brouillon.',
        saved='Relevé journalier enregistré : {id}. Qualité : non vérifiée. Aucun calcul, état du sol ou équipement n’a été modifié.',
        cancelled='Brouillon des totaux d’eau annulé.', media='Répondez avec votre propre texte ; les informations générées à partir d’une image ne peuvent pas remplir les totaux.',
        stale='Le brouillon a changé ou a été terminé ; reprenez /waterlog.', changed='Les totaux ont changé ; reprenez /waterlog et vérifiez le dernier relevé.',
        context='Le fuseau horaire ou les détails de culture ont changé ; /cancel puis recommencez /waterlog.',
        conflict='Terminez ou annulez la création de l’unité ou une autre saisie avant /waterlog.',
        select='Sélectionnez une unité cultivée en sol avant /waterlog.',
        invalid='Saisissez un nombre fini non négatif en mm, avec un point décimal et sans séparateur de milliers. Inconnu ne signifie pas zéro ; /cancel si le total est indisponible.',
        incomplete='Terminez les questions et vérifiez avant d’enregistrer.',
        date_error='Utilisez une journée locale terminée, dans le cycle de culture s’il est sélectionné.',
        labels=('Cycle','Jour','Pluie (mm)','Irrigation nette (mm)','Ruissellement de pluie (mm)','Source','Provenance','Motif de correction'),
        prior='Relevé existant à remplacer :', none='Aucun cycle', quality='Qualité : non vérifiée'),
    'zh-Hant': dict(title='記錄每日水量總計',
        cycle_id='請選擇下方作期編號或 ID；若不指定作期，請輸入無。',
        day='是哪個已結束的日期？格式 YYYY-MM-DD，時區 {timezone}；今天尚未結束。',
        rainfall_mm='該日總降雨量是多少 mm？請填明確數字；未知不等於零。',
        net_irrigation_mm='該日到達根區的淨灌溉深度是多少 mm？勿填抽水體積、運轉時間或總施水深度。若未知，請 /cancel，再用 /measure 保留原始讀值。',
        runoff_mm='該日降雨造成的逕流深度是多少 mm（不含灌溉逕流）？請填零至總降雨量間的數字；未知不等於零。',
        source='請提供全日總量的來源及量測、回報或估算方式，包含淨灌溉量的依據。',
        provenance='請選量測、回報或估算。若任何一項為估算，請選估算；否則若有回報值，請選回報。在來源中說明各項依據。',
        correction='下方已有當日總量。只有要更正整筆紀錄時才輸入取代，否則請 /cancel 保留原紀錄。',
        reason='為什麼要更正原有的每日總量？',
        ready='請核對全日總量。輸入儲存以存為未核實紀錄、/back 修改，或 /cancel。',
        footer='/waterlog 繼續；/back 修改；/cancel 放棄草稿。',
        saved='已儲存每日水量紀錄 {id}。品質為未核實。未變更計算、土壤狀態或設備操作。',
        cancelled='已取消每日水量草稿。', media='請用自己的文字回答；不能用影像生成資訊填入水量。',
        stale='草稿已變更或結束；請用 /waterlog 繼續。', changed='每日總量已變更；請用 /waterlog 繼續並核對最新紀錄。',
        context='農場時區或作物資料已變更；請 /cancel 後重新開始 /waterlog。',
        conflict='請先完成或取消單位設定或其他填寫流程，再使用 /waterlog。',
        select='請先選擇土耕生產單位，再使用 /waterlog。',
        invalid='請填有限且非負的 mm 數字，使用小數點且不加千位分隔符號。未知不等於零；無法取得總量時請 /cancel。',
        incomplete='請先完成問題並核對，再儲存。', date_error='請填已結束的當地日期；若已選作期，日期須在該作期內。',
        labels=('作期','日期','降雨量 (mm)','淨灌溉量 (mm)','降雨逕流量 (mm)','資料來源','資料依據','更正原因'),
        prior='將取代的既有紀錄：', none='無作期', quality='品質：未核實'),
}


class WaterLogError(ValueError):
    def __init__(self, key):
        self.key = key
        super().__init__(TEXT['en'][key])


def words(view, key, **values):
    return TEXT[view.get('language', 'en')][key].format(**values)


def error_text(view, error):
    return words(view, error.key) if isinstance(error, WaterLogError) else scouting_error(view, error)


def validate_language(language):
    if not isinstance(language, str) or language not in LANGUAGES:
        raise ValueError('Use /waterlog en, /waterlog fr or /waterlog zh-Hant.')


def token(view, value, kind):
    tokens = {'none': {'en': ('none',), 'fr': ('none', 'aucun'), 'zh-Hant': ('none', '無')},
              'replace': {'en': ('replace',), 'fr': ('replace', 'remplacer'), 'zh-Hant': ('replace', '取代')}}
    return value.lower() in tokens[kind][view['language']]


def provenance(view, value):
    translations = {'en': {}, 'fr': {'mesuré': 'measured', 'mesure': 'measured', 'rapporté': 'reported', 'rapporte': 'reported', 'estimé': 'estimated', 'estime': 'estimated'},
                    'zh-Hant': {'量測': 'measured', '回報': 'reported', '估算': 'estimated'}}
    return translations[view['language']].get(value.lower(), value.lower())


def translation_note(view):
    return SCOUTING_TEXT[view['language']]['note']
