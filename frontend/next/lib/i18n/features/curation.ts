/* The curation review queue (cur.*) — knowledge gaps the bot found, proposed as KB edits.

   A feature module even though `tenant.html` is the only page that renders the queue today,
   because the vocabulary is the feature's, not the page's: an operator console reviewing the
   same suggestions would need every one of these words unchanged.

   Two of them carry a safety property rather than a label. `cur.remove.word` is the literal
   string a reviewer must type to confirm hiding content ('HIDE') and `cur.remove.confirm`
   is the sentence that asks for it — translate the sentence, and the typed word has to match
   whatever `cur.remove.word` says in that same language or the confirmation can never
   succeed. `cur.foreign` marks a suggestion citing evidence from another tenant, which is
   the visible half of a cross-tenant leak check. */

import type { Dict } from '../index';

export const en: Dict = {
  'cur.tab': 'Review Queue',
  'cur.heading': 'Knowledge gaps to review',
  'cur.none': 'Nothing to review — the queue is clear.',
  'cur.loadfail': 'Could not load the review queue.',

  'cur.op.add': 'Add',
  'cur.op.update': 'Update',
  'cur.op.remove': 'Hide',

  'cur.priority': 'priority',
  'cur.asked': 'asked {n}×',
  'cur.sources': '{n} sources',
  'cur.confidence': 'confidence',
  'cur.risk': 'risk',
  'cur.window': 'window',
  'cur.evidence': 'What customers actually said',
  'cur.evidence.none': 'No quotes were captured for this cluster.',

  'cur.target': 'Target document',
  'cur.diff': 'Change against the current chunk',
  'cur.diff.current': 'Currently in the knowledge base',
  'cur.diff.proposed': 'Proposed replacement',
  'cur.diff.nochunk': 'Current chunk unavailable — showing the proposed text only.',

  'cur.proposed': 'Proposed content',
  'cur.accept': 'Accept',
  'cur.acceptedit': 'Accept with edits',
  'cur.decline': 'Decline',
  'cur.applied': 'Applied to the knowledge base',
  'cur.declinedok': 'Declined — this will stop coming back',

  'cur.edit.heading': 'Accept with edits',
  'cur.edit.hint': 'Edit the wording before it goes into the knowledge base. It is re-chunked and re-embedded on save.',

  'cur.decline.heading': 'Why are you declining?',
  'cur.decline.r.nottrue': 'Not true',
  'cur.decline.r.covered': 'Already covered',
  'cur.decline.r.dontsay': 'Don’t want the bot saying this',
  'cur.decline.r.temporary': 'Temporary / one-off',
  'cur.decline.pick': 'Pick a reason first.',

  'cur.bulk.accept': 'Accept selected',
  'cur.bulk.note': 'Bulk accept covers additions and updates only — removals are reviewed one at a time.',

  'cur.remove.heading': 'Hide this content?',
  'cur.remove.word': 'HIDE',
  'cur.remove.confirm': 'Type {word} to confirm.',
  'cur.remove.mismatch': 'That does not match — nothing was changed.',
  'cur.remove.note': 'This hides the content from answers. Nothing is deleted; an operator can still delete it by hand.',

  'cur.run': 'Run curation now',
  'cur.run.started': 'Curation run queued',

  'cur.st.pending': 'Pending',
  'cur.st.accepted': 'Accepted',
  'cur.st.declined': 'Declined',
  'cur.st.superseded': 'Superseded',
  'cur.st.apply_failed': 'Apply failed',

  'cur.filter.state': 'State',

  'cur.openjob': 'Open call',
  'cur.opensource': 'Open conversation',
  'cur.foreign': '⚠ cites evidence from another tenant',

  'cur.bulk.confirm': 'Accept {n} suggestions? They are applied to the knowledge base immediately.',
};

