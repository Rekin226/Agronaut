"""Draft translations for explicit raw-reading entry, without inference."""

from .scouting_language import LANGUAGES, scouting_error
from .scouting_language import TEXT as SCOUTING_TEXT

TEXT = {
    'en': dict(title='Record a field reading',
        cycle_id='Choose a crop cycle number or ID, or none to leave this reading unassigned.',
        quantity='Which quantity did you record? Choose a number or its code below.',
        value='Enter the reading in {unit}, using a dot for decimals. Unknown is not zero. Soil water content uses a fraction from 0 to 1, not percent.',
        observed_at='When was this reading taken? Use YYYY-MM-DD HH:MM in farm timezone {timezone}, or an ISO timestamp with an explicit offset (YYYY-MM-DDTHH:MM:SS+HH:MM). It cannot be in the future.',
        source='Describe the instrument or record, sampling location/depth and represented interval. For irrigation depth, state whether it is gross or net. A point reading does not establish a field average.',
        provenance='Choose measured, reported or estimated. This label is your declaration; the reading will remain unverified.',
        ready='Review this reading. Reply save to record it as unverified, /back to edit, or /cancel.',
        footer='/measure resumes; /back edits; /cancel discards this draft.',
        saved='Saved reading {id} as unverified. No daily total, harvest batch, soil state or equipment action was created.',
        cancelled='Measurement draft cancelled.', media='Supply your own text; image-generated facts cannot fill the reading.',
        select='Select a production unit before /measure.', conflict='Finish or cancel unit setup or another intake for this unit before /measure.',
        stale='Measurement draft changed or ended; resume /measure.', context='Farm timezone or crop details changed; /cancel and restart /measure.',
        invalid='Enter a finite plain decimal in the displayed unit. Only air temperature may be negative; soil water content must be between 0 and 1. Unknown is not zero.',
        timestamp='Use a valid date and time with minutes, in the displayed farm timezone or with an explicit UTC offset.',
        ambiguous='This local time occurs twice during a clock change. Supply an explicit UTC offset from the original record.',
        nonexistent='This local time does not exist during a clock change. Check the original timestamp; supply its explicit UTC offset if known.',
        future='A field reading cannot be in the future. Check the timestamp and offset.', incomplete='Complete the questions and review before saving.',
        labels=('Crop cycle','Quantity','Value','Observed at','Source','Provenance'), none='No crop cycle', quality='Quality: unverified',
        quantities=('Rainfall','Irrigation depth','Irrigation volume','Soil depletion','Soil water content','Air temperature','Flow','Pressure','Harvest mass reading','Runtime')),
    'fr': dict(title='Enregistrer une mesure de terrain',
        cycle_id='Choisissez un numéro ou identifiant de cycle, ou aucun pour ne pas associer de cycle.',
        quantity='Quelle grandeur avez-vous relevée ? Choisissez un numéro ou son code ci-dessous.',
        value='Saisissez la valeur en {unit}, avec un point décimal. Inconnu ne signifie pas zéro. La teneur en eau du sol est une fraction entre 0 et 1, pas un pourcentage.',
        observed_at='Quand ce relevé a-t-il été pris ? Utilisez AAAA-MM-JJ HH:MM dans le fuseau {timezone}, ou un horodatage ISO avec décalage explicite (AAAA-MM-JJTHH:MM:SS+HH:MM). Pas de date future.',
        source='Décrivez l’instrument ou le document, le lieu/la profondeur et l’intervalle représenté. Pour l’irrigation, précisez hauteur brute ou nette. Un point ne représente pas automatiquement la moyenne du champ.',
        provenance='Choisissez mesuré, rapporté ou estimé. Il s’agit de votre déclaration ; la mesure reste non vérifiée.',
        ready='Vérifiez la mesure. Répondez enregistrer pour la conserver comme non vérifiée, /back pour modifier, ou /cancel.',
        footer='/measure reprend ; /back modifie ; /cancel abandonne ce brouillon.',
        saved='Mesure enregistrée : {id}, non vérifiée. Aucun total journalier, lot de récolte, état du sol ou opération d’équipement n’a été créé.',
        cancelled='Brouillon de mesure annulé.', media='Répondez avec votre propre texte ; les faits générés à partir d’une image ne peuvent pas remplir la mesure.',
        select='Sélectionnez une unité avant /measure.', conflict='Terminez ou annulez la création de l’unité ou une autre saisie avant /measure.',
        stale='Le brouillon a changé ou a été terminé ; reprenez /measure.', context='Le fuseau horaire ou la culture a changé ; /cancel puis recommencez /measure.',
        invalid='Saisissez un nombre décimal fini dans l’unité affichée. Seule la température peut être négative ; la teneur en eau doit être entre 0 et 1. Inconnu ne signifie pas zéro.',
        timestamp='Saisissez une date et une heure valides avec les minutes, dans le fuseau affiché ou avec un décalage UTC explicite.',
        ambiguous='Cette heure locale se produit deux fois lors d’un changement d’heure. Indiquez le décalage UTC explicite du relevé original.',
        nonexistent='Cette heure locale n’existe pas lors d’un changement d’heure. Vérifiez l’horodatage original ; indiquez son décalage UTC explicite s’il est connu.',
        future='Une mesure de terrain ne peut pas être future. Vérifiez la date, l’heure et le décalage.', incomplete='Terminez les questions et vérifiez avant d’enregistrer.',
        labels=('Cycle','Grandeur','Valeur','Date et heure','Source','Provenance'), none='Aucun cycle', quality='Qualité : non vérifiée',
        quantities=('Pluie','Hauteur d’irrigation','Volume d’irrigation','Déficit hydrique du sol','Teneur en eau du sol','Température de l’air','Débit','Pression','Masse de récolte mesurée','Durée de fonctionnement')),
    'zh-Hant': dict(title='記錄田間讀值',
        cycle_id='請選作期編號或 ID；若不指定作期，請輸入無。',
        quantity='您記錄的是哪一項？請選下方編號或代碼。',
        value='請以 {unit} 輸入讀值，使用小數點。未知不等於零。土壤含水量使用 0 至 1 的比例，不是百分比。',
        observed_at='何時取得此讀值？請依農場時區 {timezone} 輸入 YYYY-MM-DD HH:MM，或輸入含明確時差的 ISO 時間（YYYY-MM-DDTHH:MM:SS+HH:MM）。不得為未來時間。',
        source='請說明儀器或紀錄來源、採樣位置／深度，以及代表的時段。灌溉深度請註明總量或淨量。單點讀值不會自動代表全田平均。',
        provenance='請選量測、回報或估算。這是您的聲明；讀值仍為未核實。',
        ready='請核對讀值。輸入儲存以存為未核實紀錄、/back 修改，或 /cancel。',
        footer='/measure 繼續；/back 修改；/cancel 放棄草稿。',
        saved='已儲存讀值 {id}，品質為未核實。未建立每日總量、收穫批次、土壤狀態或設備操作。',
        cancelled='已取消讀值草稿。', media='請用自己的文字回答；不能用影像生成資訊填入讀值。',
        select='請先選擇生產單位，再使用 /measure。', conflict='請先完成或取消單位設定或其他填寫流程，再使用 /measure。',
        stale='讀值草稿已變更或結束；請用 /measure 繼續。', context='農場時區或作物資料已變更；請 /cancel 後重新開始 /measure。',
        invalid='請以顯示單位填有限的小數。只有氣溫可為負值；土壤含水量須在 0 至 1 之間。未知不等於零。',
        timestamp='請填包含分鐘的有效日期時間，依顯示的農場時區，或附上明確 UTC 時差。',
        ambiguous='時鐘調整使這個當地時間出現兩次。請提供原始紀錄的明確 UTC 時差。',
        nonexistent='時鐘調整期間不存在這個當地時間。請核對原始時間戳；若知道其明確 UTC 時差，請附上。',
        future='田間讀值不得為未來時間。請核對日期、時間及時差。', incomplete='請先完成問題並核對，再儲存。',
        labels=('作期','量測項目','讀值','觀察時間','資料來源','資料依據'), none='無作期', quality='品質：未核實',
        quantities=('降雨','灌溉深度','灌溉體積','土壤水分虧缺','土壤含水量','氣溫','流量','壓力','收穫質量讀值','運轉時間')),
}


class MeasurementError(ValueError):
    def __init__(self, key):
        self.key = key
        super().__init__(TEXT['en'][key])


def words(view, key, **values):
    return TEXT[view.get('language', 'en')][key].format(**values)


def error_text(view, error):
    return words(view, error.key) if isinstance(error, MeasurementError) else scouting_error(view, error)


def validate_language(language):
    if not isinstance(language, str) or language not in LANGUAGES:
        raise ValueError('Use /measure en, /measure fr or /measure zh-Hant.')


def translation_note(view):
    return SCOUTING_TEXT[view['language']]['note']
