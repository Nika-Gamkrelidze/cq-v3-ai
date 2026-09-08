/* The audio editor (ed.*) — waveform, layers, selection, export.

   Filed under features/ rather than pages/ although `editor.html` is the only page that
   mounts it today: the editor is `audio-edit-core.js` + `audio-editor.js`, a self-contained
   pair that the page merely hosts, and six of these keys are read from inside that module
   rather than from the page. `ed.title` is already used by `tenant.html` as well, so the
   prefix is not single-page even now.

   Nothing here encodes editor behaviour, but two labels are easy to mistranslate into a
   different feature: `ed.selection` with an empty range means THE WHOLE TIMELINE (see
   docs/MIGRATION.md), and `ed.dl.convert` names the two-call convert flow, not the WAV
   export beside it. */

import type { Dict } from '../index';

export const en: Dict = {
  'ed.playsel': 'Play selection',
  'ed.markin': 'Mark in  [',
  'ed.markout': 'Mark out  ]',
  'ed.markhint': 'To select by ear: press Play, then Mark in when the part begins and Mark out when it ends — the keys are [ and ] (or i and o). Both work while the audio is playing, so you never have to guess from the picture. Esc clears the selection.',
  'ed.at': 'at',
  'ed.flatten': 'Flatten layers',
  'ed.mixname': 'Mix',
  'ed.added': 'Added {n} layer(s).',
  'ed.startsat': 'starts at',
  'ed.mute': 'Mute',
  'ed.unmute': 'Unmute',
  'ed.solo': 'Solo',
  'ed.unsolo': 'Unsolo',
  'ed.removelayer': 'Remove layer',

  'ed.layers.hint': 'Each file you open becomes a layer on one timeline. Set where a layer starts to place a jingle or lay a bed of room tone under a call; edits apply to the selected layer, and Play always mixes what you can hear.',

  'ed.nav': 'Audio editor',
  'ed.eyebrow': 'Audio tools',
  'ed.title': 'Audio editor',
  'ed.lead': 'Trim, cut, level and convert a recording. The audio is edited in your browser — nothing is uploaded unless you export to a format we encode on the server.',
  'ed.source': 'Recording',
  'ed.drop': 'Drop an audio or video file here',
  'ed.choose': 'Choose a file',
  'ed.loading': 'Reading the file…',
  'ed.loaded': 'Loaded “{name}”.',

  'ed.err.big': 'That file is over {mb} MB. Editing holds the whole recording in memory, so open a shorter piece — or convert it first and edit the result.',
  'ed.err.decode': 'Your browser could not decode this file. Telephony formats (gsm, alaw, amr, sln) are not playable in a browser — convert one to WAV on the Convert page first, then edit it here.',

  'ed.canvas.aria': 'Waveform. Drag to select part of the recording.',

  'ed.empty': 'No recording open yet. Choose a file above to see its waveform.',
  'ed.channel': 'Channel',
  'ed.selection': 'Selection',
  'ed.length': 'Length',
  'ed.play': 'Play',
  'ed.pause': 'Pause',
  'ed.stop': 'Stop',
  'ed.fit': 'Fit',
  'ed.zoomsel': 'Zoom to selection',
  'ed.selectall': 'Clear selection',
  'ed.undo': 'Undo',
  'ed.redo': 'Redo',
  'ed.selhint': 'Drag across the waveform to select a range. With nothing selected, every action below applies to the whole recording. Ctrl and the scroll wheel zoom; space plays.',

  'ed.g.edit': 'Edit',

  'ed.cut': 'Cut out selection',
  'ed.trim': 'Keep only selection',
  'ed.silence': 'Silence selection',
  'ed.reverse': 'Reverse',
  'ed.insert': 'Insert silence (seconds)',
  'ed.insert.go': 'Insert',

  'ed.g.level': 'Level',

  'ed.normalize': 'Normalise',
  'ed.fadein': 'Fade in',
  'ed.fadeout': 'Fade out',
  'ed.invert': 'Invert phase',

  'ed.level.hint': 'Normalise lifts the loudest point to just under maximum. Gain is clamped, so a boost can clip but never wrap into noise.',

  'ed.g.channels': 'Channels',

  'ed.mono': 'Mix to mono',
  'ed.stereo': 'Make stereo',
  'ed.swap': 'Swap left/right',

  'ed.ch.keep': 'Keep only ch {n}',
  'ed.ch.mute': 'Mute ch {n}',

  'ed.channels.hint': 'Call recordings often carry the agent on one channel and the customer on the other, so keeping one side is a normal thing to want.',

  'ed.g.export': 'Export',

  'ed.format': 'Format',

  'ed.export.hint': 'WAV is written here in your browser and costs nothing. Any other format is encoded by the server, which is what the daily allowance counts.',

  'ed.dl.wav': 'Download WAV',
  'ed.dl.convert': 'Convert & download',

  'ed.converting': 'Converting…',
  'ed.saved': 'Saved.',

  'ed.fmt.unavailable': 'Conversion unavailable',

  'ed.err.convert': 'The conversion failed.',
};

