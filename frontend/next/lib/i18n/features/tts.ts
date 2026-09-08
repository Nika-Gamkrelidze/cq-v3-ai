/* Text to speech (tts.*) and the anonymous allowance banner (quota.*).

   quota.* sits with TTS because the banner counts speech clips alongside transcriptions and
   conversions; it is one sentence assembled from these pieces, not three separate ones. */

import type { Dict } from '../index';

export const en: Dict = {
  'tts.heading': 'Generate speech from text',
  'tts.text_ph': 'Type something to say… (English, Russian or Georgian)',
  'tts.needtext': 'Enter some text.',
  'tts.pickvoice': 'Pick a specific voice to preview.',
  'tts.previewtitle': 'Preview voice (free sample)',
  'tts.previewfail': 'Could not play the preview. Try again.',

  'quota.using': 'You\'re using CommuniQ anonymously —',
  'quota.analyses': 'transcriptions',
  'quota.clips': 'speech clips',
  'quota.left': 'left today.',
  'quota.more': 'for a knowledge base and higher limits.',
  'quota.disabled': 'Anonymous access is disabled.',
  'quota.conversions': 'file conversions',

  // The rest of the TTS panel: voice picking, the model/format controls and the
  // clip that comes back. Shared by the public page and the account page.
  'tts.adv': 'Advanced voice settings',
  'tts.model': 'Model',

  'tts.model.auto': 'Auto — best model for the selected language',
  'tts.model.hint': 'Multilingual v2 is the reliable all-rounder; Eleven v3 is the most expressive and understands stage directions in brackets; Flash is the fastest and cheapest.',

  'tts.custom': 'Customise voice settings',

  'tts.custom.hint': 'Off means the voice speaks with the settings its creator chose. Turn on to adjust them for this clip.',

  'tts.stability': 'Stability',

  'tts.stability.expressive': 'More expressive',
  'tts.stability.stable': 'More stable',
  'tts.stability.hint': 'Low values give a wider emotional range but can wander; high values are consistent but flatter.',

  'tts.preset.creative': 'Creative',
  'tts.preset.natural': 'Natural',
  'tts.preset.robust': 'Robust',
  'tts.preset.hint': 'Creative is the most emotional but can hallucinate; Natural stays closest to the original voice; Robust is very consistent but ignores most stage directions.',

  'tts.similarity': 'Similarity',

  'tts.similarity.hint': 'How closely to stick to the original voice. Very high values can reproduce artefacts from the source recording.',

  'tts.style': 'Style exaggeration',

  'tts.style.hint': 'Amplifies the speaker’s style. Anything above zero adds latency; keep it at zero for narration.',

  'tts.speakerboost': 'Speaker boost',

  'tts.speakerboost.hint': 'Brings the result closer to the original speaker at a small latency cost.',

  'tts.speed': 'Speed',
  'tts.forcelang': 'Force the selected language',

  'tts.forcelang.hint': 'Tells the model which language the text is in, so a mixed or ambiguous text is not read in the wrong accent. Only Flash and Turbo models honour it.',

  'tts.v3tip': 'This model understands stage directions in square brackets, e.g. [whispers], [laughs], [excited], [sighs]. Ellipses add pauses; CAPITALS add emphasis.',
  'tts.reset': 'Reset to defaults',
  'tts.unsupported': 'Not available for this model',

  'tts.models.loadfail': 'Could not load the model list; the default model will be used.',
};

