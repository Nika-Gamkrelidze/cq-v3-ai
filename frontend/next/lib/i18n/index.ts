/* The dictionary, assembled — and nothing else.
   =============================================
   No string lives in this file. Six page agents and a shared-chrome module each own a slice
   of the dictionary, and they are merged here at import time. Splitting it that way is not
   tidiness: one `i18n.ts` is a file every page has to edit, which under parallel work means
   a merge conflict on every branch and, worse, two people quietly defining the same key with
   different wording. `scripts/check_i18n.py` now fails when two MIGRATED modules claim one
   key, so ownership is checked by machine rather than by review.

   A key that a legacy .html page still needs is COPIED into its module here, not moved out of
   `brand.js`. The checker reports every such pair as shared between the two stacks, and flags
   any pair whose wording has since diverged; the count is meant to fall to zero as the last
   .html files go.

   Module order below decides who wins a collision — but the checker fails before that can
   matter, so it is documentation, not a mechanism to rely on.

   ADDING A MODULE — two steps, and BOTH are checked, because each one alone fails silently:
     1. Declare the blocks as literally `export const en: Dict = { … }`, and the same for `ka`
        and `ru`. `scripts/check_i18n.py` finds them by that exact spelling; the equally valid
        `export const en = { … } satisfies Dict` type-checks, runs identically, and drops the
        module out of the parity and ownership checks with no diagnostic — so the checker now
        fails a module it cannot parse rather than shrugging.
     2. Import it and list it in `MODULES` below. A module that is imported but not listed
        contributes nothing to `DICT`, and `tsc` has no opinion about that; the checker fails
        on both halves. This is the one line that lives outside the module's own file, so it
        is the one that gets lost in a rebase — which is why it is machine-checked and not a
        review item. What the operator sees when it goes missing is raw key names. */

import * as chrome from './chrome';
import * as analysis from './features/analysis';
import * as convert from './features/convert';
import * as retrieval from './features/retrieval';
import * as scoring from './features/scoring';
import * as tts from './features/tts';
import * as aicfg from './pages/aicfg';
import * as usage from './pages/usage';

export type Lang = 'en' | 'ka' | 'ru';
export const LANGS: Lang[] = ['en', 'ka', 'ru'];

export type Dict = Record<string, string>;

const MODULES: Record<Lang, Dict>[] = [
  chrome,
  analysis, convert, retrieval, scoring, tts,
  aicfg, usage,
];

function assemble(lang: Lang): Dict {
  const out: Dict = {};
  for (const m of MODULES) Object.assign(out, m[lang]);
  return out;
}

export const DICT: Record<Lang, Dict> = {
  en: assemble('en'),
  ka: assemble('ka'),
  ru: assemble('ru'),
};

const STORAGE_KEY = 'cq_lang';

/** The language the visitor last chose, shared with the legacy pages through the same key so
    switching language does not reset when crossing between the two stacks.

    The browser fallback matters as much as the stored value: `brand.js` falls back to
    `navigator.language.slice(0,2)`, so a Georgian first-time visitor who has never touched the
    switch gets Georgian on a legacy page. Falling back to English here instead would make the
    app appear to change language as they navigate between the two stacks. */
export function currentLang(): Lang {
  if (typeof window === 'undefined') return 'en';
  try {
    const v = window.localStorage.getItem(STORAGE_KEY) as Lang | null;
    if (v && LANGS.includes(v)) return v;
  } catch { /* private mode */ }
  const nav = (typeof navigator !== 'undefined' ? navigator.language || '' : '').slice(0, 2) as Lang;
  return LANGS.includes(nav) ? nav : 'en';
}

export function setLang(lang: Lang): void {
  try { window.localStorage.setItem(STORAGE_KEY, lang); } catch { /* private mode */ }
  // `lang` on <html> drives hyphenation, font selection and how a screen reader pronounces the
  // page. brand.js sets it; a ported page that did not would leave a Georgian page announcing
  // itself as English.
  document.documentElement.setAttribute('lang', lang);
  // BOTH targets, and neither is redundant. A `CustomEvent` does not bubble unless asked, and
  // brand.js dispatches on `document` with the default (`brand.js:1333`) — so a document
  // dispatch never reaches a window listener and a window dispatch never reaches a document
  // one. Verified in a browser, because the intuition that document events reach window is
  // wrong here and it is exactly the kind of wrong that survives review.
  //
  // So BOTH stacks were deaf to each other: switching language from a ported header left every
  // brand.js widget still on the page (the workbench, the timeline, an open tip) in the old
  // language, and a legacy switch left a ported component in the old one. `useI18n` listens on
  // both for the same reason. Drop the document side when the last legacy script is gone, and
  // not one commit before.
  const ev = () => new CustomEvent('cq:lang', { detail: lang });
  document.dispatchEvent(ev());
  window.dispatchEvent(ev());
}

/** Look up a key. Falls back to English, then to the key itself — a missing translation
    should read as slightly wrong English, never as a blank space where a label belongs. */
export function translate(lang: Lang, key: string, vars?: Record<string, string | number>): string {
  let s = DICT[lang]?.[key] ?? DICT.en[key] ?? key;
  if (vars) {
    for (const [k, v] of Object.entries(vars)) s = s.split(`{${k}}`).join(String(v));
  }
  return s;
}