export const ka: Dict = {
  'ed.playsel': 'მონიშნულის დაკვრა',
  'ed.markin': 'საწყისი წერტილი  [',
  'ed.markout': 'ბოლო წერტილი  ]',
  'ed.markhint': 'ყურით მონიშვნისთვის: დააჭირეთ დაკვრას, შემდეგ „საწყისი წერტილი“ იქ, სადაც მონაკვეთი იწყება, და „ბოლო წერტილი“ იქ, სადაც მთავრდება — კლავიშებია [ და ] (ან i და o). ორივე მუშაობს დაკვრის დროს, ამიტომ სურათზე გამოცნობა აღარ გჭირდებათ. Esc ხსნის მონიშვნას.',
  'ed.at': 'პოზიცია',
  'ed.flatten': 'ფენების გაერთიანება',
  'ed.mixname': 'მიქსი',
  'ed.added': 'დაემატა {n} ფენა.',
  'ed.startsat': 'იწყება',
  'ed.mute': 'დადუმება',
  'ed.unmute': 'ხმის დაბრუნება',
  'ed.solo': 'მხოლოდ ეს',
  'ed.unsolo': 'ყველა',
  'ed.removelayer': 'ფენის წაშლა',

  'ed.layers.hint': 'ყოველი გახსნილი ფაილი ხდება ცალკე ფენა ერთ ტაიმლაინზე. მიუთითეთ, სად იწყება ფენა, რომ განათავსოთ ჯინგლი ან ზარის ქვეშ ფონური ხმა; რედაქტირება ვრცელდება არჩეულ ფენაზე, დაკვრა კი ყოველთვის ურევს იმას, რასაც ისმენთ.',

  'ed.nav': 'აუდიო რედაქტორი',
  'ed.eyebrow': 'აუდიო ხელსაწყოები',
  'ed.title': 'აუდიო რედაქტორი',
  'ed.lead': 'შეასწორეთ, ამოჭერით, დაარეგულირეთ ხმა და გადაიყვანეთ ჩანაწერი. აუდიო მუშავდება თქვენს ბრაუზერში — არაფერი იტვირთება სერვერზე, სანამ არ აირჩევთ ფორმატს, რომელსაც სერვერი ამუშავებს.',
  'ed.source': 'ჩანაწერი',
  'ed.drop': 'ჩააგდეთ აუდიო ან ვიდეო ფაილი აქ',
  'ed.choose': 'ფაილის არჩევა',
  'ed.loading': 'ფაილი იკითხება…',
  'ed.loaded': 'ჩაიტვირთა „{name}“.',

  'ed.err.big': 'ფაილი {mb} მბ-ზე დიდია. რედაქტირებისას მთელი ჩანაწერი მეხსიერებაშია, ამიტომ გახსენით უფრო მოკლე ნაწილი — ან ჯერ გადაიყვანეთ და შემდეგ დაარედაქტირეთ.',
  'ed.err.decode': 'ბრაუზერმა ეს ფაილი ვერ გაშიფრა. სატელეფონო ფორმატები (gsm, alaw, amr, sln) ბრაუზერში არ იკითხება — ჯერ გადაიყვანეთ WAV-ში კონვერტაციის გვერდზე, შემდეგ დაარედაქტირეთ აქ.',

  'ed.canvas.aria': 'ტალღის ფორმა. ჩანაწერის ნაწილის ასარჩევად გადაატარეთ.',

  'ed.empty': 'ჩანაწერი ჯერ არ არის გახსნილი. აირჩიეთ ფაილი ზემოთ, რომ ნახოთ ტალღის ფორმა.',
  'ed.channel': 'არხი',
  'ed.selection': 'მონიშნული',
  'ed.length': 'ხანგრძლივობა',
  'ed.play': 'დაკვრა',
  'ed.pause': 'პაუზა',
  'ed.stop': 'გაჩერება',
  'ed.fit': 'მთლიანად',
  'ed.zoomsel': 'მონიშნულზე მიახლოება',
  'ed.selectall': 'მონიშვნის მოხსნა',
  'ed.undo': 'დაბრუნება',
  'ed.redo': 'გამეორება',
  'ed.selhint': 'ტალღის ფორმაზე გადატარებით მონიშნეთ მონაკვეთი. თუ არაფერია მონიშნული, ქვემოთ მოცემული მოქმედებები მთელ ჩანაწერზე გავრცელდება. Ctrl და გორგოლაჭი აახლოებს; ინტერვალი უშვებს დაკვრას.',

  'ed.g.edit': 'რედაქტირება',

  'ed.cut': 'მონიშნულის ამოჭრა',
  'ed.trim': 'მხოლოდ მონიშნულის დატოვება',
  'ed.silence': 'მონიშნულის დადუმება',
  'ed.reverse': 'უკუღმა',
  'ed.insert': 'სიჩუმის ჩამატება (წამი)',
  'ed.insert.go': 'ჩამატება',

  'ed.g.level': 'ხმის დონე',

  'ed.normalize': 'ნორმალიზება',
  'ed.fadein': 'თანდათან გაძლიერება',
  'ed.fadeout': 'თანდათან ჩაქრობა',
  'ed.invert': 'ფაზის ინვერსია',

  'ed.level.hint': 'ნორმალიზება ყველაზე ხმამაღალ წერტილს მაქსიმუმთან ახლოს წევს. ხმის მომატება შეზღუდულია, ამიტომ შესაძლოა მოიჭრას, მაგრამ ხმაურად არ გადაიქცევა.',

  'ed.g.channels': 'არხები',

  'ed.mono': 'მონოში გადაყვანა',
  'ed.stereo': 'სტერეოდ ქცევა',
  'ed.swap': 'მარცხენა/მარჯვენის შენაცვლება',

  'ed.ch.keep': 'მხოლოდ {n} არხი',
  'ed.ch.mute': '{n} არხის დადუმება',

  'ed.channels.hint': 'ზარის ჩანაწერებში ხშირად ერთ არხზე ოპერატორია, მეორეზე — მომხმარებელი, ამიტომ ერთი მხარის დატოვება ჩვეულებრივი საჭიროებაა.',

  'ed.g.export': 'ექსპორტი',

  'ed.format': 'ფორმატი',

  'ed.export.hint': 'WAV იქმნება თქვენს ბრაუზერში და არაფერი ღირს. სხვა ფორმატებს სერვერი ამუშავებს — სწორედ ეს ითვლება დღიურ ლიმიტში.',

  'ed.dl.wav': 'WAV-ის ჩამოტვირთვა',
  'ed.dl.convert': 'გადაყვანა და ჩამოტვირთვა',

  'ed.converting': 'მიმდინარეობს გადაყვანა…',
  'ed.saved': 'შენახულია.',

  'ed.fmt.unavailable': 'გადაყვანა მიუწვდომელია',

  'ed.err.convert': 'გადაყვანა ვერ შესრულდა.',
};

