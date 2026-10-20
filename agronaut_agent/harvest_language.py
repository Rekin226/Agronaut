"""Draft language choices for recording actual crop harvest batches."""

from .scouting_language import LANGUAGES, scouting_error
from .scouting_language import TEXT as SCOUTING_TEXT

TEXT = {
    'en': dict(title='Record an actual harvest batch',
        questions=('Choose the crop cycle number or exact ID.',
            'What is the batch identifier? Use the same identifier for the same physical batch; existing batches must be corrected in Crop records.',
            'When was it harvested? Use YYYY-MM-DD in farm timezone {timezone}.',
            'What product was harvested? Describe it explicitly; do not use a predicted yield.',
            'Choose the quantity unit: kg, g, t, or count (individual items).',
            'How much was actually harvested in {unit}? Use a positive number with a decimal point and no thousands separator. Counts must be whole. Unknown is not zero.',
            'Choose the quantity basis: 1 fresh, 2 dry, or 3 as_harvested (condition as collected).',
            'What is the grade? Enter the actual grade, ungraded if not graded, or unknown if you do not know.',
            'What is the source for this harvest record, such as a dated weighing notebook?',
            'Choose measured, reported, or estimated for the quantity. These remain separate in totals.'),
        labels=('Crop cycle','Batch','Harvest day','Product','Unit','Quantity','Basis','Grade','Source','Provenance'),
        ready='Review this actual harvest batch. Reply save to record it, /back to edit, or /cancel to discard.',
        footer='/back edits the last answer; /cancel discards the draft; /harvest resumes it.',
        saved='Harvest batch saved: {id}. The crop cycle remains unchanged; no yield per area or income is inferred.',
        cancelled='Harvest draft cancelled. Existing harvest records are unchanged.',
        media='Answer in your own text; image-generated facts cannot fill this harvest draft.',
        conflict='Finish or cancel the active intake for this unit or unit setup before /harvest.',
        select='Select a production unit with /units before /harvest.',
        no_cycles='Create a crop cycle with /plant or Crop records before /harvest.',
        stale='Harvest draft changed or ended; resume /harvest before continuing.',
        context='Planting details or farm timezone changed; /cancel and start /harvest again.',
        cycle='Choose a listed crop cycle number or exact ID.',
        date_error='Use an actual harvest date in YYYY-MM-DD, between planting and today and on/before cycle closure.',
        duplicate='This batch already exists in this crop cycle. Use /back to correct the identifier, or /cancel and correct the existing record in Crop records.',
        unit='Choose kg, g, t, or count explicitly.',
        quantity='Enter a positive decimal number up to 1000000000000, without separators; count must be whole. Unknown quantities cannot be saved.',
        basis='Choose 1 fresh, 2 dry, or 3 as_harvested.',
        provenance='Choose measured, reported, or estimated explicitly.',
        long='Batch, product and grade allow 200 characters; source allows 2000.',
        incomplete='Complete the harvest questions and review the preview before saving.',
        ready_error='The harvest is ready; reply save, /back or /cancel.'),
    'fr': dict(title='Enregistrer un lot réellement récolté',
        questions=('Choisissez le numéro du cycle ou son identifiant exact.',
            'Quel est l’identifiant du lot ? Gardez le même identifiant pour le même lot physique ; corrigez les lots existants dans Crop records.',
            'Quelle est la date réelle de récolte ? Utilisez YYYY-MM-DD dans le fuseau {timezone}.',
            'Quel produit a été récolté ? Décrivez-le explicitement ; ne saisissez pas un rendement prédit.',
            'Choisissez l’unité : kg, g, t ou count (nombre d’articles).',
            'Quelle quantité a réellement été récoltée en {unit} ? Nombre positif avec un point décimal, sans séparateur de milliers. count exige un entier. Inconnu ne signifie pas zéro.',
            'Choisissez la base de quantité : 1 frais, 2 sec, ou 3 tel que récolté.',
            'Quelle est la catégorie de qualité ? Indiquez la catégorie réelle, non classé si le lot n’a pas été classé, ou inconnu si vous ne savez pas.',
            'Quelle est la source de ce relevé, par exemple un carnet de pesée daté ?',
            'Choisissez mesuré, rapporté ou estimé pour la quantité. Les totaux restent séparés.'),
        labels=('Cycle','Lot','Date de récolte','Produit','Unité','Quantité','Base','Catégorie','Source','Provenance'),
        ready='Vérifiez ce lot réellement récolté. Répondez enregistrer, /back pour modifier ou /cancel pour abandonner.',
        footer='/back modifie la dernière réponse ; /cancel abandonne ; /harvest reprend.',
        saved='Lot récolté enregistré : {id}. Le cycle reste inchangé ; aucun rendement par surface ni revenu n’est déduit.',
        cancelled='Brouillon de récolte annulé. Les relevés existants restent inchangés.',
        media='Répondez avec votre propre texte ; les données générées depuis une image ne remplissent pas ce brouillon.',
        conflict='Terminez ou annulez la saisie active de cette unité ou sa configuration avant /harvest.',
        select='Sélectionnez une unité avec /units avant /harvest.',
        no_cycles='Créez un cycle avec /plant ou Crop records avant /harvest.',
        stale='Le brouillon a changé ou est terminé ; reprenez /harvest.',
        context='La plantation ou le fuseau a changé ; utilisez /cancel puis recommencez /harvest.',
        cycle='Choisissez un numéro de cycle proposé ou son identifiant exact.',
        date_error='Utilisez une date réelle YYYY-MM-DD, entre la plantation et aujourd’hui, sans dépasser la clôture du cycle.',
        duplicate='Ce lot existe déjà dans ce cycle. Utilisez /back pour corriger l’identifiant, ou /cancel puis corrigez le relevé existant dans Crop records.',
        unit='Choisissez explicitement kg, g, t ou count.',
        quantity='Nombre décimal positif jusqu’à 1000000000000, sans séparateurs ; count exige un entier. Une quantité inconnue ne peut pas être enregistrée.',
        basis='Choisissez 1 frais, 2 sec ou 3 tel que récolté.',
        provenance='Choisissez explicitement mesuré, rapporté ou estimé.',
        long='Lot, produit et catégorie : 200 caractères ; source : 2000.',
        incomplete='Terminez les questions et vérifiez le récapitulatif avant d’enregistrer.',
        ready_error='La récolte est prête ; répondez enregistrer, /back ou /cancel.'),
    'zh-Hant': dict(title='記錄實際採收批次',
        questions=('請選擇作期編號或完整識別碼。',
            '這批採收的識別碼是什麼？同一實體批次請使用相同識別碼；既有批次請在 Crop records 更正。',
            '實際採收日期是哪一天？請依農場時區 {timezone} 填寫 YYYY-MM-DD。',
            '採收了什麼產品？請明確描述，不要填入預測產量。',
            '請選單位：kg、g、t 或 count（個別物品數量）。',
            '實際採收多少 {unit}？請填正數，使用小數點且不加千位分隔符號。count 必須是整數。未知不等於零。',
            '請選數量基準：1 鮮重、2 乾重，或 3 採收當時狀態。',
            '這批產品的等級是什麼？請填實際等級；未分級請填未分級，不知道請填未知。',
            '這筆採收紀錄的來源是什麼？例如附日期的秤重筆記。',
            '數量依據請選量測、回報或估算。各類別會分開加總。'),
        labels=('作期','批次','採收日期','產品','單位','數量','基準','等級','來源','資料依據'),
        ready='請核對實際採收批次。輸入儲存、/back 修改，或 /cancel 放棄。',
        footer='/back 修改上一個回答；/cancel 放棄；/harvest 繼續。',
        saved='已儲存採收批次：{id}。作期保持不變；不會推算單位面積產量或收入。',
        cancelled='已取消採收草稿。既有採收紀錄保持不變。',
        media='請用自己的文字回答；影像生成的資料不能填入採收草稿。',
        conflict='請先完成或取消此單位的資料輸入或單位設定，再使用 /harvest。',
        select='請先用 /units 選擇生產單位，再使用 /harvest。',
        no_cycles='請先用 /plant 或 Crop records 建立作期，再使用 /harvest。',
        stale='採收草稿已變更或結束；請用 /harvest 繼續。',
        context='種植資料或農場時區已變更；請 /cancel 後重新開始 /harvest。',
        cycle='請選擇列出的作期編號或完整識別碼。',
        date_error='請填 YYYY-MM-DD 實際採收日期，介於種植日與今天，且不得晚於作期結束日。',
        duplicate='此作期已有這個批次。請 /back 更正識別碼，或 /cancel 後在 Crop records 更正既有紀錄。',
        unit='請明確選擇 kg、g、t 或 count。',
        quantity='請填不超過 1000000000000 的正數，不加分隔符號；count 必須是整數。未知數量不能儲存。',
        basis='請選 1 鮮重、2 乾重或 3 採收當時狀態。',
        provenance='請明確選擇量測、回報或估算。',
        long='批次、產品及等級最多 200 字元；來源最多 2000 字元。',
        incomplete='請先完成問題並核對預覽，再儲存。',
        ready_error='採收已可核對；請輸入儲存、/back 或 /cancel。'),
}
BASIS = ('fresh', 'dry', 'as_harvested')
BASIS_LABELS = {'en': BASIS, 'fr': ('frais', 'sec', 'tel que récolté'), 'zh-Hant': ('鮮重', '乾重', '採收當時狀態')}
PROVENANCE_LABELS = {'en': ('measured', 'reported', 'estimated'), 'fr': ('mesuré', 'rapporté', 'estimé'),
                     'zh-Hant': ('量測', '回報', '估算')}


class HarvestError(ValueError):
    def __init__(self, key):
        self.key = key
        super().__init__(TEXT['en'][key])


def words(view, key, **values):
    return TEXT[view.get('language', 'en')][key].format(**values)


def error_text(view, error):
    return words(view, error.key) if isinstance(error, HarvestError) else scouting_error(view, error)


def validate_language(language):
    if not isinstance(language, str) or language not in LANGUAGES:
        raise ValueError('Use /harvest en, /harvest fr or /harvest zh-Hant.')


def basis(view, value):
    language = view['language']
    for index, canonical in enumerate(BASIS):
        if value.lower() in (canonical, str(index+1), BASIS_LABELS[language][index]):
            return canonical
    raise HarvestError('basis')


def translation_note(view):
    return SCOUTING_TEXT[view['language']]['note']
