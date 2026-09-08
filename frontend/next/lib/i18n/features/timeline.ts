/* The transcript timeline (tl.*) — the lane strip under a recording.

   Like the workbench block above, these came from a `CQ.extendDict({...})` call inside
   `timeline.js` rather than from `brand.js`, and for the same reason: only a page that loaded
   the script needed them. `<Timeline>` is mounted by the workbench and, in text mode (no
   audio), by anything showing a transcript, so the vocabulary belongs to the feature.

   Worth knowing while porting the component that reads these: `timeline.js` listens for
   `cq:lang` on `document`, and the new stack dispatches it on `window` as well as `document`
   precisely so both halves keep re-translating during the migration. */

import type { Dict } from '../index';

export const en: Dict = {
  'tl.play': 'Play',
  'tl.pause': 'Pause',
  'tl.speed': 'Playback speed',
  'tl.download': 'Download recording',
  'tl.position': 'Playback position',
  'tl.keyhint': 'Space plays or pauses, ← and → skip 5 seconds, Home and End jump to the start or the end.',
  'tl.speakers': 'Speakers',
  'tl.speaker': 'Speaker {n}',
  'tl.layers': 'Layers',
  'tl.loading': 'Loading waveform…',
  'tl.decodefail': 'Waveform unavailable — seeking still works.',
  'tl.playfail': 'The recording cannot be played in this browser. You can still read the transcript and download the file.',
  'tl.loadfail': 'Could not load the recording.',
  'tl.nosegments': 'The transcript is empty.',
  'tl.goto': 'Go to {t}',
};

export const ka: Dict = {
  'tl.play': 'დაკვრა',
  'tl.pause': 'პაუზა',
  'tl.speed': 'დაკვრის სიჩქარე',
  'tl.download': 'ჩანაწერის ჩამოტვირთვა',
  'tl.position': 'დაკვრის პოზიცია',
  'tl.keyhint': 'Space — დაკვრა ან პაუზა, ← და → — 5 წამით უკან ან წინ, Home და End — დასაწყისში ან დასასრულში გადასვლა.',
  'tl.speakers': 'მოსაუბრეები',
  'tl.speaker': 'მოსაუბრე {n}',
  'tl.layers': 'ფენები',
  'tl.loading': 'ტალღის ფორმა იტვირთება…',
  'tl.decodefail': 'ტალღის ფორმა მიუწვდომელია — გადახვევა მაინც მუშაობს.',
  'tl.playfail': 'ჩანაწერის დაკვრა ამ ბრაუზერში ვერ ხერხდება. ტრანსკრიფციის წაკითხვა და ფაილის ჩამოტვირთვა კვლავ შესაძლებელია.',
  'tl.loadfail': 'ჩანაწერის ჩატვირთვა ვერ მოხერხდა.',
  'tl.nosegments': 'ტრანსკრიფცია ცარიელია.',
  'tl.goto': 'გადასვლა: {t}',
};

export const ru: Dict = {
  'tl.play': 'Воспроизвести',
  'tl.pause': 'Пауза',
  'tl.speed': 'Скорость воспроизведения',
  'tl.download': 'Скачать запись',
  'tl.position': 'Позиция воспроизведения',
  'tl.keyhint': 'Пробел — воспроизведение или пауза, ← и → — на 5 секунд назад или вперёд, Home и End — в начало или в конец.',
  'tl.speakers': 'Говорящие',
  'tl.speaker': 'Говорящий {n}',
  'tl.layers': 'Слои',
  'tl.loading': 'Загрузка формы волны…',
  'tl.decodefail': 'Форма волны недоступна — перемотка по-прежнему работает.',
  'tl.playfail': 'Запись не воспроизводится в этом браузере. Расшифровку можно читать, а файл — скачать.',
  'tl.loadfail': 'Не удалось загрузить запись.',
  'tl.nosegments': 'Расшифровка пуста.',
  'tl.goto': 'Перейти к {t}',
};
