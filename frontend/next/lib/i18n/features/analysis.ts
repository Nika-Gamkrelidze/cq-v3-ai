/* Call analysis: the upload panel (an.*), the knowledge-base fact-check (fc.*) and the
   sentiment read (sn.*).

   fc.partial is NOT a synonym for fc.notinkb and the two must stay distinct in every
   language: collapsing them once told a reviewer the knowledge base had nothing to say about
   a claim it in fact partly contradicted. */

import type { Dict } from '../index';

export const en: Dict = {
  'an.heading': 'Upload a recording to analyze',
  'an.heading_kb': 'Analyze a call — uses your knowledge base',

  'fc.title': 'Knowledge base fact-check',
  'fc.accuracy': 'accuracy',
  'fc.supported': 'supported',
  'fc.contradicted': 'contradicted',
  'fc.notinkb': 'not in KB',
  'fc.partial': 'partly correct',
  'fc.misinfo': 'Possible misinformation',
  'fc.nochecked': 'No verifiable claims were found.',
  'fc.allclaims': 'All claims',

  'sn.title': 'Sentiment',
  'sn.text': 'What was said',
  'sn.voice': 'How it sounded',
  'sn.arousal': 'Energy',
  'sn.valence': 'Positivity',
  'sn.unavailable': 'Not available for this recording.',
  'sn.conflict': 'The words and the tone of voice disagree — worth listening to.',
  'sn.heading': 'How did the speaker sound?',
  'sn.run': 'Analyze sentiment',
  'sn.none': 'No sentiment could be determined for this recording.',
  'sn.config': 'Sentiment settings',
  'sn.enabled': 'Enable sentiment analysis',
  'sn.guidance': 'Guidance for the text judge (optional)',
  'sn.guidance.ph': 'e.g. Treat any complaint as at least mildly negative, even if phrased politely…',
  'sn.save': 'Save sentiment settings',
  'sn.saved': 'Saved',
  'sn.readonly': 'View only — only workspace owners can edit sentiment settings.',
  'sn.audiolabel': 'Audio to analyze',
  'sn.mode': 'Sentiment',
  'sn.disabled': 'Sentiment analysis is turned off for this workspace.',
  'sn.done': 'Sentiment ready',
};

export const ka: Dict = {
  'an.heading': 'ატვირთეთ ჩანაწერი ანალიზისთვის',
  'an.heading_kb': 'გააანალიზეთ ზარი — იყენებს თქვენს ცოდნის ბაზას',

  'fc.title': 'ფაქტების შემოწმება ცოდნის ბაზასთან',
  'fc.accuracy': 'სიზუსტე',
  'fc.supported': 'დადასტურებული',
  'fc.contradicted': 'უარყოფილი',
  'fc.notinkb': 'ბაზაში არ არის',
  'fc.partial': 'ნაწილობრივ სწორი',
  'fc.misinfo': 'შესაძლო მცდარი ინფორმაცია',
  'fc.nochecked': 'შესამოწმებელი მტკიცება ვერ მოიძებნა.',
  'fc.allclaims': 'ყველა მტკიცება',

  'sn.title': 'განწყობა',
  'sn.text': 'რა ითქვა',
  'sn.voice': 'როგორ ჟღერდა',
  'sn.arousal': 'ენერგია',
  'sn.valence': 'პოზიტიურობა',
  'sn.unavailable': 'ამ ჩანაწერისთვის მიუწვდომელია.',
  'sn.conflict': 'სიტყვები და ხმის ტონი არ ემთხვევა — ღირს მოსმენა.',
  'sn.heading': 'როგორ ჟღერდა მოსაუბრე?',
  'sn.run': 'განწყობის ანალიზი',
  'sn.none': 'ამ ჩანაწერისთვის განწყობის დადგენა ვერ მოხერხდა.',
  'sn.config': 'განწყობის პარამეტრები',
  'sn.enabled': 'განწყობის ანალიზის ჩართვა',
  'sn.guidance': 'მითითება ტექსტის შემფასებლისთვის (არასავალდებულო)',
  'sn.guidance.ph': 'მაგ. ნებისმიერი პრეტენზია ჩაითვალოს მინიმუმ ოდნავ ნეგატიურად, თუნდაც თავაზიანად იყოს ნათქვამი…',
  'sn.save': 'განწყობის პარამეტრების შენახვა',
  'sn.saved': 'შენახულია',
  'sn.readonly': 'მხოლოდ სანახავად — განწყობის პარამეტრების რედაქტირება მხოლოდ სამუშაო სივრცის მფლობელს შეუძლია.',
  'sn.audiolabel': 'გასაანალიზებელი აუდიო',
  'sn.mode': 'განწყობა',
  'sn.disabled': 'განწყობის ანალიზი გამორთულია ამ სამუშაო სივრცისთვის.',
  'sn.done': 'განწყობის შეფასება მზადაა',
};

export const ru: Dict = {
  'an.heading': 'Загрузите запись для анализа',
  'an.heading_kb': 'Анализ звонка — использует вашу базу знаний',

  'fc.title': 'Проверка по базе знаний',
  'fc.accuracy': 'точность',
  'fc.supported': 'подтверждено',
  'fc.contradicted': 'опровергнуто',
  'fc.notinkb': 'нет в базе',
  'fc.partial': 'частично верно',
  'fc.misinfo': 'Возможно, недостоверная информация',
  'fc.nochecked': 'Проверяемых утверждений не найдено.',
  'fc.allclaims': 'Все утверждения',

  'sn.title': 'Тональность',
  'sn.text': 'Что было сказано',
  'sn.voice': 'Как это прозвучало',
  'sn.arousal': 'Энергия',
  'sn.valence': 'Позитивность',
  'sn.unavailable': 'Недоступно для этой записи.',
  'sn.conflict': 'Слова и тон голоса расходятся — стоит послушать.',
  'sn.heading': 'Как звучал говорящий?',
  'sn.run': 'Анализировать тональность',
  'sn.none': 'Для этой записи не удалось определить тональность.',
  'sn.config': 'Настройки тональности',
  'sn.enabled': 'Включить анализ тональности',
  'sn.guidance': 'Указания для текстового анализатора (необязательно)',
  'sn.guidance.ph': 'напр. Любую жалобу считать как минимум слегка негативной, даже если она вежливо сформулирована…',
  'sn.save': 'Сохранить настройки тональности',
  'sn.saved': 'Сохранено',
  'sn.readonly': 'Только просмотр — настройки тональности может редактировать только владелец рабочего пространства.',
  'sn.audiolabel': 'Аудио для анализа',
  'sn.mode': 'Тональность',
  'sn.disabled': 'Анализ тональности отключён для этого рабочего пространства.',
  'sn.done': 'Оценка тональности готова',
};