export const ka: Dict = {
  'cur.tab': 'განსახილველი',
  'cur.heading': 'გადასახედი ხარვეზები ცოდნის ბაზაში',
  'cur.none': 'გადასახედი არაფერია — რიგი ცარიელია.',
  'cur.loadfail': 'რიგის ჩატვირთვა ვერ მოხერხდა.',

  'cur.op.add': 'დამატება',
  'cur.op.update': 'განახლება',
  'cur.op.remove': 'დამალვა',

  'cur.priority': 'პრიორიტეტი',
  'cur.asked': 'იკითხეს {n}-ჯერ',
  'cur.sources': '{n} წყარო',
  'cur.confidence': 'სანდოობა',
  'cur.risk': 'რისკი',
  'cur.window': 'პერიოდი',
  'cur.evidence': 'რას ამბობდნენ მომხმარებლები',
  'cur.evidence.none': 'ამ ჯგუფისთვის ციტატები არ შენახულა.',

  'cur.target': 'სამიზნე დოკუმენტი',
  'cur.diff': 'ცვლილება მიმდინარე ფრაგმენტთან შედარებით',
  'cur.diff.current': 'ამჟამად ცოდნის ბაზაში',
  'cur.diff.proposed': 'შემოთავაზებული ჩანაცვლება',
  'cur.diff.nochunk': 'მიმდინარე ფრაგმენტი მიუწვდომელია — ნაჩვენებია მხოლოდ შემოთავაზებული ტექსტი.',

  'cur.proposed': 'შემოთავაზებული ტექსტი',
  'cur.accept': 'მიღება',
  'cur.acceptedit': 'მიღება რედაქტირებით',
  'cur.decline': 'უარყოფა',
  'cur.applied': 'ცოდნის ბაზაში აისახა',
  'cur.declinedok': 'უარყოფილია — აღარ გამოჩნდება',

  'cur.edit.heading': 'მიღება რედაქტირებით',
  'cur.edit.hint': 'დაარედაქტირეთ ტექსტი ცოდნის ბაზაში შესვლამდე. შენახვისას ის ხელახლა დანაწევრდება და ემბედინგები განახლდება.',

  'cur.decline.heading': 'რატომ უარყოფთ?',
  'cur.decline.r.nottrue': 'მცდარია',
  'cur.decline.r.covered': 'ბაზაში უკვე არსებობს',
  'cur.decline.r.dontsay': 'არ მინდა, რომ ბოტმა ეს თქვას',
  'cur.decline.r.temporary': 'დროებითი / ერთჯერადი',
  'cur.decline.pick': 'ჯერ აირჩიეთ მიზეზი.',

  'cur.bulk.accept': 'მონიშნულის მიღება',
  'cur.bulk.note': 'მასობრივი მიღება მხოლოდ დამატებასა და განახლებაზე მოქმედებს — დამალვა თითო-თითოდ განიხილება.',

  'cur.remove.heading': 'დაადასტურეთ დამალვა',
  'cur.remove.word': 'დამალვა',
  'cur.remove.confirm': 'დასადასტურებლად აკრიფეთ {word}.',
  'cur.remove.mismatch': 'არ ემთხვევა — არაფერი შეცვლილა.',
  'cur.remove.note': 'ეს ტექსტი პასუხებში აღარ გამოჩნდება. არაფერი იშლება — ოპერატორს კვლავ შეუძლია მისი ხელით წაშლა.',

  'cur.run': 'კურაციის გაშვება',
  'cur.run.started': 'კურაცია რიგში დადგა',

  'cur.st.pending': 'მოლოდინში',
  'cur.st.accepted': 'მიღებული',
  'cur.st.declined': 'უარყოფილი',
  'cur.st.superseded': 'ჩანაცვლებული',
  'cur.st.apply_failed': 'ვერ აისახა',

  'cur.filter.state': 'სტატუსი',

  'cur.openjob': 'ზარის გახსნა',
  'cur.opensource': 'საუბრის გახსნა',
  'cur.foreign': '⚠ იყენებს სხვა ორგანიზაციის მტკიცებულებას',

  'cur.bulk.confirm': 'მიიღოთ {n} შემოთავაზება? ისინი ცოდნის ბაზაში მაშინვე აისახება.',
};

export const ru: Dict = {
  'cur.tab': 'На проверку',
  'cur.heading': 'Пробелы в базе знаний',
  'cur.none': 'Нечего проверять — очередь пуста.',
  'cur.loadfail': 'Не удалось загрузить очередь проверки.',

  'cur.op.add': 'Добавить',
  'cur.op.update': 'Обновить',
  'cur.op.remove': 'Скрыть',

  'cur.priority': 'приоритет',
  'cur.asked': 'спросили {n}×',
  'cur.sources': 'источников: {n}',
  'cur.confidence': 'уверенность',
  'cur.risk': 'риск',
  'cur.window': 'период',
  'cur.evidence': 'Что говорили клиенты',
  'cur.evidence.none': 'Цитаты для этой группы не сохранены.',

  'cur.target': 'Целевой документ',
  'cur.diff': 'Изменение относительно текущего фрагмента',
  'cur.diff.current': 'Сейчас в базе знаний',
  'cur.diff.proposed': 'Предлагаемая замена',
  'cur.diff.nochunk': 'Текущий фрагмент недоступен — показан только предлагаемый текст.',

  'cur.proposed': 'Предлагаемый текст',
  'cur.accept': 'Принять',
  'cur.acceptedit': 'Принять с правками',
  'cur.decline': 'Отклонить',
  'cur.applied': 'Добавлено в базу знаний',
  'cur.declinedok': 'Отклонено — больше не появится',

  'cur.edit.heading': 'Принять с правками',
  'cur.edit.hint': 'Отредактируйте текст перед добавлением в базу. При сохранении он будет заново разбит и переэмбеддён.',

  'cur.decline.heading': 'Почему отклоняете?',
  'cur.decline.r.nottrue': 'Неправда',
  'cur.decline.r.covered': 'Уже покрыто',
  'cur.decline.r.dontsay': 'Не хочу, чтобы бот это говорил',
  'cur.decline.r.temporary': 'Временное / разовое',
  'cur.decline.pick': 'Сначала выберите причину.',

  'cur.bulk.accept': 'Принять выбранные',
  'cur.bulk.note': 'Массовое принятие работает только для добавлений и обновлений — скрытие проверяется по одному.',

  'cur.remove.heading': 'Скрыть этот текст?',
  'cur.remove.word': 'СКРЫТЬ',
  'cur.remove.confirm': 'Введите {word} для подтверждения.',
  'cur.remove.mismatch': 'Не совпадает — ничего не изменено.',
  'cur.remove.note': 'Текст будет скрыт из ответов. Ничего не удаляется; оператор может удалить вручную.',

  'cur.run': 'Запустить курацию',
  'cur.run.started': 'Курация поставлена в очередь',

  'cur.st.pending': 'Ожидает',
  'cur.st.accepted': 'Принято',
  'cur.st.declined': 'Отклонено',
  'cur.st.superseded': 'Заменено',
  'cur.st.apply_failed': 'Ошибка применения',

  'cur.filter.state': 'Статус',

  'cur.openjob': 'Открыть звонок',
  'cur.opensource': 'Открыть диалог',
  'cur.foreign': '⚠ ссылается на данные другой организации',

  'cur.bulk.confirm': 'Принять предложений: {n}? Они сразу применяются к базе знаний.',
};
