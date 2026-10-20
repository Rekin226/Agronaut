"""Draft task-entry translations; technical validation errors remain English."""
from .scouting_language import LANGUAGES
from .scouting_language import TEXT as SCOUTING_TEXT

QUESTIONS = {
    'action': ('Choose plan to schedule work, completed to report completion, cancelled to cancel a saved task, or reopen to reopen it.',
        'Choisissez plan pour prévoir un travail, completed pour déclarer sa réalisation, cancelled pour annuler une tâche enregistrée, ou reopen pour la rouvrir.',
        '選 plan 安排工作、completed 回報完成、cancelled 取消已存工作，或 reopen 重新開啟工作。'),
    'cycle_id': ('Which crop cycle? Choose a number or its ID below.', 'Quel cycle cultural ? Choisissez un numéro ou son identifiant ci-dessous.', '要使用哪個作期？請選下列編號或作期 ID。'),
    'record_id': ('Which current task record ID from /overview should be updated? Copy its full ID.', 'Quelle tâche actuelle de /overview faut-il mettre à jour ? Copiez son identifiant complet.', '要更新 /overview 中哪筆目前工作？請複製完整記錄 ID。'),
    'entry': ('What unique entry name identifies this task within the crop cycle?', 'Quel nom unique identifie cette tâche dans le cycle cultural ?', '請為此作期中的工作輸入不重複的識別名稱。'),
    'title': ('What work should be done? Enter a short task title.', 'Quel travail faut-il effectuer ? Donnez un titre court.', '要做什麼工作？請輸入簡短工作名稱。'),
    'activity': ('Choose scouting, stage_check, irrigation, fertilizer, transplant, harvest, maintenance or other.', 'Choisissez scouting (observation), stage_check (stade), irrigation, fertilizer (fertilisation), transplant (repiquage), harvest (récolte), maintenance (entretien) ou other (autre).', '選 scouting（巡查）、stage_check（生育期檢查）、irrigation（灌溉）、fertilizer（施肥）、transplant（移植）、harvest（採收）、maintenance（維護）或 other（其他）。'),
    'basis': ('Choose date for fixed local dates or planting for day offsets from the planting date.', 'Choisissez date pour des dates locales fixes ou planting pour des jours décalés par rapport à la plantation.', '選 date 指定當地日期，或 planting 指定距種植日的天數。'),
    'start': ('What is the first day of the work window? Use YYYY-MM-DD in the farm timezone.', 'Quel est le premier jour de la période de travail ? Utilisez AAAA-MM-JJ dans le fuseau de la ferme.', '工作期間首日是哪天？請依農場時區填 YYYY-MM-DD。'),
    'end': ('What is the last day of the work window? Use YYYY-MM-DD, on or after the first day.', 'Quel est le dernier jour de la période ? Utilisez AAAA-MM-JJ, au plus tôt le premier jour.', '工作期間末日是哪天？請填 YYYY-MM-DD，且不得早於首日。'),
    'start_offset_days': ('How many days after planting does the window start? Enter an integer from -365 to 3650; negative means before planting.', 'Combien de jours après la plantation commence la période ? Entier de -365 à 3650 ; négatif signifie avant la plantation.', '工作期間在種植後第幾天開始？請填 -365 至 3650 的整數；負數表示種植前。'),
    'end_offset_days': ('How many days after planting does the window end? Enter an integer from -365 to 3650, at least the start offset.', 'Combien de jours après la plantation finit la période ? Entier de -365 à 3650, au moins égal au début.', '工作期間在種植後第幾天結束？請填 -365 至 3650 的整數，且不得小於起始天數。'),
    'location': ('Where within this production unit will the work take place?', 'Où dans cette unité le travail aura-t-il lieu ?', '工作將在此生產單位的哪個位置進行？'),
    'priority': ('Choose low, normal or high priority.', 'Choisissez la priorité low (faible), normal (normale) ou high (élevée).', '請選優先度 low（低）、normal（一般）或 high（高）。'),
    'notes': ('What instructions or assumptions accompany this work? Enter none if there are no additional notes.', 'Quelles consignes ou hypothèses accompagnent ce travail ? Saisissez none sans note supplémentaire.', '此工作有哪些說明或假設？沒有其他備註時請填 none。'),
    'source': ('What is the source for this plan or task-status report?', 'Quelle est la source de ce plan ou de ce compte rendu de tâche ?', '這項計畫或工作狀態回報的資料來源是什麼？'),
    'completed_on': ('When was the work actually completed? Use YYYY-MM-DD, no later than today in the farm timezone.', 'Quand le travail a-t-il réellement été terminé ? Utilisez AAAA-MM-JJ, au plus tard aujourd’hui dans le fuseau de la ferme.', '工作實際完成日期是哪天？請填 YYYY-MM-DD，不得晚於農場時區的今天。'),
    'evidence': ('To link an existing actual record, enter crop_record:ID or water_day:ID. Enter none for an operator report without linked evidence. This does not record quantities.', 'Pour lier une réalisation enregistrée, saisissez crop_record:ID ou water_day:ID. Saisissez none pour une déclaration sans pièce liée. Aucune quantité ne sera enregistrée.', '要連結既有實際記錄，請填 crop_record:ID 或 water_day:ID。僅由操作人員回報而無連結依據時填 none。此操作不會記錄數量。'),
    'reason': ('Why is this task being completed, cancelled or reopened?', 'Pourquoi cette tâche est-elle déclarée terminée, annulée ou rouverte ?', '為什麼要將此工作回報為完成、取消或重新開啟？'),
    'trigger': ('Which existing record prompted this task? Enter crop_record:ID, measurement:ID or water_day:ID, or none for no linked source. The record must belong to this unit and crop cycle.',
        'Quel enregistrement existant a motivé cette tâche ? Saisissez crop_record:ID, measurement:ID ou water_day:ID, ou none sans source liée. Il doit appartenir à cette unité et à ce cycle cultural.',
        '哪筆既有記錄促使您安排此工作？請填 crop_record:ID、measurement:ID、water_day:ID，或填 none 不連結來源。記錄必須屬於此生產單位及作期。'),
    'trigger_reason': ('Why did this record prompt the task? Describe your reasoning; the link does not establish a confirmed cause or treatment.',
        'Pourquoi cet enregistrement a-t-il motivé la tâche ? Décrivez votre raisonnement ; ce lien ne confirme ni une cause ni un traitement.',
        '為何這筆記錄促使您安排此工作？請說明您的判斷；連結不代表已確認成因或處理方法。'),
}
TEXT = {
    'en': dict(title='Crop work', ready='Review this task. Reply save, /back to edit, or /cancel to discard.',
        evidence_page='Matching actual records — page {page}/{pages}, {total} records (technical labels in English).',
        no_evidence='No matching actual records for this activity and date. Enter none for an unlinked operator report, or /back to check the date.',
        evidence_navigation='Choose a displayed number, an exact reference or none. Use next / previous for pages, or refresh to reload. Linking does not create quantities.',
        evidence_details='Selected completion record (technical labels in English):',
        task_choose='Which saved task should be updated? Choose a displayed number or enter its full record ID.',
        task_page='Saved tasks — page {page}/{pages}, {total} tasks (technical labels in English).',
        no_tasks='No eligible tasks in the crop cycles known to this draft. Use /back to change the action, or /cancel and start again after recording work or another crop cycle.',
        task_navigation='Use next / previous for pages, or refresh to reload tasks. Numbers keep their displayed meaning until refresh. Reopen lists only completed or cancelled tasks.',
        source_page='Available source records — page {page}/{pages}, {total} records (technical labels in English).',
        no_sources='No eligible source records were found in this crop cycle. Enter none to continue without a link.',
        source_navigation='Choose a displayed number, a full reference, or none. Use next / previous for pages, or refresh to reload records. Numbers keep their displayed meaning until refresh.',
        trigger_details='Selected source record (technical field labels in English):',
        footer='/task resumes this draft. /back edits the previous answer; /cancel discards it.',
        saved='Task record saved: {id}. No actual quantities or notifications were created.', cancelled='Task draft cancelled.',
        media='Task entry requires your text reply; image-generated facts cannot answer it.',
        note='Operator-supplied work windows and status reports; not agronomic recommendations. Completion does not create water, fertilizer or harvest quantities.'),
    'fr': dict(title='Travaux culturaux', ready='Vérifiez la tâche. Répondez enregistrer, /back pour modifier, ou /cancel pour abandonner.',
        evidence_page='Réalisations correspondantes — page {page}/{pages}, {total} enregistrements (libellés techniques en anglais).',
        no_evidence='Aucune réalisation correspondante pour cette activité et cette date. Saisissez none pour une déclaration sans lien, ou /back pour vérifier la date.',
        evidence_navigation='Choisissez un numéro affiché, une référence exacte ou none. Saisissez next / previous pour changer de page, ou refresh pour actualiser. Le lien ne crée aucune quantité.',
        evidence_details='Réalisation sélectionnée (libellés techniques en anglais) :',
        task_choose='Quelle tâche enregistrée faut-il mettre à jour ? Choisissez un numéro affiché ou saisissez son identifiant complet.',
        task_page='Tâches enregistrées — page {page}/{pages}, {total} tâches (libellés techniques en anglais).',
        no_tasks='Aucune tâche admissible dans les cycles connus de ce brouillon. Utilisez /back pour changer l’action, ou /cancel puis recommencez après avoir enregistré un travail ou un autre cycle.',
        task_navigation='Saisissez next / previous pour changer de page, ou refresh pour actualiser. Les numéros gardent leur sens jusqu’à refresh. Reopen liste seulement les tâches terminées ou annulées.',
        source_page='Sources disponibles — page {page}/{pages}, {total} enregistrements (libellés techniques en anglais).',
        no_sources='Aucune source admissible dans ce cycle cultural. Saisissez none pour continuer sans lien.',
        source_navigation='Choisissez un numéro affiché, une référence complète ou none. Saisissez next / previous pour changer de page, ou refresh pour actualiser. Les numéros gardent leur sens jusqu’à refresh.',
        trigger_details='Source sélectionnée (libellés techniques en anglais) :',
        footer='/task reprend le brouillon. /back modifie la réponse précédente ; /cancel abandonne.',
        saved='Tâche enregistrée : {id}. Aucune quantité réelle ni notification créée.', cancelled='Brouillon de tâche annulé.',
        media='Répondez par du texte ; les faits générés à partir d’une image ne peuvent pas remplir la tâche.',
        note='Périodes et états déclarés par l’opérateur, pas des recommandations agronomiques. Terminer une tâche ne crée aucune quantité d’eau, d’engrais ou de récolte.'),
    'zh-Hant': dict(title='作物工作', ready='請核對工作。輸入儲存、/back 修改，或 /cancel 放棄。',
        evidence_page='符合的實際記錄 — 第 {page}/{pages} 頁，共 {total} 筆（技術標籤為英文）。',
        no_evidence='此活動及日期沒有符合的實際記錄。輸入 none 可僅回報而不連結記錄，或用 /back 核對日期。',
        evidence_navigation='請選畫面上的編號、輸入完整參照或 none。輸入 next / previous 換頁，或 refresh 重新載入。連結不會建立數量。',
        evidence_details='所選完成依據記錄（技術標籤為英文）：',
        task_choose='要更新哪項已存工作？請選畫面上的編號，或輸入完整記錄 ID。',
        task_page='已存工作 — 第 {page}/{pages} 頁，共 {total} 項（技術標籤為英文）。',
        no_tasks='此草稿已知的作期中沒有符合條件的工作。可用 /back 變更動作，或 /cancel 後，在記錄工作或其他作期後重新開始。',
        task_navigation='輸入 next / previous 換頁，或 refresh 重新載入。編號在 refresh 前維持原本對應。Reopen 僅列出已完成或已取消的工作。',
        source_page='可選來源記錄 — 第 {page}/{pages} 頁，共 {total} 筆（技術標籤為英文）。',
        no_sources='此作期沒有符合條件的來源記錄。輸入 none 可不連結來源繼續。',
        source_navigation='請選畫面上的編號、輸入完整參照或 none。輸入 next / previous 換頁，或 refresh 重新載入。編號在 refresh 前維持原本對應。',
        trigger_details='所選來源記錄（技術欄位名稱為英文）：',
        footer='/task 繼續草稿。/back 修改上一個回答；/cancel 放棄。',
        saved='已儲存工作記錄：{id}。未建立實際數量或通知。', cancelled='已取消工作草稿。',
        media='請以文字回答；影像自動生成的資訊不能填入此工作。',
        note='工作期間及狀態由操作人員提供，不是農業建議。回報完成不會建立用水、肥料或採收數量。'),
}
LABELS = dict(zip(QUESTIONS, (
    ('Action','Action','動作'), ('Crop cycle','Cycle','作期'), ('Task record','Tâche','工作記錄'),
    ('Entry','Identifiant','識別名稱'), ('Title','Titre','名稱'), ('Activity','Activité','活動'),
    ('Window basis','Base de la période','期間基準'), ('Start','Début','起始日'), ('End','Fin','結束日'),
    ('Start offset (days)','Décalage initial (jours)','起始天數'), ('End offset (days)','Décalage final (jours)','結束天數'),
    ('Location','Lieu','位置'), ('Priority','Priorité','優先度'), ('Notes','Notes','備註'), ('Source','Source','來源'),
    ('Completed on','Terminé le','完成日'), ('Linked evidence','Pièce liée','連結依據'), ('Reason','Motif','原因'),
    ('Trigger record','Source déclenchante','規劃依據記錄'), ('Reason for task','Motif de la tâche','規劃理由'))))


def validate_language(language):
    if language not in LANGUAGES:
        raise ValueError('Use /task en, /task fr or /task zh-Hant.')


def words(view, key, **values):
    return TEXT[view.get('language', 'en')][key].format(**values)


def question(draft, key):
    return QUESTIONS[key][LANGUAGES.index(draft['language'])]


def translation_note(view):
    return SCOUTING_TEXT[view.get('language', 'en')]['note']