export const ru: Dict = {
  'ed.playsel': 'Воспроизвести выделение',
  'ed.markin': 'Начало  [',
  'ed.markout': 'Конец  ]',
  'ed.markhint': 'Чтобы выделить на слух: нажмите «Воспроизвести», затем «Начало» там, где фрагмент начинается, и «Конец» там, где заканчивается — клавиши [ и ] (или i и o). Обе работают во время воспроизведения, так что угадывать по картинке не нужно. Esc снимает выделение.',
  'ed.at': 'позиция',
  'ed.flatten': 'Свести слои',
  'ed.mixname': 'Микс',
  'ed.added': 'Добавлено слоёв: {n}.',
  'ed.startsat': 'начало',
  'ed.mute': 'Заглушить',
  'ed.unmute': 'Вернуть звук',
  'ed.solo': 'Только этот',
  'ed.unsolo': 'Все',
  'ed.removelayer': 'Удалить слой',

  'ed.layers.hint': 'Каждый открытый файл становится слоем на одной шкале. Укажите, где слой начинается, чтобы поставить джингл или подложить фон под звонок; правки применяются к выбранному слою, а воспроизведение всегда сводит то, что вы слышите.',

  'ed.nav': 'Аудиоредактор',
  'ed.eyebrow': 'Аудиоинструменты',
  'ed.title': 'Аудиоредактор',
  'ed.lead': 'Обрежьте, вырежьте, выровняйте громкость и сконвертируйте запись. Аудио обрабатывается в вашем браузере — на сервер ничего не загружается, пока вы не выберете формат, который кодирует сервер.',
  'ed.source': 'Запись',
  'ed.drop': 'Перетащите сюда аудио- или видеофайл',
  'ed.choose': 'Выбрать файл',
  'ed.loading': 'Читаем файл…',
  'ed.loaded': 'Загружено «{name}».',

  'ed.err.big': 'Файл больше {mb} МБ. При редактировании вся запись хранится в памяти — откройте фрагмент покороче или сначала сконвертируйте её.',
  'ed.err.decode': 'Браузер не смог декодировать этот файл. Телефонные форматы (gsm, alaw, amr, sln) в браузере не читаются — сконвертируйте в WAV на странице конвертации, затем редактируйте здесь.',

  'ed.canvas.aria': 'Форма волны. Проведите, чтобы выделить фрагмент записи.',

  'ed.empty': 'Запись ещё не открыта. Выберите файл выше, чтобы увидеть форму волны.',
  'ed.channel': 'Канал',
  'ed.selection': 'Выделение',
  'ed.length': 'Длительность',
  'ed.play': 'Воспроизвести',
  'ed.pause': 'Пауза',
  'ed.stop': 'Стоп',
  'ed.fit': 'Целиком',
  'ed.zoomsel': 'К выделению',
  'ed.selectall': 'Снять выделение',
  'ed.undo': 'Отменить',
  'ed.redo': 'Повторить',
  'ed.selhint': 'Проведите по форме волны, чтобы выделить фрагмент. Если ничего не выделено, действия ниже применяются ко всей записи. Ctrl и колесо — масштаб, пробел — воспроизведение.',

  'ed.g.edit': 'Правка',

  'ed.cut': 'Вырезать выделение',
  'ed.trim': 'Оставить только выделение',
  'ed.silence': 'Заглушить выделение',
  'ed.reverse': 'Реверс',
  'ed.insert': 'Вставить тишину (секунды)',
  'ed.insert.go': 'Вставить',

  'ed.g.level': 'Громкость',

  'ed.normalize': 'Нормализовать',
  'ed.fadein': 'Нарастание',
  'ed.fadeout': 'Затухание',
  'ed.invert': 'Инверсия фазы',

  'ed.level.hint': 'Нормализация поднимает самый громкий участок почти до максимума. Усиление ограничено: возможен клиппинг, но не превращение в шум.',

  'ed.g.channels': 'Каналы',

  'ed.mono': 'Свести в моно',
  'ed.stereo': 'Сделать стерео',
  'ed.swap': 'Поменять левый/правый',

  'ed.ch.keep': 'Оставить только канал {n}',
  'ed.ch.mute': 'Заглушить канал {n}',

  'ed.channels.hint': 'В записях звонков оператор часто на одном канале, а клиент на другом, поэтому оставить одну сторону — обычная задача.',

  'ed.g.export': 'Экспорт',

  'ed.format': 'Формат',

  'ed.export.hint': 'WAV создаётся у вас в браузере и ничего не стоит. Остальные форматы кодирует сервер — именно они учитываются в дневном лимите.',

  'ed.dl.wav': 'Скачать WAV',
  'ed.dl.convert': 'Сконвертировать и скачать',

  'ed.converting': 'Конвертируем…',
  'ed.saved': 'Сохранено.',

  'ed.fmt.unavailable': 'Конвертация недоступна',

  'ed.err.convert': 'Не удалось сконвертировать.',
};
