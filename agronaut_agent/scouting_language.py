"""Draft scouting translations; canonical record values and commands stay stable."""

import re

LANGUAGES = ('en', 'fr', 'zh-Hant')


def validate_language(language):
    if not isinstance(language, str) or language not in LANGUAGES:
        raise ValueError('Use /scout en, /scout fr or /scout zh-Hant.')
    return language


TEXT = {
    'en': dict(
        title='Scouting observation', note='', planted='planted',
        questions=(
            'Which crop cycle did you inspect? Choose its number or exact ID:',
            'When did you make this observation? Use YYYY-MM-DD in the farm timezone {timezone}.',
            'Where did you inspect, and what area or plants does this sample cover?',
            'What did you actually observe? Describe the affected parts and pattern, or say none observed. Keep suspected causes separate from observations.',
            'How many PLANTS did you inspect, and how many had these symptoms? Reply inspected,affected (for example 10,2), or unknown for both. Do not enter insect or leaf counts.',
            'What is the source of this observation, such as your dated inspection notebook?',
            'Reply observed if you inspected it yourself, or reported if someone supplied the observation.'),
        ready='Review this observation. Reply save to record it, /back to edit, or /cancel to discard.',
        footer='/back edits the previous answer; /cancel discards this draft; /scout resumes it.',
        labels=('Crop', 'Date', 'Location', 'Symptoms', 'Inspected plants', 'Affected plants', 'Source', 'Provenance'),
        unknown='unknown', observed='observed', reported='reported',
        saved='Saved scouting observation {id}. The record describes observations; it does not confirm a diagnosis or treatment.',
        cancelled='Scouting draft cancelled for this unit. Saved observations are unchanged.',
        media='Scouting intake needs your text reply; image-generated observations cannot fill it. /scout resumes the question.',
        error='The answer or current record could not be accepted. Details: {error}'),
    'fr': dict(
        title='Observation de culture', note='Traduction provisoire ; relecture linguistique et agronomique locale encore nécessaire.', planted='planté le',
        questions=(
            'Quel cycle de culture avez-vous inspecté ? Choisissez son numéro ou son identifiant exact :',
            'Quand avez-vous fait cette observation ? Utilisez AAAA-MM-JJ dans le fuseau horaire de la ferme : {timezone}.',
            'Où avez-vous inspecté, et quelle zone ou quelles plantes cet échantillon représente-t-il ?',
            'Qu’avez-vous réellement observé ? Décrivez les parties atteintes et la répartition des symptômes, ou indiquez aucun symptôme observé. Séparez les causes soupçonnées des observations.',
            'Combien de PLANTES avez-vous inspectées, et combien présentaient ces symptômes ? Répondez inspectées,atteintes (par exemple 10,2), ou inconnu pour les deux. Ne comptez pas les insectes ou les feuilles.',
            'Quelle est la source de cette observation, par exemple votre carnet d’inspection daté ?',
            'Répondez observé si vous avez inspecté vous-même, ou rapporté si une autre personne a fourni cette observation.'),
        ready='Vérifiez cette observation. Répondez enregistrer pour la sauvegarder, /back pour modifier, ou /cancel pour abandonner.',
        footer='/back modifie la réponse précédente ; /cancel abandonne ce brouillon ; /scout le reprend.',
        labels=('Culture', 'Date', 'Lieu', 'Symptômes', 'Plantes inspectées', 'Plantes atteintes', 'Source', 'Provenance'),
        unknown='inconnu', observed='observé personnellement', reported='rapporté par une autre personne',
        saved='Observation de culture enregistrée : {id}. Ce relevé décrit des observations ; il ne confirme ni diagnostic ni traitement.',
        cancelled='Brouillon d’observation annulé pour cette unité. Les observations enregistrées sont conservées.',
        media='Répondez par du texte ; une observation générée à partir d’une image ne peut pas remplir ce formulaire. /scout reprend la question.',
        error='La réponse ou le relevé actuel n’a pas pu être accepté. Détails techniques : {error}'),
    'zh-Hant': dict(
        title='作物巡查紀錄', note='翻譯草稿；尚待在地語言及農業專業審閱。', planted='種植日期',
        questions=(
            '您巡查的是哪一期作物？請輸入清單序號或完整識別碼：',
            '您何時進行這次觀察？請以農場時區 {timezone} 的日期填寫，格式為 YYYY-MM-DD（年-月-日）。',
            '您在哪裡巡查？這份樣本涵蓋哪些區域或植株？',
            '您實際觀察到什麼？請描述受影響部位及症狀分布；若沒有症狀，請填未觀察到症狀。請將推測原因與實際觀察分開。',
            '您檢查了多少株植物？其中多少株有這些症狀？請用半形逗號填寫檢查株數,有症狀株數（例如 10,2），或填未知表示兩項都不知道。請勿填昆蟲或葉片數量。',
            '這次觀察的資料來源是什麼？例如註明日期的巡查筆記。',
            '若是您本人檢查，請填親自觀察；若資料由其他人提供，請填他人回報。'),
        ready='請核對這份觀察紀錄。輸入儲存以保存、/back 修改，或 /cancel 放棄。',
        footer='/back 修改上一個回答；/cancel 放棄草稿；/scout 繼續草稿。',
        labels=('作物', '日期', '位置', '症狀', '檢查株數', '有症狀株數', '資料來源', '觀察來源'),
        unknown='未知', observed='親自觀察', reported='他人回報',
        saved='已儲存作物巡查紀錄 {id}。此紀錄描述觀察結果，並不確認診斷或治療方式。',
        cancelled='已取消此生產單位的巡查草稿。已儲存的觀察紀錄保持不變。',
        media='請用文字回答；不能用影像自動生成的觀察填入此表單。/scout 可繼續目前問題。',
        error='無法接受此回答或目前紀錄。技術詳細資訊：{error}'),
}

