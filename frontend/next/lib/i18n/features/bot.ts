/* The chat bot (bot.*) — autopilot, grounding, handoff, and the settings that shape them.

   Genuinely shared, and by two surfaces that must say the same thing: the tenant's BOT tab
   in `tenant.html` edits a workspace's own config, and the console's 'Default bot' tab in
   `admin.html` edits the baseline every workspace inherits when it has saved none. Same
   fields, same words, two owners — so one module.

   The operator's kill switch is NOT here: it is admin-only and lives with the console page
   (kill.* in pages/console.ts). */

import type { Dict } from '../index';

export const en: Dict = {
  'bot.heading': 'Public bot',
  'bot.autopilot': 'Autopilot — the bot answers customers with no human in the loop',
  'bot.autopilot.hint': 'With autopilot off the bot only drafts replies for an operator to review and send. Nothing reaches a customer unread.',

  'bot.state.live': 'Answering customers',
  'bot.state.off': 'Drafts only — a human sends every reply',
  'bot.state.killed': 'Stopped by CommuniQ',

  'bot.killed.note': 'CommuniQ support has stopped autopilot. Your settings are kept; the bot hands every conversation to a human until it is resumed.',

  'bot.needpublic.title': 'Autopilot needs at least one document shared with the bot',
  'bot.needpublic.body': 'Knowledge base documents are internal by default, and the bot may only quote documents you have shared with it. If none are shared, the bot refuses every question. Share the documents your customers are allowed to read, then enable autopilot. Nothing is made public on the internet.',
  'bot.needpublic.link': 'Open the knowledge base',

  'bot.persona': 'Persona',
  'bot.persona.ph': 'You are the support assistant for … Be brief, warm and concrete.',

  'bot.greeting': 'Greeting',
  'bot.refusal': 'Refusal copy — what the bot says when your knowledge base has no answer',
  'bot.refusal.hint': 'This is the sentence a customer sees most often. Write it in every language the bot answers in; it should offer a human, not apologise twice.',
  'bot.refusal.missing': 'Write the refusal copy in {lang} before turning autopilot on.',

  'bot.lang.en': 'English',
  'bot.lang.ka': 'Georgian',
  'bot.lang.ru': 'Russian',

  'bot.languages': 'Languages the bot answers in',
  'bot.languages.pickone': 'Pick at least one language.',

  'bot.escalation': 'Escalation keywords',
  'bot.escalation.ph': 'lawyer, complaint, chargeback',
  'bot.escalation.hint': 'Comma-separated. A match hands the conversation to a human immediately, before any answer is generated.',

  'bot.retrieval': 'Retrieval & reply limits',
  'bot.minscore': 'Minimum match score before the bot answers (0–1)',
  'bot.minhits': 'Minimum matching passages before the bot answers',
  'bot.topk': 'Passages retrieved per question',
  'bot.suggestions': 'Suggested replies per turn (drafts mode)',
  'bot.maxchars': 'Max reply characters',
  'bot.caps': 'Rate caps',

  'bot.cap.tenant': 'Operator drafts / minute (whole workspace)',
  'bot.cap.enduser': 'Operator drafts / hour (one customer)',
  'bot.cap.answer_tenant': 'Bot answers / minute (whole workspace)',
  'bot.cap.answer_enduser': 'Bot answers / hour (one customer)',
  'bot.cap.hint': 'Empty means the built-in default. Drafts are what an operator sees before sending; answers are what the bot sends to a customer on its own.',

  'bot.disclosure': 'AI disclosure',
  'bot.disclosure.hint': 'A line appended to the bot’s replies so the customer knows they are talking to software. Added by code after generation, so nothing a customer types can remove it.',
  'bot.disclosure.mode': 'When to show it',
  'bot.disclosure.first': 'First reply in a conversation',
  'bot.disclosure.always': 'Every reply',
  'bot.disclosure.off': 'Never — the channel discloses in its own interface',
  'bot.disclosure.text': 'Disclosure text (empty = built-in wording)',

  'bot.isdefault': 'You are looking at the shared default bot settings — this workspace has none of its own yet. Saving creates your own copy, which later changes to the default will not touch.',

  'bot.autopilot.default.note': 'Autopilot is never on by default. Each workspace turns its own bot on from its portal, and only after sharing at least one document with it.',

  'bot.general': 'Answer from general knowledge when the knowledge base has nothing',
  'bot.general.risk': 'Risk choice, off by default. Left off, the bot refuses with your copy above and offers a human — it can only ever repeat what you published. Turned on, it may answer from the model’s own knowledge, which is not your policy, is not auditable, and can be confidently wrong about your prices, rules and deadlines.',
  'bot.general.confirm': 'Let the bot answer from the model’s general knowledge? It will then say things that are not in your knowledge base and that nobody at your company approved.',
  'bot.general.on': 'Turn it on',

  'bot.handoff': 'Write a short summary for the human who takes over',
  'bot.handoff.hint': 'Costs one extra model call, only on handoffs. Off means the operator opens a cold conversation.',

  'bot.save': 'Save bot settings',
  'bot.saved': 'Bot settings saved',
  'bot.version': 'Version',
  'bot.loadfail': 'Could not load the bot settings.',
  'bot.unavailable': 'Bot settings are not available on this server yet.',
};

