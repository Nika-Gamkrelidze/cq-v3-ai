/* The call workbench (wb.*) — the panel that opens on a recording and runs the AI over it.

   It is a FEATURE module, not a page one, because the workbench is a shared component: the
   legacy `workbench.js` is loaded by `account.html` and `tenant.html` today and the ported
   `<Workbench>` is written to be mounted by any surface that has recordings. Three of these
   keys are already referenced from `account.html` directly (it labels the workbench's own
   controls), which is exactly the shape that makes a per-page copy wrong.

   These strings did not live in `brand.js` at all — `workbench.js` registered them at load
   time with `CQ.extendDict({...})`, so the workbench carried its own vocabulary and a page
   that did not load the script never paid for it. That mechanism has no equivalent here (the
   dictionary is assembled once, at import), so the block moves in whole rather than being
   split across the pages that mount the component. */

import type { Dict } from '../index';

export const en: Dict = {
  'wb.src.title': 'Source',
  'wb.src.audio': 'Upload a recording',
  'wb.src.paste': 'Paste transcript',

  'wb.drop.sub': 'Audio or video — transcribed with ElevenLabs Scribe, then analysed on demand. Several files at once are only for Summarise.',
  'wb.drop.sub_multi': 'Several related calls with the same people — transcribed in order and summarised together.',

  'wb.files.n': '{n} files queued',

  'wb.file.remove': 'Remove file',

  'wb.onefile': 'One recording at a time here — the previous file was replaced. Use Summarise for several related calls.',
  'wb.toomany': 'Up to {max} files per summary.',

  'wb.toobig.total': 'Up to {max} of audio in one summary.',

  'wb.paste.ph': 'Paste the call transcript — one line per turn, optionally starting with the speaker, e.g. “Agent: …”',
  'wb.paste.hint': 'A pasted transcript has no player: findings are highlighted in the text instead, and voice tone is not available.',

  'wb.upload': 'Transcribe',
  'wb.upload.text': 'Use this transcript',
  'wb.upload.sum': 'Transcribe & summarise',

  /* The transcription-override panel in the source card. Everything else it says comes from
     the shared `tr.*` vocabulary (features/transcription.ts) — the same words the console and
     the workspace use for the same four settings. These two are the workbench's own: one names
     the consequence of diarisation being off in a one-line chip, the other is the only key-term
     rule the shared set has no sentence for. */
  'wb.tr.diarize.off': 'Speakers not separated',
  'wb.tr.keyterms.toomany': 'Up to {max} key terms.',

  'wb.needsource': 'Add a recording or paste a transcript first.',
  'wb.needtext': 'Paste a transcript first.',

  'wb.stage.transcribing': 'Transcribing…',
  'wb.stage.transcribing_n': 'Transcribing {name} ({i} of {n})…',
  'wb.stage.summarising': 'Summarising the calls…',

  'wb.cancelled': 'Upload cancelled.',
  'wb.fail': 'Upload failed.',
  'wb.change': 'Change',

  'wb.src.text': 'Pasted transcript',
  'wb.src.calls': '{n} calls',

  'wb.turns': '{n} turns',
  'wb.noaudio': 'The recording is no longer stored — only the transcript and its results remain.',
  'wb.audiofail': 'Could not load the audio — showing the transcript only.',
  'wb.notl': 'Timeline unavailable — showing the transcript.',

  'wb.tab.factcheck': 'Fact-check',
  'wb.tab.score': 'Score',
  'wb.tab.semantic': 'Sentiment',
  'wb.tab.summarise': 'Summarise',

  'wb.fc.note': 'Checks only the correctness of information in the call against your knowledge base.',

  'wb.run.factcheck': 'Check the facts',
  'wb.run.score': 'Score the call',
  'wb.run.semantic': 'Analyse the tone',
  'wb.run.summarise': 'Summarise',

  'wb.rerun': 'Run again',
  'wb.running': 'Working…',

  'wb.sc.note': 'Scores the call against your active rubric.',
  'wb.sc.edit': 'Edit the rubric',
  'wb.sc.default': 'default rubric',

  'wb.sem.note': 'Judges how the conversation was conducted — the words, and with audio, the voice too.',
  'wb.sem.words': 'Words',
  'wb.sem.voice': 'Voice tone',
  'wb.sem.voice.off': 'Voice tone needs an audio recording.',
  'wb.sem.pickone': 'Tick Words or Voice tone.',
  'wb.sem.words.tip': 'Judges only what was said: the politeness, curtness or rudeness of each turn.',
  'wb.sem.voice.tip': 'Rates how each speaker sounds — aggressive, tense, calm or patient — from the audio itself, not the words.',
  'wb.sem.guidance': 'Guidance',
  'wb.sem.title': 'Sentiment analysis',
  'wb.sem.politeness': 'Politeness',
  'wb.sem.flags': 'Flags',
  'wb.sem.turns': 'Turn by turn',
  'wb.sem.novoice': 'Voice tone was not available for this recording.',

  'wb.novoice.timeout': 'The voice-tone service did not answer in time. Try again.',
  'wb.novoice.warming': 'The voice model is still loading. Try again in a minute.',
  'wb.novoice.model_error': 'The voice model could not be loaded on the server. Ask your operator to check the voice-tone service.',
  'wb.novoice.unreachable': 'The voice-tone service is not responding. Ask your operator to check it.',
  'wb.novoice.disabled': 'Voice tone is not switched on for this deployment.',
  'wb.novoice.error': 'The voice-tone service returned an unexpected answer.',
  'wb.novoice.no_timestamps': 'This recording has no per-turn timings, so the audio cannot be split by speaker turn. Recordings made before timed transcripts, and pasted transcripts, are affected.',
  'wb.novoice.no_audio': 'This is a pasted transcript — there is no audio to listen to.',

  'wb.sem.share_good': 'calm',
  'wb.sem.share_bad': 'tense',
  'wb.sem.summary': 'Overall',

  'wb.confidence': 'confidence',

  'wb.tone.polite': 'polite',
  'wb.tone.neutral': 'neutral',
  'wb.tone.curt': 'curt',
  'wb.tone.impolite': 'impolite',
  'wb.tone.rude': 'rude',
  'wb.tone.aggressive': 'aggressive',

  'wb.voice.aggressive': 'aggressive',
  'wb.voice.tense': 'tense',
  'wb.voice.calm': 'calm',
  'wb.voice.patient': 'patient',
  'wb.voice.unknown': 'unknown',

  'wb.vl.angry': 'angry',
  'wb.vl.frustrated': 'frustrated',
  'wb.vl.disgusted': 'disgusted',
  'wb.vl.fearful': 'fearful',
  'wb.vl.sad': 'sad',
  'wb.vl.neutral': 'neutral',
  'wb.vl.calm': 'calm',
  'wb.vl.happy': 'happy',
  'wb.vl.excited': 'excited',
  'wb.vl.other': 'other',
  'wb.vl.unknown': 'unknown',

  'wb.role.agent': 'Agent',
  'wb.role.customer': 'Customer',
  'wb.role.other': 'Other',
  'wb.role.unknown': 'Unknown',

  'wb.speaker': 'Speaker {n}',

  'wb.sum.note': 'One or several related calls — a short summary, the key points and the full transcripts.',
  'wb.sum.title': 'Summary',
  'wb.sum.participants': 'Participants',
  'wb.sum.calls': 'Calls',
  'wb.sum.outcome': 'Outcome',
  'wb.sum.transcripts': 'Full transcripts',
  'wb.sum.appears': 'calls {list}',
  'wb.sum.needaudio': 'Summarise works on audio recordings — add one or more files in the source card.',
  'wb.sum.done': 'Summary ready',

  'wb.fc.done': 'Fact-check ready',

  'wb.sem.done': 'Tone analysis ready',

  'wb.fc.partial': 'partially supported',

  'wb.call': 'Call {n}',
  'wb.seek': 'Jump to this moment',

  'wb.lane.factcheck': 'Fact-check',
  'wb.lane.words': 'Sentiment Words',
  'wb.lane.voice': 'Sentiment Voice',

  'wb.sc.save': 'Save scores',
  'wb.sc.cancel': 'Cancel',
  'wb.sc.whynote': 'Why are you changing this? (optional)',
  'wb.sc.saved': 'Scores updated.',
  'wb.sc.history': 'Show history',
  'wb.sc.hide': 'Hide history',
  'wb.sc.nohistory': 'No changes yet — these are the original scores.',
  'wb.sc.original': 'Original (AI)',
  'wb.sc.was': 'was',
  'wb.sc.editedby': 'edited by',
  'wb.sc.rev': 'Revision',
  'wb.sc.edited': 'edited',
  'wb.sc.themodel': 'the model',
};

