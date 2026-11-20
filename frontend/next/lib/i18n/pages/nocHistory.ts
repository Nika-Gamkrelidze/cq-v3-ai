/* The NOC page's History view (noch.*): the stored last 30 days, a date picker, a day timeline and
   the board as it was at a chosen moment. The live board's own strings stay in noc.ts. */

import type { Dict } from '../index';

export const en: Dict = {
  'noch.date': 'Day',
  'noch.today': 'Today',
  'noch.yesterday': 'Yesterday',
  'noch.kept': 'Stored for {n} days.',

  'noch.days.title': 'Last 31 days',
  'noch.days.hint': 'Colour is the worst status of the day. Grey: nothing was stored.',
  'noch.day.tip': '{day} — {samples} samples · {crit} critical · {warn} warnings',
  'noch.day.none': '{day} — nothing stored',
  'noch.day.empty': 'Nothing was stored for this day.',

  'noch.tl.title': 'Timeline',
  'noch.tl.hint': 'One cell is 5 minutes, coloured by the worst status stored in it. Click a cell to see the whole board as it was.',
  'noch.tl.cell': '{time} — {status}',
  'noch.tl.cell.none': '{time} — nothing stored',
  'noch.tl.prev': '‹ 5 min',
  'noch.tl.next': '5 min ›',
  'noch.tl.prev.label': 'Previous 5 minutes',
  'noch.tl.next.label': 'Next 5 minutes',
  'noch.tl.sel': 'Selected: {time}',
  'noch.tl.none': 'No moment selected',
  'noch.tl.now': 'now',

  'noch.inc.title': 'Incidents',
  'noch.inc.none': 'No warnings or outages were stored on this day.',
  'noch.inc.more': '+{n} more',

  'noch.snap.note': 'Snapshot of {when}',
  'noch.snap.live': 'Back to live',
  'noch.snap.pick': 'Pick a moment on the timeline, or an incident, to see the whole board as it was.',
  'noch.snap.none': 'No snapshot was stored near that time.',

  'noch.c.title': 'This day',
  'noch.c.cpu': 'CPU',
  'noch.c.mem': 'Memory',
  'noch.c.req': 'Requests per minute',
  'noch.c.5xx': 'Server errors (5xx)',
  'noch.c.db': 'Database latency',
  'noch.c.ai': 'Failed AI calls',
  'noch.s.mem': 'memory %',
  'noch.s.net': 'received + sent',
  'noch.s.5xx': '5xx %',
  'noch.s.db': 'latency',
};

