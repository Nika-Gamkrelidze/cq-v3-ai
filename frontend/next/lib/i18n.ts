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
  'nav.aicfg': 'AI setup',
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
  'usage.th.byo': 'On their key',
  'usage.byo.hint': 'Part of the total that ran on the workspace’s own provider key, so it was not billed to us.',
  'usage.configure': 'AI setup for this workspace',

  'aicfg.title': 'AI setup',
  'aicfg.lead': 'Every workspace runs on the deployment’s model and key. Change that here only when a customer has asked for it.',
  'aicfg.default.is': 'Default for all workspaces: {model}',
  'aicfg.default.unset': 'No default model is configured yet — set one in the console before overriding a workspace.',
  'aicfg.search': 'Search workspaces',
  'aicfg.none': 'No workspaces yet.',
  'aicfg.nomatch': 'No workspace matches that.',
  'aicfg.loadfail': 'Could not load workspaces.',
  'aicfg.th.tenant': 'Workspace',
  'aicfg.th.runson': 'Runs on',
  'aicfg.th.billing': 'Billed to',
  'aicfg.th.updated': 'Changed',
  'aicfg.runs.default': 'Default',
  'aicfg.runs.staged': 'Custom, switched off',
  'aicfg.bill.us': 'Us',
  'aicfg.bill.them': 'The customer',
  'aicfg.enabled': 'Use a custom setup for this workspace',
  'aicfg.enabled.off': 'Off. This workspace runs on the default, and the fields below are kept but not applied.',
  'aicfg.enabled.on': 'On. This workspace runs on the settings below.',
  'aicfg.model': 'Model',
  'aicfg.model.hint': 'Leave empty to keep the default model.',
  'aicfg.provider': 'Provider',
  'aicfg.baseurl': 'API endpoint',
  'aicfg.baseurl.hint': 'Leave empty for Anthropic. Only set this for a gateway the customer has asked you to use — every transcript this workspace submits will be sent there.',
  'aicfg.key': 'Customer’s API key',
  'aicfg.key.none': 'No key stored. This workspace runs on ours, and its usage is our cost.',
  'aicfg.key.set': 'A key is stored. It cannot be shown again — enter a new one to replace it.',
  'aicfg.key.ph': 'Leave empty to keep the stored key',
  'aicfg.key.remove': 'Remove stored key',
  'aicfg.key.removing': 'The stored key will be removed when you save, and this workspace will go back to ours.',
  'aicfg.key.keep': 'Keep it',
  'aicfg.notes': 'Note',
  'aicfg.notes.hint': 'Why this workspace is different. Operators only — the customer never sees it.',
  'aicfg.save': 'Save',
  'aicfg.saving': 'Saving…',
  'aicfg.saved': 'Saved.',
  'aicfg.savefail': 'Could not save.',
  'aicfg.back': 'All workspaces',
  'aicfg.changed': 'Changed {when} by {who}',
  'aicfg.never': 'Never changed.',
  'aicfg.usage': 'AI usage for this workspace',
  'aicfg.adminonly': 'This page is for CommuniQ operators. Sign in with an administrator account.',
};