export const ka: Dict = {
  'wb.src.title': 'წყარო',
  'wb.src.audio': 'ჩანაწერის ატვირთვა',
  'wb.src.paste': 'ტრანსკრიპტის ჩასმა',

  'wb.drop.sub': 'აუდიო ან ვიდეო — ტრანსკრიფცია ElevenLabs Scribe-ით, შემდეგ ანალიზი მოთხოვნისამებრ. რამდენიმე ფაილი ერთდროულად მხოლოდ შეჯამებისთვის.',
  'wb.drop.sub_multi': 'ერთი და იმავე ადამიანების რამდენიმე დაკავშირებული ზარი — ტრანსკრიფცია თანმიმდევრობით და ერთობლივი შეჯამება.',

  'wb.files.n': 'რიგშია {n} ფაილი',

  'wb.file.remove': 'ფაილის წაშლა',

  'wb.onefile': 'აქ ერთდროულად მხოლოდ ერთი ჩანაწერია — წინა ფაილი შეიცვალა. რამდენიმე დაკავშირებული ზარისთვის გამოიყენეთ შეჯამება.',
  'wb.toomany': 'ერთ შეჯამებაში მაქსიმუმ {max} ფაილი.',

  'wb.toobig.total': 'ერთ შეჯამებაში მაქსიმუმ {max} აუდიო.',

  'wb.paste.ph': 'ჩასვით ზარის ტრანსკრიპტი — თითო რეპლიკა ცალკე ხაზზე, სურვილისამებრ მოსაუბრის სახელით დასაწყისში, მაგ. „ოპერატორი: …“',
  'wb.paste.hint': 'ჩასმულ ტრანსკრიპტს დამკვრელი არ აქვს: მიგნებები ტექსტშივე მოინიშნება, ხმის ტონი კი მიუწვდომელია.',

  'wb.upload': 'ტრანსკრიფცია',
  'wb.upload.text': 'ამ ტრანსკრიპტის გამოყენება',
  'wb.upload.sum': 'ტრანსკრიფცია და შეჯამება',

  'wb.tr.diarize.off': 'მოსაუბრეები არ იმიჯნება',
  'wb.tr.keyterms.toomany': 'მაქსიმუმ {max} საკვანძო სიტყვა.',

  'wb.needsource': 'ჯერ დაამატეთ ჩანაწერი ან ჩასვით ტრანსკრიპტი.',
  'wb.needtext': 'ჯერ ჩასვით ტრანსკრიპტი.',

  'wb.stage.transcribing': 'მიმდინარეობს ტრანსკრიფცია…',
  'wb.stage.transcribing_n': 'ტრანსკრიფცია: {name} ({i}/{n})…',
  'wb.stage.summarising': 'ზარების შეჯამება…',

  'wb.cancelled': 'ატვირთვა გაუქმდა.',
  'wb.fail': 'ატვირთვა ვერ მოხერხდა.',
  'wb.change': 'შეცვლა',

  'wb.src.text': 'ჩასმული ტრანსკრიპტი',
  'wb.src.calls': '{n} ზარი',

  'wb.turns': '{n} რეპლიკა',
  'wb.noaudio': 'ჩანაწერი აღარ ინახება — დარჩა მხოლოდ ტრანსკრიპტი და მისი შედეგები.',
  'wb.audiofail': 'აუდიო ვერ ჩაიტვირთა — ნაჩვენებია მხოლოდ ტრანსკრიპტი.',
  'wb.notl': 'დროის ხაზი მიუწვდომელია — ნაჩვენებია ტრანსკრიპტი.',

  'wb.tab.factcheck': 'ფაქტების შემოწმება',
  'wb.tab.score': 'შეფასება',
  'wb.tab.semantic': 'სენტიმენტი',
  'wb.tab.summarise': 'შეჯამება',

  'wb.fc.note': 'ამოწმებს მხოლოდ ზარში გაცემული ინფორმაციის სისწორეს თქვენი ცოდნის ბაზის მიხედვით.',

  'wb.run.factcheck': 'ფაქტების შემოწმება',
  'wb.run.score': 'ზარის შეფასება',
  'wb.run.semantic': 'ტონის ანალიზი',
  'wb.run.summarise': 'შეჯამება',

  'wb.rerun': 'ხელახლა გაშვება',
  'wb.running': 'მუშავდება…',

  'wb.sc.note': 'აფასებს ზარს თქვენი აქტიური რუბრიკის მიხედვით.',
  'wb.sc.edit': 'რუბრიკის რედაქტირება',
  'wb.sc.default': 'ნაგულისხმევი რუბრიკა',

  'wb.sem.note': 'აფასებს, როგორ წარიმართა საუბარი — სიტყვებით, ხოლო აუდიოს შემთხვევაში ხმითაც.',
  'wb.sem.words': 'სიტყვები',
  'wb.sem.voice': 'ხმის ტონი',
  'wb.sem.voice.off': 'ხმის ტონს აუდიოჩანაწერი სჭირდება.',
  'wb.sem.pickone': 'მონიშნეთ სიტყვები ან ხმის ტონი.',
  'wb.sem.words.tip': 'აფასებს მხოლოდ ნათქვამს: თითოეული რეპლიკის თავაზიანობას, სიმშრალეს ან უხეშობას.',
  'wb.sem.voice.tip': 'აფასებს, როგორ ჟღერს თითოეული მოსაუბრე — აგრესიულად, დაძაბულად, მშვიდად თუ მომთმენად — თავად აუდიოს მიხედვით და არა სიტყვების.',
  'wb.sem.guidance': 'მითითებები',
  'wb.sem.title': 'სენტიმენტის ანალიზი',
  'wb.sem.politeness': 'თავაზიანობა',
  'wb.sem.flags': 'შენიშვნები',
  'wb.sem.turns': 'რეპლიკების მიხედვით',
  'wb.sem.novoice': 'ამ ჩანაწერისთვის ხმის ტონი მიუწვდომელი იყო.',

  'wb.novoice.timeout': 'ხმის ტონის სერვისმა დროულად ვერ უპასუხა. სცადეთ ხელახლა.',
  'wb.novoice.warming': 'ხმის მოდელი ჯერ იტვირთება. სცადეთ ერთ წუთში.',
  'wb.novoice.model_error': 'ხმის მოდელი სერვერზე ვერ ჩაიტვირთა. სთხოვეთ ოპერატორს, შეამოწმოს ხმის ტონის სერვისი.',
  'wb.novoice.unreachable': 'ხმის ტონის სერვისი არ პასუხობს. სთხოვეთ ოპერატორს შეამოწმოს.',
  'wb.novoice.disabled': 'ხმის ტონი ამ სისტემაზე ჩართული არ არის.',
  'wb.novoice.error': 'ხმის ტონის სერვისმა მოულოდნელი პასუხი დააბრუნა.',
  'wb.novoice.no_timestamps': 'ამ ჩანაწერს რეპლიკების დროები არ აქვს, ამიტომ აუდიოს მოსაუბრეების მიხედვით დაყოფა ვერ ხერხდება. ეს ეხება დროებამდე გაკეთებულ ჩანაწერებსა და ჩასმულ ტრანსკრიფციებს.',
  'wb.novoice.no_audio': 'ეს ჩასმული ტრანსკრიფციაა — მოსასმენი აუდიო არ არსებობს.',

  'wb.sem.share_good': 'მშვიდი',
  'wb.sem.share_bad': 'დაძაბული',
  'wb.sem.summary': 'ზოგადი შეფასება',

  'wb.confidence': 'სანდოობა',

  'wb.tone.polite': 'თავაზიანი',
  'wb.tone.neutral': 'ნეიტრალური',
  'wb.tone.curt': 'მშრალი',
  'wb.tone.impolite': 'უზრდელი',
  'wb.tone.rude': 'უხეში',
  'wb.tone.aggressive': 'აგრესიული',

  'wb.voice.aggressive': 'აგრესიული',
  'wb.voice.tense': 'დაძაბული',
  'wb.voice.calm': 'მშვიდი',
  'wb.voice.patient': 'მომთმენი',
  'wb.voice.unknown': 'უცნობი',

  'wb.vl.angry': 'გაბრაზებული',
  'wb.vl.frustrated': 'გაღიზიანებული',
  'wb.vl.disgusted': 'ზიზღი',
  'wb.vl.fearful': 'შეშინებული',
  'wb.vl.sad': 'მოწყენილი',
  'wb.vl.neutral': 'ნეიტრალური',
  'wb.vl.calm': 'მშვიდი',
  'wb.vl.happy': 'მხიარული',
  'wb.vl.excited': 'აღფრთოვანებული',
  'wb.vl.other': 'სხვა',
  'wb.vl.unknown': 'უცნობი',

  'wb.role.agent': 'ოპერატორი',
  'wb.role.customer': 'კლიენტი',
  'wb.role.other': 'სხვა',
  'wb.role.unknown': 'უცნობი',

  'wb.speaker': 'მოსაუბრე {n}',

  'wb.sum.note': 'ერთი ან რამდენიმე დაკავშირებული ზარი — მოკლე შეჯამება, მთავარი პუნქტები და სრული ტრანსკრიპტები.',
  'wb.sum.title': 'შეჯამება',
  'wb.sum.participants': 'მონაწილეები',
  'wb.sum.calls': 'ზარები',
  'wb.sum.outcome': 'შედეგი',
  'wb.sum.transcripts': 'სრული ტრანსკრიპტები',
  'wb.sum.appears': 'ზარები: {list}',
  'wb.sum.needaudio': 'შეჯამება მუშაობს აუდიოჩანაწერებზე — წყაროს ბარათში დაამატეთ ერთი ან რამდენიმე ფაილი.',
  'wb.sum.done': 'შეჯამება მზადაა',

  'wb.fc.done': 'ფაქტების შემოწმება მზადაა',

  'wb.sem.done': 'ტონის ანალიზი მზადაა',

  'wb.fc.partial': 'ნაწილობრივ დასტურდება',

  'wb.call': 'ზარი {n}',
  'wb.seek': 'ამ მომენტზე გადასვლა',

  'wb.lane.factcheck': 'ფაქტები',
  'wb.lane.words': 'სენტიმენტი: სიტყვები',
  'wb.lane.voice': 'სენტიმენტი: ხმა',

  'wb.sc.save': 'ქულების შენახვა',
  'wb.sc.cancel': 'გაუქმება',
  'wb.sc.whynote': 'რატომ ცვლით? (არასავალდებულო)',
  'wb.sc.saved': 'ქულები განახლდა.',
  'wb.sc.history': 'ისტორიის ჩვენება',
  'wb.sc.hide': 'ისტორიის დამალვა',
  'wb.sc.nohistory': 'ცვლილებები არ ყოფილა — ეს საწყისი ქულებია.',
  'wb.sc.original': 'საწყისი (AI)',
  'wb.sc.was': 'იყო',
  'wb.sc.editedby': 'შეასწორა',
  'wb.sc.rev': 'ვერსია',
  'wb.sc.edited': 'შესწორებული',
  'wb.sc.themodel': 'მოდელი',
};

