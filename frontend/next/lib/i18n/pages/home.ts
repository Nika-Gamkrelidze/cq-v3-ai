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

  /* The fourth state of the allowance banner, and the only string the port had to add.
     `GET /limits` now answers `enabled:false, visitor_identified:false` when the deployment
     cannot tell one anonymous visitor from another — its own NAT address is what reaches the
     app — and the anonymous tier then refuses everything with a 503 rather than metering the
     whole internet on one shared allowance (`services/limits.py::_anon_unidentifiable`).

     It needs its own sentence because `quota.disabled` ("Anonymous access is disabled") names
     an operator SWITCH, and this is a server condition nobody chose; and because the zeroes
     that come back in `remaining` are the absence of a counter, so the ordinary "…left today"
     wording would tell a first-time visitor they had spent an allowance they never had.

     Assembled from wording already in the dictionary in all three languages — "anonymous
     access" from `quota.disabled`, "on this server" from `cv.unavailable`, "temporarily
     unavailable" from `err.unavailable` — rather than newly translated. */
  'hero.quota.unavailable': 'Anonymous access is temporarily unavailable on this server.',
};

export const ka: Dict = {
  'hero.eyebrow': 'CommuniQ ხმოვანი AI',
  'hero.title': 'ისაუბრეთ და გაიგეთ ყველა ზარი.',

  'hero.quota.unavailable': 'ანონიმური წვდომა ამ სერვერზე დროებით მიუწვდომელია.',
};

export const ru: Dict = {
  'hero.eyebrow': 'Голосовой ИИ CommuniQ',
  'hero.title': 'Озвучивайте текст и понимайте каждый звонок.',

  'hero.quota.unavailable': 'Анонимный доступ на этом сервере временно недоступен.',
};