# Explicit response tokens only; free text is never translated or interpreted.
TOKENS = {
    'en': dict(unknown=('unknown',), observed=('observed',), reported=('reported',), save=('save',)),
    'fr': dict(unknown=('unknown', 'inconnu', 'inconnue'), observed=('observed', 'observé', 'observe'),
               reported=('reported', 'rapporté', 'rapporte'), save=('save', 'enregistrer')),
    'zh-Hant': dict(unknown=('unknown', '未知'), observed=('observed', '親自觀察'),
                    reported=('reported', '他人回報'), save=('save', '儲存')),
}


def scouting_text(view, key, **values):
    return TEXT[view.get('language', 'en')][key].format(**values)


def scouting_token(view, value, kind):
    return value.strip().lower() in TOKENS[view.get('language', 'en')][kind]


# Service/API errors keep their English contract; chat translates known validation
# errors and preserves unexpected technical details under a localized explanation.
ERRORS = {
    'Select a production unit with /units before /scout.': (
        'Sélectionnez une unité de production avec /units avant /scout.', '請先用 /units 選擇生產單位，再使用 /scout。'),
    'Finish or cancel unit setup first, then use /scout.': (
        'Terminez ou annulez la création de l’unité, puis utilisez /scout.', '請先完成或取消生產單位設定，再使用 /scout。'),
    'Finish or cancel unit setup before starting /scout.': (
        'Terminez ou annulez la création de l’unité avant /scout.', '請先完成或取消生產單位設定，再使用 /scout。'),
    'Finish or cancel field planning for this unit before starting /scout.': (
        'Terminez ou annulez la planification de cette unité avant /scout.', '請先完成或取消此生產單位的田間規劃，再使用 /scout。'),
    'Create a crop cycle with /plant or Crop records first, then use /scout.': (
        'Créez d’abord un cycle avec /plant ou dans Crop records, puis utilisez /scout.', '請先用 /plant 或在 Crop records 建立作物期別，再使用 /scout。'),
    'Finish or cancel planting entry for this unit before starting /scout.': (
        'Terminez ou annulez la saisie de plantation avant /scout.', '請先完成或取消種植紀錄，再使用 /scout。'),
    'answer must be nonempty text': (
        'Veuillez saisir une réponse non vide.', '請輸入非空白的回答。'),
    'Use a valid observation date in YYYY-MM-DD format.': (
        'Saisissez une date valide au format AAAA-MM-JJ.', '請輸入有效日期，格式為 YYYY-MM-DD（年-月-日）。'),
    'Choose a listed crop cycle number or exact ID.': (
        'Choisissez le numéro d’un cycle dans la liste ou son identifiant exact.', '請選擇清單中的作物期別序號或完整識別碼。'),
    'Observation date must fall between planting and today in the farm timezone.': (
        'La date doit être comprise entre la plantation et aujourd’hui dans le fuseau horaire de la ferme.', '觀察日期須介於種植日與農場時區的今天之間。'),
    'Use inspected,affected whole PLANT counts, or unknown.': (
        'Utilisez des nombres entiers de PLANTES : inspectées,atteintes (10,2), ou inconnu.', '請填植株整數：檢查株數,有症狀株數（10,2），或未知。'),
    'Inspected must be 1–1000000000 and affected 0–inspected.': (
        'Le nombre inspecté doit être de 1 à 1000000000 ; le nombre atteint, de 0 au nombre inspecté.', '檢查株數須為 1–1000000000；有症狀株數須介於 0 與檢查株數之間。'),
    'Choose observed or reported explicitly.': (
        'Choisissez explicitement observé ou rapporté.', '請明確選擇親自觀察或他人回報。'),
    'The observation is ready; reply save, /back or /cancel.': (
        'L’observation est prête ; répondez enregistrer, /back ou /cancel.', '觀察紀錄已可核對；請輸入儲存、/back 或 /cancel。'),
    'Keep each answer within 12000 characters.': (
        'Limitez chaque réponse à 12000 caractères.', '每個回答請勿超過 12000 個字元。'),
    'Farm timezone changed; /cancel and start /scout again.': (
        'Le fuseau horaire a changé ; utilisez /cancel puis recommencez avec /scout.', '農場時區已變更；請先 /cancel，再用 /scout 重新開始。'),
    'Crop planting details changed; /cancel and start /scout again.': (
        'Les détails de plantation ont changé ; utilisez /cancel puis recommencez avec /scout.', '種植資料已變更；請先 /cancel，再用 /scout 重新開始。'),
}


def scouting_error(view, error):
    message, language = str(error), view.get('language', 'en')
    if language == 'en':
        return message
    if message in ERRORS:
        return ERRORS[message][0 if language == 'fr' else 1]
    ended = re.fullmatch(r'record date is after the crop cycle end \(([0-9]{4}-[0-9]{2}-[0-9]{2})\); reopen or correct the closure first', message)
    if ended:
        day = ended.group(1)
        return (f'La date est après la fin du cycle ({day}) ; rouvrez le cycle ou corrigez sa clôture.'
                if language == 'fr' else f'紀錄日期晚於作物期別結束日（{day}）；請先重新開啟作期或更正結束紀錄。')
    return scouting_text(view, 'error', error=message)
