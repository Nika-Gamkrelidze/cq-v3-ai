/* CommuniQ shared UI: API, theme, i18n, header, toasts, confirm, custom <select>, audio player. */
const CQ = (() => {
  const API = (location.port === '' || location.port === '80')
    ? '/api' : `${location.protocol}//${location.hostname}:8000`;

  const LOGO = `<svg width="28" height="24" viewBox="0 0 40 34" fill="none" aria-hidden="true">
    <path d="M21 4.5 A12.5 12.5 0 1 0 21 29.5" stroke="currentColor" stroke-width="5.5" stroke-linecap="round" fill="none"/>
    <circle cx="29.5" cy="15" r="9.5" fill="#fa3b3c"/>
    <path d="M34 21 L40 28.5" stroke="#fa3b3c" stroke-width="5.5" stroke-linecap="round"/>
  </svg>`;

  /* ---------------- Theme ---------------- */
  function currentTheme() { return document.documentElement.getAttribute('data-theme') || 'dark'; }
  function toggleTheme() {
    const t = currentTheme() === 'light' ? 'dark' : 'light';
    document.documentElement.setAttribute('data-theme', t);
    try { localStorage.setItem('cq_theme', t); } catch {}
    document.querySelectorAll('[data-theme-btn]').forEach(b => b.textContent = t === 'light' ? '☾' : '☀');
  }

  /* ---------------- i18n ---------------- */
  const DICT = {
    en: {
      'nav.public':'Public app','nav.signin':'Sign in','nav.logout':'Log out','nav.kb':'Knowledge Base',
      'f.username':'Username','f.password':'Password','f.language':'Language','f.voice':'Voice','f.text':'Text',
      'f.category':'Category','f.title':'Title','f.tags':'Tags (comma-separated)','f.name':'Name','f.industry':'Industry','f.region':'Region',
      'f.audiofile':'Audio file','f.provider':'Provider','f.dimension':'Dimension','f.model':'Model','f.baseurl':'Base URL',
      'f.anthropic':'Anthropic (Claude) API key','f.eleven':'ElevenLabs API key','f.claudemodel':'Claude model','f.sttmodel':'Scribe (STT) model','f.ttsmodel':'TTS model','f.voiceid':'TTS voice ID','f.openaikey':'API key (openai only)',
      'btn.signin':'Sign in','btn.save':'Save','btn.savesettings':'Save settings','btn.savelimits':'Save limits','btn.refresh':'Refresh','btn.delete':'Delete','btn.cancel':'Cancel','btn.test':'Test','btn.testconn':'Test connections','btn.search':'Search','btn.import':'Import','btn.analyze':'Analyze','btn.synth':'Synthesize speech','btn.create':'Create tenant','btn.adduser':'Add user','btn.rotate':'Rotate','btn.remove':'Remove','btn.apikey':'API key','btn.users':'Users','btn.chunks':'Chunks',
      'hero.eyebrow':'CommuniQ Voice AI','hero.title':'Speak & understand every call.','hero.sub':'Turn text into natural speech — including Georgian — or upload a recording and get an instant AI analysis.',
      'tab.tts':'Text to Speech','tab.analyze':'Analyze Audio','tab.kb':'Knowledge Base','tab.history':'History',
      'tts.heading':'Generate speech from text','tts.text_ph':'Type something to say… (English, Russian or Georgian)',
      'an.heading':'Upload a recording to analyze','an.heading_kb':'Analyze a call — uses your knowledge base',
      'drop.title':'Drop an audio file here, or click to browse','drop.sub':'mp3, wav, m4a, ogg — transcribed with ElevenLabs Scribe, analyzed by Claude','drop.sub_kb':'Transcribed, then analyzed against your knowledge base',
      'rec.or':'or','rec.record':'Record','rec.stop':'Stop','rec.recording':'Recording','rec.ready':'Recorded — ready to analyze','rec.unsupported':'Recording needs HTTPS or localhost','rec.denied':'Microphone access denied',
      'res.analysis':'Analysis','res.language':'Language','res.sentiment':'Sentiment','res.topics':'Topics','res.time':'Time','res.quality':'Quality','res.summary':'Summary','res.keypoints':'Key points','res.actions':'Action items','res.transcript':'Transcript','res.kbused':'Knowledge base used','res.nokb':'No knowledge base context matched.','res.empty':'(empty)','res.done':'Analysis complete',
      'login.heading':'Sign in','login.hint':'Sign in to your workspace, or with your administrator credentials.',
      'kb.import':'Import knowledge','imp.file':'Upload file','imp.paste':'Paste text','imp.csv':'CSV (Q&A / key-value)','kb.filelabel':'File (PDF / DOCX / TXT / MD)','kb.csvlabel':'CSV file (first row = header)','kb.searchlabel':'Search knowledge base','kb.search_ph':'ask a question…','kb.documents':'Documents','kb.none':'No documents yet. Import some knowledge above.','kb.processing':'processing…','kb.nomatch':'No matches.',
      'th.title':'Title','th.category':'Category','th.status':'Status','th.chunks':'Chunks','th.file':'File','th.lang':'Lang','th.when':'When','th.name':'Name','th.slug':'Slug','th.industry':'Industry','th.active':'Active','th.users':'Users','th.docs':'Docs',
      'hist.heading':'Recent analyses','hist.none':'No analyses yet.',
      'adm.tenants':'Tenants','adm.embeddings':'Embeddings','adm.anon':'Anonymous limits','adm.integrations':'Integrations',
      'adm.createtenant':'Create tenant','adm.embprov':'Embeddings provider','adm.embnote':'Changing the dimension requires re-embedding the KB (documents must be re-imported).',
      'adm.anonheading':'Anonymous (no-login) user limits','adm.allowanon':'Allow anonymous users','adm.maxanalyses':'Max analyses / day','adm.maxmb':'Max audio MB','adm.maxtts':'Max TTS / day','adm.features':'Features allowed','feat.analyze':'Analyze','feat.tts':'Text-to-Speech',
      'adm.intkeys':'Integration keys','adm.models':'Models & voice','adm.instructions':'Analysis instructions',
      'toast.saved':'Settings saved','toast.imported':'Import started','toast.deleted':'Deleted','toast.created':'Created','toast.welcome':'Welcome','toast.error':'Something went wrong',
      'quota.using':"You're using CommuniQ anonymously —",'quota.analyses':'analyses','quota.clips':'speech clips','quota.left':'left today.','quota.more':'for a knowledge base and higher limits.','quota.disabled':'Anonymous access is disabled.',
      'fc.title':'Knowledge base fact-check','fc.accuracy':'accuracy','fc.supported':'supported','fc.contradicted':'contradicted','fc.notinkb':'not in KB','fc.misinfo':'Possible misinformation','fc.nochecked':'No verifiable claims were found.',
      'adm.voices':'Voices','adm.voicevis':'Customer-visible voices','f.restrictvoices':'Show only the ticked voices to customers','f.defaultvoice':'Default voice','v.hint':'Unticked voices are hidden from the customer voice list and rejected by the TTS API. Leave the box unticked to show every voice. System defaults (incl. the Georgian voice) are always on.','v.search':'Search voices…','v.selected':'selected','v.system':'System default','v.nopreview':'No preview','v.unavailable':'Not in this ElevenLabs account','v.pickone':'Select at least one voice, or untick the restriction.','v.loadfail':'Could not load voices from ElevenLabs. Check the API key in Integrations.','msg.voicegone':'That voice is no longer available. The list has been refreshed.',
      'fc.allclaims':'All claims','pg.tab.retrieval':'Retrieval','pg.tab.score':'Answer scoring','pg.ans.label':'Operator answer (any language)','pg.ans.ph':'Paste or type what the operator said or replied — it will be scored against this tenant’s rubric…','pg.ans.run':'Score answer','pg.ans.hint':'Scored with the tenant’s active rubric; claims are checked against their knowledge base.','pg.ans.norubric':'No active rubric — define one in the Scoring tab first.','pg.ans.empty':'Enter an answer to score.','pg.ans.usingv':'rubric version',
      'kba.title':'Knowledge Base Management','kba.tenant':'Tenant','kba.selecttenant':'Select a tenant to manage its knowledge base.',
      'kba.tab.overview':'Overview','kba.tab.documents':'Documents','kba.tab.import':'Import','kba.tab.playground':'Playground','kba.tab.duplicates':'Duplicates','kba.tab.activity':'Activity',
      'kba.stat.documents':'Documents','kba.stat.chunks':'Chunks','kba.stat.coverage':'Embedding coverage','kba.stat.failed':'Failed imports','kba.stat.tokens':'Approx. tokens','kba.stat.lastupd':'Last updated','kba.stat.inprogress':'In progress',
      'kba.params':'Active configuration','kba.export':'Export','kba.exportcsv':'Export CSV','kba.reembedall':'Re-embed all','kba.refresh':'Refresh',
      'kba.f.status':'Status','kba.f.type':'Type','kba.f.tag':'Tag','kba.f.search':'Search title/content','kba.f.all':'All',
      'kba.selected':'selected','kba.bulk.delete':'Delete','kba.bulk.reembed':'Re-embed','kba.bulk.retag':'Retag','kba.selectall':'Select all',
      'kba.edit':'Edit','kba.chunks':'Chunks','kba.reembed':'Re-embed','kba.delete':'Delete','kba.save':'Save','kba.nodocs':'No documents. Import some below.',
      'kba.doc.title':'Title','kba.doc.type':'Category','kba.doc.tags':'Tags','kba.doc.meta':'Metadata (JSON)','kba.doc.content':'Content (editing re-chunks & re-embeds)',
      'kba.pg.query':'Query (any language)','kba.pg.topk':'Top-k','kba.pg.threshold':'Threshold','kba.pg.run':'Run retrieval','kba.pg.method':'method','kba.pg.nohits':'No chunks retrieved.',
      'kba.dup.exact':'Exact duplicates','kba.dup.near':'Near-duplicates','kba.dup.none':'No duplicates found.','kba.dup.sim':'similarity',
      'kba.act.none':'No activity yet.','kba.chunk.edit':'Edit chunk','kba.chunk.delete':'Delete chunk',
      'kba.warn.mismatch':'Embedding dimension mismatch — re-embed required',
      'kba.tab.scoring':'Scoring',
      'sc.title':'Rubric Score','sc.weighted':'weighted','sc.weight':'weight','sc.contribution':'contribution',
      'sc.heading':'Scoring rubric','sc.desc':'Define this tenant’s scoring dimensions, weights and guidance. Calls are scored against the active version.',
      'sc.rubric':'Overall rubric / guidance','sc.rubric.ph':'Optional overall guidance for the evaluator (tone, what matters most, how strict to be)…',
      'sc.adddim':'+ Add dimension','sc.dname':'Dimension name','sc.dname.ph':'e.g. Greeting & identification',
      'sc.ddesc':'Short description','sc.dweight':'Weight','sc.dguide':'Scoring guidance',
      'sc.dguide.ph':'How to score this dimension: what earns a high vs low score…',
      'sc.remove':'Remove','sc.save':'Save rubric','sc.saved':'Rubric saved','sc.sum':'Total weight',
      'sc.nodims':'No dimensions yet — add one to start.','sc.version':'Version','sc.none':'No active rubric for this tenant yet.',
      'sc.needname':'Every dimension needs a name.','sc.needone':'Add at least one dimension.',
    },
    ka: {
      'nav.public':'საჯარო აპი','nav.signin':'შესვლა','nav.logout':'გასვლა','nav.kb':'ცოდნის ბაზა',
      'f.username':'მომხმარებელი','f.password':'პაროლი','f.language':'ენა','f.voice':'ხმა','f.text':'ტექსტი',
      'f.category':'კატეგორია','f.title':'სათაური','f.tags':'ტეგები (მძიმით)','f.name':'სახელი','f.industry':'ინდუსტრია','f.region':'რეგიონი',
      'f.audiofile':'აუდიო ფაილი','f.provider':'პროვაიდერი','f.dimension':'განზომილება','f.model':'მოდელი','f.baseurl':'საბაზო URL',
      'f.anthropic':'Anthropic (Claude) API გასაღები','f.eleven':'ElevenLabs API გასაღები','f.claudemodel':'Claude მოდელი','f.sttmodel':'Scribe (STT) მოდელი','f.ttsmodel':'TTS მოდელი','f.voiceid':'TTS ხმის ID','f.openaikey':'API გასაღები (openai)',
      'btn.signin':'შესვლა','btn.save':'შენახვა','btn.savesettings':'პარამეტრების შენახვა','btn.savelimits':'ლიმიტების შენახვა','btn.refresh':'განახლება','btn.delete':'წაშლა','btn.cancel':'გაუქმება','btn.test':'ტესტი','btn.testconn':'კავშირის ტესტი','btn.search':'ძებნა','btn.import':'იმპორტი','btn.analyze':'ანალიზი','btn.synth':'ხმის გენერაცია','btn.create':'ტენანტის შექმნა','btn.adduser':'მომხმარებლის დამატება','btn.rotate':'განახლება','btn.remove':'წაშლა','btn.apikey':'API გასაღები','btn.users':'მომხმარებლები','btn.chunks':'ფრაგმენტები',
      'hero.eyebrow':'CommuniQ ხმის AI','hero.title':'ისაუბრე და გაიგე ყველა ზარი.','hero.sub':'გადააქციე ტექსტი ბუნებრივ მეტყველებად — ქართულის ჩათვლით — ან ატვირთე ჩანაწერი და მიიღე მყისიერი AI ანალიზი.',
      'tab.tts':'ტექსტი მეტყველებად','tab.analyze':'აუდიოს ანალიზი','tab.kb':'ცოდნის ბაზა','tab.history':'ისტორია',
      'tts.heading':'ტექსტიდან მეტყველების გენერაცია','tts.text_ph':'აკრიფე სათქმელი… (ინგლისური, რუსული ან ქართული)',
      'an.heading':'ატვირთე ჩანაწერი ანალიზისთვის','an.heading_kb':'გააანალიზე ზარი — იყენებს შენს ცოდნის ბაზას',
      'drop.title':'ჩააგდე აუდიო ფაილი აქ ან დააჭირე ასარჩევად','drop.sub':'mp3, wav, m4a, ogg — გადაიწერება ElevenLabs Scribe-ით, ანალიზი Claude-ით','drop.sub_kb':'ჯერ გადაიწერება, შემდეგ ანალიზდება შენს ცოდნის ბაზასთან',
      'rec.or':'ან','rec.record':'ჩაწერა','rec.stop':'გაჩერება','rec.recording':'მიმდინარეობს ჩაწერა','rec.ready':'ჩაწერილია — მზადაა ანალიზისთვის','rec.unsupported':'ჩაწერა საჭიროებს HTTPS-ს ან localhost-ს','rec.denied':'მიკროფონზე წვდომა უარყოფილია',
      'res.analysis':'ანალიზი','res.language':'ენა','res.sentiment':'განწყობა','res.topics':'თემები','res.time':'დრო','res.quality':'ხარისხი','res.summary':'შეჯამება','res.keypoints':'ძირითადი პუნქტები','res.actions':'სამოქმედო პუნქტები','res.transcript':'ტრანსკრიფცია','res.kbused':'გამოყენებული ცოდნის ბაზა','res.nokb':'ცოდნის ბაზასთან დამთხვევა ვერ მოიძებნა.','res.empty':'(ცარიელი)','res.done':'ანალიზი დასრულდა',
      'login.heading':'შესვლა','login.hint':'შედი შენს სამუშაო სივრცეში ან ადმინისტრატორის მონაცემებით.',
      'kb.import':'ცოდნის იმპორტი','imp.file':'ფაილის ატვირთვა','imp.paste':'ტექსტის ჩასმა','imp.csv':'CSV (კითხვა-პასუხი)','kb.filelabel':'ფაილი (PDF / DOCX / TXT / MD)','kb.csvlabel':'CSV ფაილი (პირველი რიგი = სათაური)','kb.searchlabel':'ცოდნის ბაზაში ძებნა','kb.search_ph':'დასვი კითხვა…','kb.documents':'დოკუმენტები','kb.none':'ჯერ არ არის დოკუმენტები. დაამატე ცოდნა ზემოთ.','kb.processing':'მუშავდება…','kb.nomatch':'დამთხვევა ვერ მოიძებნა.',
      'th.title':'სათაური','th.category':'კატეგორია','th.status':'სტატუსი','th.chunks':'ფრაგმენტები','th.file':'ფაილი','th.lang':'ენა','th.when':'როდის','th.name':'სახელი','th.slug':'იდენტიფ.','th.industry':'ინდუსტრია','th.active':'აქტიური','th.users':'მომხმ.','th.docs':'დოკ.',
      'hist.heading':'ბოლო ანალიზები','hist.none':'ჯერ არ არის ანალიზი.',
      'adm.tenants':'ტენანტები','adm.embeddings':'ემბედინგები','adm.anon':'ანონიმური ლიმიტები','adm.integrations':'ინტეგრაციები',
      'adm.createtenant':'ტენანტის შექმნა','adm.embprov':'ემბედინგის პროვაიდერი','adm.embnote':'განზომილების შეცვლა მოითხოვს ცოდნის ბაზის ხელახლა ემბედინგს (დოკუმენტების ხელახლა იმპორტი).',
      'adm.anonheading':'ანონიმური (უავტორიზაციო) ლიმიტები','adm.allowanon':'ანონიმური მომხმარებლების დაშვება','adm.maxanalyses':'მაქს. ანალიზი / დღე','adm.maxmb':'მაქს. აუდიო MB','adm.maxtts':'მაქს. TTS / დღე','adm.features':'დაშვებული ფუნქციები','feat.analyze':'ანალიზი','feat.tts':'ტექსტი-მეტყველებად',
      'adm.intkeys':'ინტეგრაციის გასაღებები','adm.models':'მოდელები და ხმა','adm.instructions':'ანალიზის ინსტრუქციები',
      'toast.saved':'პარამეტრები შენახულია','toast.imported':'იმპორტი დაიწყო','toast.deleted':'წაიშალა','toast.created':'შეიქმნა','toast.welcome':'კეთილი იყოს თქვენი მობრძანება','toast.error':'რაღაც ვერ მოხერხდა',
      'quota.using':'თქვენ იყენებთ CommuniQ-ს ანონიმურად —','quota.analyses':'ანალიზი','quota.clips':'აუდიო კლიპი','quota.left':'დარჩა დღეს.','quota.more':'ცოდნის ბაზისა და მაღალი ლიმიტებისთვის.','quota.disabled':'ანონიმური წვდომა გათიშულია.',
      'fc.title':'ცოდნის ბაზასთან შემოწმება','fc.accuracy':'სიზუსტე','fc.supported':'დადასტურებული','fc.contradicted':'გაბათილებული','fc.notinkb':'ბაზაში არ არის','fc.misinfo':'შესაძლო მცდარი ინფორმაცია','fc.nochecked':'შესამოწმებელი მტკიცება ვერ მოიძებნა.',
      'adm.voices':'ხმები','adm.voicevis':'მომხმარებლისთვის ხილული ხმები','f.restrictvoices':'მომხმარებელს მხოლოდ მონიშნული ხმები აჩვენე','f.defaultvoice':'ნაგულისხმევი ხმა','v.hint':'მოუნიშნავი ხმები დაიმალება მომხმარებლის სიიდან და TTS მათ არ მიიღებს. თუ ველი მოუნიშნავია — ყველა ხმა ჩანს. სისტემური ნაგულისხმევები (მათ შორის ქართული ხმა) ყოველთვის ჩართულია.','v.search':'ხმების ძებნა…','v.selected':'მონიშნული','v.system':'სისტემური','v.nopreview':'გადასმენა არ არის','v.unavailable':'არ არის ამ ElevenLabs ანგარიშში','v.pickone':'მონიშნე მინიმუმ ერთი ხმა ან მოხსენი შეზღუდვა.','v.loadfail':'ხმების ჩატვირთვა ვერ მოხერხდა. შეამოწმე API გასაღები ინტეგრაციებში.','msg.voicegone':'ეს ხმა აღარ არის ხელმისაწვდომი. სია განახლდა.',
      'fc.allclaims':'ყველა მტკიცება','pg.tab.retrieval':'მოძიება','pg.tab.score':'პასუხის შეფასება','pg.ans.label':'ოპერატორის პასუხი (ნებისმიერ ენაზე)','pg.ans.ph':'ჩასვი ან აკრიფე ოპერატორის პასუხი — შეფასდება ამ კლიენტის რუბრიკით…','pg.ans.run':'პასუხის შეფასება','pg.ans.hint':'ფასდება კლიენტის აქტიური რუბრიკით; მტკიცებები მოწმდება მის ცოდნის ბაზასთან.','pg.ans.norubric':'აქტიური რუბრიკა არ არის — ჯერ განსაზღვრე შეფასების ტაბში.','pg.ans.empty':'შეიყვანე პასუხი შესაფასებლად.','pg.ans.usingv':'რუბრიკის ვერსია',
      'kba.title':'ცოდნის ბაზის მართვა','kba.tenant':'ტენანტი','kba.selecttenant':'აირჩიეთ ტენანტი მისი ცოდნის ბაზის სამართავად.',
      'kba.tab.overview':'მიმოხილვა','kba.tab.documents':'დოკუმენტები','kba.tab.import':'იმპორტი','kba.tab.playground':'სათამაშო','kba.tab.duplicates':'დუბლიკატები','kba.tab.activity':'აქტივობა',
      'kba.stat.documents':'დოკუმენტები','kba.stat.chunks':'ფრაგმენტები','kba.stat.coverage':'ემბედინგის დაფარვა','kba.stat.failed':'ჩავარდნილი იმპორტი','kba.stat.tokens':'დაახლ. ტოკენები','kba.stat.lastupd':'ბოლო განახლება','kba.stat.inprogress':'მიმდინარე',
      'kba.params':'აქტიური კონფიგურაცია','kba.export':'ექსპორტი','kba.exportcsv':'CSV ექსპორტი','kba.reembedall':'ხელახლა ემბედინგი','kba.refresh':'განახლება',
      'kba.f.status':'სტატუსი','kba.f.type':'ტიპი','kba.f.tag':'ტეგი','kba.f.search':'ძებნა სათაური/კონტენტი','kba.f.all':'ყველა',
      'kba.selected':'არჩეული','kba.bulk.delete':'წაშლა','kba.bulk.reembed':'ხელახლა ემბედინგი','kba.bulk.retag':'ტეგების შეცვლა','kba.selectall':'ყველას მონიშვნა',
      'kba.edit':'რედაქტირება','kba.chunks':'ფრაგმენტები','kba.reembed':'ხელახლა ემბედინგი','kba.delete':'წაშლა','kba.save':'შენახვა','kba.nodocs':'დოკუმენტები არ არის. დაამატეთ ქვემოთ.',
      'kba.doc.title':'სათაური','kba.doc.type':'კატეგორია','kba.doc.tags':'ტეგები','kba.doc.meta':'მეტამონაცემები (JSON)','kba.doc.content':'კონტენტი (რედაქტირება ხელახლა დაანაწევრებს და ემბედავს)',
      'kba.pg.query':'მოთხოვნა (ნებისმიერ ენაზე)','kba.pg.topk':'Top-k','kba.pg.threshold':'ზღვარი','kba.pg.run':'ძებნის გაშვება','kba.pg.method':'მეთოდი','kba.pg.nohits':'ფრაგმენტები ვერ მოიძებნა.',
      'kba.dup.exact':'ზუსტი დუბლიკატები','kba.dup.near':'მსგავსი დუბლიკატები','kba.dup.none':'დუბლიკატები ვერ მოიძებნა.','kba.dup.sim':'მსგავსება',
      'kba.act.none':'აქტივობა ჯერ არ არის.','kba.chunk.edit':'ფრაგმენტის რედაქტირება','kba.chunk.delete':'ფრაგმენტის წაშლა',
      'kba.warn.mismatch':'ემბედინგის განზომილება არ ემთხვევა — საჭიროა ხელახლა ემბედინგი',
      'kba.tab.scoring':'შეფასება',
      'sc.title':'შეფასების ქულა','sc.weighted':'შეწონილი','sc.weight':'წონა','sc.contribution':'წვლილი',
      'sc.heading':'შეფასების რუბრიკა','sc.desc':'განსაზღვრეთ ამ კლიენტის შეფასების განზომილებები, წონები და მითითებები. ზარები ფასდება აქტიური ვერსიით.',
      'sc.rubric':'ზოგადი რუბრიკა / მითითება','sc.rubric.ph':'არასავალდებულო ზოგადი მითითება შემფასებლისთვის (ტონი, რა არის მთავარი, სიმკაცრე)…',
      'sc.adddim':'+ განზომილების დამატება','sc.dname':'განზომილების სახელი','sc.dname.ph':'მაგ. მისალმება და იდენტიფიკაცია',
      'sc.ddesc':'მოკლე აღწერა','sc.dweight':'წონა','sc.dguide':'შეფასების მითითება',
      'sc.dguide.ph':'როგორ შევაფასოთ ეს განზომილება: რა იძლევა მაღალ ან დაბალ ქულას…',
      'sc.remove':'წაშლა','sc.save':'რუბრიკის შენახვა','sc.saved':'რუბრიკა შენახულია','sc.sum':'სრული წონა',
      'sc.nodims':'განზომილებები ჯერ არ არის — დაამატეთ ერთი დასაწყებად.','sc.version':'ვერსია','sc.none':'ამ კლიენტს აქტიური რუბრიკა ჯერ არ აქვს.',
      'sc.needname':'თითოეულ განზომილებას სჭირდება სახელი.','sc.needone':'დაამატეთ მინიმუმ ერთი განზომილება.',
    },
    ru: {
      'nav.public':'Публичное приложение','nav.signin':'Войти','nav.logout':'Выйти','nav.kb':'База знаний',
      'f.username':'Имя пользователя','f.password':'Пароль','f.language':'Язык','f.voice':'Голос','f.text':'Текст',
      'f.category':'Категория','f.title':'Заголовок','f.tags':'Теги (через запятую)','f.name':'Название','f.industry':'Отрасль','f.region':'Регион',
      'f.audiofile':'Аудиофайл','f.provider':'Провайдер','f.dimension':'Размерность','f.model':'Модель','f.baseurl':'Базовый URL',
      'f.anthropic':'API-ключ Anthropic (Claude)','f.eleven':'API-ключ ElevenLabs','f.claudemodel':'Модель Claude','f.sttmodel':'Модель Scribe (STT)','f.ttsmodel':'Модель TTS','f.voiceid':'ID голоса TTS','f.openaikey':'API-ключ (только openai)',
      'btn.signin':'Войти','btn.save':'Сохранить','btn.savesettings':'Сохранить настройки','btn.savelimits':'Сохранить лимиты','btn.refresh':'Обновить','btn.delete':'Удалить','btn.cancel':'Отмена','btn.test':'Тест','btn.testconn':'Проверить подключения','btn.search':'Поиск','btn.import':'Импорт','btn.analyze':'Анализ','btn.synth':'Синтез речи','btn.create':'Создать арендатора','btn.adduser':'Добавить пользователя','btn.rotate':'Обновить','btn.remove':'Удалить','btn.apikey':'API-ключ','btn.users':'Пользователи','btn.chunks':'Фрагменты',
      'hero.eyebrow':'CommuniQ Голосовой AI','hero.title':'Говорите и понимайте каждый звонок.','hero.sub':'Превратите текст в естественную речь — включая грузинский — или загрузите запись и получите мгновенный AI-анализ.',
      'tab.tts':'Текст в речь','tab.analyze':'Анализ аудио','tab.kb':'База знаний','tab.history':'История',
      'tts.heading':'Генерация речи из текста','tts.text_ph':'Введите текст… (английский, русский или грузинский)',
      'an.heading':'Загрузите запись для анализа','an.heading_kb':'Анализ звонка — использует вашу базу знаний',
      'drop.title':'Перетащите аудиофайл сюда или нажмите для выбора','drop.sub':'mp3, wav, m4a, ogg — расшифровка ElevenLabs Scribe, анализ Claude','drop.sub_kb':'Сначала расшифровка, затем анализ по вашей базе знаний',
      'rec.or':'или','rec.record':'Записать','rec.stop':'Стоп','rec.recording':'Идёт запись','rec.ready':'Записано — готово к анализу','rec.unsupported':'Для записи нужен HTTPS или localhost','rec.denied':'Доступ к микрофону запрещён',
      'res.analysis':'Анализ','res.language':'Язык','res.sentiment':'Тональность','res.topics':'Темы','res.time':'Время','res.quality':'Качество','res.summary':'Резюме','res.keypoints':'Ключевые моменты','res.actions':'Действия','res.transcript':'Транскрипция','res.kbused':'Использованная база знаний','res.nokb':'Совпадений в базе знаний не найдено.','res.empty':'(пусто)','res.done':'Анализ завершён',
      'login.heading':'Войти','login.hint':'Войдите в своё рабочее пространство или с учётными данными администратора.',
      'kb.import':'Импорт знаний','imp.file':'Загрузить файл','imp.paste':'Вставить текст','imp.csv':'CSV (вопрос-ответ)','kb.filelabel':'Файл (PDF / DOCX / TXT / MD)','kb.csvlabel':'CSV-файл (первая строка = заголовок)','kb.searchlabel':'Поиск по базе знаний','kb.search_ph':'задайте вопрос…','kb.documents':'Документы','kb.none':'Документов пока нет. Импортируйте знания выше.','kb.processing':'обработка…','kb.nomatch':'Совпадений нет.',
      'th.title':'Заголовок','th.category':'Категория','th.status':'Статус','th.chunks':'Фрагменты','th.file':'Файл','th.lang':'Язык','th.when':'Когда','th.name':'Название','th.slug':'Идент.','th.industry':'Отрасль','th.active':'Активен','th.users':'Польз.','th.docs':'Док.',
      'hist.heading':'Недавние анализы','hist.none':'Анализов пока нет.',
      'adm.tenants':'Арендаторы','adm.embeddings':'Эмбеддинги','adm.anon':'Анонимные лимиты','adm.integrations':'Интеграции',
      'adm.createtenant':'Создать арендатора','adm.embprov':'Провайдер эмбеддингов','adm.embnote':'Изменение размерности требует переэмбеддинга базы знаний (документы нужно импортировать заново).',
      'adm.anonheading':'Лимиты анонимных пользователей','adm.allowanon':'Разрешить анонимных пользователей','adm.maxanalyses':'Макс. анализов / день','adm.maxmb':'Макс. аудио МБ','adm.maxtts':'Макс. TTS / день','adm.features':'Разрешённые функции','feat.analyze':'Анализ','feat.tts':'Текст в речь',
      'adm.intkeys':'Ключи интеграций','adm.models':'Модели и голос','adm.instructions':'Инструкции анализа',
      'toast.saved':'Настройки сохранены','toast.imported':'Импорт начат','toast.deleted':'Удалено','toast.created':'Создано','toast.welcome':'Добро пожаловать','toast.error':'Что-то пошло не так',
      'quota.using':'Вы используете CommuniQ анонимно —','quota.analyses':'анализов','quota.clips':'аудиоклипов','quota.left':'осталось сегодня.','quota.more':'для базы знаний и более высоких лимитов.','quota.disabled':'Анонимный доступ отключён.',
      'fc.title':'Проверка по базе знаний','fc.accuracy':'точность','fc.supported':'подтверждено','fc.contradicted':'опровергнуто','fc.notinkb':'нет в базе','fc.misinfo':'Возможная дезинформация','fc.nochecked':'Проверяемых утверждений не найдено.',
      'adm.voices':'Голоса','adm.voicevis':'Голоса, видимые клиентам','f.restrictvoices':'Показывать клиентам только отмеченные голоса','f.defaultvoice':'Голос по умолчанию','v.hint':'Неотмеченные голоса скрыты из списка для клиентов и отклоняются TTS. Оставьте флажок снятым, чтобы показывать все голоса. Системные (включая грузинский) всегда включены.','v.search':'Поиск голосов…','v.selected':'выбрано','v.system':'Системный','v.nopreview':'Нет образца','v.unavailable':'Нет в этом аккаунте ElevenLabs','v.pickone':'Выберите хотя бы один голос или снимите ограничение.','v.loadfail':'Не удалось загрузить голоса из ElevenLabs. Проверьте API-ключ в «Интеграциях».','msg.voicegone':'Этот голос больше недоступен. Список обновлён.',
      'fc.allclaims':'Все утверждения','pg.tab.retrieval':'Поиск','pg.tab.score':'Оценка ответа','pg.ans.label':'Ответ оператора (на любом языке)','pg.ans.ph':'Вставьте или напишите ответ оператора — он будет оценён по рубрике этого клиента…','pg.ans.run':'Оценить ответ','pg.ans.hint':'Оценивается по активной рубрике клиента; утверждения проверяются по его базе знаний.','pg.ans.norubric':'Нет активной рубрики — сначала задайте её во вкладке «Оценка».','pg.ans.empty':'Введите ответ для оценки.','pg.ans.usingv':'версия рубрики',
      'kba.title':'Управление базой знаний','kba.tenant':'Арендатор','kba.selecttenant':'Выберите арендатора для управления его базой знаний.',
      'kba.tab.overview':'Обзор','kba.tab.documents':'Документы','kba.tab.import':'Импорт','kba.tab.playground':'Песочница','kba.tab.duplicates':'Дубликаты','kba.tab.activity':'Активность',
      'kba.stat.documents':'Документы','kba.stat.chunks':'Фрагменты','kba.stat.coverage':'Покрытие эмбеддингами','kba.stat.failed':'Ошибки импорта','kba.stat.tokens':'Прибл. токены','kba.stat.lastupd':'Обновлено','kba.stat.inprogress':'В процессе',
      'kba.params':'Активная конфигурация','kba.export':'Экспорт','kba.exportcsv':'Экспорт CSV','kba.reembedall':'Переэмбеддинг','kba.refresh':'Обновить',
      'kba.f.status':'Статус','kba.f.type':'Тип','kba.f.tag':'Тег','kba.f.search':'Поиск по заголовку/тексту','kba.f.all':'Все',
      'kba.selected':'выбрано','kba.bulk.delete':'Удалить','kba.bulk.reembed':'Переэмбеддинг','kba.bulk.retag':'Изменить теги','kba.selectall':'Выбрать все',
      'kba.edit':'Редактировать','kba.chunks':'Фрагменты','kba.reembed':'Переэмбеддинг','kba.delete':'Удалить','kba.save':'Сохранить','kba.nodocs':'Нет документов. Импортируйте ниже.',
      'kba.doc.title':'Заголовок','kba.doc.type':'Категория','kba.doc.tags':'Теги','kba.doc.meta':'Метаданные (JSON)','kba.doc.content':'Текст (редактирование пере-разбивает и пере-эмбеддит)',
      'kba.pg.query':'Запрос (на любом языке)','kba.pg.topk':'Top-k','kba.pg.threshold':'Порог','kba.pg.run':'Выполнить поиск','kba.pg.method':'метод','kba.pg.nohits':'Фрагменты не найдены.',
      'kba.dup.exact':'Точные дубликаты','kba.dup.near':'Похожие дубликаты','kba.dup.none':'Дубликаты не найдены.','kba.dup.sim':'сходство',
      'kba.act.none':'Активности пока нет.','kba.chunk.edit':'Редактировать фрагмент','kba.chunk.delete':'Удалить фрагмент',
      'kba.warn.mismatch':'Несовпадение размерности эмбеддинга — требуется переэмбеддинг',
      'kba.tab.scoring':'Оценка',
      'sc.title':'Оценка по рубрике','sc.weighted':'взвешенно','sc.weight':'вес','sc.contribution':'вклад',
      'sc.heading':'Рубрика оценки','sc.desc':'Задайте измерения оценки, веса и указания для этого клиента. Звонки оцениваются по активной версии.',
      'sc.rubric':'Общая рубрика / указания','sc.rubric.ph':'Необязательные общие указания для оценщика (тон, что важнее всего, насколько строго)…',
      'sc.adddim':'+ Добавить измерение','sc.dname':'Название измерения','sc.dname.ph':'напр. Приветствие и идентификация',
      'sc.ddesc':'Краткое описание','sc.dweight':'Вес','sc.dguide':'Указания по оценке',
      'sc.dguide.ph':'Как оценивать это измерение: что даёт высокий или низкий балл…',
      'sc.remove':'Удалить','sc.save':'Сохранить рубрику','sc.saved':'Рубрика сохранена','sc.sum':'Общий вес',
      'sc.nodims':'Пока нет измерений — добавьте одно, чтобы начать.','sc.version':'Версия','sc.none':'У этого клиента ещё нет активной рубрики.',
      'sc.needname':'У каждого измерения должно быть название.','sc.needone':'Добавьте хотя бы одно измерение.',
    },
  };
  let LANG = (() => { try { return localStorage.getItem('cq_lang') || (navigator.language||'en').slice(0,2); } catch { return 'en'; } })();
  if (!DICT[LANG]) LANG = 'en';
  function t(key) { return (DICT[LANG] && DICT[LANG][key]) || DICT.en[key] || key; }
  function lang() { return LANG; }
  function applyI18n(root = document) {
    root.querySelectorAll('[data-i18n]').forEach(el => { el.textContent = t(el.getAttribute('data-i18n')); });
    root.querySelectorAll('[data-i18n-ph]').forEach(el => { el.setAttribute('placeholder', t(el.getAttribute('data-i18n-ph'))); });
    root.querySelectorAll('[data-i18n-title]').forEach(el => { el.setAttribute('title', t(el.getAttribute('data-i18n-title'))); });
  }
  function setLang(code) {
    if (!DICT[code]) return;
    LANG = code; try { localStorage.setItem('cq_lang', code); } catch {}
    document.documentElement.setAttribute('lang', code);
    document.querySelectorAll('[data-lang-btn]').forEach(b => b.classList.toggle('active', b.getAttribute('data-lang-btn') === code));
    applyI18n(document);
    document.dispatchEvent(new CustomEvent('cq:lang', { detail: code }));
  }

  /* ---------------- Header ---------------- */
  function header(opts = {}) {
    const nav = (opts.nav || []).map(n =>
      `<a href="${n.href}"${n.id ? ` id="${n.id}"` : ''}${n.cls ? ` class="${n.cls}"` : ''}${n.i18n ? ` data-i18n="${n.i18n}"` : ''}>${n.label || ''}</a>`).join('');
    const who = opts.who ? `<span class="who" id="${opts.who}"></span>` : '';
    const langSwitch = `<div class="lang-switch" role="group" aria-label="Language">
      ${['en','ka','ru'].map(c => `<button data-lang-btn="${c}">${c.toUpperCase()}</button>`).join('')}</div>`;
    const theme = opts.theme === false ? '' :
      `<button class="icon-btn" data-theme-btn title="Toggle light/dark" aria-label="Toggle theme"></button>`;
    return `<header class="app-header">
      <a class="brand" href="index.html" aria-label="CommuniQ home">${LOGO}
        <span class="brand-name">CommuniQ</span>
        ${opts.tag ? `<span class="brand-tag">${opts.tag}</span>` : ''}
      </a>
      <nav class="app-nav">${nav}${opts.extra || ''}${who}${langSwitch}${theme}</nav>
    </header>`;
  }
  function mountHeader(opts) {
    const el = document.getElementById('cq-header');
    if (el) el.outerHTML = header(opts);
    document.querySelectorAll('[data-theme-btn]').forEach(b => {
      b.textContent = currentTheme() === 'light' ? '☾' : '☀';
      b.addEventListener('click', toggleTheme);
    });
    document.querySelectorAll('[data-lang-btn]').forEach(b => {
      b.classList.toggle('active', b.getAttribute('data-lang-btn') === LANG);
      b.addEventListener('click', () => setLang(b.getAttribute('data-lang-btn')));
    });
    document.documentElement.setAttribute('lang', LANG);
    applyI18n(document);
  }

  /* ---------------- Toasts ---------------- */
  function toast(message, type = 'info', ms = 3600) {
    let host = document.getElementById('cq-toasts');
    if (!host) { host = document.createElement('div'); host.id = 'cq-toasts'; document.body.appendChild(host); }
    const el = document.createElement('div');
    el.className = 'cq-toast ' + (type === 'ok' ? 'ok' : type === 'err' ? 'err' : '');
    el.textContent = message;
    host.appendChild(el);
    setTimeout(() => { el.classList.add('leaving'); setTimeout(() => el.remove(), 260); }, ms);
  }

  /* ---------------- Confirm modal ---------------- */
  function confirm(message, { ok = 'Confirm', cancel, danger = true } = {}) {
    cancel = cancel || t('btn.cancel');
    return new Promise(resolve => {
      const bg = document.createElement('div'); bg.className = 'cq-modal-bg';
      bg.innerHTML = `<div class="cq-modal" role="dialog" aria-modal="true">
        <p>${message}</p>
        <div class="actions"><button class="ghost" data-c>${cancel}</button>
        <button class="${danger ? 'danger' : 'primary'}" data-o>${ok}</button></div></div>`;
      document.body.appendChild(bg);
      const done = v => { bg.remove(); resolve(v); };
      bg.querySelector('[data-o]').addEventListener('click', () => done(true));
      bg.querySelector('[data-c]').addEventListener('click', () => done(false));
      bg.addEventListener('click', e => { if (e.target === bg) done(false); });
      bg.querySelector('[data-o]').focus();
    });
  }

  /* ---------------- Custom <select> ---------------- */
  function select(native) {
    if (!native || native._cq) return native._cq;
    const wrap = document.createElement('div'); wrap.className = 'cq-select';
    native.parentNode.insertBefore(wrap, native);
    wrap.appendChild(native);
    native.classList.add('cq-select-native');
    const trigger = document.createElement('button');
    trigger.type = 'button'; trigger.className = 'cq-select-trigger';
    trigger.innerHTML = `<span class="cq-select-label"></span><span class="cq-select-arrow">▾</span>`;
    const panel = document.createElement('div'); panel.className = 'cq-select-panel';
    wrap.appendChild(trigger); wrap.appendChild(panel);
    const labelEl = trigger.querySelector('.cq-select-label');

    function render() {
      const opts = Array.from(native.options);
      labelEl.textContent = native.selectedOptions[0] ? native.selectedOptions[0].textContent : '';
      panel.innerHTML = opts.map((o, i) =>
        `<div class="cq-opt${i === native.selectedIndex ? ' sel' : ''}" data-i="${i}">${o.textContent.replace(/</g,'&lt;')}</div>`).join('');
      panel.querySelectorAll('.cq-opt').forEach(el => el.addEventListener('click', () => {
        native.selectedIndex = +el.dataset.i;
        native.dispatchEvent(new Event('change', { bubbles: true }));
        close();
      }));
    }
    function open() {
      render(); wrap.classList.add('open');
      const r = wrap.getBoundingClientRect();
      panel.classList.toggle('up', r.bottom + 300 > window.innerHeight && r.top > 320);
      document.addEventListener('click', outside, true);
      const s = panel.querySelector('.cq-opt.sel'); if (s) s.scrollIntoView({ block: 'nearest' });
    }
    function close() { wrap.classList.remove('open'); document.removeEventListener('click', outside, true); }
    function outside(e) { if (!wrap.contains(e.target)) close(); }
    trigger.addEventListener('click', e => { e.preventDefault(); wrap.classList.contains('open') ? close() : open(); });
    trigger.addEventListener('keydown', e => {
      if (e.key === 'Enter' || e.key === ' ' || e.key === 'ArrowDown') { e.preventDefault(); open(); }
      else if (e.key === 'Escape') close();
    });
    native.addEventListener('change', render);
    new MutationObserver(render).observe(native, { childList: true });
    render();
    native._cq = { render, refresh: render };
    return native._cq;
  }
  function enhanceSelects(root = document) { root.querySelectorAll('select:not(.cq-select-native)').forEach(select); }
  function syncSelect(el) { if (el && el._cq) el._cq.render(); }

  /* ---------------- Audio player ---------------- */
  function fmt(s) { s = Math.floor(s || 0); return `${Math.floor(s/60)}:${String(s%60).padStart(2,'0')}`; }
  function player(container, src, { name = 'audio', autoplay = true } = {}) {
    container.innerHTML = `<div class="cq-player">
      <button class="cq-play" aria-label="Play/pause">▶</button>
      <input class="cq-seek" type="range" min="0" max="100" value="0" step="0.1" aria-label="Seek" />
      <span class="cq-time">0:00</span>
      <a class="cq-dl icon-btn" title="Download" download="${name}">⭳</a>
    </div>`;
    const el = container.querySelector('.cq-player');
    const audio = new Audio(src); audio.preload = 'metadata';
    const btn = el.querySelector('.cq-play'), seek = el.querySelector('.cq-seek'),
      time = el.querySelector('.cq-time'), dl = el.querySelector('.cq-dl');
    dl.href = src;
    let seeking = false;
    const setBtn = () => btn.textContent = audio.paused ? '▶' : '❚❚';
    btn.addEventListener('click', () => { audio.paused ? audio.play() : audio.pause(); });
    audio.addEventListener('play', setBtn); audio.addEventListener('pause', setBtn);
    audio.addEventListener('loadedmetadata', () => { time.textContent = fmt(audio.duration); });
    audio.addEventListener('timeupdate', () => {
      if (!seeking && audio.duration) { seek.value = (audio.currentTime / audio.duration) * 100;
        time.textContent = `${fmt(audio.currentTime)} / ${fmt(audio.duration)}`; }
    });
    audio.addEventListener('ended', () => { seek.value = 0; setBtn(); });
    seek.addEventListener('input', () => { seeking = true; });
    seek.addEventListener('change', () => { if (audio.duration) audio.currentTime = (seek.value/100)*audio.duration; seeking = false; });
    if (autoplay) audio.play().catch(() => {});
    return { audio, el, load(newSrc){ audio.src = newSrc; dl.href = newSrc; audio.play().catch(()=>{}); }, toggle(){ audio.paused ? audio.play() : audio.pause(); } };
  }

  /* ---------------- Result renderers (scorecard / KB fact-check) ----------------
     Shared by the tenant portal and the admin answer-scoring playground so both
     render identically. Defensive about shapes the model might return. */
  function _esc(s) { return (s ?? '').toString().replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
  function _arr(v) {
    if (v == null) return [];
    if (Array.isArray(v)) return v.filter(x => x != null).map(x => typeof x === 'object' ? Object.values(x).filter(Boolean).join(' — ') : String(x)).filter(s => s.trim());
    if (typeof v === 'object') return Object.values(v).filter(Boolean).map(String);
    const s = String(v).trim(); return s ? [s] : [];
  }

  function scorecardHTML(sc) {
    if (!sc || !Array.isArray(sc.dimensions) || !sc.dimensions.length) return '';
    const total = sc.weighted_total;
    const band = v => v == null ? 'muted' : v >= 80 ? 'ok' : v >= 50 ? 'pending' : 'alert';
    const barcls = v => v == null ? '' : v >= 80 ? 'good' : v >= 50 ? 'mid' : 'bad';
    const dimRow = d => {
      const s = d.score, ev = _arr(d.evidence);
      return `<div class="sc-dim">
        <div class="sc-dim-head">
          <span class="sc-dim-name">${_esc(d.name)}</span>
          <span class="sc-dim-score" style="color:var(--${band(s)})">${s==null?'—':s}<span class="sc-meta">/100</span></span>
        </div>
        <div class="sc-meta">${t('sc.weight')} ${d.weight}% · ${t('sc.contribution')} ${d.contribution}</div>
        <div class="sc-bar ${barcls(s)}"><span style="width:${Math.max(0,Math.min(100,s||0))}%"></span></div>
        ${d.rationale ? `<div class="hint" style="margin-top:6px">${_esc(d.rationale)}</div>` : ''}
        ${ev.length ? `<div class="sc-evid">${ev.map(q=>`<q>${_esc(q)}</q>`).join('')}</div>` : ''}
      </div>`;
    };
    return `<div class="card">
      <div class="row" style="justify-content:space-between; align-items:center">
        <h3 style="margin:0">${t('sc.title')}</h3>
        <div class="sc-total"><div class="num" style="color:var(--${band(total)})">${total==null?'—':total}</div><span class="muted">${t('sc.weighted')} / ${sc.max_total||100}</span></div>
      </div>
      ${sc.dimensions.map(dimRow).join('')}
    </div>`;
  }

  function factcheckHTML(kb) {
    if (!kb) return '';
    const claims = Array.isArray(kb.claims) ? kb.claims : [];
    const c = kb.counts || {};
    if (!claims.length) return `<div class="card"><h3>${t('fc.title')}</h3><div class="empty">${t('fc.nochecked')}</div></div>`;
    const acc = kb.accuracy_score;
    const accVar = acc == null ? 'muted' : acc >= 80 ? 'ok' : acc >= 50 ? 'pending' : 'alert';
    const vcls = v => ({SUPPORTED:'supported', CONTRADICTED:'contradicted', NOT_IN_KB:'notinkb'}[v] || 'notinkb');
    const contradicted = claims.filter(x => x.verdict === 'CONTRADICTED');
    const claimCard = cl => {
      const ev = cl.evidence;
      const conf = cl.confidence != null ? ' · ' + Math.round(cl.confidence * 100) + '%' : '';
      const cat = cl.category ? ' · ' + _esc(cl.category) : '';
      return `<div class="fc-claim v-${_esc(cl.verdict)}">
        <div class="inline" style="justify-content:space-between;gap:8px">
          <span class="pill ${vcls(cl.verdict)}">${t('fc.' + vcls(cl.verdict))}</span>
          <span class="hint">${_esc(cl.speaker || '')}${cat}${conf}</span>
        </div>
        <div style="margin-top:6px">${_esc(cl.claim)}</div>
        ${cl.rationale ? `<div class="hint" style="margin-top:4px">${_esc(cl.rationale)}</div>` : ''}
        ${ev ? `<div class="fc-ev"><div class="fc-ev-src">📄 ${_esc(ev.title || ev.doc_type || 'KB')}${ev.score != null ? ' · ' + ev.score : ''}</div>${_esc(ev.snippet || '')}</div>` : ''}
      </div>`;
    };
    return `<div class="card">
      <div class="row" style="justify-content:space-between; align-items:center">
        <h3 style="margin:0">${t('fc.title')}</h3>
        <div class="fc-accuracy"><div class="num" style="color:var(--${accVar})">${acc == null ? '—' : acc}</div><span class="muted">${t('fc.accuracy')}</span></div>
      </div>
      <div style="margin-top:8px">
        <span class="pill supported">${c.supported || 0} ${t('fc.supported')}</span>
        <span class="pill contradicted">${c.contradicted || 0} ${t('fc.contradicted')}</span>
        <span class="pill notinkb">${c.not_in_kb || 0} ${t('fc.notinkb')}</span>
      </div>
      ${contradicted.length ? `<h4 style="color:var(--coral)">⚠ ${t('fc.misinfo')}</h4>${contradicted.map(claimCard).join('')}<h4>${t('fc.allclaims')}</h4>` : ''}
      ${claims.map(claimCard).join('')}
    </div>`;
  }

  /* ---------------- Mic recorder (for the analyzer) ----------------
     Records from the microphone and drops the result into an existing <input type=file>
     so the normal "analyze" flow works unchanged. getUserMedia requires a SECURE CONTEXT
     (https or http://localhost) — on plain http the button disables itself with a reason. */
  function attachRecorder({ button, status, fileInput, onReady } = {}) {
    if (!button) return;
    const setStatus = (msg, cls) => { if (status) { status.textContent = msg || ''; status.className = 'rec-status hint' + (cls ? ' ' + cls : ''); } };
    const supported = window.isSecureContext && navigator.mediaDevices &&
      navigator.mediaDevices.getUserMedia && typeof MediaRecorder !== 'undefined';
    let rec = null, stream = null, chunks = [], timer = null, seconds = 0, recording = false;
    const label = () => { button.innerHTML = recording ? '<span class="rec-dot"></span>' + t('rec.stop') : '● ' + t('rec.record'); };
    if (!supported) {
      button.disabled = true; button.classList.add('rec-off');
      button.innerHTML = '● ' + t('rec.record'); setStatus(t('rec.unsupported'));
      document.addEventListener('cq:lang', () => { button.innerHTML = '● ' + t('rec.record'); setStatus(t('rec.unsupported')); });
      return;
    }
    async function start() {
      try { stream = await navigator.mediaDevices.getUserMedia({ audio: true }); }
      catch (e) { setStatus(t('rec.denied'), 'err'); return; }
      chunks = []; seconds = 0;
      let mime = '';
      ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg'].some(m => MediaRecorder.isTypeSupported(m) && (mime = m));
      rec = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
      rec.ondataavailable = e => { if (e.data && e.data.size) chunks.push(e.data); };
      rec.onstop = () => {
        clearInterval(timer);
        if (stream) stream.getTracks().forEach(tr => tr.stop());
        const type = (rec.mimeType || mime || 'audio/webm').split(';')[0];
        const ext = type.includes('mp4') ? 'm4a' : type.includes('ogg') ? 'ogg' : 'webm';
        const file = new File(chunks, 'recording.' + ext, { type });
        if (fileInput) {
          try { const dt = new DataTransfer(); dt.items.add(file); fileInput.files = dt.files; fileInput.dispatchEvent(new Event('change')); }
          catch (e) { /* very old browsers can't set input.files — rely on onReady */ }
        }
        recording = false; label();
        setStatus(t('rec.ready') + ' (' + fmt(seconds) + ')', 'ok');
        if (onReady) onReady(file, seconds);
      };
      rec.start();
      recording = true; label();
      setStatus(t('rec.recording') + ' 0:00', 'rec-live');
      timer = setInterval(() => { seconds++; setStatus(t('rec.recording') + ' ' + fmt(seconds), 'rec-live'); }, 1000);
    }
    function stop() { if (rec && rec.state !== 'inactive') rec.stop(); }
    button.addEventListener('click', () => { recording ? stop() : start(); });
    document.addEventListener('cq:lang', () => { if (!recording) label(); });
    label();
  }

  return { API, LOGO, t, lang, setLang, applyI18n, toggleTheme, currentTheme, header, mountHeader,
           toast, confirm, select, enhanceSelects, syncSelect, player, attachRecorder,
           scorecardHTML, factcheckHTML };
})();
