/* The superadmin console (adm.*, pb.*, cred.*, kill.*, v.*) — `admin.html` → `/console`.

   Five prefixes, one page, one owner. They are grouped here rather than in features/ because
   nothing outside the console renders them — each is an operator-only surface:
     adm.*   integrations, models and the anonymous allowance
     pb.*    the platform tabs — registered accounts, storage retention, the default rubric
             and the default bot config, plus the per-account limit and password dialogs
     cred.*  the chat-service integration credentials (`cqi_…`) and their per-tenant grants
     kill.*  the autopilot kill switch, global and per tenant — the operator's brake, which
             is independent of a tenant's own `autopilot_enabled`
     v.*     the ElevenLabs voice allowlist

   Two of these are shown exactly once and never again — `pb.pw.once` says so, because only
   the hash is stored — so their wording is the only warning a operator gets before closing
   the dialog. */

import type { Dict } from '../index';

export const en: Dict = {
  'adm.testnote': 'Each capability is probed for real — the connection test spends a fraction of a second of speech-to-text and a few text-to-speech characters, because ElevenLabs offers no way to read a key’s permissions. “Deep” additionally exercises the fact-check and scoring tools.',
  'adm.tenants': 'Tenants',
  'adm.embeddings': 'Embeddings',
  'adm.anon': 'Anonymous limits',
  'adm.integrations': 'Integrations',
  'adm.createtenant': 'Create tenant',
  'adm.embprov': 'Embeddings provider',
  'adm.embnote': 'Changing the dimension requires re-embedding the KB (documents must be re-imported).',
  'adm.anonheading': 'Anonymous (no-login) user limits',
  'adm.allowanon': 'Allow anonymous users',
  'adm.maxanalyses': 'Max analyses / day',
  'adm.maxmb': 'Max audio MB',
  'adm.maxtts': 'Max TTS / day',
  'adm.features': 'Features allowed',
  'adm.intkeys': 'Integration keys',
  'adm.models': 'Models & voice',
  'adm.instructions': 'Analysis instructions',
  'adm.voices': 'Voices',
  'adm.voicevis': 'Customer-visible voices',

  'v.hint': 'Unticked voices are hidden from the customer voice list and rejected by the TTS API. Leave the box unticked to show every voice. System defaults (incl. the Georgian voice) are always on.',
  'v.search': 'Search voices…',
  'v.selected': 'selected',
  'v.system': 'System default',
  'v.nopreview': 'No preview',
  'v.unavailable': 'Not in this ElevenLabs account',
  'v.pickone': 'Select at least one voice, or untick the restriction.',
  'v.loadfail': 'Could not load voices from ElevenLabs. Check the API key in Integrations.',

  'pb.defbot': 'Default bot',
  'pb.defbot.heading': 'Default bot settings',
  'pb.defbot.desc': 'Inherited by every workspace that has not saved bot settings of its own. A workspace that saves its own copy keeps it; later changes here do not reach them.',
  'pb.defbot.saved': 'Default bot settings saved',
  'pb.defbot.updated': 'Updated {when} by {who}',
  'pb.defbot.source.stored': 'Edited by you',
  'pb.defbot.source.builtin': 'Built-in defaults',

  'adm.bot': 'Bot control',

  'kill.heading': 'Autopilot kill switch',

  'cred.heading': 'Chat connections',
  'cred.lead': 'One connection is one chat service. It may act only for the workspaces granted here, and its key is shown once — at creation and at rotation — then never again.',
  'cred.new': 'New connection',
  'cred.create': 'Create connection',
  'cred.created': 'Connection created',
  'cred.empty': 'No chat connections yet. Create one to let a chat service talk to this server.',
  'cred.loadfail': 'Could not load the chat connections.',
  'cred.unavailable': 'Chat connections are not available on this server yet.',
  'cred.scopes': 'Scopes',
  'cred.workspaces': 'Workspaces',
  'cred.keys': 'Keys',

  'cred.state.on': 'Active',
  'cred.state.off': 'Deactivated',

  'cred.scope.turn': 'ingest customer messages for operator drafts',
  'cred.scope.suggest': 'read drafts and stream them',
  'cred.scope.answer': 'the public autopilot',
  'cred.scope.sync': 'mirror history and GDPR purge',

  'cred.name.ph': 'e.g. Intercom bridge',
  'cred.name.required': 'Give the connection a name.',

  'cred.scopes.required': 'Pick at least one scope.',

  'cred.workspaces.hint': 'Only active workspaces are listed; grants can be added or removed later.',
  'cred.workspaces.none': 'There is no active workspace to grant.',

  'cred.reveal.title': 'Connection key',
  'cred.reveal.once': 'This key is shown once. Copy it now — it cannot be read back later, only rotated.',
  'cred.reveal.overlap': 'The previous key keeps working for {days} more days, then stops.',
  'cred.reveal.headers': 'Headers the chat backend sends on every request:',
  'cred.reveal.serverside': 'Keep the key server-side only — never in a browser, an app bundle or a repository.',

  'cred.snippet.tenant': 'workspace client_id',
  'cred.snippet.same': 'the same client_id',

  'cred.copyfail': 'Copy failed — select the key and copy it by hand.',

  'cred.rotate.confirm': 'Rotate the key of “{name}”? The old key keeps working for {days} days, then stops. The new key is shown once.',

  'cred.rotated': 'Key rotated',
  'cred.deactivate': 'Deactivate',
  'cred.deactivated': 'Connection deactivated',

  'cred.deactivate.confirm': 'Deactivate “{name}”? Every key of this connection stops working immediately, for every workspace. The record is kept for the audit trail.',

  'cred.grant.add': 'Add workspace',
  'cred.grant.title': 'Grant a workspace to “{name}”',
  'cred.grant.pick': 'Workspace',
  'cred.grant.submit': 'Grant',
  'cred.grant.allgranted': 'Every active workspace is already granted to this connection.',
  'cred.grant.added': 'Workspace granted',
  'cred.grant.remove': 'Remove grant',
  'cred.grant.removed': 'Workspace grant removed',
  'cred.grant.remove.confirm': 'Remove “{ws}” from “{name}”? The connection can no longer act for that workspace.',
  'cred.grant.none': 'No workspaces yet',

  'cred.keys.none': 'No keys',

  'cred.key.created': 'created',
  'cred.key.lastused': 'last used',
  'cred.key.revoked': 'Revoked',
  'cred.key.expired': 'Expired',
  'cred.key.expires': 'Expires {when}',

  'kill.desc': 'The brake. It stops the public bot from answering; conversations hand off to humans instead. Tenant settings are untouched, so resuming is one click.',
  'kill.global': 'Stop autopilot for every tenant',
  'kill.global.on': 'Stopped everywhere',
  'kill.global.off': 'Running normally',

  'kill.tenants': 'Per tenant',
  'kill.stop': 'Stop',
  'kill.resume': 'Resume',

  'kill.confirm.global': 'Stop autopilot for every tenant? Every bot hands off to a human until you resume.',
  'kill.confirm.resume.global': 'Resume autopilot for every tenant that has it enabled?',
  'kill.confirm.tenant': 'Stop autopilot for “{name}”?',
  'kill.confirm.resume': 'Resume autopilot for “{name}”?',

  'kill.state.live': 'Live',
  'kill.state.stopped': 'Stopped',
  'kill.state.off': 'Autopilot off',

  'kill.saved': 'Kill switch updated',
  'kill.loadfail': 'Could not read the kill switch.',
  'kill.unavailable': 'The kill switch is not deployed on this server yet.',
  'kill.overviewfail': 'Could not read the per-tenant autopilot state.',

  'adm.retention': 'Keep anonymous data (days)',
  'adm.retention.hint': 'How long an unregistered visitor’s IP, audio and text are kept before the worker deletes them. 0 keeps them indefinitely.',

  'adm.sentiment.heading': 'Public sentiment analysis',

  'adm.deltenant.confirm': 'Delete “{name}”? This permanently removes the organization, all of its users, its knowledge base and its call history. This cannot be undone.',

  'adm.rotate.confirm': 'Generate a new API key for this organization? The current key stops working immediately — any integration using it must be updated.',
  'adm.rotate.done': 'New API key generated',

  'adm.rmuser.confirm': 'Remove user “{u}”? They lose access immediately.',

  'adm.user.newpw': 'New password (leave empty to keep)',
  'adm.user.saved': 'User updated',

  'pb.nav.account': 'My account',

  'pb.users': 'Users',
  'pb.storage': 'Storage',
  'pb.defrubric': 'Default rubric',

  'pb.reg.heading': 'Registered accounts — daily limits',
  'pb.reg.desc': 'Applies to every self-service account that has no override of its own.',
  'pb.reg.signups': 'Sign-ups open',
  'pb.reg.signups.hint': 'Turning this off closes the public sign-up form. Existing accounts keep working — deactivate one from the table below.',
  'pb.reg.maxconv': 'Max conversions / day',

  'pb.feat.convert': 'Audio conversion',
  'pb.feat.summarise': 'Summarise',
  'pb.feat.score': 'Scoring',
  'pb.feat.semantic': 'Sentiment analysis',

  'pb.users.heading': 'Accounts',
  'pb.users.search': 'Search email or name',
  'pb.users.none': 'No registered accounts yet.',
  'pb.users.nomatch': 'No account matches that search.',
  'pb.users.legend': '“Today” counts analyses · TTS clips · conversions used since midnight.',

  'pb.th.email': 'Email',
  'pb.th.name': 'Name',
  'pb.th.created': 'Created',
  'pb.th.lastlogin': 'Last login',
  'pb.th.today': 'Today',

  'pb.never': 'Never',

  'pb.act.activate': 'Activate',
  'pb.act.deactivate': 'Deactivate',
  'pb.act.limits': 'Limits',
  'pb.act.resetpw': 'Reset password',

  'pb.user.saved': 'Account updated',

  'pb.lim.title': 'Per-account limits',
  'pb.lim.note': 'An empty field means this account uses the tier’s number. Saving replaces every override this account has.',
  'pb.lim.saved': 'Limits saved',

  'pb.pw.title': 'New password',
  'pb.pw.once': 'Shown once. Only its hash is stored, so it cannot be shown again — pass it to the user now.',
  'pb.pw.confirm': 'Generate a new password for {email}? Their current password stops working immediately.',

  'pb.copy': 'Copy',
  'pb.copied': 'Copied',
  'pb.copyfail': 'Copy failed — select the password and copy it by hand.',
  'pb.close': 'Close',

  'pb.del.confirm': 'Delete the account {email}? Their recordings, summaries and TTS clips are NOT deleted — those stay until the storage retention period removes the files.',
  'pb.del.done': 'Account deleted. Their recordings stay until the storage retention removes the files.',

  'pb.storage.heading': 'Stored recordings',
  'pb.storage.desc': 'One retention period for every stored recording and TTS clip — anonymous visitors, tenants and registered accounts alike.',
  'pb.storage.days': 'Keep recordings for (days)',
  'pb.storage.hint': '0 keeps them indefinitely. An anonymous visitor’s row is stripped in full; a tenant’s or an account’s row keeps its transcript and results and loses only the audio file.',
  'pb.storage.moved': 'Retention moved to the Storage tab: one number now covers every stored recording, not only anonymous ones.',

  'pb.defrubric.heading': 'Default rubric',
  'pb.defrubric.desc': 'Scored against by every tenant and registered account that has not saved a rubric of its own.',

  'pb.src.stored': 'Saved by an operator',
  'pb.src.demo': 'Seeded from the demo tenant — not saved yet',
  'pb.src.builtin': 'Built-in starter — not saved yet',

  'pb.defrubric.updated': 'Updated {when} by {who}',
  'pb.defrubric.saved': 'Default rubric saved',
};

