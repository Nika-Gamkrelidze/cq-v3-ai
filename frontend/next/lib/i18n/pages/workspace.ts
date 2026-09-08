/* The workspace portal (tn.*, con.*) — `tenant.html` → `/workspace`.

   Only the strings the page itself owns: its tabs, its account/API-key panel and its
   workspace picker. Everything the page actually spends its lines on lives in feature
   modules it shares with other surfaces — the KB (features/kb.ts), the bot (features/bot.ts),
   the review queue (features/curation.ts), scoring, the workbench and the timeline.

   `tn.tr.*` is the exception that proves it: the transcription settings vocabulary is shared
   (features/transcription.ts, `tr.*`, rendered identically by the console, this tab and the
   upload panel), but the two sentences below are about THIS surface only — who may edit a
   workspace's settings, and what dropping its override costs. Neither belongs on the other two.

   `con.*` is the act-as-tenant picker: one page, two consoles behind one URL, so the picker
   only appears for a superadmin scoping into a workspace. `con.tenant.pick` is what a
   customer never sees and an operator sees before choosing. */

import type { Dict } from '../index';

export const en: Dict = {
  'con.tenant': 'Workspace',
  'con.tenant.none': 'No workspaces yet — create one in the console.',
  'con.tenant.pick': 'Choose a workspace to see this.',

  'tn.hist.rec': 'Recordings',
  'tn.hist.sum': 'Summaries',
  'tn.hist.rec.none': 'Nothing here yet — upload a call or paste a transcript in Analyse.',
  'tn.hist.sum.none': 'No summaries yet.',
  'tn.hist.open': 'Open in Analyse',

  'tn.th.source': 'Source',
  'tn.th.length': 'Length',
  'tn.th.who': 'By',

  'tn.who.unknown': 'not recorded',

  'tn.th.ran': 'Analysed',
  'tn.th.calls': 'Calls',
  'tn.th.summary': 'Summary',

  'tn.src.audio': 'Audio',
  'tn.src.text': 'Text',

  'tn.bands.heading': 'Score colours',
  'tn.bands.lead': 'Where a score changes colour on the scorecard and on the timeline.',
  'tn.bands.red': 'Red',
  'tn.bands.yellow': 'Yellow',
  'tn.bands.green': 'Green',
  'tn.bands.below': 'below',
  'tn.bands.upto': 'up to',
  'tn.bands.from': 'from',
  'tn.bands.andup': 'and above',
  'tn.bands.save': 'Save colours',
  'tn.bands.reset': 'Reset colours to default',
  'tn.bands.saved': 'Score colours updated.',

  'tn.sc.reset': 'Reset to default',
  'tn.sc.reset.heading': 'Reset this rubric to the default?',
  'tn.sc.reset.warn': 'The rubric is replaced by a copy of the shared default, saved as a new version — earlier versions stay in the history. Enter your own password to confirm.',
  'tn.sc.reset.pw': 'Your password',
  'tn.sc.reset.needpw': 'Enter your password.',
  'tn.sc.reset.bad': 'That password does not match.',
  'tn.sc.reset.done': 'The rubric was reset to the default.',
  'tn.sc.isdefault': 'You are looking at the shared default rubric — this workspace has none of its own yet. Saving creates your own copy, which later changes to the default will not touch.',

  'tn.tr.readonly': 'View only — only workspace owners can change the transcription settings.',
  'tn.tr.reset.confirm': 'Drop this workspace’s own transcription settings and go back to the inherited ones? Its key terms are discarded.',
};