const ka: Dict = {
  'nav.public': 'საჯარო აპლიკაცია',
  'nav.editor': 'აუდიო რედაქტორი',
  'nav.console': 'კონსოლი',
  'nav.kb': 'ცოდნის ბაზა',
  'nav.workspace': 'ჩემი სამუშაო სივრცე',
  'nav.account': 'ჩემი ანგარიში',
  'nav.usage': 'AI-ს ხარჯვა',
  'nav.aicfg': 'AI-ს პარამეტრები',
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
  'usage.th.byo': 'მათივე გასაღებით',
  'usage.byo.hint': 'ჯამის ის ნაწილი, რომელიც ორგანიზაციის საკუთარი გასაღებით შესრულდა — ანუ ჩვენ არ დაგვეკისრა.',
  'usage.configure': 'ამ ორგანიზაციის AI-ს პარამეტრები',

  'aicfg.title': 'AI-ს პარამეტრები',
  'aicfg.lead': 'ყველა ორგანიზაცია ნაგულისხმევი მოდელითა და გასაღებით მუშაობს. აქ მხოლოდ მაშინ შეცვალეთ, როცა კლიენტმა ეს მოითხოვა.',
  'aicfg.default.is': 'ნაგულისხმევი ყველასთვის: {model}',
  'aicfg.default.unset': 'ნაგულისხმევი მოდელი ჯერ არ არის მითითებული — ჯერ კონსოლში დააყენეთ.',
  'aicfg.search': 'ორგანიზაციის ძებნა',
  'aicfg.none': 'ორგანიზაციები ჯერ არ არის.',
  'aicfg.nomatch': 'ასეთი ორგანიზაცია ვერ მოიძებნა.',
  'aicfg.loadfail': 'ორგანიზაციები ვერ ჩაიტვირთა.',
  'aicfg.th.tenant': 'ორგანიზაცია',
  'aicfg.th.runson': 'რაზე მუშაობს',
  'aicfg.th.billing': 'ვის ეკისრება',
  'aicfg.th.updated': 'შეიცვალა',
  'aicfg.runs.default': 'ნაგულისხმევი',
  'aicfg.runs.staged': 'ინდივიდუალური, გამორთული',
  'aicfg.bill.us': 'ჩვენ',
  'aicfg.bill.them': 'კლიენტს',
  'aicfg.enabled': 'ამ ორგანიზაციისთვის ინდივიდუალური პარამეტრების გამოყენება',
  'aicfg.enabled.off': 'გამორთულია. ორგანიზაცია ნაგულისხმევზე მუშაობს; ქვემოთ მითითებული პარამეტრები ინახება, მაგრამ არ გამოიყენება.',
  'aicfg.enabled.on': 'ჩართულია. ორგანიზაცია ქვემოთ მითითებულ პარამეტრებზე მუშაობს.',
  'aicfg.model': 'მოდელი',
  'aicfg.model.hint': 'ცარიელი დატოვეთ, რომ ნაგულისხმევი მოდელი შენარჩუნდეს.',
  'aicfg.provider': 'პროვაიდერი',
  'aicfg.baseurl': 'API-ს მისამართი',
  'aicfg.baseurl.hint': 'Anthropic-ისთვის ცარიელი დატოვეთ. მიუთითეთ მხოლოდ ის მისამართი, რომელიც კლიენტმა მოითხოვა — ამ ორგანიზაციის ყველა ტრანსკრიპტი იქ გაიგზავნება.',
  'aicfg.key': 'კლიენტის API-გასაღები',
  'aicfg.key.none': 'გასაღები არ ინახება. ორგანიზაცია ჩვენით მუშაობს და ხარჯიც ჩვენია.',
  'aicfg.key.set': 'გასაღები ინახება. მისი ხელახლა ჩვენება შეუძლებელია — ჩასაცვლელად ახალი შეიყვანეთ.',
  'aicfg.key.ph': 'ცარიელი დატოვეთ შენახული გასაღების შესანარჩუნებლად',
  'aicfg.key.remove': 'შენახული გასაღების წაშლა',
  'aicfg.key.removing': 'შენახვისას გასაღები წაიშლება და ორგანიზაცია ისევ ჩვენზე გადმოვა.',
  'aicfg.key.keep': 'დარჩეს',
  'aicfg.notes': 'შენიშვნა',
  'aicfg.notes.hint': 'რატომ განსხვავდება ეს ორგანიზაცია. მხოლოდ ოპერატორებისთვის — კლიენტი ვერ ხედავს.',
  'aicfg.save': 'შენახვა',
  'aicfg.saving': 'ინახება…',
  'aicfg.saved': 'შენახულია.',
  'aicfg.savefail': 'ვერ შეინახა.',
  'aicfg.back': 'ყველა ორგანიზაცია',
  'aicfg.changed': 'შეიცვალა {when}, {who}-ის მიერ',
  'aicfg.never': 'არასდროს შეცვლილა.',
  'aicfg.usage': 'ამ ორგანიზაციის AI-ს ხარჯვა',
  'aicfg.adminonly': 'ეს გვერდი CommuniQ-ის ოპერატორებისთვისაა. შედით ადმინისტრატორის ანგარიშით.',
};

