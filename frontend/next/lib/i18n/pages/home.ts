/* The public app (hero.*) — `index.html` → `/`.

   Named home.ts, not index.ts, because `lib/i18n/index.ts` is the barrel: two files under one
   tree both called index would make every import and every stack trace ambiguous about which
   one is meant, and the barrel is the file every module already imports `Dict` from.

   Almost nothing is page-owned here: the public page is assembled out of shared feature
   vocabularies — TTS and the anonymous quota banner (features/tts.ts), transcription and
   sentiment (features/analysis.ts), audio conversion (features/convert.ts) — so only the
   masthead above them belongs to the page. The module exists anyway, so that the port has a
   place to put a string that is genuinely the public page's and does not reach for chrome.ts
   (which every page loads) to hold it. */

import type { Dict } from '../index';

export const en: Dict = {
  'hero.eyebrow': 'CommuniQ Voice AI',
  'hero.title': 'Speak & understand every call.',
};

export const ka: Dict = {
  'hero.eyebrow': 'CommuniQ ხმოვანი AI',
  'hero.title': 'ისაუბრეთ და გაიგეთ ყველა ზარი.',
};

export const ru: Dict = {
  'hero.eyebrow': 'Голосовой ИИ CommuniQ',
  'hero.title': 'Озвучивайте текст и понимайте каждый звонок.',
};