export const ka: Dict = {
  'con.tenant': 'ორგანიზაცია',
  'con.tenant.none': 'ორგანიზაცია ჯერ არ არის — შექმენით კონსოლში.',
  'con.tenant.pick': 'ასარჩევად აირჩიეთ ორგანიზაცია.',

  'tn.hist.rec': 'ჩანაწერები',
  'tn.hist.sum': 'შეჯამებები',
  'tn.hist.rec.none': 'ჯერ არაფერია — ატვირთეთ ზარი ან ჩასვით ტრანსკრიპტი ანალიზის ჩანართში.',
  'tn.hist.sum.none': 'შეჯამებები ჯერ არ არის.',
  'tn.hist.open': 'გახსნა ანალიზის ჩანართში',

  'tn.th.source': 'წყარო',
  'tn.th.length': 'ხანგრძლივობა',
  'tn.th.who': 'ვინ',

  'tn.who.unknown': 'არ არის ჩაწერილი',

  'tn.th.ran': 'გაანალიზებულია',
  'tn.th.calls': 'ზარები',
  'tn.th.summary': 'შეჯამება',

  'tn.src.audio': 'აუდიო',
  'tn.src.text': 'ტექსტი',

  'tn.bands.heading': 'ქულის ფერები',
  'tn.bands.lead': 'რომელ ზღვარზე იცვლება ქულის ფერი შეფასების ბარათსა და დროის ხაზზე.',
  'tn.bands.red': 'წითელი',
  'tn.bands.yellow': 'ყვითელი',
  'tn.bands.green': 'მწვანე',
  'tn.bands.below': 'ქვემოთ',
  'tn.bands.upto': '—მდე',
  'tn.bands.from': '-დან',
  'tn.bands.andup': 'და ზემოთ',
  'tn.bands.save': 'ფერების შენახვა',
  'tn.bands.reset': 'ფერების ნაგულისხმევზე დაბრუნება',
  'tn.bands.saved': 'ქულის ფერები განახლდა.',

  'tn.sc.reset': 'ნაგულისხმევზე დაბრუნება',
  'tn.sc.reset.heading': 'დაბრუნდეს რუბრიკა ნაგულისხმევზე?',
  'tn.sc.reset.warn': 'რუბრიკა ჩანაცვლდება საერთო ნაგულისხმევის ასლით და შეინახება როგორც ახალი ვერსია — წინა ვერსიები ისტორიაში რჩება. დასადასტურებლად შეიყვანეთ თქვენი პაროლი.',
  'tn.sc.reset.pw': 'თქვენი პაროლი',
  'tn.sc.reset.needpw': 'შეიყვანეთ თქვენი პაროლი.',
  'tn.sc.reset.bad': 'პაროლი არ ემთხვევა.',
  'tn.sc.reset.done': 'რუბრიკა დაბრუნდა ნაგულისხმევზე.',
  'tn.sc.isdefault': 'ხედავთ საერთო ნაგულისხმევ რუბრიკას — ამ სამუშაო სივრცეს ჯერ საკუთარი არ აქვს. შენახვისას შეიქმნება თქვენი ასლი, რომელსაც ნაგულისხმევის შემდგომი ცვლილებები აღარ შეეხება.',

  'tn.tr.readonly': 'მხოლოდ სანახავად — ტრანსკრიფციის პარამეტრების შეცვლა მხოლოდ სამუშაო სივრცის მფლობელს შეუძლია.',
  'tn.tr.reset.confirm': 'წაიშალოს ამ სამუშაო სივრცის საკუთარი ტრანსკრიფციის პარამეტრები და დაბრუნდეს მემკვიდრეობითზე? მისი საკვანძო სიტყვები დაიკარგება.',
};

export const ru: Dict = {
  'con.tenant': 'Организация',
  'con.tenant.none': 'Организаций пока нет — создайте в консоли.',
  'con.tenant.pick': 'Выберите организацию, чтобы увидеть это.',

  'tn.hist.rec': 'Записи',
  'tn.hist.sum': 'Сводки',
  'tn.hist.rec.none': 'Здесь пока пусто — загрузите звонок или вставьте транскрипт на вкладке анализа.',
  'tn.hist.sum.none': 'Сводок пока нет.',
  'tn.hist.open': 'Открыть на вкладке анализа',

  'tn.th.source': 'Источник',
  'tn.th.length': 'Длительность',
  'tn.th.who': 'Кто',

  'tn.who.unknown': 'не записано',

  'tn.th.ran': 'Проанализировано',
  'tn.th.calls': 'Звонки',
  'tn.th.summary': 'Сводка',

  'tn.src.audio': 'Аудио',
  'tn.src.text': 'Текст',

  'tn.bands.heading': 'Цвета оценок',
  'tn.bands.lead': 'Где меняется цвет оценки на карточке и на шкале времени.',
  'tn.bands.red': 'Красный',
  'tn.bands.yellow': 'Жёлтый',
  'tn.bands.green': 'Зелёный',
  'tn.bands.below': 'ниже',
  'tn.bands.upto': 'до',
  'tn.bands.from': 'от',
  'tn.bands.andup': 'и выше',
  'tn.bands.save': 'Сохранить цвета',
  'tn.bands.reset': 'Сбросить цвета',
  'tn.bands.saved': 'Цвета оценок обновлены.',

  'tn.sc.reset': 'Сбросить к умолчанию',
  'tn.sc.reset.heading': 'Сбросить рубрику к значению по умолчанию?',
  'tn.sc.reset.warn': 'Рубрика будет заменена копией общей рубрики по умолчанию и сохранена как новая версия — прежние версии останутся в истории. Для подтверждения введите свой пароль.',
  'tn.sc.reset.pw': 'Ваш пароль',
  'tn.sc.reset.needpw': 'Введите свой пароль.',
  'tn.sc.reset.bad': 'Пароль не совпадает.',
  'tn.sc.reset.done': 'Рубрика сброшена к значению по умолчанию.',
  'tn.sc.isdefault': 'Перед вами общая рубрика по умолчанию — у этого рабочего пространства пока нет своей. При сохранении будет создана ваша копия, и дальнейшие изменения умолчания её не затронут.',

  'tn.tr.readonly': 'Только просмотр — изменять настройки транскрипции может только владелец рабочего пространства.',
  'tn.tr.reset.confirm': 'Удалить собственные настройки транскрипции этого рабочего пространства и вернуться к унаследованным? Его ключевые термины будут потеряны.',
};
