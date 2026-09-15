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

  'bot.policy': 'Answer policy',
  'bot.policy.kb_only': 'Shared documents only — no general knowledge',
  'bot.policy.kb_only.hint': 'Questions about your business, or related to it, that the shared documents don’t answer get your refusal message, and a colleague takes over. The bot still replies to greetings and to questions about today’s date, the time and your opening hours, steers questions unrelated to your business back on topic and counts them, and in an emergency gives safety guidance and passes the conversation to a colleague.',
  'bot.policy.general': 'Shared documents first, general knowledge for related questions',
  'bot.policy.general.hint': 'Questions within your field that the documents don’t cover get a short general answer that says it is general guidance — a hospital’s bot can say which kind of doctor treats a broken leg. Prices, deadlines and promises still come only from your documents. Questions unrelated to your business are steered back on topic and counted. In an emergency the bot gives safety guidance and passes the conversation to a colleague.',
  'bot.policy.confirm': 'Let the bot answer related questions from general knowledge? Those answers are not in your documents and nobody at your company approved them. The bot presents them as general guidance, and prices, deadlines and promises still come only from your documents.',
  'bot.policy.confirm.ok': 'Allow general answers',

  'bot.scope': 'About the business',
  'bot.scope.ph': 'We are an internet provider in Tbilisi: fibre for homes and businesses, TV and mobile plans.',
  'bot.scope.hint': 'One or two sentences on what your business does. The bot uses it to tell questions related to your business from unrelated ones.',

  'bot.hours': 'Opening hours',
  'bot.hours.tz': 'Time zone',
  'bot.hours.tz.hint': 'The bot works out today’s date, the time and whether you are open right now in this time zone.',
  'bot.hours.on': 'Tell the bot our opening hours',
  'bot.hours.day.mon': 'Monday',
  'bot.hours.day.tue': 'Tuesday',
  'bot.hours.day.wed': 'Wednesday',
  'bot.hours.day.thu': 'Thursday',
  'bot.hours.day.fri': 'Friday',
  'bot.hours.day.sat': 'Saturday',
  'bot.hours.day.sun': 'Sunday',
  'bot.hours.open': 'Open',
  'bot.hours.from': 'Opens',
  'bot.hours.to': 'Closes',
  'bot.hours.closed': 'Closed',
  'bot.hours.hint': 'A closing time earlier than the opening time means past midnight — for example 22:00–02:00.',
  'bot.hours.more': 'Also {ranges} — kept as it is when you save',
  'bot.hours.invalid': '{day}: set an opening and a closing time, and make them different.',
  'bot.hours.note': 'Note on opening hours',
  'bot.hours.note.ph': 'Closed on public holidays.',

  'bot.offtopic': 'Off-topic questions',
  'bot.offtopic.warn_after': 'Warn after (questions)',
  'bot.offtopic.cutoff_after': 'Stop after (questions)',
  'bot.offtopic.hint': 'Counts questions unrelated to your business within one conversation; 0 turns that step off. When the count reaches the warning number, the bot adds a warning to its reply. When it reaches the stop number, the bot stops answering anything your documents don’t clearly cover and points the customer to a person — it does not close the chat.',
  'bot.offtopic.order': '“Stop after” must be greater than “Warn after” — or turn one of them off with 0.',
  'bot.offtopic.range': '“Warn after” takes a whole number from 0 to 50, “Stop after” one from 0 to 100.',
  'bot.offtopic.warning': 'Warning text (empty = built-in wording)',
  'bot.offtopic.cutoff': 'Stop text (empty = built-in wording)',

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

  'bot.policy': 'პასუხების პოლიტიკა',
  'bot.policy.kb_only': 'მხოლოდ დაშვებული დოკუმენტები — ზოგადი ცოდნის გარეშე',
  'bot.policy.kb_only.hint': 'თქვენი ბიზნესის ან მასთან დაკავშირებულ კითხვებზე, რომლებზეც დაშვებულ დოკუმენტებში პასუხი არ არის, ბოტი თქვენი უარის ტექსტით პასუხობს და საუბარს კოლეგას გადასცემს. ამასთან, ბოტი მაინც პასუხობს მისალმებაზე და კითხვებზე დღევანდელ თარიღის, დროისა და თქვენი სამუშაო საათების შესახებ, ბიზნესთან დაუკავშირებელ კითხვებზე საუბარს თემაზე აბრუნებს და ასეთ კითხვებს ითვლის, გადაუდებელ შემთხვევაში კი უსაფრთხოების რეკომენდაციას იძლევა და საუბარს კოლეგას გადასცემს.',
  'bot.policy.general': 'ჯერ დაშვებული დოკუმენტები, თემასთან დაკავშირებულ კითხვებზე — ზოგადი ცოდნაც',
  'bot.policy.general.hint': 'თქვენი სფეროს კითხვებზე, რომლებსაც დოკუმენტები არ მოიცავს, ბოტი მოკლე ზოგად პასუხს იძლევა და კლიენტს ეუბნება, რომ ეს ზოგადი რეკომენდაციაა — მაგალითად, საავადმყოფოს ბოტს შეუძლია თქვას, რომელ ექიმს მიმართოს ფეხის მოტეხილობისას. ფასებს, ვადებსა და დაპირებებს ბოტი კვლავ მხოლოდ თქვენი დოკუმენტებიდან იღებს. ბიზნესთან დაუკავშირებელ კითხვებზე ის საუბარს თემაზე აბრუნებს და ასეთ კითხვებს ითვლის. გადაუდებელ შემთხვევაში ბოტი უსაფრთხოების რეკომენდაციას იძლევა და საუბარს კოლეგას გადასცემს.',
  'bot.policy.confirm': 'მივცე ბოტს უფლება, თემასთან დაკავშირებულ კითხვებზე ზოგადი ცოდნით უპასუხოს? ასეთ პასუხები თქვენს დოკუმენტებში არ არის და თქვენს კომპანიაში არავის დაუმტკიცებია. ბოტი მათ ზოგად რეკომენდაციად წარადგენს, ფასებს, ვადებსა და დაპირებებს კი კვლავ მხოლოდ დოკუმენტებიდან იღებს.',
  'bot.policy.confirm.ok': 'ზოგადი პასუხების დაშვება',

  'bot.scope': 'ბიზნესის შესახებ',
  'bot.scope.ph': 'ჩვენ თბილისის ინტერნეტ-პროვაიდერი ვართ: ოპტიკური ინტერნეტი სახლისა და ბიზნესისთვის, ტელევიზია და მობილური ტარიფები.',
  'bot.scope.hint': 'ერთ-ორ წინადადებით აღწერეთ, რითი დაკავებულია თქვენი ბიზნესი. ამ აღწერით ბოტი ასხვავებს თქვენს საქმიანობასთან დაკავშირებულ კითხვებს დაუკავშირებელისაგან.',

  'bot.hours': 'სამუშაო საათები',
  'bot.hours.tz': 'საათის სარტყელი',
  'bot.hours.tz.hint': 'ამ სარტყელის მიხედვით ბოტი განსაზღვრავს დღევანდელ თარიღს, დროს და იმას, ღია ხართ თუ არა ამ მომენტში.',
  'bot.hours.on': 'ბოტმა იცოდეს ჩვენი სამუშაო საათები',
  'bot.hours.day.mon': 'ორშაბათი',
  'bot.hours.day.tue': 'სამშაბათი',
  'bot.hours.day.wed': 'ოთხშაბათი',
  'bot.hours.day.thu': 'ხუთშაბათი',
  'bot.hours.day.fri': 'პარასკევი',
  'bot.hours.day.sat': 'შაბათი',
  'bot.hours.day.sun': 'კვირა',
  'bot.hours.open': 'ღია',
  'bot.hours.from': 'გახსნა',
  'bot.hours.to': 'დაკეტვა',
  'bot.hours.closed': 'დასვენების დღე',
  'bot.hours.hint': 'თუ დაკეტვის დრო გახსნის დროზე ადრეა, ეს შუაღამის შემდეგ დაკეტვას ნიშნავს — მაგალითად, 22:00–02:00.',
  'bot.hours.more': 'ასევე {ranges} — შენახვისას უცვლელი დარჩება',
  'bot.hours.invalid': '{day}: მიუთითეთ გახსნისა და დაკეტვის დრო — ისინი ერთმანეთისაგან უნდა განსხვავდებოდნენ.',
  'bot.hours.note': 'შენიშნა სამუშაო საათების შესახებ',
  'bot.hours.note.ph': 'სახელმწიფო დასვენების დღეებში არ ვმუშავებთ.',

  'bot.offtopic': 'თემას გარეშე კითხვები',
  'bot.offtopic.warn_after': 'რამდენი კითხვის შემდეგ გააფრთხილოს',
  'bot.offtopic.cutoff_after': 'რამდენი კითხვის შემდეგ შეჩერდეს',
  'bot.offtopic.hint': 'ბოტი ითვლის, რამდენი კითხვა არ ეხება თქვენს ბიზნესს ერთი საუბრის განმავლობაში; 0 ნიშნავს, რომ ეს ზღვარი გამორთულია. გაფრთხილების ზღვარზე ბოტი პასუხს გაფრთხილებას ამატებს. შეჩერების ზღვარზე ის ამარ პასუხობს ყველაფერზე, რასაც დოკუმენტები მკაფიოდ არ მოიცავს, და კლიენტს თანამშრომელთან დაკავშირებას ურჩევს — ჩატი არ დაიხურება.',
  'bot.offtopic.order': 'შეჩერების ზღვარი გაფრთხილების ზღვარზე მეტი უნდა იყოს — ან ერთ-ერთი გამორთეთ, მიუთითეთ 0.',
  'bot.offtopic.range': 'გაფრთხილების ზღვარი მთელი რიცხვი უნდა იყოს 0-დან 50-მდე, შეჩერების ზღვარი — 0-დან 100-მდე.',
  'bot.offtopic.warning': 'გაფრთხილების ტექსტი (ცარიელი = ჩაშენებული ფორმულირება)',
  'bot.offtopic.cutoff': 'შეჩერების ტექსტი (ცარიელი = ჩაშენებული ფორმულირება)',

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

  'bot.policy': 'Политика ответов',
  'bot.policy.kb_only': 'Только открытые боту документы — без общих знаний',
  'bot.policy.kb_only.hint': 'На вопросы о вашем бизнесе и смежные вопросы, ответа на которые нет в открытых документах, бот отвечает вашим текстом отказа и передаёт разговор коллеге. При этом бот по-прежнему отвечает на приветствия и на вопросы о сегодняшней дате, времени и ваших часах работы, возвращает к теме вопросы, не связанные с вашим бизнесом, и считает их, а в экстренной ситуации даёт рекомендации по безопасности и передаёт разговор коллеге.',
  'bot.policy.general': 'Сначала документы, на смежные вопросы — общие знания',
  'bot.policy.general.hint': 'На вопросы из вашей сферы, которых нет в документах, бот даёт короткий общий ответ и прямо говорит, что это общая рекомендация: например, бот больницы подскажет, к какому врачу идти с переломом ноги. Цены, сроки и обещания бот по-прежнему берёт только из ваших документов. Вопросы не по теме он возвращает к теме и считает. В экстренной ситуации бот даёт рекомендации по безопасности и передаёт разговор коллеге.',
  'bot.policy.confirm': 'Разрешить боту отвечать на смежные вопросы из общих знаний? Таких ответов нет в ваших документах, и никто в вашей компании их не утверждал. Бот подаёт их как общую рекомендацию, а цены, сроки и обещания по-прежнему берёт только из документов.',
  'bot.policy.confirm.ok': 'Разрешить общие ответы',

  'bot.scope': 'О бизнесе',
  'bot.scope.ph': 'Мы интернет-провайдер в Тбилиси: оптоволоконный интернет для дома и бизнеса, ТВ и мобильные тарифы.',
  'bot.scope.hint': 'Одно-два предложения о том, чем занимается ваш бизнес. По этому описанию бот отличает вопросы по вашей теме от посторонних.',

  'bot.hours': 'Часы работы',
  'bot.hours.tz': 'Часовой пояс',
  'bot.hours.tz.hint': 'По этому поясу бот определяет сегодняшнюю дату, время и открыты ли вы прямо сейчас.',
  'bot.hours.on': 'Сообщить боту наши часы работы',
  'bot.hours.day.mon': 'Понедельник',
  'bot.hours.day.tue': 'Вторник',
  'bot.hours.day.wed': 'Среда',
  'bot.hours.day.thu': 'Четверг',
  'bot.hours.day.fri': 'Пятница',
  'bot.hours.day.sat': 'Суббота',
  'bot.hours.day.sun': 'Воскресенье',
  'bot.hours.open': 'Открыто',
  'bot.hours.from': 'Открытие',
  'bot.hours.to': 'Закрытие',
  'bot.hours.closed': 'Выходной',
  'bot.hours.hint': 'Если время закрытия раньше времени открытия, это значит «после полуночи» — например, 22:00–02:00.',
  'bot.hours.more': 'Также {ranges} — при сохранении останется без изменений',
  'bot.hours.invalid': '{day}: укажите время открытия и закрытия, и они должны различаться.',
  'bot.hours.note': 'Примечание к часам работы',
  'bot.hours.note.ph': 'В государственные праздники не работаем.',

  'bot.offtopic': 'Вопросы не по теме',
  'bot.offtopic.warn_after': 'Предупредить после (вопросов)',
  'bot.offtopic.cutoff_after': 'Остановиться после (вопросов)',
  'bot.offtopic.hint': 'Считаются вопросы, не связанные с вашим бизнесом, в пределах одного разговора; 0 отключает этот шаг. Когда счёт доходит до порога предупреждения, бот добавляет к ответу предупреждение. Когда он доходит до порога остановки, бот перестаёт отвечать на всё, что явно не описано в документах, и советует клиенту обратиться к сотруднику — чат при этом не закрывается.',
  'bot.offtopic.order': '«Остановиться после» должно быть больше, чем «Предупредить после», — или отключите один из порогов, указав 0.',
  'bot.offtopic.range': '«Предупредить после» — целое число от 0 до 50, «Остановиться после» — от 0 до 100.',
  'bot.offtopic.warning': 'Текст предупреждения (пусто = встроенная формулировка)',
  'bot.offtopic.cutoff': 'Текст при остановке (пусто = встроенная формулировка)',

  'bot.handoff': 'Писать короткое резюме для человека, который перехватывает диалог',
  'bot.handoff.hint': 'Один дополнительный вызов модели, только при передаче. Выключено — оператор открывает диалог с нуля.',

  'bot.save': 'Сохранить настройки бота',
  'bot.saved': 'Настройки бота сохранены',
  'bot.version': 'Версия',
  'bot.loadfail': 'Не удалось загрузить настройки бота.',
  'bot.unavailable': 'Настройки бота пока недоступны на этом сервере.',
};
