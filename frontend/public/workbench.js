/* CQ.Workbench — the shared "Analyse a call" panel used by tenant.html and account.html.
   Self-contained: injects its own CSS (<style id="cq-workbench-css">) and registers its own
   en/ka/ru strings via CQ.extendDict (keys prefixed wb.). Loads AFTER brand.js and, when a
   player is wanted, after timeline.js; attaches as CQ.Workbench.

   Contract: design-v2.md §8 (routes), §2/§3 (segments, spans), §13.2 (this panel).

     const wb = CQ.Workbench(container, {
       api: CQ.API, headers: () => authH(), fetch: apiFetch,
       features: ['factcheck','score','semantic','summarise'],
       rubricHref: '#rubric' | null,
       sentimentConfig: { get: async () => ({guidance, readonly?}), put: async ({guidance}) => .. } | null,
       onUnauthorized: () => sessionExpired()      // optional: XHR uploads cannot go through opts.fetch
     });
     wb.open(recording); wb.openSummary(summary); wb.reset(); wb.setTab(kind); wb.destroy();

   The v2 result renderers are exposed as CQ.Workbench.renderFactcheck / renderScorecard /
   renderSemantic / renderSummary (html strings; clickable items carry data-seek/data-start). */
(() => {
  'use strict';
  if (typeof CQ === 'undefined') { console.error('workbench.js: brand.js must load first'); return; }

  /* ---------------- i18n (keep this literal free of comments with quotes: check_i18n.py walks it) ---------------- */
  CQ.extendDict({
    en: {
      'wb.src.title':'Source','wb.src.audio':'Upload a recording','wb.src.paste':'Paste transcript',
      'wb.drop.sub':'Audio or video — transcribed with ElevenLabs Scribe, then analysed on demand. Several files at once are only for Summarise.',
      'wb.drop.sub_multi':'Several related calls with the same people — transcribed in order and summarised together.',
      'wb.files.n':'{n} files queued','wb.file.remove':'Remove file',
      'wb.onefile':'One recording at a time here — the previous file was replaced. Use Summarise for several related calls.',
      'wb.toomany':'Up to {max} files per summary.','wb.toobig.total':'Up to {max} of audio in one summary.',
      'wb.paste.ph':'Paste the call transcript — one line per turn, optionally starting with the speaker, e.g. “Agent: …”',
      'wb.paste.hint':'A pasted transcript has no player: findings are highlighted in the text instead, and voice tone is not available.',
      'wb.upload':'Transcribe','wb.upload.text':'Use this transcript','wb.upload.sum':'Transcribe & summarise',
      'wb.needsource':'Add a recording or paste a transcript first.','wb.needtext':'Paste a transcript first.',
      'wb.stage.transcribing':'Transcribing…','wb.stage.transcribing_n':'Transcribing {name} ({i} of {n})…','wb.stage.summarising':'Summarising the calls…',
      'wb.cancelled':'Upload cancelled.','wb.fail':'Upload failed.','wb.change':'Change',
      'wb.src.text':'Pasted transcript','wb.src.calls':'{n} calls','wb.turns':'{n} turns',
      'wb.noaudio':'The recording is no longer stored — only the transcript and its results remain.',
      'wb.audiofail':'Could not load the audio — showing the transcript only.',
      'wb.notl':'Timeline unavailable — showing the transcript.',
      'wb.tab.factcheck':'Fact-check','wb.tab.score':'Score','wb.tab.semantic':'Sentiment','wb.tab.summarise':'Summarise',
      'wb.fc.note':'Checks only the correctness of information in the call against your knowledge base.',
      'wb.run.factcheck':'Check the facts','wb.run.score':'Score the call','wb.run.semantic':'Analyse the tone','wb.run.summarise':'Summarise',
      'wb.rerun':'Run again','wb.running':'Working…',
      'wb.sc.note':'Scores the call against your active rubric.','wb.sc.edit':'Edit the rubric','wb.sc.default':'default rubric',
      'wb.sem.note':'Judges how the conversation was conducted — the words, and with audio, the voice too.',
      'wb.sem.words':'Words','wb.sem.voice':'Voice tone','wb.sem.voice.off':'Voice tone needs an audio recording.','wb.sem.pickone':'Tick Words or Voice tone.',
      'wb.sem.words.tip':'Judges only what was said: the politeness, curtness or rudeness of each turn.',
      'wb.sem.voice.tip':'Rates how each speaker sounds — aggressive, tense, calm or patient — from the audio itself, not the words.',
      'wb.sem.guidance':'Guidance','wb.sem.title':'Sentiment analysis','wb.sem.politeness':'Politeness','wb.sem.flags':'Flags',
      'wb.sem.turns':'Turn by turn','wb.sem.novoice':'Voice tone was not available for this recording.','wb.novoice.timeout':'The voice-tone service did not answer in time. Try again.','wb.novoice.warming':'The voice model is still loading. Try again in a minute.','wb.novoice.model_error':'The voice model could not be loaded on the server. Ask your operator to check the voice-tone service.','wb.novoice.unreachable':'The voice-tone service is not responding. Ask your operator to check it.','wb.novoice.disabled':'Voice tone is not switched on for this deployment.','wb.novoice.error':'The voice-tone service returned an unexpected answer.','wb.novoice.no_timestamps':'This recording has no per-turn timings, so the audio cannot be split by speaker turn. Recordings made before timed transcripts, and pasted transcripts, are affected.','wb.novoice.no_audio':'This is a pasted transcript — there is no audio to listen to.',
      'wb.sem.share_good':'calm','wb.sem.share_bad':'tense','wb.sem.summary':'Overall','wb.confidence':'confidence',
      'wb.tone.polite':'polite','wb.tone.neutral':'neutral','wb.tone.curt':'curt','wb.tone.impolite':'impolite','wb.tone.rude':'rude','wb.tone.aggressive':'aggressive',
      'wb.voice.aggressive':'aggressive','wb.voice.tense':'tense','wb.voice.calm':'calm','wb.voice.patient':'patient','wb.voice.unknown':'unknown',
      'wb.vl.angry':'angry','wb.vl.frustrated':'frustrated','wb.vl.disgusted':'disgusted','wb.vl.fearful':'fearful','wb.vl.sad':'sad',
      'wb.vl.neutral':'neutral','wb.vl.calm':'calm','wb.vl.happy':'happy','wb.vl.excited':'excited','wb.vl.other':'other','wb.vl.unknown':'unknown',
      'wb.role.agent':'Agent','wb.role.customer':'Customer','wb.role.other':'Other','wb.role.unknown':'Unknown','wb.speaker':'Speaker {n}',
      'wb.sum.note':'One or several related calls — a short summary, the key points and the full transcripts.',
      'wb.sum.title':'Summary','wb.sum.participants':'Participants','wb.sum.calls':'Calls','wb.sum.outcome':'Outcome',
      'wb.sum.transcripts':'Full transcripts','wb.sum.appears':'calls {list}',
      'wb.sum.needaudio':'Summarise works on audio recordings — add one or more files in the source card.',
      'wb.sum.done':'Summary ready','wb.fc.done':'Fact-check ready','wb.sem.done':'Tone analysis ready',
      'wb.fc.partial':'partially supported','wb.call':'Call {n}','wb.seek':'Jump to this moment',
      'wb.lane.factcheck':'Fact-check','wb.lane.words':'Sentiment Words','wb.lane.voice':'Sentiment Voice','wb.sc.save':'Save scores','wb.sc.cancel':'Cancel','wb.sc.whynote':'Why are you changing this? (optional)','wb.sc.saved':'Scores updated.','wb.sc.history':'Show history','wb.sc.hide':'Hide history','wb.sc.nohistory':'No changes yet — these are the original scores.','wb.sc.original':'Original (AI)','wb.sc.was':'was','wb.sc.editedby':'edited by','wb.sc.rev':'Revision','wb.sc.edited':'edited','wb.sc.themodel':'the model',
    },
    ka: {
      'wb.src.title':'წყარო','wb.src.audio':'ჩანაწერის ატვირთვა','wb.src.paste':'ტრანსკრიპტის ჩასმა',
      'wb.drop.sub':'აუდიო ან ვიდეო — ტრანსკრიფცია ElevenLabs Scribe-ით, შემდეგ ანალიზი მოთხოვნისამებრ. რამდენიმე ფაილი ერთდროულად მხოლოდ შეჯამებისთვის.',
      'wb.drop.sub_multi':'ერთი და იმავე ადამიანების რამდენიმე დაკავშირებული ზარი — ტრანსკრიფცია თანმიმდევრობით და ერთობლივი შეჯამება.',
      'wb.files.n':'რიგშია {n} ფაილი','wb.file.remove':'ფაილის წაშლა',
      'wb.onefile':'აქ ერთდროულად მხოლოდ ერთი ჩანაწერია — წინა ფაილი შეიცვალა. რამდენიმე დაკავშირებული ზარისთვის გამოიყენეთ შეჯამება.',
      'wb.toomany':'ერთ შეჯამებაში მაქსიმუმ {max} ფაილი.','wb.toobig.total':'ერთ შეჯამებაში მაქსიმუმ {max} აუდიო.',
      'wb.paste.ph':'ჩასვით ზარის ტრანსკრიპტი — თითო რეპლიკა ცალკე ხაზზე, სურვილისამებრ მოსაუბრის სახელით დასაწყისში, მაგ. „ოპერატორი: …“',
      'wb.paste.hint':'ჩასმულ ტრანსკრიპტს დამკვრელი არ აქვს: მიგნებები ტექსტშივე მოინიშნება, ხმის ტონი კი მიუწვდომელია.',
      'wb.upload':'ტრანსკრიფცია','wb.upload.text':'ამ ტრანსკრიპტის გამოყენება','wb.upload.sum':'ტრანსკრიფცია და შეჯამება',
      'wb.needsource':'ჯერ დაამატეთ ჩანაწერი ან ჩასვით ტრანსკრიპტი.','wb.needtext':'ჯერ ჩასვით ტრანსკრიპტი.',
      'wb.stage.transcribing':'მიმდინარეობს ტრანსკრიფცია…','wb.stage.transcribing_n':'ტრანსკრიფცია: {name} ({i}/{n})…','wb.stage.summarising':'ზარების შეჯამება…',
      'wb.cancelled':'ატვირთვა გაუქმდა.','wb.fail':'ატვირთვა ვერ მოხერხდა.','wb.change':'შეცვლა',
      'wb.src.text':'ჩასმული ტრანსკრიპტი','wb.src.calls':'{n} ზარი','wb.turns':'{n} რეპლიკა',
      'wb.noaudio':'ჩანაწერი აღარ ინახება — დარჩა მხოლოდ ტრანსკრიპტი და მისი შედეგები.',
      'wb.audiofail':'აუდიო ვერ ჩაიტვირთა — ნაჩვენებია მხოლოდ ტრანსკრიპტი.',
      'wb.notl':'დროის ხაზი მიუწვდომელია — ნაჩვენებია ტრანსკრიპტი.',
      'wb.tab.factcheck':'ფაქტების შემოწმება','wb.tab.score':'შეფასება','wb.tab.semantic':'სენტიმენტი','wb.tab.summarise':'შეჯამება',
      'wb.fc.note':'ამოწმებს მხოლოდ ზარში გაცემული ინფორმაციის სისწორეს თქვენი ცოდნის ბაზის მიხედვით.',
      'wb.run.factcheck':'ფაქტების შემოწმება','wb.run.score':'ზარის შეფასება','wb.run.semantic':'ტონის ანალიზი','wb.run.summarise':'შეჯამება',
      'wb.rerun':'ხელახლა გაშვება','wb.running':'მუშავდება…',
      'wb.sc.note':'აფასებს ზარს თქვენი აქტიური რუბრიკის მიხედვით.','wb.sc.edit':'რუბრიკის რედაქტირება','wb.sc.default':'ნაგულისხმევი რუბრიკა',
      'wb.sem.note':'აფასებს, როგორ წარიმართა საუბარი — სიტყვებით, ხოლო აუდიოს შემთხვევაში ხმითაც.',
      'wb.sem.words':'სიტყვები','wb.sem.voice':'ხმის ტონი','wb.sem.voice.off':'ხმის ტონს აუდიოჩანაწერი სჭირდება.','wb.sem.pickone':'მონიშნეთ სიტყვები ან ხმის ტონი.',
      'wb.sem.words.tip':'აფასებს მხოლოდ ნათქვამს: თითოეული რეპლიკის თავაზიანობას, სიმშრალეს ან უხეშობას.',
      'wb.sem.voice.tip':'აფასებს, როგორ ჟღერს თითოეული მოსაუბრე — აგრესიულად, დაძაბულად, მშვიდად თუ მომთმენად — თავად აუდიოს მიხედვით და არა სიტყვების.',
      'wb.sem.guidance':'მითითებები','wb.sem.title':'სენტიმენტის ანალიზი','wb.sem.politeness':'თავაზიანობა','wb.sem.flags':'შენიშვნები',
      'wb.sem.turns':'რეპლიკების მიხედვით','wb.sem.novoice':'ამ ჩანაწერისთვის ხმის ტონი მიუწვდომელი იყო.','wb.novoice.timeout':'ხმის ტონის სერვისმა დროულად ვერ უპასუხა. სცადეთ ხელახლა.','wb.novoice.warming':'ხმის მოდელი ჯერ იტვირთება. სცადეთ ერთ წუთში.','wb.novoice.model_error':'ხმის მოდელი სერვერზე ვერ ჩაიტვირთა. სთხოვეთ ოპერატორს, შეამოწმოს ხმის ტონის სერვისი.','wb.novoice.unreachable':'ხმის ტონის სერვისი არ პასუხობს. სთხოვეთ ოპერატორს შეამოწმოს.','wb.novoice.disabled':'ხმის ტონი ამ სისტემაზე ჩართული არ არის.','wb.novoice.error':'ხმის ტონის სერვისმა მოულოდნელი პასუხი დააბრუნა.','wb.novoice.no_timestamps':'ამ ჩანაწერს რეპლიკების დროები არ აქვს, ამიტომ აუდიოს მოსაუბრეების მიხედვით დაყოფა ვერ ხერხდება. ეს ეხება დროებამდე გაკეთებულ ჩანაწერებსა და ჩასმულ ტრანსკრიფციებს.','wb.novoice.no_audio':'ეს ჩასმული ტრანსკრიფციაა — მოსასმენი აუდიო არ არსებობს.',
      'wb.sem.share_good':'მშვიდი','wb.sem.share_bad':'დაძაბული','wb.sem.summary':'ზოგადი შეფასება','wb.confidence':'სანდოობა',
      'wb.tone.polite':'თავაზიანი','wb.tone.neutral':'ნეიტრალური','wb.tone.curt':'მშრალი','wb.tone.impolite':'უზრდელი','wb.tone.rude':'უხეში','wb.tone.aggressive':'აგრესიული',
      'wb.voice.aggressive':'აგრესიული','wb.voice.tense':'დაძაბული','wb.voice.calm':'მშვიდი','wb.voice.patient':'მომთმენი','wb.voice.unknown':'უცნობი',
      'wb.vl.angry':'გაბრაზებული','wb.vl.frustrated':'გაღიზიანებული','wb.vl.disgusted':'ზიზღი','wb.vl.fearful':'შეშინებული','wb.vl.sad':'მოწყენილი',
      'wb.vl.neutral':'ნეიტრალური','wb.vl.calm':'მშვიდი','wb.vl.happy':'მხიარული','wb.vl.excited':'აღფრთოვანებული','wb.vl.other':'სხვა','wb.vl.unknown':'უცნობი',
      'wb.role.agent':'ოპერატორი','wb.role.customer':'კლიენტი','wb.role.other':'სხვა','wb.role.unknown':'უცნობი','wb.speaker':'მოსაუბრე {n}',
      'wb.sum.note':'ერთი ან რამდენიმე დაკავშირებული ზარი — მოკლე შეჯამება, მთავარი პუნქტები და სრული ტრანსკრიპტები.',
      'wb.sum.title':'შეჯამება','wb.sum.participants':'მონაწილეები','wb.sum.calls':'ზარები','wb.sum.outcome':'შედეგი',
      'wb.sum.transcripts':'სრული ტრანსკრიპტები','wb.sum.appears':'ზარები: {list}',
      'wb.sum.needaudio':'შეჯამება მუშაობს აუდიოჩანაწერებზე — წყაროს ბარათში დაამატეთ ერთი ან რამდენიმე ფაილი.',
      'wb.sum.done':'შეჯამება მზადაა','wb.fc.done':'ფაქტების შემოწმება მზადაა','wb.sem.done':'ტონის ანალიზი მზადაა',
      'wb.fc.partial':'ნაწილობრივ დასტურდება','wb.call':'ზარი {n}','wb.seek':'ამ მომენტზე გადასვლა',
      'wb.lane.factcheck':'ფაქტები','wb.lane.words':'სენტიმენტი: სიტყვები','wb.lane.voice':'სენტიმენტი: ხმა','wb.sc.save':'ქულების შენახვა','wb.sc.cancel':'გაუქმება','wb.sc.whynote':'რატომ ცვლით? (არასავალდებულო)','wb.sc.saved':'ქულები განახლდა.','wb.sc.history':'ისტორიის ჩვენება','wb.sc.hide':'ისტორიის დამალვა','wb.sc.nohistory':'ცვლილებები არ ყოფილა — ეს საწყისი ქულებია.','wb.sc.original':'საწყისი (AI)','wb.sc.was':'იყო','wb.sc.editedby':'შეასწორა','wb.sc.rev':'ვერსია','wb.sc.edited':'შესწორებული','wb.sc.themodel':'მოდელი',
    },
    ru: {
      'wb.src.title':'Источник','wb.src.audio':'Загрузить запись','wb.src.paste':'Вставить транскрипт',
      'wb.drop.sub':'Аудио или видео — расшифровывается ElevenLabs Scribe, затем анализируется по запросу. Несколько файлов сразу — только для сводки.',
      'wb.drop.sub_multi':'Несколько связанных звонков с теми же людьми — расшифровываются по порядку и обобщаются вместе.',
      'wb.files.n':'Файлов в очереди: {n}','wb.file.remove':'Убрать файл',
      'wb.onefile':'Здесь только одна запись за раз — предыдущий файл заменён. Для нескольких связанных звонков используйте «Сводку».',
      'wb.toomany':'Не больше {max} файлов в одной сводке.','wb.toobig.total':'Не больше {max} аудио в одной сводке.',
      'wb.paste.ph':'Вставьте транскрипт звонка — каждая реплика с новой строки, при желании с именем говорящего в начале, напр. «Оператор: …»',
      'wb.paste.hint':'У вставленного транскрипта нет плеера: находки подсвечиваются прямо в тексте, а тон голоса недоступен.',
      'wb.upload':'Расшифровать','wb.upload.text':'Использовать транскрипт','wb.upload.sum':'Расшифровать и обобщить',
      'wb.needsource':'Сначала добавьте запись или вставьте транскрипт.','wb.needtext':'Сначала вставьте транскрипт.',
      'wb.stage.transcribing':'Идёт расшифровка…','wb.stage.transcribing_n':'Расшифровка {name} ({i} из {n})…','wb.stage.summarising':'Составляется сводка звонков…',
      'wb.cancelled':'Загрузка отменена.','wb.fail':'Не удалось загрузить.','wb.change':'Изменить',
      'wb.src.text':'Вставленный транскрипт','wb.src.calls':'Звонков: {n}','wb.turns':'Реплик: {n}',
      'wb.noaudio':'Запись больше не хранится — остались только транскрипт и его результаты.',
      'wb.audiofail':'Не удалось загрузить аудио — показан только транскрипт.',
      'wb.notl':'Шкала времени недоступна — показан транскрипт.',
      'wb.tab.factcheck':'Проверка фактов','wb.tab.score':'Оценка','wb.tab.semantic':'Сентимент','wb.tab.summarise':'Сводка',
      'wb.fc.note':'Проверяет только правильность сведений, прозвучавших в звонке, по вашей базе знаний.',
      'wb.run.factcheck':'Проверить факты','wb.run.score':'Оценить звонок','wb.run.semantic':'Проанализировать тон','wb.run.summarise':'Составить сводку',
      'wb.rerun':'Запустить снова','wb.running':'Обработка…',
      'wb.sc.note':'Оценивает звонок по вашей активной рубрике.','wb.sc.edit':'Изменить рубрику','wb.sc.default':'рубрика по умолчанию',
      'wb.sem.note':'Оценивает, как велась беседа — по словам, а при наличии аудио и по голосу.',
      'wb.sem.words':'Слова','wb.sem.voice':'Тон голоса','wb.sem.voice.off':'Для тона голоса нужна аудиозапись.','wb.sem.pickone':'Отметьте «Слова» или «Тон голоса».',
      'wb.sem.words.tip':'Оценивает только сказанное: вежливость, сухость или грубость каждой реплики.',
      'wb.sem.voice.tip':'Оценивает, как звучит каждый говорящий — агрессивно, напряжённо, спокойно или терпеливо — по самому аудио, а не по словам.',
      'wb.sem.guidance':'Указания','wb.sem.title':'Анализ сентимента','wb.sem.politeness':'Вежливость','wb.sem.flags':'Замечания',
      'wb.sem.turns':'По репликам','wb.sem.novoice':'Тон голоса для этой записи недоступен.','wb.novoice.timeout':'Сервис тона голоса не ответил вовремя. Повторите попытку.','wb.novoice.warming':'Модель голоса ещё загружается. Попробуйте через минуту.','wb.novoice.model_error':'Не удалось загрузить модель голоса на сервере. Попросите оператора проверить сервис тона голоса.','wb.novoice.unreachable':'Служба тона голоса не отвечает. Попросите оператора её проверить.','wb.novoice.disabled':'Тон голоса не включён в этой системе.','wb.novoice.error':'Служба тона голоса вернула неожиданный ответ.','wb.novoice.no_timestamps':'У этой записи нет таймингов реплик, поэтому аудио нельзя разбить по говорящим. Это касается записей, сделанных до появления таймингов, и вставленных расшифровок.','wb.novoice.no_audio':'Это вставленная расшифровка — аудио для прослушивания нет.',
      'wb.sem.share_good':'спокойно','wb.sem.share_bad':'напряжённо','wb.sem.summary':'Общая оценка','wb.confidence':'уверенность',
      'wb.tone.polite':'вежливо','wb.tone.neutral':'нейтрально','wb.tone.curt':'сухо','wb.tone.impolite':'невежливо','wb.tone.rude':'грубо','wb.tone.aggressive':'агрессивно',
      'wb.voice.aggressive':'агрессивный','wb.voice.tense':'напряжённый','wb.voice.calm':'спокойный','wb.voice.patient':'терпеливый','wb.voice.unknown':'неизвестно',
      'wb.vl.angry':'злость','wb.vl.frustrated':'раздражение','wb.vl.disgusted':'отвращение','wb.vl.fearful':'страх','wb.vl.sad':'грусть',
      'wb.vl.neutral':'нейтрально','wb.vl.calm':'спокойствие','wb.vl.happy':'радость','wb.vl.excited':'воодушевление','wb.vl.other':'другое','wb.vl.unknown':'неизвестно',
      'wb.role.agent':'Оператор','wb.role.customer':'Клиент','wb.role.other':'Другой','wb.role.unknown':'Неизвестно','wb.speaker':'Говорящий {n}',
      'wb.sum.note':'Один или несколько связанных звонков — краткая сводка, ключевые моменты и полные транскрипты.',
      'wb.sum.title':'Сводка','wb.sum.participants':'Участники','wb.sum.calls':'Звонки','wb.sum.outcome':'Итог',
      'wb.sum.transcripts':'Полные транскрипты','wb.sum.appears':'звонки {list}',
      'wb.sum.needaudio':'Сводка работает с аудиозаписями — добавьте один или несколько файлов в карточке источника.',
      'wb.sum.done':'Сводка готова','wb.fc.done':'Проверка фактов готова','wb.sem.done':'Анализ тона готов',
      'wb.fc.partial':'частично подтверждено','wb.call':'Звонок {n}','wb.seek':'Перейти к этому моменту',
      'wb.lane.factcheck':'Факты','wb.lane.words':'Сентимент: слова','wb.lane.voice':'Сентимент: голос','wb.sc.save':'Сохранить оценки','wb.sc.cancel':'Отмена','wb.sc.whynote':'Почему вы это меняете? (необязательно)','wb.sc.saved':'Оценки обновлены.','wb.sc.history':'Показать историю','wb.sc.hide':'Скрыть историю','wb.sc.nohistory':'Изменений не было — это исходные оценки.','wb.sc.original':'Исходные (ИИ)','wb.sc.was':'было','wb.sc.editedby':'изменил','wb.sc.rev':'Версия','wb.sc.edited':'изменено','wb.sc.themodel':'модель',
    },
  });

  /* ---------------- CSS (brand.css tokens; class prefix wb-) ---------------- */
  const CSS = `
.wb-src-head { display:flex; justify-content:space-between; align-items:center; gap:10px; flex-wrap:wrap; }
.wb-src-head h3 { margin:0; }
.wb .wb-modes, .card .wb-src-head .wb-modes { margin:0; padding:0; }
.wb-src .drop { margin-top:14px; }
.wb-files { list-style:none; margin:10px 0 0; padding:0; display:flex; flex-direction:column; gap:6px; }
.wb-file { display:flex; align-items:center; gap:10px; padding:8px 12px; border:1px solid var(--hairline); border-radius:var(--r-md); background:var(--input-bg); font-size:13px; }
.wb-file-num { width:22px; height:22px; border-radius:50%; flex:none; display:inline-flex; align-items:center; justify-content:center;
  background:color-mix(in oklab,var(--beam) 16%,transparent); color:var(--beam); font-size:11px; font-weight:700; }
.wb-file-name { flex:1; min-width:0; overflow-wrap:anywhere; }
.wb-file-size { color:var(--muted); font-variant-numeric:tabular-nums; white-space:nowrap; font-size:11.5px; }
.wb-mode-text textarea { min-height:140px; }
.wb-done-row { display:flex; align-items:center; gap:12px; flex-wrap:wrap; }
.wb-done-icon { font-size:20px; flex:none; }
.wb-done-text { flex:1; min-width:0; font-size:13.5px; color:var(--paper); overflow-wrap:anywhere; }
.wb-done-text .wb-meta { color:var(--muted); }
.wb-done-note { margin-top:8px; color:var(--pending); }
.wb-switcher { margin-bottom:10px; }
.wb-tl { margin-bottom:16px; } .wb-tl[hidden] { display:none; }
.wb-tl-note { margin:0 0 8px; color:var(--pending); }
.wb-pane { display:none; } .wb-pane.active { display:block; animation:fade .25s var(--ease); }
.wb-note { margin:0 0 4px; font-size:12.5px; } .wb-note a { color:var(--beam); font-weight:600; }
.wb-run-row { align-items:center; margin-top:12px; }
.wb-opts { display:flex; gap:16px; flex-wrap:wrap; align-items:center; margin-top:10px; }
.wb-opts label { display:inline-flex; align-items:center; gap:8px; margin:0; font-size:13px; color:var(--paper); cursor:pointer; }
.wb-opts label.off { opacity:.55; cursor:not-allowed; }
.wb-guide { margin-top:12px; border:1px solid var(--hairline); border-radius:var(--r-md); background:var(--input-bg); }
.wb-guide summary { cursor:pointer; padding:10px 14px; font-size:13px; color:var(--mist); font-weight:600; list-style:none; }
.wb-guide summary::-webkit-details-marker { display:none; }
.wb-guide summary::before { content:'▸'; display:inline-block; margin-right:8px; transition:transform .16s var(--ease); }
.wb-guide[open] summary::before { transform:rotate(90deg); }
.wb-guide[open] summary { border-bottom:1px solid var(--hairline); }
.wb-guide-body { padding:4px 14px 14px; }
.wb-res { margin-top:14px; }
.wb-res-head { display:flex; justify-content:space-between; align-items:flex-start; gap:12px; flex-wrap:wrap; }
.wb-res-head h3 { margin:0; }
.wb-pills { display:flex; gap:6px; flex-wrap:wrap; margin-top:10px; }
.pill.partial { background:color-mix(in oklab,var(--pending) 18%,transparent); color:var(--pending); }
.fc-claim.v-PARTIALLY_SUPPORTED { border-left-color:var(--pending); background:color-mix(in oklab,var(--pending) 6%,var(--input-bg)); }
[data-seek] { cursor:pointer; }
/* Score / accuracy numbers colour by band. A class, not an inline style, so the light theme can
   darken them (an inline colour would need !important to override). */
.wb-band-ok { color:var(--ok); } .wb-band-pending { color:var(--pending); }
.wb-band-alert { color:var(--alert); } .wb-band-muted { color:var(--muted); }
/* The level tokens are tuned for dark cards: at 11px bold on a near-white pill they land around
   3:1, under WCAG AA for small text. Same hues, darkened, only inside a workbench result. */
[data-theme="light"] .wb-res .pill.supported, [data-theme="light"] .wb-res .wb-tone.good,
[data-theme="light"] .wb-band-ok { color:#0f6b40; }
[data-theme="light"] .wb-res .pill.partial, [data-theme="light"] .wb-res .pill.pending,
[data-theme="light"] .wb-res .wb-tone.mid, [data-theme="light"] .wb-band-pending { color:#8a5200; }
[data-theme="light"] .wb-res .pill.contradicted, [data-theme="light"] .wb-res .wb-tone.bad,
[data-theme="light"] .wb-band-alert { color:#b3241a; }
.wb-seekcard:hover { border-color:color-mix(in oklab,var(--beam) 55%,var(--hairline)); }
.wb-claim-head { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
.wb-claim-head .hint { margin:0; }
.wb-claim-text { margin-top:6px; }
.wb-time { font-size:11px; color:var(--muted); font-variant-numeric:tabular-nums; white-space:nowrap; }
.wb-time::before { content:'▶ '; font-size:9px; }
.wb-q { padding:3px 6px; margin:2px -6px; border-radius:6px; }
.wb-q:hover { background:color-mix(in oklab,var(--surface-2) 60%,transparent); }
.wb-q .wb-time { margin-left:8px; }
.wb-spk { display:grid; grid-template-columns:repeat(auto-fit, minmax(240px, 1fr)); gap:12px; margin-top:10px; }
.wb-spk-card { border:1px solid var(--hairline); border-radius:var(--r-md); padding:12px 14px; background:var(--input-bg); min-width:0; }
.wb-spk-head { display:flex; justify-content:space-between; align-items:center; gap:8px; flex-wrap:wrap; }
.wb-spk-name { font-weight:700; }
.wb-sec { margin-top:10px; }
.wb-sec-title { font-size:11.5px; color:var(--muted); text-transform:uppercase; letter-spacing:.06em; font-weight:600; margin-bottom:4px; }
.wb-inline { display:flex; gap:10px; align-items:center; flex-wrap:wrap; }
.wb-flags { margin-top:6px; }
.wb-tone { display:inline-block; padding:2px 9px; border-radius:999px; font-size:11px; font-weight:600; white-space:nowrap; }
.wb-tone.good { background:color-mix(in oklab,var(--ok) 18%,transparent); color:var(--ok); }
.wb-tone.mid { background:color-mix(in oklab,var(--pending) 18%,transparent); color:var(--pending); }
.wb-tone.bad { background:color-mix(in oklab,var(--alert) 16%,transparent); color:var(--coral); }
.wb-tone.none { background:color-mix(in oklab,var(--muted) 20%,transparent); color:var(--muted); }
.wb-segs { margin-top:8px; display:flex; flex-direction:column; gap:6px; max-height:320px; overflow:auto; padding:2px; }
.wb-seg { padding:8px 12px; border:1px solid var(--hairline); border-left:3px solid var(--muted); border-radius:var(--r-md); background:var(--input-bg); font-size:13px; }
.wb-seg.good { border-left-color:var(--ok); } .wb-seg.mid { border-left-color:var(--pending); } .wb-seg.bad { border-left-color:var(--alert); }
.wb-seg.now { background:color-mix(in oklab,var(--beam) 8%,var(--input-bg)); }
.wb-seg-head { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
.wb-seg-text { margin-top:4px; color:var(--mist); }
.wb-seg-note { margin-top:3px; }
.wb-spkchip { display:inline-block; padding:2px 8px; border-radius:999px; font-size:11px; font-weight:600; white-space:nowrap;
  background:color-mix(in oklab,var(--beam) 12%,transparent); color:var(--paper); border:1px solid color-mix(in oklab,var(--beam) 35%,transparent); }
.wb-sum-short { font-size:14.5px; color:var(--paper); margin:8px 0 12px; }
.wb-part { display:inline-flex; gap:6px; align-items:center; padding:5px 11px; border-radius:999px; border:1px solid var(--hairline);
  background:var(--input-bg); font-size:12.5px; margin:4px 6px 0 0; }
.wb-calls { display:grid; grid-template-columns:repeat(auto-fit, minmax(240px, 1fr)); gap:12px; margin-top:8px; }
.wb-call { border:1px solid var(--hairline); border-radius:var(--r-md); padding:12px 14px; background:var(--input-bg); min-width:0; }
.wb-call.active { border-color:var(--beam); }
.wb-call-title { font-weight:700; } .wb-call-file { color:var(--muted); font-size:11.5px; overflow-wrap:anywhere; }
.wb-call p { margin:6px 0 0; font-size:13px; color:var(--mist); }
.wb-tx-head { display:flex; gap:8px; align-items:baseline; flex-wrap:wrap; margin-top:14px; font-size:13px; }
.wb-tx { margin-top:6px; max-height:320px; overflow:auto; border:1px solid var(--hairline); border-radius:var(--r-md); background:var(--input-bg); padding:8px 10px; }
.wb-tx p { margin:2px 0; font-size:13px; color:var(--mist); display:flex; gap:8px; align-items:baseline; flex-wrap:wrap; padding:4px 6px; border-radius:6px; }
.wb-tx p:hover { background:color-mix(in oklab,var(--surface-2) 40%,transparent); }
.wb-tx p span:last-child { flex:1 1 200px; min-width:0; }
.wb-flash { animation:wbflash 1.6s var(--ease); }
@keyframes wbflash { 0%,60% { box-shadow:0 0 0 3px color-mix(in oklab,var(--beam) 60%,transparent); } 100% { box-shadow:none; } }
`;
  if (!document.getElementById('cq-workbench-css')) {
    const s = document.createElement('style'); s.id = 'cq-workbench-css'; s.textContent = CSS; document.head.appendChild(s);
  }

  /* ---------------- helpers ---------------- */
  const t = k => CQ.t(k);
  const tr = (k, vars) => Object.keys(vars || {}).reduce((s, v) => s.split('{' + v + '}').join(String(vars[v])), t(k));
  const esc = s => (s ?? '').toString().replace(/[&<>"]/g, c => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;' }[c]));
  const num = v => (typeof v === 'number' && isFinite(v)) ? v : (typeof v === 'string' && v.trim() !== '' && isFinite(+v) ? +v : null);
  const arr = v => Array.isArray(v) ? v : [];
  const fmtT = s => { s = Math.max(0, Math.floor(num(s) || 0)); return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0'); };
  const fmtSize = b => b >= 1048576 ? (b / 1048576).toFixed(1) + ' MB' : Math.max(1, Math.round(b / 1024)) + ' KB';
  /* Where a score changes colour. Red below `amber_from`, amber up to `green_from`, green
     above — the workspace's own thresholds, fetched once per panel (GET /scoring/bands) and
     falling back to the built-in 50/80 until they arrive.

     These are the ONE source of the three colours: the scorecard number, its bar, and the
     timeline lane all read them, so a dimension cannot be red in the card and olive on the
     timeline the way it was when the lane used a continuous hue ramp of its own. */
  const BANDS = { amber_from: 50, green_from: 80 };
  const scoreLevel = v => v == null ? 'none'
    : (v >= BANDS.green_from ? 'good' : v >= BANDS.amber_from ? 'mid' : 'bad');
  const band = v => ({ good: 'ok', mid: 'pending', bad: 'alert', none: 'muted' })[scoreLevel(v)];
  const barcls = v => v == null ? '' : scoreLevel(v);
  const pct = v => Math.max(0, Math.min(100, Math.round(num(v) || 0)));
  const LEVELS = ['good', 'mid', 'bad', 'none'];
  const lvl = l => LEVELS.includes(l) ? l : 'none';
  const worst = (a, b) => { const r = { none: 0, good: 1, mid: 2, bad: 3 }; return (r[lvl(b)] > r[lvl(a)]) ? lvl(b) : lvl(a); };
  const toneLevel = w => ({ polite:'good', neutral:'good', curt:'mid', impolite:'bad', rude:'bad', aggressive:'bad' }[String(w || '').toLowerCase()] || 'none');
  const verdictLevel = w => ({ patient:'good', calm:'good', tense:'mid', aggressive:'bad' }[String(w || '').toLowerCase()] || 'none');
  /* Why there is no voice half. The backend now names the cause (`voice_status`); older
     results carry none, so an unrecognised or missing status falls back to the generic line.
     This matters because "still starting up" is worth retrying and "no per-turn timings" never
     is, and both used to read identically. */
  const noVoiceReason = (r) => {
    const st = r && r.voice_status;
    if (!st || st === 'ok') return t('wb.sem.novoice');
    const k = 'wb.novoice.' + st, v = t(k);
    return v === k ? t('wb.sem.novoice') : v;
  };
  const word = (prefix, w, fallback) => { if (!w) return fallback == null ? '—' : fallback; const k = prefix + String(w).toLowerCase(); const v = t(k); return v === k ? String(w) : v; };
  const langName = c => { if (!c) return ''; const k = 'lang.' + String(c).toLowerCase().slice(0, 2); const v = t(k); return v === k ? String(c) : v; };

  /* Title-case a label only where the script actually has case. Georgian is unicameral:
     toUpperCase() maps Mkhedruli to MTAVRULI, so "ოპერატორი" would render as "Ოპერატორი" — one
     Mtavruli letter followed by Mkhedruli, a mixed-script word the orthography never produces.
     Per §2 a pasted transcript keeps its own label as the speaker id, so this is the normal path
     for Georgian calls; Latin and Cyrillic ids still capitalise. */
  const GEORGIAN = /[\u10A0-\u10FF\u1C90-\u1CBF\u2D00-\u2D2F]/;
  const capFirst = v => { const str = String(v == null ? '' : v), c = str.charAt(0), u = c.toUpperCase();
    return (!c || u === c || u.length !== 1 || GEORGIAN.test(c)) ? str : u + str.slice(1); };

  function speakerName(id, labels) {
    if (!id) return '';
    if (labels && labels[id]) return labels[id];
    const m = /^speaker_(\d+)$/.exec(id);
    if (m) return tr('wb.speaker', { n: +m[1] + 1 });
    const v = word('wb.role.', id, id);
    return v === id ? capFirst(id) : v;
  }
  /* Attributes that make an element a "jump to this moment" control. The delegated handler in the
     component reads them; renderers stay pure html. */
  function seekAttrs(start, seg, callIdx) {
    const a = [' data-seek="1" tabindex="0" role="button"', ` title="${esc(t('wb.seek'))}"`];
    if (num(start) != null) a.push(` data-start="${num(start)}"`);
    if (num(seg) != null) a.push(` data-seg="${num(seg)}"`);
    if (callIdx != null) a.push(` data-call="${callIdx}"`);
    return a.join('');
  }
  const timeBadge = (start, end) => num(start) == null ? ''
    : `<span class="wb-time">${fmtT(start)}${num(end) != null ? '–' + fmtT(end) : ''}</span>`;

  /* ---------------- v2 renderers ---------------- */
  function renderFactcheck(kb, ctx = {}) {
    if (!kb) return '';
    const claims = arr(kb.claims), c = kb.counts || {};
    if (!claims.length) return `<div class="wb-res"><h3>${t('fc.title')}</h3><div class="empty">${t('fc.nochecked')}</div></div>`;
    const acc = num(kb.accuracy_score);
    const V = { SUPPORTED:['supported','fc.supported'], PARTIALLY_SUPPORTED:['partial','wb.fc.partial'],
                CONTRADICTED:['contradicted','fc.contradicted'], NOT_IN_KB:['notinkb','fc.notinkb'] };
    const card = cl => {
      const v = V[cl.verdict] || V.NOT_IN_KB;
      const ev = cl.evidence && typeof cl.evidence === 'object' ? cl.evidence : null;
      const seg = arr(cl.segments)[0];
      const conf = num(cl.confidence) != null ? ' · ' + Math.round(cl.confidence * 100) + '%' : '';
      const who = cl.speaker ? speakerName(cl.speaker, ctx.speakerLabels) : '';
      const meta = [who, cl.category ? String(cl.category) : ''].filter(Boolean).join(' · ') + conf;
      return `<div class="fc-claim v-${esc(cl.verdict)} wb-seekcard"${seekAttrs(cl.start, seg, ctx.callIndex)}>
        <div class="wb-claim-head"><span class="pill ${v[0]}">${t(v[1])}</span>${timeBadge(cl.start, cl.end)}${meta ? `<span class="hint">${esc(meta)}</span>` : ''}</div>
        <div class="wb-claim-text">${esc(cl.claim)}</div>
        ${cl.rationale ? `<div class="hint">${esc(cl.rationale)}</div>` : ''}
        ${ev ? `<div class="fc-ev"><div class="fc-ev-src">📄 ${esc(ev.title || ev.doc_type || 'KB')}${num(ev.score) != null ? ' · ' + ev.score : ''}</div>${esc(ev.snippet || '')}</div>` : ''}
      </div>`;
    };
    const bad = claims.filter(x => x.verdict === 'CONTRADICTED');
    return `<div class="wb-res">
      <div class="wb-res-head"><h3>${t('fc.title')}</h3>
        <div class="fc-accuracy"><div class="num wb-band-${band(acc)}">${acc == null ? '—' : acc}</div><span class="muted">${t('fc.accuracy')}</span></div></div>
      <div class="wb-pills"><span class="pill supported">${c.supported || 0} ${t('fc.supported')}</span>
        <span class="pill partial">${c.partially_supported || 0} ${t('wb.fc.partial')}</span>
        <span class="pill contradicted">${c.contradicted || 0} ${t('fc.contradicted')}</span>
        <span class="pill notinkb">${c.not_in_kb || 0} ${t('fc.notinkb')}</span></div>
      ${bad.length ? `<h4 style="color:var(--coral)">⚠ ${t('fc.misinfo')}</h4>${bad.map(card).join('')}<h4>${t('fc.allclaims')}</h4>` : ''}
      ${claims.map(card).join('')}
    </div>`;
  }

  function renderScorecard(sc, ctx = {}) {
    if (!sc || !arr(sc.dimensions).length) return '';
    const total = num(sc.weighted_total);
    const evItem = e => {
      if (typeof e === 'string') return `<q>${esc(e)}</q>`;
      if (!e || typeof e !== 'object') return '';
      return `<q class="wb-q"${seekAttrs(e.start, arr(e.segments)[0], ctx.callIndex)}>${esc(e.quote || e.text || '')}${timeBadge(e.start, e.end)}</q>`;
    };
    const dim = d => { const s = num(d.score), ev = arr(d.evidence);
      // When the reader may overrule the model, the number itself becomes the control —
      // a separate "edit" mode would hide the evidence they are judging against.
      const scoreCell = ctx.editable
        ? `<span class="sc-dim-score wb-sc-editcell"><input type="number" class="wb-sc-in" min="0" max="100"
             data-key="${esc(d.key)}" value="${s == null ? '' : s}" aria-label="${esc(d.name)}" />
             <span class="sc-meta">/100</span></span>`
        : `<span class="sc-dim-score wb-band-${band(s)}">${s == null ? '—' : s}<span class="sc-meta">/100</span></span>`;
      // An edited dimension says so, and keeps the model's number visible beside it: the
      // point of an override is the disagreement, and hiding the original hides that.
      const wasAi = d.edited && d.ai_score != null
        ? `<span class="pill" style="margin-left:6px">${esc(t('wb.sc.edited'))} · ${esc(t('wb.sc.was'))} ${d.ai_score}</span>` : '';
      return `<div class="sc-dim">
        <div class="sc-dim-head"><span class="sc-dim-name">${esc(d.name)}${wasAi}</span>
          ${scoreCell}</div>
        <div class="sc-meta">${t('sc.weight')} ${d.weight ?? '—'}% · ${t('sc.contribution')} ${d.contribution ?? '—'}</div>
        <div class="sc-bar ${barcls(s)}"><span style="width:${pct(s)}%"></span></div>
        ${d.rationale ? `<div class="hint" style="margin-top:6px">${esc(d.rationale)}</div>` : ''}
        ${ev.length ? `<div class="sc-evid">${ev.map(evItem).join('')}</div>` : ''}
      </div>`; };
    const ver = (sc.is_default || sc.version === 0) ? t('wb.sc.default') : (sc.version != null ? `${t('sc.version')} ${esc(sc.version)}` : '');
    const editedBy = sc.manually_edited && sc.edited_by
      ? `<div class="sc-meta">${esc(t('wb.sc.editedby'))} ${esc(sc.edited_by)}</div>` : '';
    const tools = ctx.editable || ctx.jobId ? `<div class="wb-sc-tools">
        ${ctx.editable ? `<input class="wb-sc-note" type="text" placeholder="${esc(t('wb.sc.whynote'))}" />
          <button class="primary wb-sc-save" type="button">${esc(t('wb.sc.save'))}</button>` : ''}
        ${ctx.jobId ? `<button class="ghost wb-sc-hist" type="button">${esc(t('wb.sc.history'))}</button>` : ''}
      </div><div class="wb-sc-histbox hidden"></div><div class="msg wb-sc-msg"></div>` : '';
    return `<div class="wb-res" data-job="${esc(ctx.jobId || '')}">
      <div class="wb-res-head"><div><h3>${t('sc.title')}</h3>${ver ? `<div class="sc-meta">${ver}</div>` : ''}${editedBy}</div>
        <div class="sc-total"><div class="num wb-band-${band(total)}">${total == null ? '—' : total}</div><span class="muted">${t('sc.weighted')} / ${sc.max_total || 100}</span></div></div>
      ${sc.dimensions.map(dim).join('')}
      ${tools}
    </div>`;
  }

  function renderSemantic(sm, ctx = {}) {
    if (!sm) return '';
    const segs = arr(ctx.segments), labels = ctx.speakerLabels;
    const modes = arr(sm.modes);
    const spk = s => {
      const name = speakerName(s.speaker, labels), roleWord = s.role && s.role !== 'unknown' ? word('wb.role.', s.role) : '';
      const role = roleWord && roleWord !== name ? `<span class="pill">${esc(roleWord)}</span>` : '';   // no "Agent · Agent"
      let tx = '';
      if (s.text) {
        const p = num(s.text.politeness), flags = arr(s.text.flags).filter(Boolean);
        tx = `<div class="wb-sec"><div class="wb-sec-title">${t('wb.lane.words')}</div>
          <div class="wb-inline"><span class="wb-tone ${toneLevel(s.text.overall)}">${esc(word('wb.tone.', s.text.overall))}</span>
            <span class="sc-meta">${t('wb.sem.politeness')} ${p == null ? '—' : p}/100</span></div>
          <div class="sc-bar ${barcls(p)}"><span style="width:${pct(p)}%"></span></div>
          ${flags.length ? `<div class="wb-flags">${flags.map(f => `<span class="chip">${esc(f)}</span>`).join('')}</div>` : ''}
          ${s.text.rationale ? `<div class="hint">${esc(s.text.rationale)}</div>` : ''}</div>`;
      }
      let vc = '';
      if (s.voice) {
        const g = pct((num(s.voice.share_good) || 0) * 100), b = pct((num(s.voice.share_bad) || 0) * 100);
        vc = `<div class="wb-sec"><div class="wb-sec-title">${t('wb.lane.voice')}</div>
          <div class="wb-inline"><span class="wb-tone ${verdictLevel(s.voice.voice)}">${esc(word('wb.voice.', s.voice.voice))}</span></div>
          <div class="sc-meta" style="margin-top:6px">${t('wb.sem.share_good')} · ${g}%</div><div class="sc-bar good"><span style="width:${g}%"></span></div>
          <div class="sc-meta" style="margin-top:6px">${t('wb.sem.share_bad')} · ${b}%</div><div class="sc-bar bad"><span style="width:${b}%"></span></div></div>`;
      } else if (modes.includes('voice')) {
        vc = `<div class="wb-sec"><div class="wb-sec-title">${t('wb.lane.voice')}</div><div class="hint">${esc(noVoiceReason(sm))}</div></div>`;
      }
      return `<div class="wb-spk-card"><div class="wb-spk-head"><span class="wb-spk-name">${esc(name)}</span>${role}</div>${tx}${vc}</div>`;
    };
    const row = sg => {
      const i = num(sg.i); const src = i != null ? segs[i] : null;
      const text = (src && src.text) || sg.text || '';
      const level = worst(sg.text_tone ? sg.text_level : 'none', sg.voice_label ? sg.voice_level : 'none');
      const chips = [];
      if (sg.text_tone) chips.push(`<span class="wb-tone ${lvl(sg.text_level)}">${esc(word('wb.tone.', sg.text_tone))}</span>`);
      if (sg.voice_label) chips.push(`<span class="wb-tone ${lvl(sg.voice_level)}">🎙 ${esc(word('wb.vl.', sg.voice_label))}${num(sg.voice_confidence) != null ? ' · ' + Math.round(sg.voice_confidence * 100) + '%' : ''}</span>`);
      const start = sg.start != null ? sg.start : (src ? src.start : null), end = sg.end != null ? sg.end : (src ? src.end : null);
      return `<div class="wb-seg ${level}"${seekAttrs(start, i, ctx.callIndex)}>
        <div class="wb-seg-head"><span class="wb-spkchip">${esc(speakerName(sg.speaker || (src && src.speaker), labels))}</span>${timeBadge(start, end)}${chips.join('')}</div>
        ${text ? `<div class="wb-seg-text">${esc(text)}</div>` : ''}
        ${sg.text_note ? `<div class="hint wb-seg-note">${esc(sg.text_note)}</div>` : ''}
      </div>`;
    };
    const segRows = arr(sm.segments);
    return `<div class="wb-res">
      <div class="wb-res-head"><h3>${t('wb.sem.title')}</h3>${sm.language ? `<span class="muted">${esc(langName(sm.language))}</span>` : ''}</div>
      ${modes.includes('voice') && sm.voice_available === false ? `<div class="msg" style="color:var(--pending)">${esc(noVoiceReason(sm))}</div>` : ''}
      <div class="wb-spk">${arr(sm.speakers).map(spk).join('')}</div>
      ${sm.summary ? `<h4>${t('wb.sem.summary')}</h4><p style="margin:0">${esc(sm.summary)}</p>` : ''}
      ${segRows.length ? `<h4>${t('wb.sem.turns')}</h4><div class="wb-segs">${segRows.map(row).join('')}</div>` : ''}
    </div>`;
  }

  /* One transcript block for a call: a paragraph per segment, each a seek control. */
  function transcriptHTML(segments, callIdx, labels, transcript) {
    const segs = arr(segments);
    if (!segs.length) return `<pre class="tx">${esc(transcript) || t('res.empty')}</pre>`;
    return `<div class="wb-tx">${segs.map((s, i) => `<p${seekAttrs(s.start, s.i != null ? s.i : i, callIdx)}>
      <span class="wb-spkchip">${esc(speakerName(s.speaker, labels))}</span>${timeBadge(s.start, s.end)}<span>${esc(s.text)}</span></p>`).join('')}</div>`;
  }

  function renderSummary(sum, ctx = {}) {
    if (!sum) return '';
    const s = (sum.summary && typeof sum.summary === 'object') ? sum.summary : sum;
    const calls = arr(ctx.calls).length ? arr(ctx.calls) : arr(sum.calls);
    const active = ctx.active != null ? ctx.active : 0;
    const list = v => { const a = arr(v).filter(x => x != null && String(x).trim()); return a.length ? `<ul class="tight">${a.map(x => `<li>${esc(x)}</li>`).join('')}</ul>` : `<span class="muted">—</span>`; };
    const parts = arr(s.participants).map(p => `<span class="wb-part"><b>${esc(p.label)}</b>${p.role ? `<span class="muted">${esc(word('wb.role.', p.role))}</span>` : ''}${arr(p.appears_in).length ? `<span class="muted">· ${esc(tr('wb.sum.appears', { list: arr(p.appears_in).map(i => (num(i) || 0) + 1).join(', ') }))}</span>` : ''}</span>`).join('');
    const cardOf = (c, i) => { const idx = num(c.index) != null ? num(c.index) : i; const call = calls[idx] || {};
      return `<div class="wb-call${idx === active ? ' active' : ''}"${calls.length > 1 ? seekAttrs(null, null, idx) : ''}>
        <div class="wb-call-title">${esc(c.title || tr('wb.call', { n: idx + 1 }))}</div>
        <div class="wb-call-file">${esc(c.filename || call.filename || '')}${call.duration != null ? ' · ' + fmtT(call.duration) : ''}</div>
        ${c.summary ? `<p>${esc(c.summary)}</p>` : ''}
        ${c.outcome ? `<p><b style="color:var(--mist)">${t('wb.sum.outcome')}:</b> ${esc(c.outcome)}</p>` : ''}
      </div>`; };
    const tx = calls.map((c, i) => `<div class="wb-tx-head"><b>${esc(tr('wb.call', { n: i + 1 }))}</b>${c.filename ? `<span class="muted">${esc(c.filename)}</span>` : ''}${c.language ? `<span class="muted">· ${esc(langName(c.language))}</span>` : ''}${num(c.duration) != null ? `<span class="muted">· ${fmtT(c.duration)}</span>` : ''}</div>
      ${transcriptHTML(c.segments, i, c.speakerLabels, c.transcript)}`).join('');
    return `<div class="wb-res">
      <div class="wb-res-head"><h3>${t('wb.sum.title')}</h3>${s.language ? `<span class="muted">${esc(langName(s.language))}</span>` : ''}</div>
      <p class="wb-sum-short">${esc(s.short_summary || '')}</p>
      <div class="row"><div><b style="color:var(--mist)">${t('res.keypoints')}</b>${list(s.key_points)}</div>
        <div><b style="color:var(--mist)">${t('res.actions')}</b>${list(s.action_items)}</div></div>
      ${parts ? `<h4>${t('wb.sum.participants')}</h4><div>${parts}</div>` : ''}
      ${arr(s.calls).length ? `<h4>${t('wb.sum.calls')}</h4><div class="wb-calls">${arr(s.calls).map(cardOf).join('')}</div>` : ''}
      ${calls.length ? `<h4>${t('wb.sum.transcripts')}</h4>${tx}` : ''}
    </div>`;
  }

  /* ---------------- SSE frame parser (shared with the rubric import in tenant.html) ----------------
     Pulls whole `event: x\ndata: {...}\n\n` frames out of a buffer that grows between ticks and keeps
     the partial tail — a frame can arrive split across two progress events. */
  function sseFrames(st, text) {
    st.buf += text.replace(/\r\n/g, '\n');
    const out = []; let i;
    while ((i = st.buf.indexOf('\n\n')) >= 0) {
      const raw = st.buf.slice(0, i); st.buf = st.buf.slice(i + 2);
      let name = '', data = '';
      raw.split('\n').forEach(line => {
        if (!line || line[0] === ':') return;
        if (line.indexOf('event:') === 0) name = line.slice(6).trim();
        else if (line.indexOf('data:') === 0) data += (data ? '\n' : '') + line.slice(5).trim();
      });
      if (!name) continue;
      let payload; try { payload = data ? JSON.parse(data) : {}; } catch { continue; }
      out.push([name, payload]);
    }
    return out;
  }
  const isSse = xhr => (xhr.getResponseHeader('Content-Type') || '').toLowerCase().indexOf('text/event-stream') >= 0;

  /* ---------------- the component ---------------- */
  const ALL = ['factcheck', 'score', 'semantic', 'summarise'];
  const ACCEPT = 'audio/*,video/*,.m4a,.aac,.flac,.opus,.wma,.amr,.aiff,.oga,.3gp';
  const MAX_FILES = 10, MAX_MB = 100, MAX_TOTAL_MB = 300;   // §8: 1..10 files, <= 100 MB each, <= 300 MB total

  function Workbench(container, opts = {}) {
    const api = opts.api || CQ.API;
    const fetchFn = typeof opts.fetch === 'function' ? opts.fetch : (u, i) => fetch(u, i);
    const headers = typeof opts.headers === 'function' ? opts.headers : () => ({});
    // The caller's order wins (§13.2 passes an array); unknown names are dropped, duplicates too.
    const want = arr(opts.features).filter(k => ALL.includes(k));
    const ORDER = (want.length ? want : ALL).filter((k, i, a) => a.indexOf(k) === i);
    const S = { mode: 'audio', files: [], tab: ORDER[0] || 'score', source: null, summary: null, xhr: null, running: {}, guideLoaded: false };

    const root = document.createElement('div'); root.className = 'wb';
    const paneHTML = k => {
      let extra = '';
      if (k === 'factcheck') extra = `<p class="hint wb-note" data-i18n="wb.fc.note"></p>`;
      else if (k === 'score') extra = `<p class="hint wb-note"><span data-i18n="wb.sc.note"></span>${opts.rubricHref ? ` <a href="${esc(opts.rubricHref)}" data-i18n="wb.sc.edit"></a>` : ''}</p>`;
      else if (k === 'semantic') extra = `<p class="hint wb-note" data-i18n="wb.sem.note"></p>
        <div class="wb-opts">
          <label><input type="checkbox" class="wb-ck-text" checked /> <span data-i18n="wb.sem.words"></span><button type="button" class="tip" data-tip-i18n="wb.sem.words.tip"></button></label>
          <label class="wb-voice-lab"><input type="checkbox" class="wb-ck-voice" /> <span data-i18n="wb.sem.voice"></span><button type="button" class="tip" data-tip-i18n="wb.sem.voice.tip"></button></label>
          <span class="hint wb-voice-hint hidden" data-i18n="wb.sem.voice.off"></span>
        </div>
        ${opts.sentimentConfig ? `<details class="wb-guide"><summary data-i18n="wb.sem.guidance"></summary><div class="wb-guide-body">
          <p class="hint hidden wb-guide-ro" data-i18n="sn.readonly" style="color:var(--pending)"></p>
          <label data-i18n="sn.guidance"></label>
          <textarea class="wb-guide-text" data-i18n-ph="sn.guidance.ph" style="min-height:70px"></textarea>
          <div class="actions"><button type="button" class="ghost wb-guide-save" data-i18n="sn.save"></button></div>
          <div class="msg wb-guide-msg" aria-live="polite"></div></div></details>` : ''}`;
      else extra = `<p class="hint wb-note" data-i18n="wb.sum.note"></p>`;
      return `<div class="wb-pane" data-an="${k}" role="tabpanel">${extra}
        <div class="actions wb-run-row"><button type="button" class="primary wb-run" data-run="${k}"></button></div>
        <div class="msg err wb-err" aria-live="polite"></div>
        <div class="wb-result"></div></div>`;
    };
    root.innerHTML = `
      <section class="card wb-src">
        <div class="wb-src-head"><h3 data-i18n="wb.src.title"></h3>
          <div class="subtabs wb-modes" role="tablist">
            <button type="button" class="subtab active" role="tab" aria-selected="true" data-mode="audio" data-i18n="wb.src.audio"></button>
            <button type="button" class="subtab" role="tab" aria-selected="false" data-mode="text" data-i18n="wb.src.paste"></button>
          </div></div>
        <div class="wb-mode-audio">
          <div class="drop wb-drop">
            <input type="file" accept="${ACCEPT}" multiple data-i18n-aria="f.audiofile" />
            <div class="drop-title" data-i18n="drop.title"></div>
            <div class="drop-sub wb-drop-sub"></div>
          </div>
          <ul class="wb-files"></ul>
        </div>
        <div class="wb-mode-text hidden">
          <textarea class="wb-paste" data-i18n-ph="wb.paste.ph" data-i18n-aria="wb.src.paste"></textarea>
          <div class="hint" data-i18n="wb.paste.hint"></div>
        </div>
        <div class="actions"><button type="button" class="primary wb-go"></button>
          <button type="button" class="ghost wb-cancel hidden" data-i18n="btn.cancel"></button></div>
        <div class="msg err wb-src-err" aria-live="polite"></div>
      </section>
      <section class="card wb-done hidden">
        <div class="wb-done-row"><span class="wb-done-icon" aria-hidden="true">🎧</span><span class="wb-done-text"></span>
          <button type="button" class="ghost wb-change" data-i18n="wb.change"></button></div>
        <div class="msg wb-done-note hidden"></div>
      </section>
      <div class="imp-prog hidden wb-prog">
        <div class="imp-prog-head"><span class="wb-prog-label" aria-live="polite"></span><span class="imp-prog-pct" aria-hidden="true"></span></div>
        <div class="sc-bar" role="progressbar" aria-valuemin="0" aria-valuemax="100"><span></span></div>
      </div>
      <div class="subtabs wb-switcher hidden" role="tablist"></div>
      <div class="wb-timelines"></div>
      <section class="card wb-an">
        <div class="subtabs wb-tabs" role="tablist">${ORDER.map(k => `<button type="button" class="subtab" role="tab" aria-selected="false" data-tab="${k}" data-i18n="wb.tab.${k}"></button>`).join('')}</div>
        ${ORDER.map(paneHTML).join('')}
      </section>`;
    container.innerHTML = ''; container.appendChild(root);
    const $ = sel => root.querySelector(sel), $$ = sel => Array.from(root.querySelectorAll(sel));
    const srcEl = $('.wb-src'), doneEl = $('.wb-done'), progEl = $('.wb-prog'), srcErr = $('.wb-src-err');
    // The progress bar lives inside the source card while that card is open, and moves into the
    // collapsed line when a re-run (Summarise on stored audio) streams with the card hidden.
    srcEl.insertBefore(progEl, srcErr);
    const paneEl = k => root.querySelector(`.wb-pane[data-an="${k}"]`);
    const activeCall = () => S.source ? S.source.calls[S.source.active] || null : null;
    // One message box per card/pane: `.msg` for a neutral note, `.msg.err` for a failure.
    const errBox = (el, msg, isErr = true) => { if (!el) return; el.classList.toggle('err', isErr); el.textContent = msg || ''; };

    /* ---- wiring ---- */
    $$('.wb-modes .subtab').forEach(b => b.addEventListener('click', () => setMode(b.dataset.mode)));
    const drop = $('.wb-drop'), input = $('.wb-drop input');
    ['dragover', 'dragenter'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add('drag'); }));
    ['dragleave', 'drop'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove('drag'); }));
    drop.addEventListener('drop', e => addFiles(e.dataTransfer && e.dataTransfer.files));
    input.addEventListener('change', () => { addFiles(input.files); input.value = ''; });
    $('.wb-files').addEventListener('click', e => { const b = e.target.closest('[data-rm]'); if (b) { S.files.splice(+b.dataset.rm, 1); sync(); } });
    $('.wb-go').addEventListener('click', go);
    $('.wb-cancel').addEventListener('click', () => { if (S.xhr) S.xhr.abort(); });
    $('.wb-change').addEventListener('click', () => reset());
    $$('.wb-tabs .subtab').forEach(b => b.addEventListener('click', () => setTab(b.dataset.tab)));
    $$('.wb-run').forEach(b => b.addEventListener('click', () => run(b.dataset.run)));
    const vck = $('.wb-ck-voice'); if (vck) vck.addEventListener('change', () => { vck.dataset.touched = '1'; });
    root.addEventListener('click', e => { const el = e.target.closest('[data-seek]'); if (el && root.contains(el)) seekTo(el); });
    root.addEventListener('keydown', e => {
      if (e.key !== 'Enter' && e.key !== ' ') return;
      const el = e.target.closest('[data-seek]'); if (el && el === e.target) { e.preventDefault(); seekTo(el); }
    });
    const guide = $('.wb-guide');
    if (guide) { guide.addEventListener('toggle', () => { if (guide.open && !S.guideLoaded) loadGuide(); }); $('.wb-guide-save').addEventListener('click', saveGuide); }

    /* The workspace's own colour thresholds, once per panel. Failure is silent and harmless:
       BANDS keeps the built-in 50/80, which is what an unconfigured workspace gets anyway. */
    (async () => {
      try {
        const r = await fetchFn(`${api}/scoring/bands`, { headers: headers() });
        if (!r.ok) return;
        const d = await r.json();
        const a = Number(d.amber_from), g = Number(d.green_from);
        if (Number.isFinite(a) && Number.isFinite(g) && a < g) { BANDS.amber_from = a; BANDS.green_from = g; renderAll(); }
      } catch (_) { /* built-in thresholds stand */ }
    })();
    const onLang = () => {
      CQ.applyI18n(root); sync(); renderAll();
      // Lane names ("Fact-check", "Words", "Voice") and speaker chips are translated at push time — push again.
      if (S.source) S.source.calls.forEach(c => { if (c.tl && c.tl.setSpeakerLabels) { try { c.tl.setSpeakerLabels(labelsOf(c)); } catch {} } if (c.tl) pushAllLanes(c); });
      if (S.source) { const call = activeCall(); if (S.source.kind === 'summary') collapse(doneTextSummary(S.source.calls), doneEl.querySelector('.wb-done-note').textContent); else if (call) collapse(doneText(call), call.note); }
    };
    document.addEventListener('cq:lang', onLang);
    const onHide = () => { if (S.xhr) S.xhr.abort(); };
    window.addEventListener('pagehide', onHide);

    /* ---- source card ---- */
    function setMode(m) {
      S.mode = m === 'text' ? 'text' : 'audio';
      $$('.wb-modes .subtab').forEach(b => { const on = b.dataset.mode === S.mode; b.classList.toggle('active', on); b.setAttribute('aria-selected', String(on)); });
      $('.wb-mode-audio').classList.toggle('hidden', S.mode !== 'audio');
      $('.wb-mode-text').classList.toggle('hidden', S.mode !== 'text');
      errBox(srcErr, ''); sync();
    }
    function setTab(k) {
      if (!ORDER.includes(k)) return;
      S.tab = k;
      $$('.wb-tabs .subtab').forEach(b => { const on = b.dataset.tab === k; b.classList.toggle('active', on); b.setAttribute('aria-selected', String(on)); });
      $$('.wb-pane').forEach(p => p.classList.toggle('active', p.dataset.an === k));
      sync();
    }
    function addFiles(list) {
      const files = Array.from(list || []).filter(f => f && typeof f.size === 'number');
      if (!files.length) return;
      if (S.tab !== 'summarise') {
        const replaced = S.files.length > 0 || files.length > 1;
        S.files = [files[0]];
        if (replaced) CQ.toast(t('wb.onefile'), 'info');
      } else {
        let total = S.files.reduce((a, f) => a + (f.size || 0), 0);
        for (const f of files) {
          if (f.size > MAX_MB * 1048576) { CQ.toast(tr('cv.toobig', { name: f.name, max: MAX_MB + ' MB' }), 'err'); continue; }
          if (S.files.length >= MAX_FILES) { CQ.toast(tr('wb.toomany', { max: MAX_FILES }), 'err'); break; }
          // The server rejects the whole upload over 300 MB — say so before the bytes go up the wire.
          if (total + f.size > MAX_TOTAL_MB * 1048576) { CQ.toast(tr('wb.toobig.total', { max: MAX_TOTAL_MB + ' MB' }), 'err'); continue; }
          S.files.push(f); total += f.size;
        }
      }
      errBox(srcErr, '');
      if (S.mode !== 'audio') setMode('audio'); else sync();
    }
    function sync() {
      const busy = !!S.xhr, call = activeCall();
      const multi = S.files.length > 1 || S.tab === 'summarise';
      const go = $('.wb-go');
      go.textContent = S.mode === 'text' ? t('wb.upload.text') : multi ? t('wb.upload.sum') : t('wb.upload');
      go.disabled = busy;
      $('.wb-cancel').classList.toggle('hidden', !busy);
      $('.wb-drop-sub').textContent = t(S.tab === 'summarise' ? 'wb.drop.sub_multi' : 'wb.drop.sub');
      const title = $('.wb-drop .drop-title');
      if (S.files.length === 1) title.innerHTML = `<span class="drop-file">${esc(S.files[0].name)}</span>`;
      else if (S.files.length > 1) title.innerHTML = `<span class="drop-file">${esc(tr('wb.files.n', { n: S.files.length }))}</span>`;
      else title.textContent = t('drop.title');
      $('.wb-files').innerHTML = S.files.length > 1 ? S.files.map((f, i) => `<li class="wb-file"><span class="wb-file-num">${i + 1}</span>
        <span class="wb-file-name">${esc(f.name)}</span><span class="wb-file-size">${fmtSize(f.size)}</span>
        <button type="button" class="act" data-rm="${i}" title="${esc(t('wb.file.remove'))}" aria-label="${esc(t('wb.file.remove'))}">✕</button></li>`).join('') : '';
      ORDER.forEach(k => {
        const b = root.querySelector(`.wb-run[data-run="${k}"]`);
        if (S.running[k]) return;
        const has = k === 'summarise' ? !!S.summary : !!(call && call.results[k]);
        const can = k === 'summarise' ? (S.files.length > 0 || !!(call && (call.blob || call.hasAudio))) : !!call;
        b.textContent = t(has ? 'wb.rerun' : 'wb.run.' + k);
        b.disabled = !can || busy;
      });
      if (vck) {
        const audio = !!(call && call.source === 'audio' && (call.blob || call.hasAudio));
        vck.disabled = !audio;
        if (!audio) vck.checked = false; else if (!vck.dataset.touched) vck.checked = true;
        $('.wb-voice-lab').classList.toggle('off', !audio);
        $('.wb-voice-hint').classList.toggle('hidden', audio || !call);
      }
    }
    function go() {
      errBox(srcErr, '');
      if (S.xhr) return;
      if (S.mode === 'text') return submitText();
      if (!S.files.length) return errBox(srcErr, t('wb.needsource'));
      if (S.files.length > 1 || S.tab === 'summarise') return uploadSummary(S.files.slice(), srcErr);
      uploadRecording(S.files[0]);
    }
    async function submitText() {
      const text = $('.wb-paste').value.trim();
      if (!text) return errBox(srcErr, t('wb.needtext'));
      const go = $('.wb-go'); go.disabled = true; go.innerHTML = '<span class="spinner"></span>' + esc(t('wb.running'));
      try {
        const r = await fetchFn(`${api}/recordings/text`, { method: 'POST', headers: { ...headers(), 'Content-Type': 'application/json' }, body: JSON.stringify({ text }) });
        const rec = await CQ.readResp(r);
        adoptRecording(rec, null, text);
        CQ.toast(t('stt.done'), 'ok');
      } catch (e) { errBox(srcErr, e.message); CQ.toast(e.message, 'err'); }
      finally { sync(); }
    }
    function uploadRecording(file) {
      const fd = new FormData(); fd.append('file', file);
      stream(`${api}/recordings?stream=1`, fd, srcErr, {
        onStage: () => prog(t('wb.stage.transcribing'), null),
        onDone: rec => { adoptRecording(rec, file); CQ.toast(t('stt.done'), 'ok'); },
      });
    }
    function uploadSummary(files, errEl, keep) {
      if (files.reduce((a, f) => a + ((f && f.size) || 0), 0) > MAX_TOTAL_MB * 1048576) {
        const m = tr('wb.toobig.total', { max: MAX_TOTAL_MB + ' MB' });
        S.running.summarise = false; errBox(errEl, m); CQ.toast(m, 'err'); sync(); return;
      }
      const fd = new FormData(); files.forEach(f => fd.append('files', f));
      stream(`${api}/summaries?stream=1`, fd, errEl, {
        onStage: d => {
          if (d.stage === 'summarising') return prog(t('wb.stage.summarising'), null);
          const i = num(d.index); // 0-based (Python enumerate) — the count is decoration, the filename is the news
          prog(tr('wb.stage.transcribing_n', { name: d.filename || '', i: i == null ? '' : i + 1, n: num(d.count) || files.length }), null);
        },
        onDone: sum => {
          if (keep) { S.summary = sum; renderPane('summarise'); sync(); }
          else adoptSummary(sum, files);
          CQ.toast(t('wb.sum.done'), 'ok');
        },
        onEnd: keep ? () => { S.running.summarise = false; } : null,   // the Run button spins for the whole stream
      });
    }
    /* XHR, not fetch: the request has two long phases (upload, then minutes of model work) and both
       must be visible — xhr.upload.onprogress gives real bytes, xhr.onprogress exposes the SSE body as
       it grows. fetch cannot report upload progress; EventSource cannot carry a file. */
    function stream(url, fd, errEl, h) {
      const st = { buf: '', seen: 0, done: false, streaming: null, staged: false };
      const xhr = new XMLHttpRequest(); S.xhr = xhr;
      if (srcEl.classList.contains('hidden')) doneEl.appendChild(progEl); else srcEl.insertBefore(progEl, srcErr);
      const finish = () => { if (S.xhr === xhr) S.xhr = null; if (typeof h.onEnd === 'function') h.onEnd(); progHide(); sync(); };
      const fail = msg => { if (st.done) return; st.done = true; finish(); const m = msg || t('wb.fail'); errBox(errEl, m); CQ.toast(m, 'err'); };
      const ok = d => { if (st.done) return; st.done = true; finish(); try { h.onDone(d); } catch (e) { console.error('workbench:', e); errBox(errEl, e.message); } };
      const apply = ([name, d]) => { if (st.done) return; if (name === 'done') ok(d); else if (name === 'error') fail(d && d.detail); else if (name === 'stage') { st.staged = true; h.onStage(d || {}); } };
      xhr.upload.addEventListener('progress', e => { if (st.done || st.staged) return; prog(t('sc.import.stage.upload'), e.lengthComputable && e.total ? (e.loaded / e.total) * 100 : null); });
      xhr.upload.addEventListener('load', () => { if (!st.done && !st.staged) prog(t('sc.import.stage.queued'), null); });
      xhr.addEventListener('readystatechange', () => { if (xhr.readyState !== 2 || st.streaming !== null) return; st.streaming = isSse(xhr); if (!st.streaming) prog(t('sc.import.stage.queued'), null); });
      xhr.addEventListener('progress', () => { if (!st.streaming || st.done) return; const chunk = xhr.responseText.slice(st.seen); st.seen = xhr.responseText.length; if (chunk) sseFrames(st, chunk).forEach(apply); });
      xhr.addEventListener('load', async () => {
        if (st.done) return;
        if (st.streaming === null) st.streaming = isSse(xhr);
        if (xhr.status === 401 && typeof opts.onUnauthorized === 'function') opts.onUnauthorized();
        if (st.streaming) { const tail = xhr.responseText.slice(st.seen); st.seen = xhr.responseText.length; sseFrames(st, tail).forEach(apply); if (!st.done) fail(t('wb.fail')); return; }
        if (!xhr.status) return fail(t('sc.import.netfail'));
        try { ok(await CQ.readResp(new Response(xhr.responseText || '', { status: xhr.status }))); } catch (e) { fail(e.message); }
      });
      xhr.addEventListener('error', () => fail(t('sc.import.netfail')));
      xhr.addEventListener('abort', () => { if (st.done) return; st.done = true; finish(); errBox(errEl, t('wb.cancelled'), false); });
      xhr.open('POST', url, true);
      const hd = headers() || {}; Object.keys(hd).forEach(k => xhr.setRequestHeader(k, hd[k]));
      errBox(errEl, ''); prog(t('sc.import.stage.upload'), 0); sync();
      xhr.send(fd);                                         // no timeout: this runs for minutes
    }
    function prog(label, p) {
      const bar = progEl.querySelector('.sc-bar'), fill = bar.firstElementChild;
      progEl.classList.remove('hidden'); progEl.querySelector('.wb-prog-label').textContent = label;
      if (p === null) { bar.classList.add('indet'); fill.style.width = ''; bar.removeAttribute('aria-valuenow'); progEl.querySelector('.imp-prog-pct').textContent = ''; }
      else { const v = pct(p); bar.classList.remove('indet'); fill.style.width = v + '%'; bar.setAttribute('aria-valuenow', String(v)); progEl.querySelector('.imp-prog-pct').textContent = v + '%'; }
    }
    function progHide() { progEl.classList.add('hidden'); const bar = progEl.querySelector('.sc-bar'); bar.classList.remove('indet'); bar.firstElementChild.style.width = '0%'; }

    /* ---- calls, timelines, lanes ---- */
    function callFromRow(row, extra = {}) {
      row = row || {};
      const src = extra.source || row.source || (row.audio_url ? 'audio' : 'text');
      const hasAudio = src === 'audio' && (row.has_audio != null ? !!row.has_audio : !!(row.audio_url || extra.blob));
      let segments = arr(row.segments);
      const transcript = row.transcript || extra.text || '';
      if (!segments.length && transcript) segments = transcript.split(/\n+/).map(s => s.trim()).filter(Boolean).map((text, i) => ({ i, speaker: 'speaker_0', start: null, end: null, text }));
      // A pasted transcript's "Agent:" / "ოპერატორი:" labels arrive as the speaker id itself (§2);
      // keep them as roles so the chips read "Agent", not "Speaker 1". Semantic results refine this.
      const roles = {};
      segments.forEach(s => { const id = s && s.speaker; if (id && !/^speaker_\d+$/.test(id) && !roles[id]) roles[id] = String(id); });
      return { id: row.id || row.job_id, filename: row.filename || (extra.blob && extra.blob.name) || '', language: row.language || '',
        duration: num(row.duration_s), segments, transcript, source: src, hasAudio, audioUrl: row.audio_url || null, blob: extra.blob || null,
        results: { factcheck: row.kb_check || null, score: row.scoring || null, semantic: row.semantic || null },
        lanes: {}, roles, tl: null, player: null, el: null, note: '' };
    }
    /* Speaker id → display label in the CURRENT language (roles are stored, labels are derived, so a
       language switch relabels everything). */
    const labelsOf = call => { const out = {}; Object.keys(call.roles || {}).forEach(id => {
      const r = call.roles[id], v = word('wb.role.', r, r); out[id] = v === r ? capFirst(r) : v; }); return out; };
    const audioHref = u => /^https?:\/\//i.test(u) ? u : api.replace(/\/$/, '') + (u.startsWith('/') ? u : '/' + u);
    async function ensureAudio(call) {
      if (call.blob || !call.hasAudio || !call.audioUrl) return;
      try {
        const r = await fetchFn(audioHref(call.audioUrl), { headers: headers() });
        if (!r.ok) throw new Error(String(r.status));
        call.blob = await r.blob();
      } catch (e) { call.hasAudio = false; call.note = t('wb.audiofail'); }
    }
    function mount(call) {
      const host = $('.wb-timelines'), idx = S.source ? S.source.calls.indexOf(call) : 0;
      const el = document.createElement('div'); el.className = 'wb-tl'; call.el = el; host.appendChild(el);
      if (call.note) { const n = document.createElement('p'); n.className = 'hint wb-tl-note'; n.textContent = call.note; el.appendChild(n); }
      const box = document.createElement('div'); el.appendChild(box);
      if (typeof CQ.Timeline === 'function') {
        try {
          call.tl = CQ.Timeline(box, { src: call.blob || null, duration: call.duration, segments: call.segments, speakerLabels: labelsOf(call),
            lanes: [], fetchInit: { headers: headers() }, filename: call.filename || undefined, onSeek: () => {},
            onSpanClick: (lane, span) => onSpanClick(call, lane, span), onSegment: i => onSegment(call, i) });
        } catch (e) { console.error('workbench: timeline failed', e); call.tl = null; }
      }
      if (!call.tl) {
        box.innerHTML = `<p class="hint">${esc(t('wb.notl'))}</p>`;
        if (call.blob) { const p = document.createElement('div'); box.appendChild(p); call.player = CQ.player(p, URL.createObjectURL(call.blob), { name: call.filename || 'audio', autoplay: false }); }
        box.insertAdjacentHTML('beforeend', transcriptHTML(call.segments, idx, labelsOf(call), call.transcript));
      }
      return el;
    }
    /* One mark per segment: two spans of the same lane can cite the same segment (a CONTRADICTED
       and a NOT_IN_KB claim, say), and the timeline keeps the LAST mark for an index — which would
       paint a misinformation hit grey. Merge instead: worst level wins, the lowest score wins, and
       every label shows in the tooltip. Scores only survive when every span had one (a scored lane),
       otherwise the level decides the colour. */
    const marksOf = spans => {
      const by = new Map();
      arr(spans).forEach(sp => {
        const level = lvl(sp.level), score = num(sp.score), title = sp.label || '';
        arr(sp.segments).forEach(i => {
          let m = by.get(i);
          if (!m) { m = { i, level, score, scored: score != null, titles: [] }; by.set(i, m); }
          else {
            m.level = worst(m.level, level);
            m.scored = m.scored && score != null;
            if (score != null && (m.score == null || score < m.score)) m.score = score;
          }
          if (title && m.titles.indexOf(title) < 0) m.titles.push(title);
        });
      });
      return Array.from(by.values()).map(m => {
        const out = { i: m.i, level: m.level, title: m.titles.join(' · ') };
        if (m.scored && m.score != null) out.score = m.score;
        return out;
      });
    };
    function lanesFor(kind, d) {
      if (!d) return [];
      if (kind === 'factcheck') return [{ id: 'factcheck', name: t('wb.lane.factcheck'), spans: arr(d.spans) }];
      if (kind === 'score') return arr(d.lanes).map((l, i) => ({
        id: 'score:' + (l.key || i), name: l.name || l.key || '',
        // `score: null` on purpose: it makes the timeline colour by `level`, i.e. by the
        // workspace's own three bands, instead of its own continuous ramp.
        spans: arr(l.spans).map(sp => Object.assign({}, sp, {
          level: sp.score == null ? lvl(sp.level) : scoreLevel(num(sp.score)), score: null })),
      }));
      if (kind === 'semantic') {
        const sp = d.spans || {}, modes = arr(d.modes), out = [];
        if (modes.includes('text') || arr(sp.text).length) out.push({ id: 'semantic:text', name: t('wb.lane.words'), spans: arr(sp.text) });
        if (arr(sp.voice).length || (modes.includes('voice') && d.voice_available !== false)) out.push({ id: 'semantic:voice', name: t('wb.lane.voice'), spans: arr(sp.voice) });
        return out;
      }
      return [];
    }
    /* A lane the timeline cannot draw is noise: in audio mode a lane whose spans all lack times
       would be an empty 18px row plus a legend entry. It is kept out of setLanes but still marked
       on the transcript (markSegments works for a lane the timeline does not show). Text mode draws
       no spans at all, so there the legend stays for every lane that has spans — it toggles the
       transcript marks (§16). */
    function laneDrawable(call, lane) {
      const spans = arr(lane && lane.spans);
      const audio = !!((call.tl && call.tl.audio) || call.blob);
      return audio ? spans.some(sp => num(sp.start) != null) : spans.length > 0;
    }
    /* Re-running an analyser replaces exactly that analyser's lanes; the others stay. */
    function setLanesFor(call, kind, lanes) {
      const mine = k => k === kind || k.startsWith(kind + ':');
      Object.keys(call.lanes).filter(mine).forEach(k => { delete call.lanes[k]; if (call.tl && call.tl.markSegments) { try { call.tl.markSegments(k, []); } catch {} } });
      lanes.forEach(l => { call.lanes[l.id] = l; });
      if (!call.tl) return;
      const rank = id => ['factcheck', 'score', 'semantic'].indexOf(id.split(':')[0]);
      const list = Object.values(call.lanes).sort((a, b) => rank(a.id) - rank(b.id));
      try { call.tl.setLanes(list.filter(l => laneDrawable(call, l))); } catch (e) { console.error('workbench: setLanes', e); }
      list.forEach(l => { if (call.tl.markSegments) { try { call.tl.markSegments(l.id, marksOf(l.spans)); } catch (e) { console.error('workbench: markSegments', e); } } });
    }
    const pushAllLanes = call => ['factcheck', 'score', 'semantic'].forEach(k => { if (call.results[k]) setLanesFor(call, k, lanesFor(k, call.results[k])); });
    function applySpeakerLabels(call, sm) {
      if (!sm) return;
      arr(sm.speakers).forEach(s => { if (s && s.speaker && (s.role === 'agent' || s.role === 'customer')) call.roles[s.speaker] = s.role; });
      if (call.tl && call.tl.setSpeakerLabels) { try { call.tl.setSpeakerLabels(labelsOf(call)); } catch {} }
    }
    function onSpanClick(call, lane, span) {
      const kind = String((lane && lane.id) || '').split(':')[0];
      if (!ORDER.includes(kind)) return;
      setTab(kind);
      const seg = arr(span && span.segments)[0]; if (seg == null) return;
      const el = paneEl(kind).querySelector(`[data-seg="${seg}"]`);
      if (el) { el.scrollIntoView({ block: 'center', behavior: 'smooth' }); el.classList.add('wb-flash'); setTimeout(() => el.classList.remove('wb-flash'), 1600); }
    }
    function onSegment(call, i) {
      if (call !== activeCall()) return;
      const pane = paneEl('semantic'); if (!pane) return;
      pane.querySelectorAll('.wb-seg.now').forEach(x => x.classList.remove('now'));
      const el = pane.querySelector(`.wb-seg[data-seg="${i}"]`); if (!el) return;
      el.classList.add('now');
      // The list scrolls inside its own box now — follow the playhead without moving the page.
      const box = el.parentElement;
      if (box && box.scrollHeight > box.clientHeight + 4) {
        const top = el.offsetTop - box.offsetTop, bottom = top + el.offsetHeight;
        if (top < box.scrollTop + 4) box.scrollTop = Math.max(0, top - 4);
        else if (bottom > box.scrollTop + box.clientHeight - 4) box.scrollTop = bottom - box.clientHeight + 4;
      }
    }
    function seekTo(el) {
      const ci = el.dataset.call != null && el.dataset.call !== '' ? +el.dataset.call : null;
      if (ci != null && S.source && ci !== S.source.active) { showCall(ci).then(() => doSeek(S.source && S.source.calls[ci], el)); return; }
      doSeek(activeCall(), el);
    }
    function doSeek(call, el) {
      if (!call) return;
      const start = el.dataset.start != null && el.dataset.start !== '' ? +el.dataset.start : null;
      const seg = el.dataset.seg != null && el.dataset.seg !== '' ? +el.dataset.seg : null;
      if (start == null && seg == null) return;
      if (call.tl) { try { if (start != null && call.tl.seek) call.tl.seek(start); if (seg != null && call.tl.highlightSegment) call.tl.highlightSegment(seg); } catch (e) { console.error('workbench: seek', e); } }
      else if (call.player && start != null) { try { call.player.audio.currentTime = start; } catch {} }
      if (call.el) call.el.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }

    /* ---- adopting a source ---- */
    function clearSource() {
      if (S.source) S.source.calls.forEach(c => { if (c.tl && c.tl.destroy) { try { c.tl.destroy(); } catch {} } if (c.player && c.player.audio) { try { c.player.audio.pause(); } catch {} } });
      S.source = null; S.summary = null;
      $('.wb-timelines').innerHTML = '';
      doneEl.querySelector('.wb-done-note').classList.add('hidden');
      buildSwitcher();
    }
    function collapse(html, note) {
      srcEl.classList.add('hidden'); doneEl.classList.remove('hidden');
      doneEl.querySelector('.wb-done-text').innerHTML = html;
      const n = doneEl.querySelector('.wb-done-note'); n.textContent = note || ''; n.classList.toggle('hidden', !note);
    }
    const metaOf = call => [num(call.duration) != null ? fmtT(call.duration) : '', langName(call.language), call.source === 'text' && call.segments.length ? tr('wb.turns', { n: call.segments.length }) : ''].filter(Boolean).join(' · ');
    const doneText = call => `<b>${esc(call.source === 'text' ? t('wb.src.text') : (call.filename || tr('wb.call', { n: 1 })))}</b> <span class="wb-meta">${esc(metaOf(call))}</span>`;
    const doneTextSummary = calls => { const total = calls.reduce((a, c) => a + (num(c.duration) || 0), 0);
      return `<b>${esc(tr('wb.src.calls', { n: calls.length }))}</b> <span class="wb-meta">${esc([total ? fmtT(total) : '', langName(calls[0] && calls[0].language)].filter(Boolean).join(' · '))}</span>`; };
    /* The queue is consumed by a successful upload: the Files live on in the calls (as their
       blobs), and a later "Run again" must re-send THOSE, keeping the calls and their results —
       not treat the old queue as a fresh source. */
    function adoptRecording(rec, file, text) {
      clearSource(); S.files = [];
      const call = callFromRow(rec, { blob: file || null, source: file ? 'audio' : 'text', text });
      S.source = { kind: 'recording', calls: [call], active: 0 };
      collapse(doneText(call)); mount(call); afterSourceChange();
    }
    function adoptSummary(sum, files) {
      clearSource(); S.files = [];
      const calls = arr(sum.calls).map((c, i) => callFromRow(c, { blob: files && files[i] ? files[i] : null, source: 'audio' }));
      S.source = { kind: 'summary', calls, active: 0, id: sum.id }; S.summary = sum;
      collapse(doneTextSummary(calls)); buildSwitcher();
      if (calls[0]) mount(calls[0]);
      afterSourceChange(); setTab('summarise');
    }
    function afterSourceChange() {
      const call = activeCall();
      if (call) { applySpeakerLabels(call, call.results.semantic); pushAllLanes(call); }
      renderAll(); sync();
      const first = ORDER.find(k => k === 'summarise' ? !!S.summary : !!(call && call.results[k]));
      if (first) setTab(first);
    }
    function buildSwitcher() {
      const sw = $('.wb-switcher'), calls = S.source ? S.source.calls : [];
      sw.classList.toggle('hidden', calls.length < 2);
      sw.innerHTML = calls.length < 2 ? '' : calls.map((c, i) => `<button type="button" class="subtab${i === S.source.active ? ' active' : ''}" role="tab" aria-selected="${i === S.source.active}" data-call="${i}">${esc(c.filename ? `${i + 1}. ${c.filename}` : tr('wb.call', { n: i + 1 }))}</button>`).join('');
      sw.querySelectorAll('.subtab').forEach(b => b.addEventListener('click', () => showCall(+b.dataset.call)));
    }
    async function showCall(i) {
      const src = S.source; if (!src || !src.calls[i]) return;
      src.active = i;
      $$('.wb-switcher .subtab').forEach(b => { const on = +b.dataset.call === i; b.classList.toggle('active', on); b.setAttribute('aria-selected', String(on)); });
      src.calls.forEach((c, j) => { if (j !== i && c.tl && c.tl.pause) { try { c.tl.pause(); } catch {} } });
      const call = src.calls[i];
      if (!call.el) { await ensureAudio(call); if (S.source !== src) return; mount(call); applySpeakerLabels(call, call.results.semantic); pushAllLanes(call); }
      src.calls.forEach((c, j) => { if (c.el) c.el.hidden = j !== i; });
      renderAll(); sync();
    }

    /* ---- analysers ---- */
    function renderPane(kind) {
      const pane = paneEl(kind); if (!pane) return;
      const out = pane.querySelector('.wb-result'), call = activeCall(), idx = S.source ? S.source.active : 0;
      let html = '';
      if (kind === 'summarise') html = S.summary ? renderSummary(S.summary, { calls: S.source ? S.source.calls.map(c => Object.assign({}, c, { speakerLabels: labelsOf(c) })) : [], active: idx }) : '';
      else if (call && call.results[kind]) {
        const ctx = { segments: call.segments, speakerLabels: labelsOf(call), callIndex: idx,
                      jobId: call.id, editable: kind === 'score' && opts.canEditScores !== false };
        html = kind === 'factcheck' ? renderFactcheck(call.results[kind], ctx) : kind === 'score' ? renderScorecard(call.results[kind], ctx) : renderSemantic(call.results[kind], ctx);
      }
      out.innerHTML = html; CQ.mountTips(out);
      if (kind === 'score') wireScoreEditing(out);
    }
    /* ---- manual score edits -------------------------------------------------------------
       A reviewer disagreeing with the model is the normal case, not an exception, so the
       numbers are editable in place next to the evidence they were judged on. The browser
       sends only the changed dimensions; the server recomputes the weighted total and keeps
       the model's own scorecard as revision 1, so an override never erases what it overrode. */
    function wireScoreEditing(out) {
      const box = out.querySelector('.wb-res[data-job]'); if (!box) return;
      const jobId = box.dataset.job; if (!jobId) return;
      const msg = box.querySelector('.wb-sc-msg');
      const save = box.querySelector('.wb-sc-save');
      const hist = box.querySelector('.wb-sc-hist');
      const histBox = box.querySelector('.wb-sc-histbox');

      if (save) save.addEventListener('click', async () => {
        const scores = [];
        box.querySelectorAll('.wb-sc-in').forEach(inp => {
          const raw = inp.value.trim();
          if (raw === '') return;
          const v = Math.max(0, Math.min(100, Math.round(Number(raw))));
          if (Number.isFinite(v)) scores.push({ key: inp.dataset.key, score: v });
        });
        if (!scores.length) return;
        const note = (box.querySelector('.wb-sc-note') || {}).value || '';
        save.disabled = true; save.innerHTML = '<span class="spinner"></span>';
        try {
          const r = await fetchFn(`${api}/recordings/${jobId}/score`, {
            method: 'PATCH', headers: Object.assign({ 'Content-Type': 'application/json' }, headers()),
            body: JSON.stringify({ scores, note }) });
          const d = await CQ.readResp(r);
          const call = activeCall();
          if (call) call.results.score = d;      // so a re-render shows the saved numbers
          CQ.toast(t('wb.sc.saved'), 'ok');
          renderPane('score');
          // The lanes carry the score colour, so an edited number has to repaint them too.
          if (call) setLanesFor(call, 'score', lanesFor('score', d));
        } catch (e) {
          msg.className = 'msg err wb-sc-msg'; msg.textContent = e.message;
          save.disabled = false; save.textContent = t('wb.sc.save');
        }
      });

      if (hist) hist.addEventListener('click', async () => {
        if (!histBox.classList.contains('hidden')) {
          histBox.classList.add('hidden'); hist.textContent = t('wb.sc.history'); return;
        }
        hist.disabled = true;
        try {
          const r = await fetchFn(`${api}/recordings/${jobId}/score/revisions`, { headers: headers() });
          const d = await CQ.readResp(r);
          histBox.innerHTML = renderHistory(arr(d.revisions));
          histBox.classList.remove('hidden'); hist.textContent = t('wb.sc.hide');
        } catch (e) {
          msg.className = 'msg err wb-sc-msg'; msg.textContent = e.message;
        } finally { hist.disabled = false; }
      });
    }

    function renderHistory(revs) {
      if (revs.length < 2) return `<div class="hint">${esc(t('wb.sc.nohistory'))}</div>`;
      // Oldest first: the model's own numbers, then what each person changed them to.
      return revs.map((r, n) => {
        const sc = r.scoring || {};
        const who = r.revision === 1 ? t('wb.sc.themodel') : (r.edited_by || '—');
        const prev = n > 0 ? (revs[n - 1].scoring || {}).dimensions || [] : null;
        const prevBy = {}; arr(prev).forEach(d => { prevBy[d.key] = d.score; });
        const dims = arr(sc.dimensions).map(d => {
          const was = prev && prevBy[d.key] != null && prevBy[d.key] !== d.score
            ? ` <span class="muted">(${esc(t('wb.sc.was'))} ${prevBy[d.key]})</span>` : '';
          return `<div class="sc-meta">${esc(d.name)}: <b>${d.score == null ? '—' : d.score}</b>${was}</div>`;
        }).join('');
        return `<div class="wb-rev">
          <div class="wb-inline" style="justify-content:space-between">
            <b>${esc(t('wb.sc.rev'))} ${r.revision}${r.revision === 1 ? ' · ' + esc(t('wb.sc.original')) : ''}</b>
            <span class="sc-meta">${esc(who)} · ${esc(new Date(r.created_at).toLocaleString())}</span>
          </div>
          <div class="sc-meta" style="margin:4px 0"><b>${sc.weighted_total == null ? '—' : sc.weighted_total}</b> / ${sc.max_total || 100}</div>
          ${dims}
          ${r.note ? `<div class="hint" style="margin-top:4px">${esc(r.note)}</div>` : ''}
        </div>`;
      }).join('');
    }

    const renderAll = () => ORDER.forEach(renderPane);
    const busyBtn = b => { b.disabled = true; b.innerHTML = '<span class="spinner"></span>' + esc(t('wb.running')); };
    async function run(kind) {
      if (kind === 'summarise') return runSummarise();
      const call = activeCall(), pane = paneEl(kind), err = pane.querySelector('.wb-err');
      errBox(err, '');
      if (!call) return errBox(err, t('wb.needsource'));
      let body = null;
      if (kind === 'semantic') {
        const modes = []; if ($('.wb-ck-text').checked) modes.push('text'); if (vck.checked && !vck.disabled) modes.push('voice');
        if (!modes.length) return errBox(err, t('wb.sem.pickone'));
        body = { modes };
      }
      const btn = pane.querySelector('.wb-run'); S.running[kind] = true; busyBtn(btn);
      try {
        const init = { method: 'POST', headers: { ...headers() } };
        if (body) { init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(body); }
        const r = await fetchFn(`${api}/recordings/${encodeURIComponent(call.id)}/${kind}`, init);
        const data = await CQ.readResp(r);
        call.results[kind] = data;
        if (kind === 'semantic') applySpeakerLabels(call, data);
        setLanesFor(call, kind, lanesFor(kind, data));
        renderAll();
        CQ.toast(t(kind === 'score' ? 'pg.done' : kind === 'factcheck' ? 'wb.fc.done' : 'wb.sem.done'), 'ok');
      } catch (e) { errBox(err, e.message); CQ.toast(e.message, 'err'); }
      finally { S.running[kind] = false; sync(); }
    }
    async function runSummarise() {
      const pane = paneEl('summarise'), err = pane.querySelector('.wb-err');
      errBox(err, '');
      if (S.xhr) return;
      if (S.files.length) { if (S.mode !== 'audio') setMode('audio'); return uploadSummary(S.files.slice(), err); }
      const calls = S.source ? S.source.calls : [];
      if (!calls.length) return errBox(err, t('wb.needsource'));
      const btn = pane.querySelector('.wb-run'); S.running.summarise = true; busyBtn(btn);
      try {
        const files = [];
        for (const c of calls) {
          await ensureAudio(c);
          if (!c.blob) throw new Error(t('wb.sum.needaudio'));
          files.push(c.blob instanceof File ? c.blob : new File([c.blob], c.filename || 'call.bin', { type: c.blob.type || '' }));
        }
        uploadSummary(files, err, true);        // keeps the current calls + their results; only the summary is replaced (onEnd clears `running`)
      } catch (e) { S.running.summarise = false; errBox(err, e.message); sync(); }
    }

    /* ---- semantic guidance (the tenant's sentiment guidance, editable in place) ---- */
    async function loadGuide() {
      const cfg = opts.sentimentConfig, ta = $('.wb-guide-text'), msg = $('.wb-guide-msg');
      try {
        const d = (await cfg.get()) || {};
        ta.value = d.guidance || '';
        const ro = typeof cfg.put !== 'function' || d.readonly === true || cfg.readonly === true;
        ta.disabled = ro; $('.wb-guide-save').classList.toggle('hidden', ro); $('.wb-guide-ro').classList.toggle('hidden', !ro);
        S.guideLoaded = true; if (CQ.autogrow) CQ.autogrow(ta);
      } catch (e) { errBox(msg, e.message); }
    }
    async function saveGuide() {
      const btn = $('.wb-guide-save'), ta = $('.wb-guide-text'), msg = $('.wb-guide-msg');
      errBox(msg, ''); btn.disabled = true; btn.innerHTML = '<span class="spinner"></span>';
      try { await opts.sentimentConfig.put({ guidance: ta.value }); CQ.toast(t('sn.saved'), 'ok'); }
      catch (e) { errBox(msg, e.message); }
      finally { btn.disabled = false; btn.textContent = t('sn.save'); }
    }

    /* ---- public ---- */
    function reset() {
      if (S.xhr) S.xhr.abort();
      clearSource(); S.files = [];
      srcEl.classList.remove('hidden'); doneEl.classList.add('hidden'); srcEl.insertBefore(progEl, srcErr);
      errBox(srcErr, ''); ORDER.forEach(k => errBox(paneEl(k).querySelector('.wb-err'), ''));
      renderAll(); sync();
    }
    async function open(rec) {
      try {
        let row = rec || {};
        if (!Array.isArray(row.segments) || row.transcript == null) {
          if (!row.id) throw new Error(t('err.badresp'));
          row = await CQ.readResp(await fetchFn(`${api}/recordings/${encodeURIComponent(row.id)}`, { headers: headers() }));
        }
        reset();
        const call = callFromRow(row);
        S.source = { kind: 'recording', calls: [call], active: 0 };
        collapse(doneText(call));
        if (call.hasAudio) await ensureAudio(call);
        if (S.source && S.source.calls[0] !== call) return;      // superseded while the audio was loading
        if (call.source === 'audio' && !call.blob) { call.hasAudio = false; call.note = call.note || t('wb.noaudio'); }
        mount(call); afterSourceChange();
        if (call.note) collapse(doneText(call), call.note);
      } catch (e) { reset(); errBox(srcErr, e.message); CQ.toast(e.message, 'err'); }
    }
    async function openSummary(sum) {
      try {
        let row = sum || {};
        if (!Array.isArray(row.calls) || !row.summary) {
          if (!row.id) throw new Error(t('err.badresp'));
          row = await CQ.readResp(await fetchFn(`${api}/summaries/${encodeURIComponent(row.id)}`, { headers: headers() }));
        }
        reset();
        const calls = arr(row.calls).map(c => callFromRow(c, { source: 'audio' }));
        S.source = { kind: 'summary', calls, active: 0, id: row.id }; S.summary = row;
        collapse(doneTextSummary(calls)); buildSwitcher();
        await showCall(0);
        const call = calls[0];
        if (call && call.source === 'audio' && !call.blob && !call.note) { collapse(doneTextSummary(calls), t('wb.noaudio')); }
        afterSourceChange(); setTab('summarise');
      } catch (e) { reset(); errBox(srcErr, e.message); CQ.toast(e.message, 'err'); }
    }
    function destroy() {
      if (S.xhr) S.xhr.abort();
      clearSource();
      document.removeEventListener('cq:lang', onLang); window.removeEventListener('pagehide', onHide);
      root.remove();
    }

    CQ.applyI18n(root); CQ.mountTips(root);
    setMode('audio'); setTab(S.tab); renderAll(); sync();
    return { el: root, open, openSummary, reset, setTab, destroy };
  }

  /* §13.2 names the two NEW v2 renderers CQ.semanticHTML / CQ.summaryHTML — publish them under
     those names too. CQ.factcheckHTML / CQ.scorecardHTML stay brand.js's legacy renderers (§13.3,
     kb-admin's playground); the v2 pair for those lives on CQ.Workbench.* only. */
  if (typeof CQ.semanticHTML !== 'function') CQ.semanticHTML = renderSemantic;
  if (typeof CQ.summaryHTML !== 'function') CQ.summaryHTML = renderSummary;
  Workbench.renderFactcheck = renderFactcheck;
  Workbench.renderScorecard = renderScorecard;
  Workbench.renderSemantic = renderSemantic;
  Workbench.renderSummary = renderSummary;
  CQ.Workbench = Workbench;
})();
