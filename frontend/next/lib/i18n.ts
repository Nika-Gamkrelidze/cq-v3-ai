/* Translations for the pages that have been migrated.
   ===================================================
   The legacy `brand.js` holds a ~993-key dictionary for the pages that have not moved yet.
   Keys are NOT copied here wholesale: a key travels with its page, so when a page ports its
   strings move out of brand.js and into this file, and the two cannot drift because no PAGE
   string is defined in both.

   The shared chrome is the one exception. The header exists on both stacks while the
   migration is in flight, so its `nav.*` keys are defined in both files. They cannot shadow
   each other — a page is served by one stack or the other, never both — but they can drift,
   so `check_i18n.py` lists them on every run and the list is meant to shrink to nothing as
   the last .html pages go.

   `scripts/check_i18n.py` enforces en/ka/ru parity on both files; a key present in one
   language and missing in another fails the check rather than rendering a raw key at a
   customer. */

export type Lang = 'en' | 'ka' | 'ru';
export const LANGS: Lang[] = ['en', 'ka', 'ru'];

type Dict = Record<string, string>;

const en: Dict = {
  'nav.public': 'Public app',
  'nav.editor': 'Audio editor',
  'nav.console': 'Console',
  'nav.kb': 'Knowledge Base',
  'nav.workspace': 'My workspace',
  'nav.account': 'My account',
  'nav.usage': 'AI usage',
  'nav.signin': 'Sign in',
  'nav.create': 'Create account',
  'nav.logout': 'Log out',

  'usage.title': 'AI usage',
  'usage.lead': 'What each workspace consumed. Tokens are counted, not priced — the rate depends on the model and your contract, so apply your own.',
  'usage.window': 'Period',
  'usage.window.24h': 'Last 24 hours',
  'usage.window.7d': 'Last 7 days',
  'usage.window.30d': 'Last 30 days',
  'usage.window.90d': 'Last 90 days',
  'usage.tenants': 'Workspaces',
  'usage.none': 'No AI usage recorded in this period.',
  'usage.loadfail': 'Could not load usage.',
  'usage.th.tenant': 'Workspace',
  'usage.th.total': 'Total tokens',
  'usage.th.in': 'Input',
  'usage.th.out': 'Output',
  'usage.th.cache': 'Cached',
  'usage.th.calls': 'Calls',
  'usage.th.failed': 'Failed',
  'usage.th.last': 'Last used',
  'usage.byuser': 'By user',
  'usage.byfeature': 'By feature',
  'usage.bymodel': 'By model',
  'usage.byjob': 'By recording',
  'usage.th.user': 'Who',
  'usage.th.feature': 'Feature',
  'usage.th.model': 'Model',
  'usage.th.job': 'Recording',
  'usage.back': 'All workspaces',
  'usage.unattributed': 'Unattributed',
  'usage.purged': 'Recording deleted',
  'usage.adminonly': 'This page is for CommuniQ operators. Sign in with an administrator account.',
  'usage.empty.section': 'Nothing in this period.',
};