export const ka: Dict = {
  'bot.heading': 'საჯარო ბოტი',
  'bot.autopilot': 'ავტოპილოტი — ბოტი პასუხობს კლიენტს ადამიანის ჩარევის გარეშე',
  'bot.autopilot.hint': 'გამორთული ავტოპილოტის დროს ბოტი მხოლოდ ამზადებს პასუხის მონახაზს, რომელსაც ოპერატორი ამოწმებს და აგზავნის. კლიენტამდე წაუკითხავი არაფერი მიდის.',

  'bot.state.live': 'პასუხობს კლიენტებს',
  'bot.state.off': 'მხოლოდ მონახაზები — პასუხს ადამიანი აგზავნის',
  'bot.state.killed': 'შეჩერებულია CommuniQ-ის მიერ',

  'bot.killed.note': 'CommuniQ-ის მხარდაჭერის გუნდმა ავტოპილოტი შეაჩერა. თქვენი პარამეტრები შენახულია; აღდგენამდე ბოტი ყველა საუბარს ადამიანს გადასცემს.',

  'bot.needpublic.title': 'ავტოპილოტს სჭირდება ბოტისთვის დაშვებული მინიმუმ ერთი დოკუმენტი',
  'bot.needpublic.body': 'ცოდნის ბაზის დოკუმენტები ნაგულისხმევად შიდაა და ბოტი მხოლოდ დაშვებულ დოკუმენტებს ციტირებს. თუ არცერთი არ არის დაშვებული, ბოტი ყველა კითხვაზე უარს იტყვის. დაუშვით ის დოკუმენტები, რომელთა წაკითხვის უფლებაც კლიენტს აქვს, და შემდეგ ჩართეთ ავტოპილოტი. ინტერნეტში არაფერი ქვეყნდება.',
  'bot.needpublic.link': 'ცოდნის ბაზის გახსნა',

  'bot.persona': 'პერსონა',
  'bot.persona.ph': 'თქვენ ხართ … მხარდაჭერის ასისტენტი. იყავით ლაკონიური, თბილი და კონკრეტული.',

  'bot.greeting': 'მისალმება',
  'bot.refusal': 'უარის ტექსტი — რას ამბობს ბოტი, როცა ცოდნის ბაზაში პასუხი არ არის',
  'bot.refusal.hint': 'ეს ის წინადადებაა, რომელსაც კლიენტი ყველაზე ხშირად ხედავს. დაწერეთ ყველა ენაზე, რომელზეც ბოტი პასუხობს; ტექსტი ადამიანის დახმარებას უნდა სთავაზობდეს და არა ორჯერ ბოდიშობდეს.',
  'bot.refusal.missing': 'ავტოპილოტის ჩართვამდე დაწერეთ უარის ტექსტი {lang} ენაზე.',

  'bot.lang.en': 'ინგლისური',
  'bot.lang.ka': 'ქართული',
  'bot.lang.ru': 'რუსული',

  'bot.languages': 'ენები, რომლებზეც ბოტი პასუხობს',
  'bot.languages.pickone': 'აირჩიეთ მინიმუმ ერთი ენა.',

  'bot.escalation': 'ესკალაციის საკვანძო სიტყვები',
  'bot.escalation.ph': 'ადვოკატი, საჩივარი, თანხის დაბრუნება',
  'bot.escalation.hint': 'მძიმით გამოყოფილი. დამთხვევისას საუბარი მაშინვე ადამიანს გადაეცემა, პასუხის გენერაციამდე.',

  'bot.retrieval': 'ძიება და პასუხის ლიმიტები',
  'bot.minscore': 'დამთხვევის მინიმალური ქულა პასუხამდე (0–1)',
  'bot.minhits': 'დამთხვეული ფრაგმენტების მინიმუმი პასუხამდე',
  'bot.topk': 'მოძიებული ფრაგმენტები თითო კითხვაზე',
  'bot.suggestions': 'შეთავაზებული პასუხები თითო რეპლიკაზე (დრაფტების რეჟიმი)',
  'bot.maxchars': 'პასუხის მაქს. სიმბოლოები',
  'bot.caps': 'სიხშირის ლიმიტები',

  'bot.cap.tenant': 'ოპერატორის მონახაზები / წუთში (მთელი სამუშაო სივრცე)',
  'bot.cap.enduser': 'ოპერატორის მონახაზები / საათში (ერთი კლიენტი)',
  'bot.cap.answer_tenant': 'ბოტის პასუხები / წუთში (მთელი სამუშაო სივრცე)',
  'bot.cap.answer_enduser': 'ბოტის პასუხები / საათში (ერთი კლიენტი)',
  'bot.cap.hint': 'ცარიელი ნიშნავს ჩაშენებულ ნაგულისხმევს. მონახაზი ის არის, რასაც ოპერატორი გაგზავნამდე ხედავს; პასუხი ის, რასაც ბოტი კლიენტს თავად უგზავნის.',

  'bot.disclosure': 'AI-ს შესახებ შეტყობინება',
  'bot.disclosure.hint': 'ბოტის პასუხებს ემატება ერთი სტრიქონი, რომ კლიენტმა იცოდეს, რომ პროგრამასთან საუბრობს. კოდი ამატებს გენერაციის შემდეგ, ამიტომ კლიენტის დაწერილი ვერაფერი ვერ მოხსნის.',
  'bot.disclosure.mode': 'როდის გამოჩნდეს',
  'bot.disclosure.first': 'საუბრის პირველ პასუხზე',
  'bot.disclosure.always': 'ყველა პასუხზე',
  'bot.disclosure.off': 'არასდროს — არხი თავად აჩვენებს თავის ინტერფეისში',
  'bot.disclosure.text': 'შეტყობინების ტექსტი (ცარიელი = ჩაშენებული ფორმულირება)',

  'bot.isdefault': 'თქვენ ხედავთ ბოტის საზიარო ნაგულისხმევ პარამეტრებს — ამ სამუშაო სივრცეს საკუთარი ჯერ არ აქვს. შენახვა შექმნის თქვენს ასლს, რომელსაც ნაგულისხმევის მომდევნო ცვლილებები არ შეეხება.',

  'bot.autopilot.default.note': 'ავტოპილოტი ნაგულისხმევად არასდროს არის ჩართული. თითოეული სამუშაო სივრცე საკუთარ ბოტს თავად რთავს პორტალიდან, და მხოლოდ მას შემდეგ, რაც მინიმუმ ერთ დოკუმენტს დაუშვებს.',

  'bot.general': 'უპასუხოს ზოგადი ცოდნით, როცა ცოდნის ბაზაში არაფერია',
  'bot.general.risk': 'სარისკო არჩევანია, ნაგულისხმევად გამორთული. თუ გამორთულია, ბოტი ზემოთ მითითებული ტექსტით ამბობს უარს და ადამიანს სთავაზობს — ის მხოლოდ იმას იმეორებს, რაც თქვენ ბოტისთვის დაუშვით. თუ ჩართულია, შესაძლოა მოდელის საკუთარი ცოდნით უპასუხოს — ეს არ არის თქვენი პოლიტიკა, არ ექვემდებარება აუდიტს და შეიძლება დარწმუნებით შეცდეს თქვენს ფასებში, წესებსა და ვადებში.',
  'bot.general.confirm': 'მივცე ბოტს უფლება, მოდელის ზოგადი ცოდნით უპასუხოს? მაშინ ის იტყვის ისეთ რამეს, რაც თქვენს ცოდნის ბაზაში არ არის და თქვენს კომპანიაში არავის დაუმტკიცებია.',
  'bot.general.on': 'ჩართვა',

  'bot.handoff': 'დაწეროს მოკლე შეჯამება ადამიანისთვის, რომელიც საუბარს გადაიბარებს',
  'bot.handoff.hint': 'საჭიროებს მოდელის ერთ დამატებით გამოძახებას, მხოლოდ გადაცემისას. თუ გამორთულია, ოპერატორი საუბარს კონტექსტის გარეშე იღებს.',

  'bot.save': 'ბოტის პარამეტრების შენახვა',
  'bot.saved': 'ბოტის პარამეტრები შენახულია',
  'bot.version': 'ვერსია',
  'bot.loadfail': 'ბოტის პარამეტრები ვერ ჩაიტვირთა.',
  'bot.unavailable': 'ბოტის პარამეტრები ამ სერვერზე ჯერ ხელმისაწვდომი არ არის.',
};