const ru: Dict = {
  'nav.public': 'Публичное приложение',
  'nav.editor': 'Аудиоредактор',
  'nav.console': 'Консоль',
  'nav.kb': 'База знаний',
  'nav.workspace': 'Моя организация',
  'nav.account': 'Мой аккаунт',
  'nav.usage': 'Расход ИИ',
  'nav.aicfg': 'Настройка ИИ',
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
  'usage.th.byo': 'На своём ключе',
  'usage.byo.hint': 'Часть общего расхода, выполненная на собственном ключе организации — то есть не за наш счёт.',
  'usage.configure': 'Настройка ИИ для этой организации',

  'aicfg.title': 'Настройка ИИ',
  'aicfg.lead': 'Все организации работают на модели и ключе по умолчанию. Меняйте это только по запросу клиента.',
  'aicfg.default.is': 'По умолчанию для всех: {model}',
  'aicfg.default.unset': 'Модель по умолчанию ещё не задана — сначала укажите её в консоли.',
  'aicfg.search': 'Поиск организации',
  'aicfg.none': 'Организаций пока нет.',
  'aicfg.nomatch': 'Ничего не найдено.',
  'aicfg.loadfail': 'Не удалось загрузить организации.',
  'aicfg.th.tenant': 'Организация',
  'aicfg.th.runson': 'Работает на',
  'aicfg.th.billing': 'За чей счёт',
  'aicfg.th.updated': 'Изменено',
  'aicfg.runs.default': 'По умолчанию',
  'aicfg.runs.staged': 'Своя настройка, выключена',
  'aicfg.bill.us': 'За наш',
  'aicfg.bill.them': 'За счёт клиента',
  'aicfg.enabled': 'Использовать отдельную настройку для этой организации',
  'aicfg.enabled.off': 'Выключено. Организация работает на настройках по умолчанию; поля ниже сохраняются, но не применяются.',
  'aicfg.enabled.on': 'Включено. Организация работает на настройках ниже.',
  'aicfg.model': 'Модель',
  'aicfg.model.hint': 'Оставьте пустым, чтобы сохранить модель по умолчанию.',
  'aicfg.provider': 'Провайдер',
  'aicfg.baseurl': 'Адрес API',
  'aicfg.baseurl.hint': 'Для Anthropic оставьте пустым. Указывайте только тот адрес, о котором попросил клиент — туда будут уходить все расшифровки этой организации.',
  'aicfg.key': 'API-ключ клиента',
  'aicfg.key.none': 'Ключ не сохранён. Организация работает на нашем, и расход — наш.',
  'aicfg.key.set': 'Ключ сохранён. Показать его снова нельзя — введите новый, чтобы заменить.',
  'aicfg.key.ph': 'Оставьте пустым, чтобы сохранить текущий ключ',
  'aicfg.key.remove': 'Удалить сохранённый ключ',
  'aicfg.key.removing': 'При сохранении ключ будет удалён, и организация вернётся на наш.',
  'aicfg.key.keep': 'Оставить',
  'aicfg.notes': 'Заметка',
  'aicfg.notes.hint': 'Почему эта организация отличается. Только для операторов — клиент этого не видит.',
  'aicfg.save': 'Сохранить',
  'aicfg.saving': 'Сохранение…',
  'aicfg.saved': 'Сохранено.',
  'aicfg.savefail': 'Не удалось сохранить.',
  'aicfg.back': 'Все организации',
  'aicfg.changed': 'Изменено {when}, автор: {who}',
  'aicfg.never': 'Не изменялось.',
  'aicfg.usage': 'Расход ИИ этой организации',
  'aicfg.adminonly': 'Эта страница для операторов CommuniQ. Войдите под учётной записью администратора.',
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
