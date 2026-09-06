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
};