export const ka: Dict = {
  'noch.date': 'დღე',
  'noch.today': 'დღეს',
  'noch.yesterday': 'გუშინ',
  'noch.kept': 'ინახება {n} დღის განმავლობაში.',

  'noch.days.title': 'ბოლო 31 დღე',
  'noch.days.hint': 'ფერი დღის ყველაზე ცუდი სტატუსია. ნაცრისფერი: არაფერი შენახულა.',
  'noch.day.tip': '{day} — {samples} ჩანაწერი · {crit} კრიტიკული · {warn} გაფრთხილება',
  'noch.day.none': '{day} — არაფერი შენახულა',
  'noch.day.empty': 'ამ დღისთვის არაფერი შენახულა.',

  'noch.tl.title': 'დროის ხაზი',
  'noch.tl.hint': 'ერთი უჯრა 5 წუთია და მასში შენახული ყველაზე ცუდი სტატუსის ფერისაა. დააჭირეთ უჯრას, რომ ნახოთ მთელი დაფა იმ მომენტში.',
  'noch.tl.cell': '{time} — {status}',
  'noch.tl.cell.none': '{time} — არაფერი შენახულა',
  'noch.tl.prev': '‹ 5 წთ',
  'noch.tl.next': '5 წთ ›',
  'noch.tl.prev.label': 'წინა 5 წუთი',
  'noch.tl.next.label': 'შემდეგი 5 წუთი',
  'noch.tl.sel': 'არჩეულია: {time}',
  'noch.tl.none': 'მომენტი არჩეული არ არის',
  'noch.tl.now': 'ახლა',

  'noch.inc.title': 'ინციდენტები',
  'noch.inc.none': 'ამ დღეს არც გაფრთხილება და არც ავარია არ შენახულა.',
  'noch.inc.more': '+{n} კიდევ',

  'noch.snap.note': 'მდგომარეობა: {when}',
  'noch.snap.live': 'პირდაპირ ეთერში დაბრუნება',
  'noch.snap.pick': 'აირჩიეთ მომენტი დროის ხაზზე ან ინციდენტი, რომ ნახოთ მთელი დაფა იმ დროისთვის.',
  'noch.snap.none': 'ამ დროსთან ახლოს მდგომარეობა არ შენახულა.',

  'noch.c.title': 'ეს დღე',
  'noch.c.cpu': 'პროცესორი',
  'noch.c.mem': 'მეხსიერება',
  'noch.c.req': 'მოთხოვნები წუთში',
  'noch.c.5xx': 'სერვერის შეცდომები (5xx)',
  'noch.c.db': 'ბაზის დაყოვნება',
  'noch.c.ai': 'AI-ის წარუმატებელი გამოძახებები',
  'noch.s.mem': 'მეხსიერება %',
  'noch.s.net': 'მიღებული + გაგზავნილი',
  'noch.s.5xx': '5xx %',
  'noch.s.db': 'დაყოვნება',
};

export const ru: Dict = {
  'noch.date': 'День',
  'noch.today': 'Сегодня',
  'noch.yesterday': 'Вчера',
  'noch.kept': 'Хранится {n} дн.',

  'noch.days.title': 'Последние 31 день',
  'noch.days.hint': 'Цвет — худший статус за день. Серый: ничего не сохранено.',
  'noch.day.tip': '{day} — записей: {samples} · критических: {crit} · предупреждений: {warn}',
  'noch.day.none': '{day} — ничего не сохранено',
  'noch.day.empty': 'За этот день ничего не сохранено.',

  'noch.tl.title': 'Шкала времени',
  'noch.tl.hint': 'Одна ячейка — 5 минут, цвет — худший статус, сохранённый в ней. Нажмите на ячейку, чтобы увидеть всю панель на тот момент.',
  'noch.tl.cell': '{time} — {status}',
  'noch.tl.cell.none': '{time} — ничего не сохранено',
  'noch.tl.prev': '‹ 5 мин',
  'noch.tl.next': '5 мин ›',
  'noch.tl.prev.label': 'Предыдущие 5 минут',
  'noch.tl.next.label': 'Следующие 5 минут',
  'noch.tl.sel': 'Выбрано: {time}',
  'noch.tl.none': 'Момент не выбран',
  'noch.tl.now': 'сейчас',

  'noch.inc.title': 'Инциденты',
  'noch.inc.none': 'В этот день не сохранено ни предупреждений, ни аварий.',
  'noch.inc.more': 'ещё {n}',

  'noch.snap.note': 'Снимок на {when}',
  'noch.snap.live': 'Вернуться к живым данным',
  'noch.snap.pick': 'Выберите момент на шкале или инцидент, чтобы увидеть всю панель на тот момент.',
  'noch.snap.none': 'Рядом с этим временем снимок не сохранён.',

  'noch.c.title': 'Этот день',
  'noch.c.cpu': 'Процессор',
  'noch.c.mem': 'Память',
  'noch.c.req': 'Запросов в минуту',
  'noch.c.5xx': 'Ошибки сервера (5xx)',
  'noch.c.db': 'Задержка базы данных',
  'noch.c.ai': 'Неудачные вызовы AI',
  'noch.s.mem': 'память %',
  'noch.s.net': 'получено + отправлено',
  'noch.s.5xx': '5xx %',
  'noch.s.db': 'задержка',
};