export const ru: Dict = {
  'bot.heading': 'Публичный бот',
  'bot.autopilot': 'Автопилот — бот отвечает клиентам без участия человека',
  'bot.autopilot.hint': 'При выключенном автопилоте бот только готовит черновики ответов, которые оператор проверяет и отправляет. Клиенту не уходит ни одного непрочитанного сообщения.',

  'bot.state.live': 'Отвечает клиентам',
  'bot.state.off': 'Только черновики — ответ отправляет человек',
  'bot.state.killed': 'Остановлен CommuniQ',

  'bot.killed.note': 'Поддержка CommuniQ остановила автопилот. Настройки сохранены; до возобновления бот передаёт все диалоги человеку.',

  'bot.needpublic.title': 'Автопилоту нужен хотя бы один документ, доступный боту',
  'bot.needpublic.body': 'Документы базы знаний по умолчанию внутренние, и бот может цитировать только документы, которые вы ему открыли. Если не открыт ни один, бот отказывает на каждый вопрос. Откройте боту документы, которые вашим клиентам разрешено читать, затем включите автопилот. В интернете ничего не публикуется.',
  'bot.needpublic.link': 'Открыть базу знаний',

  'bot.persona': 'Персона',
  'bot.persona.ph': 'Вы — ассистент поддержки … Отвечайте кратко, доброжелательно и конкретно.',

  'bot.greeting': 'Приветствие',
  'bot.refusal': 'Текст отказа — что бот говорит, когда в базе знаний нет ответа',
  'bot.refusal.hint': 'Эту фразу клиент видит чаще всего. Напишите её на всех языках, на которых отвечает бот; она должна предлагать человека, а не извиняться дважды.',
  'bot.refusal.missing': 'Напишите текст отказа на языке «{lang}» перед включением автопилота.',

  'bot.lang.en': 'Английский',
  'bot.lang.ka': 'Грузинский',
  'bot.lang.ru': 'Русский',

  'bot.languages': 'Языки, на которых отвечает бот',
  'bot.languages.pickone': 'Выберите хотя бы один язык.',

  'bot.escalation': 'Ключевые слова эскалации',
  'bot.escalation.ph': 'юрист, жалоба, возврат платежа',
  'bot.escalation.hint': 'Через запятую. Совпадение сразу передаёт диалог человеку, до генерации любого ответа.',

  'bot.retrieval': 'Поиск и лимиты ответа',
  'bot.minscore': 'Минимальный балл совпадения до ответа (0–1)',
  'bot.minhits': 'Минимум совпавших фрагментов до ответа',
  'bot.topk': 'Фрагментов на вопрос',
  'bot.suggestions': 'Подсказок на реплику (режим черновиков)',
  'bot.maxchars': 'Макс. символов в ответе',
  'bot.caps': 'Лимиты частоты',

  'bot.cap.tenant': 'Черновики для оператора / минуту (вся организация)',
  'bot.cap.enduser': 'Черновики для оператора / час (один клиент)',
  'bot.cap.answer_tenant': 'Ответы бота / минуту (вся организация)',
  'bot.cap.answer_enduser': 'Ответы бота / час (один клиент)',
  'bot.cap.hint': 'Пусто — значит встроенное значение по умолчанию. Черновики — то, что оператор видит перед отправкой; ответы — то, что бот сам отправляет клиенту.',

  'bot.disclosure': 'Уведомление об ИИ',
  'bot.disclosure.hint': 'Строка, добавляемая к ответам бота, чтобы клиент знал, что общается с программой. Добавляется кодом после генерации, поэтому ничто написанное клиентом не может её убрать.',
  'bot.disclosure.mode': 'Когда показывать',
  'bot.disclosure.first': 'В первом ответе диалога',
  'bot.disclosure.always': 'В каждом ответе',
  'bot.disclosure.off': 'Никогда — канал сообщает об этом в своём интерфейсе',
  'bot.disclosure.text': 'Текст уведомления (пусто = встроенная формулировка)',

  'bot.isdefault': 'Вы видите общие настройки бота по умолчанию — у этой организации своих ещё нет. Сохранение создаст вашу копию, которую последующие изменения значений по умолчанию не затронут.',

  'bot.autopilot.default.note': 'Автопилот никогда не включён по умолчанию. Каждая организация включает своего бота сама из портала, и только после того, как откроет боту хотя бы один документ.',

  'bot.general': 'Отвечать из общих знаний, когда в базе знаний ничего нет',
  'bot.general.risk': 'Рискованный выбор, по умолчанию выключен. Выключено — бот отказывает вашим текстом выше и предлагает человека; он способен повторить только то, что вы опубликовали. Включено — он может ответить из собственных знаний модели: это не ваша политика, это не проверяемо и он может уверенно ошибиться в ваших ценах, правилах и сроках.',
  'bot.general.confirm': 'Разрешить боту отвечать из общих знаний модели? Тогда он будет говорить то, чего нет в вашей базе знаний и что никто в вашей компании не утверждал.',
  'bot.general.on': 'Включить',

  'bot.handoff': 'Писать короткое резюме для человека, который перехватывает диалог',
  'bot.handoff.hint': 'Один дополнительный вызов модели, только при передаче. Выключено — оператор открывает диалог с нуля.',

  'bot.save': 'Сохранить настройки бота',
  'bot.saved': 'Настройки бота сохранены',
  'bot.version': 'Версия',
  'bot.loadfail': 'Не удалось загрузить настройки бота.',
  'bot.unavailable': 'Настройки бота пока недоступны на этом сервере.',
};