export const ru: Dict = {
  'wb.src.title': 'Источник',
  'wb.src.audio': 'Загрузить запись',
  'wb.src.paste': 'Вставить транскрипт',

  'wb.drop.sub': 'Аудио или видео — расшифровывается ElevenLabs Scribe, затем анализируется по запросу. Несколько файлов сразу — только для сводки.',
  'wb.drop.sub_multi': 'Несколько связанных звонков с теми же людьми — расшифровываются по порядку и обобщаются вместе.',

  'wb.files.n': 'Файлов в очереди: {n}',

  'wb.file.remove': 'Убрать файл',

  'wb.onefile': 'Здесь только одна запись за раз — предыдущий файл заменён. Для нескольких связанных звонков используйте «Сводку».',
  'wb.toomany': 'Не больше {max} файлов в одной сводке.',

  'wb.toobig.total': 'Не больше {max} аудио в одной сводке.',

  'wb.paste.ph': 'Вставьте транскрипт звонка — каждая реплика с новой строки, при желании с именем говорящего в начале, напр. «Оператор: …»',
  'wb.paste.hint': 'У вставленного транскрипта нет плеера: находки подсвечиваются прямо в тексте, а тон голоса недоступен.',

  'wb.upload': 'Расшифровать',
  'wb.upload.text': 'Использовать транскрипт',
  'wb.upload.sum': 'Расшифровать и обобщить',

  'wb.tr.diarize.off': 'Говорящие не разделяются',
  'wb.tr.keyterms.toomany': 'Не больше {max} ключевых терминов.',

  'wb.needsource': 'Сначала добавьте запись или вставьте транскрипт.',
  'wb.needtext': 'Сначала вставьте транскрипт.',

  'wb.stage.transcribing': 'Идёт расшифровка…',
  'wb.stage.transcribing_n': 'Расшифровка {name} ({i} из {n})…',
  'wb.stage.summarising': 'Составляется сводка звонков…',

  'wb.cancelled': 'Загрузка отменена.',
  'wb.fail': 'Не удалось загрузить.',
  'wb.change': 'Изменить',

  'wb.src.text': 'Вставленный транскрипт',
  'wb.src.calls': 'Звонков: {n}',

  'wb.turns': 'Реплик: {n}',
  'wb.noaudio': 'Запись больше не хранится — остались только транскрипт и его результаты.',
  'wb.audiofail': 'Не удалось загрузить аудио — показан только транскрипт.',
  'wb.notl': 'Шкала времени недоступна — показан транскрипт.',

  'wb.tab.factcheck': 'Проверка фактов',
  'wb.tab.score': 'Оценка',
  'wb.tab.semantic': 'Сентимент',
  'wb.tab.summarise': 'Сводка',

  'wb.fc.note': 'Проверяет только правильность сведений, прозвучавших в звонке, по вашей базе знаний.',

  'wb.run.factcheck': 'Проверить факты',
  'wb.run.score': 'Оценить звонок',
  'wb.run.semantic': 'Проанализировать тон',
  'wb.run.summarise': 'Составить сводку',

  'wb.rerun': 'Запустить снова',
  'wb.running': 'Обработка…',

  'wb.sc.note': 'Оценивает звонок по вашей активной рубрике.',
  'wb.sc.edit': 'Изменить рубрику',
  'wb.sc.default': 'рубрика по умолчанию',

  'wb.sem.note': 'Оценивает, как велась беседа — по словам, а при наличии аудио и по голосу.',
  'wb.sem.words': 'Слова',
  'wb.sem.voice': 'Тон голоса',
  'wb.sem.voice.off': 'Для тона голоса нужна аудиозапись.',
  'wb.sem.pickone': 'Отметьте «Слова» или «Тон голоса».',
  'wb.sem.words.tip': 'Оценивает только сказанное: вежливость, сухость или грубость каждой реплики.',
  'wb.sem.voice.tip': 'Оценивает, как звучит каждый говорящий — агрессивно, напряжённо, спокойно или терпеливо — по самому аудио, а не по словам.',
  'wb.sem.guidance': 'Указания',
  'wb.sem.title': 'Анализ сентимента',
  'wb.sem.politeness': 'Вежливость',
  'wb.sem.flags': 'Замечания',
  'wb.sem.turns': 'По репликам',
  'wb.sem.novoice': 'Тон голоса для этой записи недоступен.',

  'wb.novoice.timeout': 'Сервис тона голоса не ответил вовремя. Повторите попытку.',
  'wb.novoice.warming': 'Модель голоса ещё загружается. Попробуйте через минуту.',
  'wb.novoice.model_error': 'Не удалось загрузить модель голоса на сервере. Попросите оператора проверить сервис тона голоса.',
  'wb.novoice.unreachable': 'Служба тона голоса не отвечает. Попросите оператора её проверить.',
  'wb.novoice.disabled': 'Тон голоса не включён в этой системе.',
  'wb.novoice.error': 'Служба тона голоса вернула неожиданный ответ.',
  'wb.novoice.no_timestamps': 'У этой записи нет таймингов реплик, поэтому аудио нельзя разбить по говорящим. Это касается записей, сделанных до появления таймингов, и вставленных расшифровок.',
  'wb.novoice.no_audio': 'Это вставленная расшифровка — аудио для прослушивания нет.',

  'wb.sem.share_good': 'спокойно',
  'wb.sem.share_bad': 'напряжённо',
  'wb.sem.summary': 'Общая оценка',

  'wb.confidence': 'уверенность',

  'wb.tone.polite': 'вежливо',
  'wb.tone.neutral': 'нейтрально',
  'wb.tone.curt': 'сухо',
  'wb.tone.impolite': 'невежливо',
  'wb.tone.rude': 'грубо',
  'wb.tone.aggressive': 'агрессивно',

  'wb.voice.aggressive': 'агрессивный',
  'wb.voice.tense': 'напряжённый',
  'wb.voice.calm': 'спокойный',
  'wb.voice.patient': 'терпеливый',
  'wb.voice.unknown': 'неизвестно',

  'wb.vl.angry': 'злость',
  'wb.vl.frustrated': 'раздражение',
  'wb.vl.disgusted': 'отвращение',
  'wb.vl.fearful': 'страх',
  'wb.vl.sad': 'грусть',
  'wb.vl.neutral': 'нейтрально',
  'wb.vl.calm': 'спокойствие',
  'wb.vl.happy': 'радость',
  'wb.vl.excited': 'воодушевление',
  'wb.vl.other': 'другое',
  'wb.vl.unknown': 'неизвестно',

  'wb.role.agent': 'Оператор',
  'wb.role.customer': 'Клиент',
  'wb.role.other': 'Другой',
  'wb.role.unknown': 'Неизвестно',

  'wb.speaker': 'Говорящий {n}',

  'wb.sum.note': 'Один или несколько связанных звонков — краткая сводка, ключевые моменты и полные транскрипты.',
  'wb.sum.title': 'Сводка',
  'wb.sum.participants': 'Участники',
  'wb.sum.calls': 'Звонки',
  'wb.sum.outcome': 'Итог',
  'wb.sum.transcripts': 'Полные транскрипты',
  'wb.sum.appears': 'звонки {list}',
  'wb.sum.needaudio': 'Сводка работает с аудиозаписями — добавьте один или несколько файлов в карточке источника.',
  'wb.sum.done': 'Сводка готова',

  'wb.fc.done': 'Проверка фактов готова',

  'wb.sem.done': 'Анализ тона готов',

  'wb.fc.partial': 'частично подтверждено',

  'wb.call': 'Звонок {n}',
  'wb.seek': 'Перейти к этому моменту',

  'wb.lane.factcheck': 'Факты',
  'wb.lane.words': 'Сентимент: слова',
  'wb.lane.voice': 'Сентимент: голос',

  'wb.sc.save': 'Сохранить оценки',
  'wb.sc.cancel': 'Отмена',
  'wb.sc.whynote': 'Почему вы это меняете? (необязательно)',
  'wb.sc.saved': 'Оценки обновлены.',
  'wb.sc.history': 'Показать историю',
  'wb.sc.hide': 'Скрыть историю',
  'wb.sc.nohistory': 'Изменений не было — это исходные оценки.',
  'wb.sc.original': 'Исходные (ИИ)',
  'wb.sc.was': 'было',
  'wb.sc.editedby': 'изменил',
  'wb.sc.rev': 'Версия',
  'wb.sc.edited': 'изменено',
  'wb.sc.themodel': 'модель',
};