export const ka: Dict = {
  'tts.heading': 'ტექსტიდან მეტყველების გენერაცია',
  'tts.text_ph': 'აკრიფეთ სათქმელი… (ინგლისურად, რუსულად ან ქართულად)',
  'tts.needtext': 'შეიყვანეთ ტექსტი.',
  'tts.pickvoice': 'მოსასმენად აირჩიეთ კონკრეტული ხმა.',
  'tts.previewtitle': 'ხმის მოსმენა (უფასო ნიმუში)',
  'tts.previewfail': 'ნიმუშის დაკვრა ვერ მოხერხდა. სცადეთ ხელახლა.',

  'quota.using': 'თქვენ იყენებთ CommuniQ-ს ანონიმურად —',
  'quota.analyses': 'ტრანსკრიფცია',
  'quota.clips': 'აუდიო კლიპი',
  'quota.left': 'დარჩა დღეს.',
  'quota.more': 'ცოდნის ბაზისა და გაზრდილი ლიმიტებისთვის.',
  'quota.disabled': 'ანონიმური წვდომა გათიშულია.',
  'quota.conversions': 'ფაილის კონვერტაცია',

  // The rest of the TTS panel: voice picking, the model/format controls and the
  // clip that comes back. Shared by the public page and the account page.
  'tts.adv': 'ხმის დამატებითი პარამეტრები',
  'tts.model': 'მოდელი',

  'tts.model.auto': 'ავტომატური — საუკეთესო მოდელი არჩეული ენისთვის',
  'tts.model.hint': 'Multilingual v2 საიმედო უნივერსალური მოდელია; Eleven v3 ყველაზე ექსპრესიულია და ესმის სცენური მითითებები კვადრატულ ფრჩხილებში; Flash ყველაზე სწრაფი და იაფია.',

  'tts.custom': 'ხმის პარამეტრების მორგება',

  'tts.custom.hint': 'გამორთულისას ხმა ლაპარაკობს იმ პარამეტრებით, რომლებიც მისმა შემქმნელმა აირჩია. ჩართეთ, რომ ამ ჩანაწერისთვის შეცვალოთ.',

  'tts.stability': 'სტაბილურობა',

  'tts.stability.expressive': 'უფრო ექსპრესიული',
  'tts.stability.stable': 'უფრო სტაბილური',
  'tts.stability.hint': 'დაბალი მნიშვნელობა უფრო ფართო ემოციურ დიაპაზონს იძლევა, მაგრამ შეიძლება „გადაუხვიოს“; მაღალი — თანმიმდევრულია, მაგრამ უფრო მონოტონური.',

  'tts.preset.creative': 'კრეატიული',
  'tts.preset.natural': 'ბუნებრივი',
  'tts.preset.robust': 'მყარი',
  'tts.preset.hint': 'კრეატიული ყველაზე ემოციურია, მაგრამ შეიძლება შეცდეს; ბუნებრივი ორიგინალ ხმასთან ყველაზე ახლოსაა; მყარი ძალიან თანმიმდევრულია, მაგრამ სცენურ მითითებებს უმეტესად უგულებელყოფს.',

  'tts.similarity': 'მსგავსება',

  'tts.similarity.hint': 'რამდენად მჭიდროდ მიჰყვეს ორიგინალ ხმას. ძალიან მაღალმა მნიშვნელობამ შეიძლება საწყისი ჩანაწერის ხარვეზებიც გაიმეოროს.',

  'tts.style': 'სტილის გაძლიერება',

  'tts.style.hint': 'აძლიერებს მოსაუბრის სტილს. ნულზე მეტი დაყოვნებას ამატებს; თხრობისთვის დატოვეთ ნულზე.',

  'tts.speakerboost': 'მოსაუბრის გაძლიერება',

  'tts.speakerboost.hint': 'შედეგს ორიგინალ მოსაუბრესთან აახლოებს მცირე დაყოვნების ფასად.',

  'tts.speed': 'სიჩქარე',
  'tts.forcelang': 'არჩეული ენის იძულება',

  'tts.forcelang.hint': 'მოდელს ეუბნება, რომელ ენაზეა ტექსტი, რომ შერეული ან ორაზროვანი ტექსტი არასწორი აქცენტით არ წაიკითხოს. მხოლოდ Flash და Turbo მოდელები ითვალისწინებენ.',

  'tts.v3tip': 'ამ მოდელს ესმის სცენური მითითებები კვადრატულ ფრჩხილებში, მაგ. [whispers], [laughs], [excited], [sighs]. მრავალწერტილი პაუზას ამატებს; დიდი ასოები — ხაზგასმას.',
  'tts.reset': 'ნაგულისხმევზე დაბრუნება',
  'tts.unsupported': 'ამ მოდელისთვის მიუწვდომელია',

  'tts.models.loadfail': 'მოდელების სია ვერ ჩაიტვირთა; გამოყენებული იქნება ნაგულისხმევი მოდელი.',
};