export const ka: Dict = {
  'adm.testnote': 'თითოეული შესაძლებლობა რეალურად მოწმდება — ტესტი ხარჯავს წამის მცირე ნაწილს მეტყველების ამოცნობაზე და რამდენიმე სიმბოლოს ხმის სინთეზზე, რადგან ElevenLabs არ იძლევა გასაღების ნებართვების წაკითხვის საშუალებას. „ღრმა“ დამატებით ამოწმებს ფაქტების შემოწმებისა და შეფასების ხელსაწყოებს.',
  'adm.tenants': 'ორგანიზაციები',
  'adm.embeddings': 'ემბედინგები',
  'adm.anon': 'ანონიმური ლიმიტები',
  'adm.integrations': 'ინტეგრაციები',
  'adm.createtenant': 'ორგანიზაციის შექმნა',
  'adm.embprov': 'ემბედინგების პროვაიდერი',
  'adm.embnote': 'განზომილების შეცვლა მოითხოვს ცოდნის ბაზის ხელახალ ემბედინგს (საჭიროა დოკუმენტების ხელახალი იმპორტი).',
  'adm.anonheading': 'ანონიმური მომხმარებლების (ავტორიზაციის გარეშე) ლიმიტები',
  'adm.allowanon': 'ანონიმური მომხმარებლების დაშვება',
  'adm.maxanalyses': 'მაქს. ანალიზი / დღე',
  'adm.maxmb': 'მაქს. აუდიო MB',
  'adm.maxtts': 'მაქს. TTS / დღე',
  'adm.features': 'დაშვებული ფუნქციები',
  'adm.intkeys': 'ინტეგრაციის გასაღებები',
  'adm.models': 'მოდელები და ხმა',
  'adm.instructions': 'ანალიზის ინსტრუქციები',
  'adm.voices': 'ხმები',
  'adm.voicevis': 'მომხმარებლისთვის ხილული ხმები',

  'v.hint': 'მოუნიშნავი ხმები იმალება მომხმარებლის სიიდან და TTS API მათ უარყოფს. თუ ველი მოუნიშნავია, ყველა ხმა ჩანს. სისტემური ნაგულისხმევი ხმები (მათ შორის ქართული) ყოველთვის ჩართულია.',
  'v.search': 'ხმების ძებნა…',
  'v.selected': 'მონიშნული',
  'v.system': 'სისტემური',
  'v.nopreview': 'ნიმუში არ არის',
  'v.unavailable': 'ამ ElevenLabs ანგარიშში არ არის',
  'v.pickone': 'მონიშნეთ მინიმუმ ერთი ხმა ან მოხსენით შეზღუდვა.',
  'v.loadfail': 'ხმების ჩატვირთვა ElevenLabs-იდან ვერ მოხერხდა. შეამოწმეთ API გასაღები ინტეგრაციებში.',

  'pb.defbot': 'ნაგულისხმევი ბოტი',
  'pb.defbot.heading': 'ბოტის ნაგულისხმევი პარამეტრები',
  'pb.defbot.desc': 'მემკვიდრეობით იღებს ყველა სამუშაო სივრცე, რომელსაც საკუთარი ბოტის პარამეტრები არ შეუნახავს. ვისაც საკუთარი ასლი აქვს, ინარჩუნებს; აქ შეტანილი ცვლილებები მათ არ ეხება.',
  'pb.defbot.saved': 'ბოტის ნაგულისხმევი პარამეტრები შენახულია',
  'pb.defbot.updated': 'განახლდა {when}, {who}',
  'pb.defbot.source.stored': 'თქვენი რედაქტირებული',
  'pb.defbot.source.builtin': 'ჩაშენებული ნაგულისხმევი',

  'adm.bot': 'ბოტის მართვა',

  'kill.heading': 'ავტოპილოტის ავარიული გამორთვა',

  'cred.heading': 'ჩატის კავშირები',
  'cred.lead': 'ერთი კავშირი ერთი ჩატის სერვისია. ის მხოლოდ იმ სამუშაო სივრცეების სახელით მოქმედებს, რომლებზეც აქ წვდომა მიეცა, ხოლო მისი გასაღები მხოლოდ ერთხელ ჩანს — შექმნისას და როტაციისას — და მეტჯერ არასდროს.',
  'cred.new': 'ახალი კავშირი',
  'cred.create': 'კავშირის შექმნა',
  'cred.created': 'კავშირი შეიქმნა',
  'cred.empty': 'ჩატის კავშირები ჯერ არ არის. შექმენით ერთი, რომ ჩატის სერვისმა ამ სერვერთან მუშაობა შეძლოს.',
  'cred.loadfail': 'ჩატის კავშირების ჩატვირთვა ვერ მოხერხდა.',
  'cred.unavailable': 'ჩატის კავშირები ამ სერვერზე ჯერ ხელმისაწვდომი არ არის.',
  'cred.scopes': 'უფლებები',
  'cred.workspaces': 'სამუშაო სივრცეები',
  'cred.keys': 'გასაღებები',

  'cred.state.on': 'აქტიური',
  'cred.state.off': 'გამორთული',

  'cred.scope.turn': 'მომხმარებლის შეტყობინებების მიღება ოპერატორის მონახაზებისთვის',
  'cred.scope.suggest': 'მონახაზების წაკითხვა და ნაკადით მიწოდება',
  'cred.scope.answer': 'საჯარო ავტოპილოტი',
  'cred.scope.sync': 'ისტორიის სარკისებური ასლი და GDPR-ის მიხედვით წაშლა',

  'cred.name.ph': 'მაგ. Intercom-ის ხიდი',
  'cred.name.required': 'მიუთითეთ კავშირის სახელი.',

  'cred.scopes.required': 'აირჩიეთ მინიმუმ ერთი უფლება.',

  'cred.workspaces.hint': 'ჩამოთვლილია მხოლოდ აქტიური სამუშაო სივრცეები; წვდომის დამატება ან მოხსნა მოგვიანებითაც შეიძლება.',
  'cred.workspaces.none': 'აქტიური სამუშაო სივრცე, რომელსაც წვდომა მიეცემა, არ არის.',

  'cred.reveal.title': 'კავშირის გასაღები',
  'cred.reveal.once': 'ეს გასაღები მხოლოდ ერთხელ ჩანს. დააკოპირეთ ახლავე — მოგვიანებით მისი წაკითხვა შეუძლებელია, მხოლოდ როტაცია.',
  'cred.reveal.overlap': 'წინა გასაღები კიდევ {days} დღე მუშაობს, შემდეგ წყდება.',
  'cred.reveal.headers': 'სათაურები (headers), რომლებსაც ჩატის სერვერი ყოველ მოთხოვნაზე აგზავნის:',
  'cred.reveal.serverside': 'გასაღები მხოლოდ სერვერის მხარეს შეინახეთ — არასდროს ბრაუზერში, აპლიკაციის პაკეტში ან რეპოზიტორიაში.',

  'cred.snippet.tenant': 'სამუშაო სივრცის client_id',
  'cred.snippet.same': 'იგივე client_id',

  'cred.copyfail': 'კოპირება ვერ მოხერხდა — მონიშნეთ გასაღები და ხელით დააკოპირეთ.',

  'cred.rotate.confirm': 'გავაკეთო „{name}“-ის გასაღების როტაცია? ძველი გასაღები კიდევ {days} დღე მუშაობს, შემდეგ წყდება. ახალი გასაღები მხოლოდ ერთხელ გამოჩნდება.',

  'cred.rotated': 'გასაღების როტაცია შესრულდა',
  'cred.deactivate': 'გამორთვა',
  'cred.deactivated': 'კავშირი გამორთულია',

  'cred.deactivate.confirm': 'გამოვრთო „{name}“? ამ კავშირის ყველა გასაღები დაუყოვნებლივ წყვეტს მუშაობას ყველა სამუშაო სივრცისთვის. ჩანაწერი აუდიტისთვის შენარჩუნდება.',

  'cred.grant.add': 'სამუშაო სივრცის დამატება',
  'cred.grant.title': 'წვდომის მიცემა „{name}“-ისთვის',
  'cred.grant.pick': 'სამუშაო სივრცე',
  'cred.grant.submit': 'წვდომის მიცემა',
  'cred.grant.allgranted': 'ყველა აქტიურ სამუშაო სივრცეს ამ კავშირზე წვდომა უკვე აქვს.',
  'cred.grant.added': 'სამუშაო სივრცეს წვდომა მიეცა',
  'cred.grant.remove': 'წვდომის მოხსნა',
  'cred.grant.removed': 'სამუშაო სივრცის წვდომა მოიხსნა',
  'cred.grant.remove.confirm': 'მოვხსნა „{ws}“ „{name}“-იდან? კავშირი ამ სამუშაო სივრცის სახელით მოქმედებას ვეღარ შეძლებს.',
  'cred.grant.none': 'სამუშაო სივრცეები ჯერ არ არის',

  'cred.keys.none': 'გასაღებები არ არის',

  'cred.key.created': 'შეიქმნა',
  'cred.key.lastused': 'ბოლოს გამოყენებული',
  'cred.key.revoked': 'გაუქმებული',
  'cred.key.expired': 'ვადაგასული',
  'cred.key.expires': 'ვადა იწურება {when}',

  'kill.desc': 'ეს მუხრუჭია: აჩერებს საჯარო ბოტის პასუხებს და საუბრები ადამიანებს გადაეცემა. ორგანიზაციის პარამეტრები ხელუხლებელი რჩება, ამიტომ აღდგენა ერთი დაწკაპუნებით ხდება.',
  'kill.global': 'ავტოპილოტის შეჩერება ყველა ორგანიზაციისთვის',
  'kill.global.on': 'შეჩერებულია ყველგან',
  'kill.global.off': 'მუშაობს ნორმალურად',

  'kill.tenants': 'ორგანიზაციების მიხედვით',
  'kill.stop': 'შეჩერება',
  'kill.resume': 'აღდგენა',

  'kill.confirm.global': 'შევაჩერო ავტოპილოტი ყველა ორგანიზაციისთვის? აღდგენამდე ყველა ბოტი საუბრებს ადამიანებს გადასცემს.',
  'kill.confirm.resume.global': 'აღვადგინო ავტოპილოტი ყველა ორგანიზაციისთვის, რომელსაც ის ჩართული აქვს?',
  'kill.confirm.tenant': 'შევაჩერო ავტოპილოტი „{name}“-სთვის?',
  'kill.confirm.resume': 'აღვადგინო ავტოპილოტი „{name}“-სთვის?',

  'kill.state.live': 'აქტიური',
  'kill.state.stopped': 'შეჩერებული',
  'kill.state.off': 'ავტოპილოტი გამორთულია',

  'kill.saved': 'ავარიული გამორთვა განახლდა',
  'kill.loadfail': 'ავარიული გამორთვის მდგომარეობის წაკითხვა ვერ მოხერხდა.',
  'kill.unavailable': 'ავარიული გამორთვა ამ სერვერზე ჯერ არ არის განთავსებული.',
  'kill.overviewfail': 'ორგანიზაციების ავტოპილოტის მდგომარეობის წაკითხვა ვერ მოხერხდა.',

  'adm.retention': 'ანონიმური მონაცემების შენახვა (დღე)',
  'adm.retention.hint': 'რამდენ ხანს ინახება არარეგისტრირებული მომხმარებლის IP, აუდიო და ტექსტი, სანამ წაიშლება. 0 — უვადოდ.',

  'adm.sentiment.heading': 'საჯარო განწყობის ანალიზი',

  'adm.deltenant.confirm': 'წაიშალოს „{name}“? ეს სამუდამოდ შლის ორგანიზაციას, მის ყველა მომხმარებელს, ცოდნის ბაზასა და ზარების ისტორიას. ამის დაბრუნება შეუძლებელია.',

  'adm.rotate.confirm': 'შეიქმნას ახალი API გასაღები ამ ორგანიზაციისთვის? მიმდინარე გასაღები მაშინვე გაითიშება — ყველა ინტეგრაცია, რომელიც მას იყენებს, უნდა განახლდეს.',
  'adm.rotate.done': 'ახალი API გასაღები შეიქმნა',

  'adm.rmuser.confirm': 'წაიშალოს მომხმარებელი „{u}“? წვდომა მაშინვე გაუუქმდება.',

  'adm.user.newpw': 'ახალი პაროლი (ცარიელი — უცვლელი)',
  'adm.user.saved': 'მომხმარებელი განახლდა',

  'pb.nav.account': 'ჩემი ანგარიში',

  'pb.users': 'მომხმარებლები',
  'pb.storage': 'შენახვა',
  'pb.defrubric': 'ნაგულისხმევი რუბრიკა',

  'pb.reg.heading': 'რეგისტრირებული ანგარიშები — დღიური ლიმიტები',
  'pb.reg.desc': 'ვრცელდება ყველა თვითრეგისტრირებულ ანგარიშზე, რომელსაც საკუთარი გამონაკლისი არ აქვს.',
  'pb.reg.signups': 'რეგისტრაცია ღიაა',
  'pb.reg.signups.hint': 'გამორთვა ხურავს საჯარო რეგისტრაციის ფორმას. არსებული ანგარიშები აგრძელებენ მუშაობას — კონკრეტული ანგარიში ქვემოთ, ცხრილში გამორთეთ.',
  'pb.reg.maxconv': 'მაქს. კონვერტაცია / დღეში',

  'pb.feat.convert': 'აუდიოს კონვერტაცია',
  'pb.feat.summarise': 'შეჯამება',
  'pb.feat.score': 'შეფასება',
  'pb.feat.semantic': 'სენტიმენტის ანალიზი',

  'pb.users.heading': 'ანგარიშები',
  'pb.users.search': 'ძებნა ელფოსტით ან სახელით',
  'pb.users.none': 'რეგისტრირებული ანგარიშები ჯერ არ არის.',
  'pb.users.nomatch': 'ამ ძებნას ანგარიში არ ემთხვევა.',
  'pb.users.legend': '„დღეს“ ითვლის ანალიზებს · TTS ჩანაწერებს · კონვერტაციებს შუაღამიდან.',

  'pb.th.email': 'ელფოსტა',
  'pb.th.name': 'სახელი',
  'pb.th.created': 'შექმნის თარიღი',
  'pb.th.lastlogin': 'ბოლო შესვლა',
  'pb.th.today': 'დღეს',

  'pb.never': 'არასდროს',

  'pb.act.activate': 'ჩართვა',
  'pb.act.deactivate': 'გამორთვა',
  'pb.act.limits': 'ლიმიტები',
  'pb.act.resetpw': 'პაროლის განულება',

  'pb.user.saved': 'ანგარიში განახლდა',

  'pb.lim.title': 'ანგარიშის ინდივიდუალური ლიმიტები',
  'pb.lim.note': 'ცარიელი ველი ნიშნავს, რომ ანგარიში საერთო ტარიფის რიცხვს იყენებს. შენახვა ჩაანაცვლებს ამ ანგარიშის ყველა გამონაკლისს.',
  'pb.lim.saved': 'ლიმიტები შენახულია',

  'pb.pw.title': 'ახალი პაროლი',
  'pb.pw.once': 'ჩანს მხოლოდ ერთხელ. ინახება მხოლოდ მისი ჰეში, ამიტომ ხელახლა ვერ გამოჩნდება — გადაეცით მომხმარებელს ახლავე.',
  'pb.pw.confirm': 'შევქმნათ ახალი პაროლი {email}-სთვის? მისი მიმდინარე პაროლი მაშინვე შეწყვეტს მუშაობას.',

  'pb.copy': 'კოპირება',
  'pb.copied': 'დაკოპირდა',
  'pb.copyfail': 'კოპირება ვერ მოხერხდა — მონიშნეთ პაროლი და ხელით დააკოპირეთ.',
  'pb.close': 'დახურვა',

  'pb.del.confirm': 'წავშალოთ ანგარიში {email}? მისი ჩანაწერები, შეჯამებები და TTS ფაილები არ წაიშლება — ისინი დარჩება, სანამ შენახვის ვადა არ წაშლის ფაილებს.',
  'pb.del.done': 'ანგარიში წაიშალა. მისი ჩანაწერები დარჩება, სანამ შენახვის ვადა არ წაშლის ფაილებს.',

  'pb.storage.heading': 'შენახული ჩანაწერები',
  'pb.storage.desc': 'ერთი შენახვის ვადა ყველა შენახული ჩანაწერისა და TTS ფაილისთვის — ანონიმური ვიზიტორების, ტენანტებისა და რეგისტრირებული ანგარიშებისთვის ერთნაირად.',
  'pb.storage.days': 'ჩანაწერების შენახვა (დღე)',
  'pb.storage.hint': '0 ნიშნავს უვადოდ შენახვას. ანონიმური ვიზიტორის ჩანაწერი მთლიანად იშლება; ტენანტისა და ანგარიშის ჩანაწერს რჩება ტრანსკრიპტი და შედეგები, კარგავს მხოლოდ აუდიოფაილს.',
  'pb.storage.moved': 'შენახვის ვადა გადავიდა ჩანართში „შენახვა“: ერთი რიცხვი ახლა ყველა შენახულ ჩანაწერზე ვრცელდება, არა მხოლოდ ანონიმურზე.',

  'pb.defrubric.heading': 'ნაგულისხმევი რუბრიკა',
  'pb.defrubric.desc': 'ამით ფასდება ყველა ტენანტი და რეგისტრირებული ანგარიში, რომელსაც საკუთარი რუბრიკა არ შეუნახავს.',

  'pb.src.stored': 'ოპერატორის მიერ შენახული',
  'pb.src.demo': 'აღებულია demo ტენანტიდან — ჯერ არ არის შენახული',
  'pb.src.builtin': 'ჩაშენებული საწყისი — ჯერ არ არის შენახული',

  'pb.defrubric.updated': 'განახლდა {when}, ავტორი: {who}',
  'pb.defrubric.saved': 'ნაგულისხმევი რუბრიკა შენახულია',
};