const ka: Dict = {
  'nav.public': 'საჯარო აპლიკაცია',
  'nav.editor': 'აუდიო რედაქტორი',
  'nav.console': 'კონსოლი',
  'nav.kb': 'ცოდნის ბაზა',
  'nav.workspace': 'ჩემი სამუშაო სივრცე',
  'nav.account': 'ჩემი ანგარიში',
  'nav.usage': 'AI-ს ხარჯვა',
  'nav.signin': 'შესვლა',
  'nav.create': 'ანგარიშის შექმნა',
  'nav.logout': 'გასვლა',

  'usage.title': 'AI-ს ხარჯვა',
  'usage.lead': 'რამდენი დახარჯა თითოეულმა ორგანიზაციამ. ტოკენები ითვლება, ფასი კი არ ედება — ტარიფი დამოკიდებულია მოდელსა და ხელშეკრულებაზე, ამიტომ თქვენი გამოიყენეთ.',
  'usage.window': 'პერიოდი',
  'usage.window.24h': 'ბოლო 24 საათი',
  'usage.window.7d': 'ბოლო 7 დღე',
  'usage.window.30d': 'ბოლო 30 დღე',
  'usage.window.90d': 'ბოლო 90 დღე',
  'usage.tenants': 'ორგანიზაციები',
  'usage.none': 'ამ პერიოდში AI-ს ხარჯვა არ დაფიქსირებულა.',
  'usage.loadfail': 'ხარჯვის მონაცემები ვერ ჩაიტვირთა.',
  'usage.th.tenant': 'ორგანიზაცია',
  'usage.th.total': 'სულ ტოკენი',
  'usage.th.in': 'შემავალი',
  'usage.th.out': 'გამომავალი',
  'usage.th.cache': 'ქეშირებული',
  'usage.th.calls': 'გამოძახება',
  'usage.th.failed': 'შეცდომა',
  'usage.th.last': 'ბოლოს',
  'usage.byuser': 'მომხმარებლების მიხედვით',
  'usage.byfeature': 'ფუნქციების მიხედვით',
  'usage.bymodel': 'მოდელების მიხედვით',
  'usage.byjob': 'ჩანაწერების მიხედვით',
  'usage.th.user': 'ვინ',
  'usage.th.feature': 'ფუნქცია',
  'usage.th.model': 'მოდელი',
  'usage.th.job': 'ჩანაწერი',
  'usage.back': 'ყველა ორგანიზაცია',
  'usage.unattributed': 'მიუკუთვნებელი',
  'usage.purged': 'ჩანაწერი წაშლილია',
  'usage.adminonly': 'ეს გვერდი CommuniQ-ის ოპერატორებისთვისაა. შედით ადმინისტრატორის ანგარიშით.',
  'usage.empty.section': 'ამ პერიოდში მონაცემები არ არის.',
};

const ru: Dict = {
  'nav.public': 'Публичное приложение',
  'nav.editor': 'Аудиоредактор',
  'nav.console': 'Консоль',
  'nav.kb': 'База знаний',
  'nav.workspace': 'Моя организация',
  'nav.account': 'Мой аккаунт',
  'nav.usage': 'Расход ИИ',
  'nav.signin': 'Войти',
  'nav.create': 'Создать аккаунт',
  'nav.logout': 'Выйти',

  'usage.title': 'Расход ИИ',
  'usage.lead': 'Сколько израсходовала каждая организация. Токены считаются, но не тарифицируются — ставка зависит от модели и договора, поэтому примените свою.',
  'usage.window': 'Период',
  'usage.window.24h': 'Последние 24 часа',
  'usage.window.7d': 'Последние 7 дней',
  'usage.window.30d': 'Последние 30 дней',
  'usage.window.90d': 'Последние 90 дней',
  'usage.tenants': 'Организации',
  'usage.none': 'За этот период расход ИИ не зафиксирован.',
  'usage.loadfail': 'Не удалось загрузить данные о расходе.',
  'usage.th.tenant': 'Организация',
  'usage.th.total': 'Всего токенов',
  'usage.th.in': 'Входные',
  'usage.th.out': 'Выходные',
  'usage.th.cache': 'Из кэша',
  'usage.th.calls': 'Вызовы',
  'usage.th.failed': 'Ошибки',
  'usage.th.last': 'Последний раз',
  'usage.byuser': 'По пользователям',
  'usage.byfeature': 'По функциям',
  'usage.bymodel': 'По моделям',
  'usage.byjob': 'По записям',
  'usage.th.user': 'Кто',
  'usage.th.feature': 'Функция',
  'usage.th.model': 'Модель',
  'usage.th.job': 'Запись',
  'usage.back': 'Все организации',
  'usage.unattributed': 'Без привязки',
  'usage.purged': 'Запись удалена',
  'usage.adminonly': 'Эта страница для операторов CommuniQ. Войдите под учётной записью администратора.',
  'usage.empty.section': 'За этот период данных нет.',
};

export const DICT: Record<Lang, Dict> = { en, ka, ru };

const STORAGE_KEY = 'cq_lang';

/** The language the visitor last chose, shared with the legacy pages through the same key so
    switching language does not reset when crossing between the two stacks. */
export function currentLang(): Lang {
  if (typeof window === 'undefined') return 'en';
  try {
    const v = window.localStorage.getItem(STORAGE_KEY) as Lang | null;
    if (v && LANGS.includes(v)) return v;
  } catch { /* private mode */ }
  return 'en';
}

export function setLang(lang: Lang): void {
  try { window.localStorage.setItem(STORAGE_KEY, lang); } catch { /* private mode */ }
  window.dispatchEvent(new CustomEvent('cq:lang', { detail: lang }));
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