export const ru: Dict = {
  'tts.heading': 'Генерация речи из текста',
  'tts.text_ph': 'Введите текст… (английский, русский или грузинский)',
  'tts.needtext': 'Введите текст.',
  'tts.pickvoice': 'Выберите конкретный голос для прослушивания.',
  'tts.previewtitle': 'Прослушать голос (бесплатный образец)',
  'tts.previewfail': 'Не удалось воспроизвести образец. Попробуйте ещё раз.',

  'quota.using': 'Вы используете CommuniQ анонимно —',
  'quota.analyses': 'расшифровок',
  'quota.clips': 'аудиоклипов',
  'quota.left': 'осталось сегодня.',
  'quota.more': 'для базы знаний и более высоких лимитов.',
  'quota.disabled': 'Анонимный доступ отключён.',
  'quota.conversions': 'конвертаций файлов',

  // The rest of the TTS panel: voice picking, the model/format controls and the
  // clip that comes back. Shared by the public page and the account page.
  'tts.adv': 'Расширенные настройки голоса',
  'tts.model': 'Модель',

  'tts.model.auto': 'Авто — лучшая модель для выбранного языка',
  'tts.model.hint': 'Multilingual v2 — надёжный универсал; Eleven v3 — самая выразительная и понимает сценические ремарки в квадратных скобках; Flash — самая быстрая и дешёвая.',

  'tts.custom': 'Настроить параметры голоса',

  'tts.custom.hint': 'Выключено — голос говорит с настройками, которые выбрал его создатель. Включите, чтобы изменить их для этой записи.',

  'tts.stability': 'Стабильность',

  'tts.stability.expressive': 'Выразительнее',
  'tts.stability.stable': 'Стабильнее',
  'tts.stability.hint': 'Низкие значения дают более широкий эмоциональный диапазон, но могут «уплывать»; высокие — последовательны, но более монотонны.',

  'tts.preset.creative': 'Творческий',
  'tts.preset.natural': 'Естественный',
  'tts.preset.robust': 'Устойчивый',
  'tts.preset.hint': 'Творческий — самый эмоциональный, но может ошибаться; Естественный ближе всего к оригинальному голосу; Устойчивый очень стабилен, но почти не реагирует на ремарки.',

  'tts.similarity': 'Сходство',

  'tts.similarity.hint': 'Насколько точно следовать оригинальному голосу. Слишком высокие значения могут воспроизвести дефекты исходной записи.',

  'tts.style': 'Усиление стиля',

  'tts.style.hint': 'Усиливает манеру говорящего. Любое значение выше нуля добавляет задержку; для повествования оставьте ноль.',

  'tts.speakerboost': 'Усиление говорящего',

  'tts.speakerboost.hint': 'Приближает результат к оригинальному голосу ценой небольшой задержки.',

  'tts.speed': 'Скорость',
  'tts.forcelang': 'Принудительно выбранный язык',

  'tts.forcelang.hint': 'Сообщает модели, на каком языке текст, чтобы смешанный или неоднозначный текст не читался с неверным акцентом. Учитывают только модели Flash и Turbo.',

  'tts.v3tip': 'Эта модель понимает сценические ремарки в квадратных скобках, напр. [whispers], [laughs], [excited], [sighs]. Многоточие добавляет паузу; ЗАГЛАВНЫЕ — акцент.',
  'tts.reset': 'Сбросить настройки',
  'tts.unsupported': 'Недоступно для этой модели',

  'tts.models.loadfail': 'Не удалось загрузить список моделей; будет использована модель по умолчанию.',
};