export const ru: Dict = {
  'adm.testnote': 'Каждая возможность проверяется по-настоящему — тест расходует доли секунды распознавания речи и несколько символов синтеза, поскольку ElevenLabs не позволяет прочитать разрешения ключа. «Глубокая» проверка дополнительно задействует инструменты проверки фактов и оценки.',
  'adm.tenants': 'Организации',
  'adm.embeddings': 'Эмбеддинги',
  'adm.anon': 'Лимиты для анонимных пользователей',
  'adm.integrations': 'Интеграции',
  'adm.createtenant': 'Создать организацию',
  'adm.embprov': 'Провайдер эмбеддингов',
  'adm.embnote': 'Изменение размерности требует переэмбеддинга базы знаний (документы нужно импортировать заново).',
  'adm.anonheading': 'Лимиты анонимных пользователей',
  'adm.allowanon': 'Разрешить анонимных пользователей',
  'adm.maxanalyses': 'Макс. анализов / день',
  'adm.maxmb': 'Макс. аудио МБ',
  'adm.maxtts': 'Макс. TTS / день',
  'adm.features': 'Разрешённые функции',
  'adm.intkeys': 'Ключи интеграций',
  'adm.models': 'Модели и голос',
  'adm.instructions': 'Инструкции анализа',
  'adm.voices': 'Голоса',
  'adm.voicevis': 'Голоса, видимые клиентам',

  'v.hint': 'Неотмеченные голоса скрыты из списка для клиентов и отклоняются TTS. Оставьте флажок снятым, чтобы показывать все голоса. Системные (включая грузинский) всегда включены.',
  'v.search': 'Поиск голосов…',
  'v.selected': 'выбрано',
  'v.system': 'Системный',
  'v.nopreview': 'Нет образца',
  'v.unavailable': 'Нет в этом аккаунте ElevenLabs',
  'v.pickone': 'Выберите хотя бы один голос или снимите ограничение.',
  'v.loadfail': 'Не удалось загрузить голоса из ElevenLabs. Проверьте API-ключ в «Интеграциях».',

  'pb.defbot': 'Бот по умолчанию',
  'pb.defbot.heading': 'Настройки бота по умолчанию',
  'pb.defbot.desc': 'Наследуются каждой организацией, которая не сохранила собственные настройки бота. Организация со своей копией сохраняет её; изменения здесь до неё не доходят.',
  'pb.defbot.saved': 'Настройки бота по умолчанию сохранены',
  'pb.defbot.updated': 'Обновлено {when}, {who}',
  'pb.defbot.source.stored': 'Изменено вами',
  'pb.defbot.source.builtin': 'Встроенные значения',

  'adm.bot': 'Управление ботом',

  'kill.heading': 'Аварийное отключение автопилота',

  'cred.heading': 'Подключения чата',
  'cred.lead': 'Одно подключение — один чат-сервис. Он может действовать только от имени организаций, доступ к которым выдан здесь, а его ключ показывается один раз — при создании и при перевыпуске — и больше никогда.',
  'cred.new': 'Новое подключение',
  'cred.create': 'Создать подключение',
  'cred.created': 'Подключение создано',
  'cred.empty': 'Подключений чата пока нет. Создайте одно, чтобы чат-сервис мог обращаться к этому серверу.',
  'cred.loadfail': 'Не удалось загрузить подключения чата.',
  'cred.unavailable': 'Подключения чата пока недоступны на этом сервере.',
  'cred.scopes': 'Права',
  'cred.workspaces': 'Организации',
  'cred.keys': 'Ключи',

  'cred.state.on': 'Активно',
  'cred.state.off': 'Отключено',

  'cred.scope.turn': 'приём сообщений клиентов для черновиков оператора',
  'cred.scope.suggest': 'чтение черновиков и их потоковая передача',
  'cred.scope.answer': 'публичный автопилот',
  'cred.scope.sync': 'зеркалирование истории и удаление по GDPR',

  'cred.name.ph': 'напр. мост Intercom',
  'cred.name.required': 'Укажите название подключения.',

  'cred.scopes.required': 'Выберите хотя бы одно право.',

  'cred.workspaces.hint': 'Показаны только активные организации; доступ можно выдать или отозвать позже.',
  'cred.workspaces.none': 'Нет активных организаций, которым можно выдать доступ.',

  'cred.reveal.title': 'Ключ подключения',
  'cred.reveal.once': 'Этот ключ показывается один раз. Скопируйте его сейчас — позже его нельзя прочитать, только перевыпустить.',
  'cred.reveal.overlap': 'Предыдущий ключ работает ещё {days} дней, затем перестаёт.',
  'cred.reveal.headers': 'Заголовки, которые чат-сервер отправляет с каждым запросом:',
  'cred.reveal.serverside': 'Храните ключ только на сервере — никогда в браузере, сборке приложения или репозитории.',

  'cred.snippet.tenant': 'client_id организации',
  'cred.snippet.same': 'тот же client_id',

  'cred.copyfail': 'Не удалось скопировать — выделите ключ и скопируйте вручную.',

  'cred.rotate.confirm': 'Перевыпустить ключ подключения «{name}»? Старый ключ работает ещё {days} дней, затем перестаёт. Новый ключ показывается один раз.',

  'cred.rotated': 'Ключ перевыпущен',
  'cred.deactivate': 'Отключить',
  'cred.deactivated': 'Подключение отключено',

  'cred.deactivate.confirm': 'Отключить «{name}»? Все ключи этого подключения немедленно перестанут работать для всех организаций. Запись сохраняется для аудита.',

  'cred.grant.add': 'Добавить организацию',
  'cred.grant.title': 'Выдать доступ подключению «{name}»',
  'cred.grant.pick': 'Организация',
  'cred.grant.submit': 'Выдать доступ',
  'cred.grant.allgranted': 'Всем активным организациям доступ к этому подключению уже выдан.',
  'cred.grant.added': 'Доступ выдан',
  'cred.grant.remove': 'Отозвать доступ',
  'cred.grant.removed': 'Доступ отозван',
  'cred.grant.remove.confirm': 'Отозвать доступ «{ws}» у «{name}»? Подключение больше не сможет действовать от имени этой организации.',
  'cred.grant.none': 'Организаций пока нет',

  'cred.keys.none': 'Ключей нет',

  'cred.key.created': 'создан',
  'cred.key.lastused': 'последнее использование',
  'cred.key.revoked': 'Отозван',
  'cred.key.expired': 'Истёк',
  'cred.key.expires': 'Истекает {when}',

  'kill.desc': 'Тормоз. Останавливает ответы публичного бота; диалоги передаются людям. Настройки клиентов не меняются, поэтому возобновление — один клик.',
  'kill.global': 'Остановить автопилот для всех организаций',
  'kill.global.on': 'Остановлен везде',
  'kill.global.off': 'Работает нормально',

  'kill.tenants': 'По организациям',
  'kill.stop': 'Остановить',
  'kill.resume': 'Возобновить',

  'kill.confirm.global': 'Остановить автопилот для всех организаций? Все боты будут передавать диалоги людям до возобновления.',
  'kill.confirm.resume.global': 'Возобновить автопилот для всех организаций, у которых он включён?',
  'kill.confirm.tenant': 'Остановить автопилот для «{name}»?',
  'kill.confirm.resume': 'Возобновить автопилот для «{name}»?',

  'kill.state.live': 'Активен',
  'kill.state.stopped': 'Остановлен',
  'kill.state.off': 'Автопилот выключен',

  'kill.saved': 'Аварийный выключатель обновлён',
  'kill.loadfail': 'Не удалось прочитать состояние выключателя.',
  'kill.unavailable': 'Аварийный выключатель ещё не развёрнут на этом сервере.',
  'kill.overviewfail': 'Не удалось прочитать состояние автопилота по клиентам.',

  'adm.retention': 'Хранить анонимные данные (дней)',
  'adm.retention.hint': 'Сколько хранятся IP, аудио и текст незарегистрированного посетителя до удаления. 0 — бессрочно.',

  'adm.sentiment.heading': 'Публичный анализ тональности',

  'adm.deltenant.confirm': 'Удалить «{name}»? Это навсегда удалит организацию, всех её пользователей, базу знаний и историю звонков. Отменить это нельзя.',

  'adm.rotate.confirm': 'Выпустить новый API-ключ для этой организации? Текущий ключ сразу перестанет работать — все интеграции, использующие его, нужно обновить.',
  'adm.rotate.done': 'Новый API-ключ выпущен',

  'adm.rmuser.confirm': 'Удалить пользователя «{u}»? Доступ будет закрыт сразу.',

  'adm.user.newpw': 'Новый пароль (пусто — не менять)',
  'adm.user.saved': 'Пользователь обновлён',

  'pb.nav.account': 'Мой аккаунт',

  'pb.users': 'Пользователи',
  'pb.storage': 'Хранение',
  'pb.defrubric': 'Рубрика по умолчанию',

  'pb.reg.heading': 'Зарегистрированные аккаунты — дневные лимиты',
  'pb.reg.desc': 'Действуют для каждого самостоятельно созданного аккаунта, у которого нет персональных исключений.',
  'pb.reg.signups': 'Регистрация открыта',
  'pb.reg.signups.hint': 'Отключение закрывает публичную форму регистрации. Существующие аккаунты продолжают работать — отдельный аккаунт отключается в таблице ниже.',
  'pb.reg.maxconv': 'Макс. конвертаций / день',

  'pb.feat.convert': 'Конвертация аудио',
  'pb.feat.summarise': 'Резюме',
  'pb.feat.score': 'Оценка',
  'pb.feat.semantic': 'Семантический анализ',

  'pb.users.heading': 'Аккаунты',
  'pb.users.search': 'Поиск по почте или имени',
  'pb.users.none': 'Зарегистрированных аккаунтов пока нет.',
  'pb.users.nomatch': 'Ни один аккаунт не подходит под запрос.',
  'pb.users.legend': '«Сегодня» — анализы · клипы TTS · конвертации с полуночи.',

  'pb.th.email': 'Эл. почта',
  'pb.th.name': 'Имя',
  'pb.th.created': 'Создан',
  'pb.th.lastlogin': 'Последний вход',
  'pb.th.today': 'Сегодня',

  'pb.never': 'Никогда',

  'pb.act.activate': 'Включить',
  'pb.act.deactivate': 'Отключить',
  'pb.act.limits': 'Лимиты',
  'pb.act.resetpw': 'Сбросить пароль',

  'pb.user.saved': 'Аккаунт обновлён',

  'pb.lim.title': 'Лимиты аккаунта',
  'pb.lim.note': 'Пустое поле означает, что аккаунт использует значение тарифа. Сохранение заменяет все исключения этого аккаунта.',
  'pb.lim.saved': 'Лимиты сохранены',

  'pb.pw.title': 'Новый пароль',
  'pb.pw.once': 'Показывается один раз. Хранится только его хеш, поэтому повторно показать нельзя — передайте его пользователю сейчас.',
  'pb.pw.confirm': 'Создать новый пароль для {email}? Текущий пароль перестанет работать немедленно.',

  'pb.copy': 'Копировать',
  'pb.copied': 'Скопировано',
  'pb.copyfail': 'Не удалось скопировать — выделите пароль и скопируйте вручную.',
  'pb.close': 'Закрыть',

  'pb.del.confirm': 'Удалить аккаунт {email}? Его записи, резюме и клипы TTS НЕ удаляются — они останутся, пока срок хранения не удалит файлы.',
  'pb.del.done': 'Аккаунт удалён. Его записи останутся, пока срок хранения не удалит файлы.',

  'pb.storage.heading': 'Сохранённые записи',
  'pb.storage.desc': 'Один срок хранения для всех сохранённых записей и клипов TTS — и для анонимных посетителей, и для тенантов, и для зарегистрированных аккаунтов.',
  'pb.storage.days': 'Хранить записи (дней)',
  'pb.storage.hint': '0 — хранить бессрочно. Запись анонимного посетителя удаляется целиком; у тенанта и у аккаунта остаются расшифровка и результаты, теряется только аудиофайл.',
  'pb.storage.moved': 'Срок хранения перенесён во вкладку «Хранение»: одно число теперь охватывает все сохранённые записи, а не только анонимные.',

  'pb.defrubric.heading': 'Рубрика по умолчанию',
  'pb.defrubric.desc': 'По ней оцениваются все тенанты и зарегистрированные аккаунты, у которых нет собственной рубрики.',

  'pb.src.stored': 'Сохранена оператором',
  'pb.src.demo': 'Взята у тенанта demo — ещё не сохранена',
  'pb.src.builtin': 'Встроенная стартовая — ещё не сохранена',

  'pb.defrubric.updated': 'Обновлена {when}, автор: {who}',
  'pb.defrubric.saved': 'Рубрика по умолчанию сохранена',
};
