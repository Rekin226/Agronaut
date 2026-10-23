"""Explicit planting prompts; translations remain drafts pending local review."""

from .scouting_language import LANGUAGES, scouting_error
from .scouting_language import TEXT as SCOUTING_TEXT

TEXT={
    'en':dict(title='Record an actual planting',crop='What crop did you plant? Enter the crop name as you want it recorded.',
        planted_on='When was it actually planted? Use YYYY-MM-DD in the farm timezone {timezone}. Future plans belong in the calendar.',
        source='What is the source for this planting, such as your dated planting notebook?',
        duplicate='The crop and date match existing cycles listed below. To record a distinct new planting, reply new; otherwise /cancel and use the existing cycle.',
        ready='Review this actual planting. Reply save to create a new crop cycle, /back to edit, or /cancel to discard.',
        labels=('Crop','Actual planting date','Source'),matches='Existing cycles with the same crop and date:',
        footer='/back edits the previous answer; /cancel discards the draft; /plant resumes it.',
        saved='Saved new crop cycle {id}. You can now use /scout to record observations.',
        cancelled='Planting draft cancelled. Existing crop cycles are unchanged.',
        media='Planting entry needs your text reply; image-generated facts cannot fill it. /plant resumes the question.',
        date_error='Use a valid actual planting date in YYYY-MM-DD format, on or before today in the farm timezone.',
        long_answer='Use a crop name of at most 200 characters or a source of at most 12000 characters.',
        duplicate_answer='Reply new only for a distinct planting, or /cancel to use an existing cycle.',
        changed='Matching crop cycles changed; resume /plant and review them before continuing.',
        timezone='Farm timezone changed; /cancel and start /plant again.',
        stale='Planting draft changed or ended; resume /plant before continuing.',
        incomplete='Complete the planting questions and review the preview before saving.',
        already_ready='The planting is ready; reply save, /back or /cancel.',
        setup='Finish or cancel unit setup before /plant.',
        conflict='Finish or cancel scouting or field planning for this unit before /plant.',
        select='Select a production unit with /units before /plant.'),
    'fr':dict(title='Enregistrer une plantation réalisée',crop='Quelle culture avez-vous plantée ? Indiquez le nom à enregistrer.',
        planted_on='Quand a-t-elle réellement été plantée ? Utilisez AAAA-MM-JJ dans le fuseau horaire {timezone}. Les projets futurs vont dans le calendrier.',
        source='Quelle est la source de cette plantation, par exemple votre carnet de plantation daté ?',
        duplicate='La culture et la date correspondent aux cycles ci-dessous. Pour une plantation distincte, répondez nouveau ; sinon utilisez /cancel et le cycle existant.',
        ready='Vérifiez cette plantation réalisée. Répondez enregistrer pour créer un nouveau cycle, /back pour modifier, ou /cancel pour abandonner.',
        labels=('Culture','Date réelle de plantation','Source'),matches='Cycles existants avec la même culture et la même date :',
        footer='/back modifie la réponse précédente ; /cancel abandonne le brouillon ; /plant le reprend.',
        saved='Nouveau cycle de culture enregistré : {id}. Vous pouvez utiliser /scout pour saisir des observations.',
        cancelled='Brouillon de plantation annulé. Les cycles existants sont conservés.',
        media='Répondez par du texte ; les faits générés à partir d’une image ne peuvent pas remplir ce formulaire. /plant reprend la question.',
        date_error='Utilisez une date réelle valide au format AAAA-MM-JJ, au plus tard aujourd’hui dans le fuseau horaire de la ferme.',
        long_answer='Limitez le nom de culture à 200 caractères et la source à 12000 caractères.',
        duplicate_answer='Répondez nouveau uniquement pour une plantation distincte, ou /cancel pour utiliser un cycle existant.',
        changed='Les cycles correspondants ont changé ; reprenez /plant et vérifiez-les avant de continuer.',
        timezone='Le fuseau horaire a changé ; utilisez /cancel puis recommencez avec /plant.',
        stale='Le brouillon a changé ou a été terminé ; reprenez /plant avant de continuer.',
        incomplete='Terminez les questions et vérifiez le récapitulatif avant d’enregistrer.',
        already_ready='La plantation est prête ; répondez enregistrer, /back ou /cancel.',
        setup='Terminez ou annulez la création de l’unité avant /plant.',
        conflict='Terminez ou annulez la saisie d’observations ou la planification de cette unité avant /plant.',
        select='Sélectionnez une unité de production avec /units avant /plant.'),
    'zh-Hant':dict(title='記錄已完成的種植',crop='您種植了什麼作物？請輸入要記錄的作物名稱。',
        planted_on='實際種植日期是哪一天？請依農場時區 {timezone} 填寫 YYYY-MM-DD（年-月-日）。未來計畫應記在行事曆中。',
        source='這次種植的資料來源是什麼？例如註明日期的種植筆記。',
        duplicate='以下作期的作物及日期相同。若要記錄另一次獨立種植，請輸入新增；否則請 /cancel 並使用現有作期。',
        ready='請核對這次已完成的種植。輸入儲存以建立新作期、/back 修改，或 /cancel 放棄。',
        labels=('作物','實際種植日期','資料來源'),matches='作物及日期相同的現有作期：',
        footer='/back 修改上一個回答；/cancel 放棄草稿；/plant 繼續草稿。',
        saved='已儲存新作期 {id}。現在可以使用 /scout 記錄觀察。',
        cancelled='已取消種植草稿。現有作期保持不變。',
        media='請用文字回答；不能用影像自動生成的資訊填入此表單。/plant 可繼續目前問題。',
        date_error='請填有效的實際種植日期，格式為 YYYY-MM-DD，且不得晚於農場時區的今天。',
        long_answer='作物名稱不得超過 200 個字元；資料來源不得超過 12000 個字元。',
        duplicate_answer='僅在記錄另一次獨立種植時輸入新增；若要使用現有作期，請 /cancel。',
        changed='相符的作期已變更；請用 /plant 繼續並重新核對。',
        timezone='農場時區已變更；請先 /cancel，再用 /plant 重新開始。',
        stale='種植草稿已變更或結束；請先用 /plant 繼續。',
        incomplete='請先完成種植問題並核對預覽，再儲存。',
        already_ready='種植紀錄已可核對；請輸入儲存、/back 或 /cancel。',
        setup='請先完成或取消生產單位設定，再使用 /plant。',
        conflict='請先完成或取消此生產單位的巡查或田間規劃，再使用 /plant。',
        select='請先用 /units 選擇生產單位，再使用 /plant。'),
}


class PlantingError(ValueError):
    def __init__(self,key):
        self.key=key
        super().__init__(TEXT['en'][key])


def planting_text(view,key,**values):
    return TEXT[view.get('language','en')][key].format(**values)


def planting_error(view,error):
    return planting_text(view,error.key) if isinstance(error,PlantingError) else scouting_error(view,error)


def validate_language(language):
    if not isinstance(language,str) or language not in LANGUAGES:
        raise ValueError('Use /plant en, /plant fr or /plant zh-Hant.')


def new_cycle_token(view,value):
    return value.lower() in {'en':('new',),'fr':('new','nouveau'),'zh-Hant':('new','新增')}[view.get('language','en')]


def translation_note(view):
    return SCOUTING_TEXT[view.get('language','en')]['note']
