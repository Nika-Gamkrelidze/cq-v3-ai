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
      'btn.signin':'Sign in','btn.save':'Save','btn.savesettings':'Save settings','btn.savelimits':'Save limits','btn.refresh':'Refresh','btn.delete':'Delete','btn.cancel':'Cancel','btn.test':'Test','btn.testconn':'Test connections','btn.testdeep':'Test everything (deep)',
      'cap.database':'Database','cap.ffmpeg':'Audio transcoding (ffmpeg)','cap.voices':'ElevenLabs · list voices','cap.stt':'ElevenLabs · speech-to-text','cap.tts':'ElevenLabs · text-to-speech','cap.ttska':'ElevenLabs · Georgian TTS','cap.embeddings':'Embeddings','cap.claude':'Claude · analysis','cap.factcheck':'Claude · fact-check tools','cap.scoring':'Claude · scoring tool',
      'cap.fixscope':'Fix: in ElevenLabs open Settings → API Keys → Edit on this key, enable the “{scope}” permission, save, then re-test.',
      'adm.testnote':'Each capability is probed for real — the connection test spends a fraction of a second of speech-to-text and a few text-to-speech characters, because ElevenLabs offers no way to read a key’s permissions. “Deep” additionally exercises the fact-check and scoring tools.','btn.search':'Search','btn.import':'Import','btn.analyze':'Analyze','btn.synth':'Synthesize speech','btn.create':'Create tenant','btn.adduser':'Add user','btn.rotate':'Rotate','btn.remove':'Remove','btn.apikey':'API key','btn.users':'Users','btn.chunks':'Chunks',
      'hero.eyebrow':'CommuniQ Voice AI','hero.title':'Speak & understand every call.','hero.sub':'Turn text into natural speech — including Georgian — or upload a recording and get an instant AI analysis.',
      'tab.tts':'Text to Speech','tab.analyze':'Analyze Audio','tab.kb':'Knowledge Base','tab.history':'History','tab.scoring':'Scoring',
      'tts.heading':'Generate speech from text','tts.text_ph':'Type something to say… (English, Russian or Georgian)',
      'an.heading':'Upload a recording to analyze','an.heading_kb':'Analyze a call — uses your knowledge base',
      'drop.title':'Drop an audio file here, or click to browse','drop.sub':'Any audio or video file — transcribed with ElevenLabs Scribe, analyzed by Claude','drop.sub_kb':'Transcribed, then analyzed against your knowledge base',
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
      'err.toolarge':'That file is too large to upload. Please use a shorter or smaller recording.','err.timeout':'The analysis took too long and timed out. Try a shorter recording.','err.unavailable':'The service is temporarily unavailable. Please try again in a moment.','err.http':'Request failed (HTTP {status}).','err.badresp':'The server returned an unexpected response. Please try again.',
      'quota.using':"You're using CommuniQ anonymously —",'quota.analyses':'analyses','quota.clips':'speech clips','quota.left':'left today.','quota.more':'for a knowledge base and higher limits.','quota.disabled':'Anonymous access is disabled.',
      'fc.title':'Knowledge base fact-check','fc.accuracy':'accuracy','fc.supported':'supported','fc.contradicted':'contradicted','fc.notinkb':'not in KB','fc.misinfo':'Possible misinformation','fc.nochecked':'No verifiable claims were found.',
      'adm.voices':'Voices','adm.voicevis':'Customer-visible voices','f.restrictvoices':'Show only the ticked voices to customers','f.defaultvoice':'Default voice','v.hint':'Unticked voices are hidden from the customer voice list and rejected by the TTS API. Leave the box unticked to show every voice. System defaults (incl. the Georgian voice) are always on.','v.search':'Search voices…','v.selected':'selected','v.system':'System default','v.nopreview':'No preview','v.unavailable':'Not in this ElevenLabs account','v.pickone':'Select at least one voice, or untick the restriction.','v.loadfail':'Could not load voices from ElevenLabs. Check the API key in Integrations.','msg.voicegone':'That voice is no longer available. The list has been refreshed.',
      'fc.allclaims':'All claims','pg.tab.retrieval':'Retrieval','pg.tab.score':'Answer scoring','pg.ans.label':'Operator answer (any language)','pg.ans.ph':'Paste or type what the operator said or replied — it will be scored against this tenant’s rubric…','pg.ans.run':'Score answer','pg.ans.hint':'Scored with the tenant’s active rubric; claims are checked against their knowledge base.','pg.ans.norubric':'No active rubric — define one in the Scoring tab first.','pg.ans.empty':'Enter an answer to score.','pg.ans.usingv':'rubric version',
      'tab.playground':'Playground','pg.heading':'Score a call or answer','pg.hint':'Score a written answer, or upload a call recording (audio or video) — it is transcribed and scored against your rubric and knowledge base.','pg.mode.text':'Text','pg.mode.audio':'Audio / Video','pg.audiolabel':'Call recording (audio or video)','pg.run':'Score','pg.audioempty':'Choose an audio or video file.',
      'kba.title':'Knowledge Base Management','kba.tenant':'Tenant','kba.selecttenant':'Select a tenant to manage its knowledge base.',
      'kba.tab.overview':'Overview','kba.tab.documents':'Documents','kba.tab.import':'Import','kba.tab.playground':'Playground','kba.tab.duplicates':'Duplicates','kba.tab.activity':'Activity',
      'kba.stat.documents':'Documents','kba.stat.chunks':'Chunks','kba.stat.coverage':'Embedding coverage','kba.stat.failed':'Failed imports','kba.stat.tokens':'Approx. tokens','kba.stat.lastupd':'Last updated','kba.stat.inprogress':'In progress',
      'kba.params':'Active configuration','kba.export':'Export','kba.exportcsv':'Export CSV','kba.reembedall':'Re-embed all','kba.refresh':'Refresh',
      'kba.f.status':'Status','kba.f.type':'Type','kba.f.tag':'Tag','kba.f.search':'Search title/content','kba.f.all':'All',
      'kba.selected':'selected','kba.bulk.delete':'Delete','kba.bulk.reembed':'Re-embed','kba.bulk.retag':'Retag','kba.selectall':'Select all',
      'kba.edit':'Edit','kba.chunks':'Chunks','kba.reembed':'Re-embed','kba.delete':'Delete','kba.save':'Save','kba.nodocs':'No documents. Import some below.',
      'kba.doc.title':'Title','kba.doc.type':'Category','kba.doc.tags':'Tags','kba.doc.meta':'Metadata (JSON)','kba.doc.content':'Content (editing re-chunks & re-embeds)',
      'kba.pg.query':'Query (any language)','kba.pg.topk':'Top-k','kba.pg.threshold':'Threshold','kba.pg.run':'Run retrieval','kba.pg.method':'method','kba.pg.nohits':'No chunks retrieved.',
      /* Retrieval confidence. A raw BGE-M3 cosine score is not calibrated in absolute terms, so a
         screen of 0.36–0.40 rows is the encoder saying "none of these match" while looking exactly
         like a result set. These strings are what makes that visible instead of silent — so they are
         written for a callcentre admin and each one says what to DO, not just that something is off. */
      'retr.m.vector':'semantic','retr.m.keyword':'text match','retr.m.none':'none',
      'retr.top':'top score','retr.spread':'spread','retr.margin':'gap to 2nd',
      'retr.level.high':'confident','retr.level.medium':'fair','retr.level.low':'weak','retr.level.none':'no match',
      'retr.opendoc':'Open document',
      'retr.flag.shown':'The closest passages are still listed below, best first.',
      'retr.flag.empty_kb':'Nothing to search yet',
      'retr.flag.empty_kb.b':'This knowledge base is empty, so no answer can be found in it. Open the Import tab and add your policies, FAQs or call scripts first.',
      'retr.flag.unavailable':'Search could not run',
      'retr.flag.unavailable.b':'This search did not complete, so it says nothing about your knowledge base — do not read it as "no answer found". Try again in a moment; if it keeps happening, report it to your CommuniQ operator.',
      'retr.flag.no_hits':'Nothing matched',
      'retr.flag.no_hits.b':'Not a single passage came back for this question. Try asking it the way a customer would, or import a document that answers it.',
      'retr.flag.keyword_fallback':'Fallback search — meaning-based search is down',
      'retr.flag.keyword_fallback.b':'The embedding service could not be reached, so these passages were matched on spelling, not on meaning, and their scores cannot be compared with normal ones. Answers stay worse than usual until it is back — report this to your CommuniQ operator.',
      'retr.flag.flat_distribution':'Nothing clearly matched',
      'retr.flag.flat_distribution.b':'Every passage scored about the same, which normally means the knowledge base has no answer for this question — what you see below is the closest text, not a real match. Import a document that covers the topic, or ask using the words your documents actually use.',
      'retr.flag.low_score':'Only weak matches',
      'retr.flag.low_score.b':'The closest passages are only loosely related to the question. Check that a document really covers this before relying on it, and add one if none does.',
      'retr.flag.generic':'Low-confidence result',
      'retr.flag.generic.b':'This search did not confidently match anything in the knowledge base.',
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
      'sc.normalize':'Normalize to 100%','sc.mustbe100':'Weights must total 100% (they total {total}%).',
      'cur.tab':'KB Health','cur.heading':'Knowledge gaps to review',
      'cur.desc':'Proposals mined from the conversations and calls your bot could not answer well. Accepting one updates the knowledge base immediately.',
      'cur.none':'Nothing to review — the queue is clear.','cur.loadfail':'Could not load the review queue.',
      'cur.op.add':'Add','cur.op.update':'Update','cur.op.remove':'Hide',
      'cur.priority':'priority','cur.asked':'asked {n}×','cur.sources':'{n} sources',
      'cur.confidence':'confidence','cur.risk':'risk','cur.window':'window',
      'cur.evidence':'What customers actually said','cur.evidence.none':'No quotes were captured for this cluster.',
      'cur.target':'Target document','cur.diff':'Change against the current chunk',
      'cur.diff.nochunk':'Current chunk unavailable — showing the proposed text only.','cur.proposed':'Proposed content',
      'cur.accept':'Accept','cur.acceptedit':'Accept with edits','cur.decline':'Decline',
      'cur.applied':'Applied to the knowledge base','cur.declinedok':'Declined — this will stop coming back',
      'cur.edit.heading':'Accept with edits','cur.edit.hint':'Edit the wording before it goes into the knowledge base. It is re-chunked and re-embedded on save.',
      'cur.decline.heading':'Why are you declining?','cur.decline.r.nottrue':'Not true',
      'cur.decline.r.covered':'Already covered','cur.decline.r.dontsay':'Don’t want the bot saying this',
      'cur.decline.r.temporary':'Temporary / one-off','cur.decline.pick':'Pick a reason first.',
      'cur.bulk.accept':'Accept selected','cur.bulk.note':'Bulk accept covers additions and updates only — removals are reviewed one at a time.',
      'cur.remove.heading':'Confirm removal','cur.remove.word':'REMOVE','cur.remove.confirm':'Type {word} to confirm.',
      'cur.remove.mismatch':'That does not match — nothing was changed.',
      'cur.remove.note':'This hides the content from answers. Nothing is deleted; an operator can still delete it by hand.',
      'cur.run':'Run curation now','cur.run.started':'Curation run queued',
      'cur.st.pending':'Pending','cur.st.accepted':'Accepted','cur.st.declined':'Declined','cur.st.superseded':'Superseded','cur.st.apply_failed':'Apply failed',
      'cur.filter.state':'State','cur.openjob':'Open call','cur.opensource':'Open conversation',
      'cur.foreign':'⚠ cites evidence from another tenant',
      'tab.bot':'Bot','bot.heading':'Public bot','bot.desc':'How the bot talks to your customers, and what it is allowed to say. Everything here is per tenant — nothing is shared with another workspace.',
      'bot.autopilot':'Autopilot — the bot answers customers with no human in the loop',
      'bot.autopilot.hint':'With autopilot off the bot only drafts replies for an operator to review and send. Nothing reaches a customer unread.',
      'bot.state.live':'Answering customers','bot.state.off':'Drafts only — a human sends every reply','bot.state.killed':'Stopped by CommuniQ',
      'bot.killed.note':'CommuniQ support has stopped autopilot. Your settings are kept; the bot hands every conversation to a human until it is resumed.',
      'bot.needpublic.title':'Autopilot needs at least one published document',
      'bot.needpublic.body':'Knowledge base documents are internal by default, and the public bot may only quote published ones. With none published it would refuse every question. Publish the documents a customer is allowed to read, then turn autopilot on.',
      'bot.needpublic.link':'Open the knowledge base',
      'bot.persona':'Persona','bot.persona.ph':'You are the support assistant for … Be brief, warm and concrete.',
      'bot.greeting':'Greeting','bot.refusal':'Refusal copy — what the bot says when your knowledge base has no answer',
      'bot.refusal.hint':'This is the sentence a customer sees most often. Write it in every language the bot answers in; it should offer a human, not apologise twice.',
      'bot.refusal.missing':'Write the refusal copy in {lang} before turning autopilot on.',
      'bot.lang.en':'English','bot.lang.ka':'Georgian','bot.lang.ru':'Russian',
      'bot.languages':'Languages the bot answers in','bot.languages.pickone':'Pick at least one language.',
      'bot.escalation':'Escalation keywords','bot.escalation.ph':'lawyer, complaint, chargeback',
      'bot.escalation.hint':'Comma-separated. A match hands the conversation to a human immediately, before any answer is generated.',
      'bot.retrieval':'Retrieval & reply limits','bot.minscore':'Minimum score','bot.minhits':'Minimum hits','bot.topk':'Top-k','bot.suggestions':'Suggestions per turn','bot.maxchars':'Max reply characters',
      'bot.caps':'Rate caps','bot.cap.tenant':'Turns / minute (whole workspace)','bot.cap.enduser':'Turns / hour (one customer)',
      'bot.general':'Answer from general knowledge when the knowledge base has nothing',
      'bot.general.risk':'Risk choice, off by default. Left off, the bot refuses with your copy above and offers a human — it can only ever repeat what you published. Turned on, it may answer from the model’s own knowledge, which is not your policy, is not auditable, and can be confidently wrong about your prices, rules and deadlines.',
      'bot.general.confirm':'Let the bot answer from the model’s general knowledge? It will then say things that are not in your knowledge base and that nobody at your company approved.',
      'bot.general.on':'Turn it on',
      'bot.handoff':'Write a short summary for the human who takes over','bot.handoff.hint':'Costs one extra model call, only on handoffs. Off means the operator opens a cold conversation.',
      'bot.save':'Save bot settings','bot.saved':'Bot settings saved','bot.version':'Version',
      'bot.loadfail':'Could not load the bot settings.','bot.unavailable':'Bot settings are not available on this server yet.',
      'adm.bot':'Bot control','kill.heading':'Autopilot kill switch',
      'kill.desc':'The brake. It stops the public bot from answering; conversations hand off to humans instead. Tenant settings are untouched, so resuming is one click.',
      'kill.global':'Stop autopilot for every tenant','kill.global.on':'Stopped everywhere','kill.global.off':'Running normally',
      'kill.tenants':'Per tenant','kill.stop':'Stop','kill.resume':'Resume',
      'kill.confirm.global':'Stop autopilot for every tenant? Every bot hands off to a human until you resume.',
      'kill.confirm.resume.global':'Resume autopilot for every tenant that has it enabled?',
      'kill.confirm.tenant':'Stop autopilot for “{name}”?','kill.confirm.resume':'Resume autopilot for “{name}”?',
      'kill.state.live':'Live','kill.state.stopped':'Stopped','kill.state.off':'Autopilot off',
      'kill.saved':'Kill switch updated','kill.loadfail':'Could not read the kill switch.',
      'kill.unavailable':'The kill switch is not deployed on this server yet.','kill.overviewfail':'Could not read the per-tenant autopilot state.',
      'th.autopilot':'Autopilot',
      /* Publishability (vis.*) and the tenant KB console (tkb.*). The vis.* vocabulary lives
         in the DICT, not in one page, because two surfaces now say Public — the operator
         console and the tenant portal — and they must never disagree about what that word
         promises a customer. Keep this comment free of apostrophes, quotes and backticks:
         scripts/check_i18n.py walks this file as a character stream. */
      'vis.col':'Visibility','vis.all':'All','vis.public':'Public','vis.internal':'Internal',
      'vis.publish':'Publish','vis.unpublish':'Unpublish',
      'vis.bulk.publish':'Publish selected','vis.bulk.unpublish':'Unpublish selected',
      'vis.stat.public':'Published',
      'vis.confirm.publish':'Publish {n} document(s)? The public bot may quote published documents verbatim to your customers.',
      'vis.confirm.unpublish':'Unpublish {n} document(s)? The public bot will stop quoting them.',
      'vis.confirm.publish.one':'Publish “{title}”? The public bot may quote it verbatim to your customers.',
      'vis.confirm.unpublish.one':'Unpublish “{title}”? The public bot will stop quoting it.',
      'vis.done.publish':'Published','vis.done.unpublish':'Unpublished',
      'tkb.tab.maint':'Maintenance','tkb.overview.heading':'Knowledge base health',
      'tkb.params.hint':'The settings retrieval actually runs with. If the configured dimension and the stored column disagree, new embeddings are failing and search has quietly stopped working.',
      'tkb.params.columndim':'Dimension (stored)','tkb.params.chunk':'Chunk size / overlap',
      'tkb.params.threshold':'Retrieval threshold','tkb.params.topk':'Default top-k',
      'tkb.params.metric':'Distance metric','tkb.params.index':'Index type','tkb.params.noembed':'Chunks without an embedding',
      'tkb.loadfail':'Could not load the knowledge base.',
      'tkb.th.source':'Source','tkb.docs.none':'No documents here yet — add some under Import.',
      'tkb.del.confirm':'Delete “{title}”? Its chunks disappear from every answer immediately, and this cannot be undone.',
      'tkb.bulk.delete.confirm':'Delete {n} document(s)? Their chunks disappear from every answer immediately, and this cannot be undone.',
      'tkb.bulk.reembed.confirm':'Re-embed {n} document(s) now? This runs immediately and briefly competes with live search.',
      'tkb.edit.warn':'Saving new text re-chunks and re-embeds this document. Retrieval switches to the new text as soon as that finishes; if it fails the document is marked with an error rather than left half-updated.',
      'tkb.badjson':'Metadata must be valid JSON.','tkb.reembed.done':'{n} chunks re-embedded',
      'tkb.chunks.pick':'Document','tkb.chunks.none':'This document has no chunks yet.',
      'tkb.chunks.pickone':'Choose a document to see its chunks.',
      'tkb.chunks.hint':'Chunks — not documents — are what retrieval matches against. Editing one re-embeds that chunk on the spot; deleting one removes it from every answer.',
      'tkb.chunk.noembed':'no embedding',
      'tkb.chunk.del.confirm':'Delete this chunk? It disappears from every answer immediately.',
      'tkb.chunk.edit.hint':'Saving re-embeds this chunk immediately. The rest of the document is untouched.',
      'tkb.pg.heading':'Retrieval playground',
      'tkb.pg.hint':'Runs exactly the retrieval your bot and call analysis use, and shows which method answered — the vector index or the keyword fallback — plus the score of every chunk. No model is called.',
      'tkb.dup.hint':'Duplicates waste retrieval slots and make the bot repeat itself; copies that contradict each other make it answer the same question differently.',
      'tkb.dup.identical':'documents with identical content','tkb.dup.keep':'keeping',
      'tkb.dup.skipped':'Near-duplicate scan skipped — this knowledge base has too many chunks to compare every pair.',
      'tkb.act.filter':'Action','tkb.act.filter.ph':'import, edit, delete, reembed…',
      'tkb.act.method':'Method','tkb.act.detail':'Detail','tkb.act.actor':'Who',
      'tkb.exp.hint':'Downloads every document in this knowledge base, including the internal ones. The export itself is recorded in the activity log.',
      'tkb.reembed.heading':'Re-embed the whole knowledge base',
      'tkb.reembed.desc':'Rebuilds the vector for every chunk — needed after the embedding model or its dimension changes. It is queued to a background worker and processed at a throttled rate, so it can take a long while on a large knowledge base; search keeps working throughout. Only one re-embed runs at a time.',
      'tkb.reembed.start':'Queue re-embed',
      'tkb.reembed.confirm':'Queue a re-embed of every document? It runs in the background and can take a long time. You cannot start another until it finishes.',
      'tkb.reembed.queued':'Re-embed queued','tkb.reembed.busy':'A re-embed is already queued or running.',
      'tkb.reembed.none':'No re-embed has been run yet.',
      'tkb.reembed.progress':'{done} of {total} documents','tkb.reembed.failed':'{n} failed',
      'tkb.reembed.state.queued':'Queued','tkb.reembed.state.running':'Running','tkb.reembed.state.done':'Finished',
      'tkb.reembed.state.error':'Failed','tkb.reembed.state.cancelled':'Cancelled',
      'sc.readonly':'View only — only workspace owners can edit the scoring rubric.',
    },
    ka: {
      'nav.public':'საჯარო აპი','nav.signin':'შესვლა','nav.logout':'გასვლა','nav.kb':'ცოდნის ბაზა',
      'f.username':'მომხმარებელი','f.password':'პაროლი','f.language':'ენა','f.voice':'ხმა','f.text':'ტექსტი',
      'f.category':'კატეგორია','f.title':'სათაური','f.tags':'ტეგები (მძიმით)','f.name':'სახელი','f.industry':'ინდუსტრია','f.region':'რეგიონი',
      'f.audiofile':'აუდიო ფაილი','f.provider':'პროვაიდერი','f.dimension':'განზომილება','f.model':'მოდელი','f.baseurl':'საბაზო URL',
      'f.anthropic':'Anthropic (Claude) API გასაღები','f.eleven':'ElevenLabs API გასაღები','f.claudemodel':'Claude მოდელი','f.sttmodel':'Scribe (STT) მოდელი','f.ttsmodel':'TTS მოდელი','f.voiceid':'TTS ხმის ID','f.openaikey':'API გასაღები (openai)',
      'btn.signin':'შესვლა','btn.save':'შენახვა','btn.savesettings':'პარამეტრების შენახვა','btn.savelimits':'ლიმიტების შენახვა','btn.refresh':'განახლება','btn.delete':'წაშლა','btn.cancel':'გაუქმება','btn.test':'ტესტი','btn.testconn':'კავშირის ტესტი','btn.testdeep':'სრული ტესტი (ღრმა)',
      'cap.database':'მონაცემთა ბაზა','cap.ffmpeg':'აუდიოს გარდაქმნა (ffmpeg)','cap.voices':'ElevenLabs · ხმების სია','cap.stt':'ElevenLabs · მეტყველება ტექსტად','cap.tts':'ElevenLabs · ტექსტი მეტყველებად','cap.ttska':'ElevenLabs · ქართული TTS','cap.embeddings':'ემბედინგები','cap.claude':'Claude · ანალიზი','cap.factcheck':'Claude · ფაქტების შემოწმება','cap.scoring':'Claude · შეფასება',
      'cap.fixscope':'გამოსწორება: ElevenLabs-ში გახსენით Settings → API Keys → Edit ამ გასაღებზე, ჩართეთ ნებართვა „{scope}“, შეინახეთ და ხელახლა შეამოწმეთ.',
      'adm.testnote':'თითოეული შესაძლებლობა რეალურად მოწმდება — ტესტი ხარჯავს წამის მცირე ნაწილს მეტყველების ამოცნობაზე და რამდენიმე სიმბოლოს ხმის სინთეზზე, რადგან ElevenLabs არ იძლევა გასაღების ნებართვების წაკითხვის საშუალებას. „ღრმა“ დამატებით ამოწმებს ფაქტების შემოწმებისა და შეფასების ხელსაწყოებს.','btn.search':'ძებნა','btn.import':'იმპორტი','btn.analyze':'ანალიზი','btn.synth':'ხმის გენერაცია','btn.create':'ტენანტის შექმნა','btn.adduser':'მომხმარებლის დამატება','btn.rotate':'განახლება','btn.remove':'წაშლა','btn.apikey':'API გასაღები','btn.users':'მომხმარებლები','btn.chunks':'ფრაგმენტები',
      'hero.eyebrow':'CommuniQ ხმის AI','hero.title':'ისაუბრე და გაიგე ყველა ზარი.','hero.sub':'გადააქციე ტექსტი ბუნებრივ მეტყველებად — ქართულის ჩათვლით — ან ატვირთე ჩანაწერი და მიიღე მყისიერი AI ანალიზი.',
      'tab.tts':'ტექსტი მეტყველებად','tab.analyze':'აუდიოს ანალიზი','tab.kb':'ცოდნის ბაზა','tab.history':'ისტორია','tab.scoring':'შეფასება',
      'tts.heading':'ტექსტიდან მეტყველების გენერაცია','tts.text_ph':'აკრიფე სათქმელი… (ინგლისური, რუსული ან ქართული)',
      'an.heading':'ატვირთე ჩანაწერი ანალიზისთვის','an.heading_kb':'გააანალიზე ზარი — იყენებს შენს ცოდნის ბაზას',
      'drop.title':'ჩააგდე აუდიო ფაილი აქ ან დააჭირე ასარჩევად','drop.sub':'ნებისმიერი აუდიო ან ვიდეო ფაილი — გადაიწერება ElevenLabs Scribe-ით, ანალიზი Claude-ით','drop.sub_kb':'ჯერ გადაიწერება, შემდეგ ანალიზდება შენს ცოდნის ბაზასთან',
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
      'err.toolarge':'ფაილი ძალიან დიდია ასატვირთად. გამოიყენეთ უფრო მოკლე ან პატარა ჩანაწერი.','err.timeout':'ანალიზმა დიდი დრო წაიღო და ვადა ამოიწურა. სცადეთ უფრო მოკლე ჩანაწერი.','err.unavailable':'სერვისი დროებით მიუწვდომელია. გთხოვთ სცადოთ ცოტა ხანში.','err.http':'მოთხოვნა ვერ შესრულდა (HTTP {status}).','err.badresp':'სერვერმა მოულოდნელი პასუხი დააბრუნა. გთხოვთ სცადოთ თავიდან.',
      'quota.using':'თქვენ იყენებთ CommuniQ-ს ანონიმურად —','quota.analyses':'ანალიზი','quota.clips':'აუდიო კლიპი','quota.left':'დარჩა დღეს.','quota.more':'ცოდნის ბაზისა და მაღალი ლიმიტებისთვის.','quota.disabled':'ანონიმური წვდომა გათიშულია.',
      'fc.title':'ცოდნის ბაზასთან შემოწმება','fc.accuracy':'სიზუსტე','fc.supported':'დადასტურებული','fc.contradicted':'გაბათილებული','fc.notinkb':'ბაზაში არ არის','fc.misinfo':'შესაძლო მცდარი ინფორმაცია','fc.nochecked':'შესამოწმებელი მტკიცება ვერ მოიძებნა.',
      'adm.voices':'ხმები','adm.voicevis':'მომხმარებლისთვის ხილული ხმები','f.restrictvoices':'მომხმარებელს მხოლოდ მონიშნული ხმები აჩვენე','f.defaultvoice':'ნაგულისხმევი ხმა','v.hint':'მოუნიშნავი ხმები დაიმალება მომხმარებლის სიიდან და TTS მათ არ მიიღებს. თუ ველი მოუნიშნავია — ყველა ხმა ჩანს. სისტემური ნაგულისხმევები (მათ შორის ქართული ხმა) ყოველთვის ჩართულია.','v.search':'ხმების ძებნა…','v.selected':'მონიშნული','v.system':'სისტემური','v.nopreview':'გადასმენა არ არის','v.unavailable':'არ არის ამ ElevenLabs ანგარიშში','v.pickone':'მონიშნე მინიმუმ ერთი ხმა ან მოხსენი შეზღუდვა.','v.loadfail':'ხმების ჩატვირთვა ვერ მოხერხდა. შეამოწმე API გასაღები ინტეგრაციებში.','msg.voicegone':'ეს ხმა აღარ არის ხელმისაწვდომი. სია განახლდა.',
      'fc.allclaims':'ყველა მტკიცება','pg.tab.retrieval':'მოძიება','pg.tab.score':'პასუხის შეფასება','pg.ans.label':'ოპერატორის პასუხი (ნებისმიერ ენაზე)','pg.ans.ph':'ჩასვი ან აკრიფე ოპერატორის პასუხი — შეფასდება ამ კლიენტის რუბრიკით…','pg.ans.run':'პასუხის შეფასება','pg.ans.hint':'ფასდება კლიენტის აქტიური რუბრიკით; მტკიცებები მოწმდება მის ცოდნის ბაზასთან.','pg.ans.norubric':'აქტიური რუბრიკა არ არის — ჯერ განსაზღვრე შეფასების ტაბში.','pg.ans.empty':'შეიყვანე პასუხი შესაფასებლად.','pg.ans.usingv':'რუბრიკის ვერსია',
      'tab.playground':'ტესტ-სივრცე','pg.heading':'შეაფასე ზარი ან პასუხი','pg.hint':'შეაფასე დაწერილი პასუხი, ან ატვირთე ზარის ჩანაწერი (აუდიო ან ვიდეო) — გადაიწერება და შეფასდება შენი რუბრიკითა და ცოდნის ბაზით.','pg.mode.text':'ტექსტი','pg.mode.audio':'აუდიო / ვიდეო','pg.audiolabel':'ზარის ჩანაწერი (აუდიო ან ვიდეო)','pg.run':'შეფასება','pg.audioempty':'აირჩიე აუდიო ან ვიდეო ფაილი.',
      'kba.title':'ცოდნის ბაზის მართვა','kba.tenant':'ტენანტი','kba.selecttenant':'აირჩიეთ ტენანტი მისი ცოდნის ბაზის სამართავად.',
      'kba.tab.overview':'მიმოხილვა','kba.tab.documents':'დოკუმენტები','kba.tab.import':'იმპორტი','kba.tab.playground':'სათამაშო','kba.tab.duplicates':'დუბლიკატები','kba.tab.activity':'აქტივობა',
      'kba.stat.documents':'დოკუმენტები','kba.stat.chunks':'ფრაგმენტები','kba.stat.coverage':'ემბედინგის დაფარვა','kba.stat.failed':'ჩავარდნილი იმპორტი','kba.stat.tokens':'დაახლ. ტოკენები','kba.stat.lastupd':'ბოლო განახლება','kba.stat.inprogress':'მიმდინარე',
      'kba.params':'აქტიური კონფიგურაცია','kba.export':'ექსპორტი','kba.exportcsv':'CSV ექსპორტი','kba.reembedall':'ხელახლა ემბედინგი','kba.refresh':'განახლება',
      'kba.f.status':'სტატუსი','kba.f.type':'ტიპი','kba.f.tag':'ტეგი','kba.f.search':'ძებნა სათაური/კონტენტი','kba.f.all':'ყველა',
      'kba.selected':'არჩეული','kba.bulk.delete':'წაშლა','kba.bulk.reembed':'ხელახლა ემბედინგი','kba.bulk.retag':'ტეგების შეცვლა','kba.selectall':'ყველას მონიშვნა',
      'kba.edit':'რედაქტირება','kba.chunks':'ფრაგმენტები','kba.reembed':'ხელახლა ემბედინგი','kba.delete':'წაშლა','kba.save':'შენახვა','kba.nodocs':'დოკუმენტები არ არის. დაამატეთ ქვემოთ.',
      'kba.doc.title':'სათაური','kba.doc.type':'კატეგორია','kba.doc.tags':'ტეგები','kba.doc.meta':'მეტამონაცემები (JSON)','kba.doc.content':'კონტენტი (რედაქტირება ხელახლა დაანაწევრებს და ემბედავს)',
      'kba.pg.query':'მოთხოვნა (ნებისმიერ ენაზე)','kba.pg.topk':'Top-k','kba.pg.threshold':'ზღვარი','kba.pg.run':'ძებნის გაშვება','kba.pg.method':'მეთოდი','kba.pg.nohits':'ფრაგმენტები ვერ მოიძებნა.',
      'retr.m.vector':'სემანტიკური','retr.m.keyword':'ტექსტური დამთხვევა','retr.m.none':'არცერთი',
      'retr.top':'საუკეთესო ქულა','retr.spread':'გაფანტვა','retr.margin':'სხვაობა მეორესთან',
      'retr.level.high':'სანდო','retr.level.medium':'საშუალო','retr.level.low':'სუსტი','retr.level.none':'დამთხვევის გარეშე',
      'retr.opendoc':'დოკუმენტის გახსნა',
      'retr.flag.shown':'ქვემოთ მაინც ჩამოთვლილია ყველაზე ახლო ფრაგმენტები, საუკეთესოდან დაწყებული.',
      'retr.flag.empty_kb':'ჯერ არაფერია მოსაძებნი',
      'retr.flag.empty_kb.b':'ეს ცოდნის ბაზა ცარიელია, ამიტომ პასუხის პოვნა შეუძლებელია. გახსენი ჩანართი „იმპორტი“ და ჯერ დაამატე შენი წესები, ხშირი კითხვები ან სასაუბრო სცენარები.',
      'retr.flag.unavailable':'ძებნა ვერ შესრულდა',
      'retr.flag.unavailable.b':'ძებნა ბოლომდე არ შესრულდა, ამიტომ ეს არაფერს ამბობს შენს ცოდნის ბაზაზე — ნუ წაიკითხავ როგორც „პასუხი ვერ მოიძებნა“. სცადე ხელახლა ცოტა ხანში; თუ პრობლემა გრძელდება, შეატყობინე CommuniQ-ის ოპერატორს.',
      'retr.flag.no_hits':'დამთხვევა ვერ მოიძებნა',
      'retr.flag.no_hits.b':'ამ კითხვაზე არცერთი ფრაგმენტი არ დაბრუნებულა. სცადე კითხვა ისე დასვა, როგორც კლიენტი დასვამდა, ან დაამატე დოკუმენტი, რომელიც მას პასუხობს.',
      'retr.flag.keyword_fallback':'სარეზერვო ძებნა — სემანტიკური ძებნა მიუწვდომელია',
      'retr.flag.keyword_fallback.b':'ემბედინგების სერვისს ვერ დავუკავშირდით, ამიტომ ეს ფრაგმენტები შეირჩა ტექსტური მსგავსებით და არა მნიშვნელობით; მათი ქულები ჩვეულებრივ ქულებს არ ედრება. აღდგენამდე შედეგები ჩვეულებრივზე სუსტი იქნება — შეატყობინე CommuniQ-ის ოპერატორს.',
      'retr.flag.flat_distribution':'ნათელი დამთხვევა არ არის',
      'retr.flag.flat_distribution.b':'ყველა ფრაგმენტმა თითქმის ერთნაირი ქულა მიიღო, რაც ჩვეულებრივ ნიშნავს, რომ ცოდნის ბაზაში ამ კითხვაზე პასუხი არ არის — ქვემოთ მხოლოდ ყველაზე ახლო ტექსტია და არა ნამდვილი დამთხვევა. დაამატე ამ თემის დოკუმენტი ან გამოიყენე ის სიტყვები, რომლებიც შენს დოკუმენტებშია.',
      'retr.flag.low_score':'მხოლოდ სუსტი დამთხვევები',
      'retr.flag.low_score.b':'ყველაზე ახლო ფრაგმენტები კითხვას მხოლოდ ზერელედ უკავშირდება. სანამ დაეყრდნობი, გადაამოწმე, ნამდვილად ფარავს თუ არა რომელიმე დოკუმენტი ამ თემას, და თუ არა — დაამატე.',
      'retr.flag.generic':'დაბალი სანდოობის შედეგი',
      'retr.flag.generic.b':'ამ ძებნამ ცოდნის ბაზაში დამაჯერებელი დამთხვევა ვერ იპოვა.',
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
      'sc.normalize':'100%-ზე მოყვანა','sc.mustbe100':'წონები უნდა შეადგენდეს 100%-ს (ახლა {total}%).',
      'cur.tab':'ცოდნის ჯანმრთელობა','cur.heading':'გადასახედი ხარვეზები ცოდნის ბაზაში',
      'cur.desc':'წინადადებები, აღებული იმ საუბრებიდან და ზარებიდან, რომლებზეც ბოტმა კარგად ვერ უპასუხა. მიღება მაშინვე განაახლებს ცოდნის ბაზას.',
      'cur.none':'გადასახედი არაფერია — რიგი ცარიელია.','cur.loadfail':'რიგის ჩატვირთვა ვერ მოხერხდა.',
      'cur.op.add':'დამატება','cur.op.update':'განახლება','cur.op.remove':'დამალვა',
      'cur.priority':'პრიორიტეტი','cur.asked':'იკითხეს {n}-ჯერ','cur.sources':'{n} წყარო',
      'cur.confidence':'დარწმუნებულობა','cur.risk':'რისკი','cur.window':'პერიოდი',
      'cur.evidence':'რას ამბობდნენ მომხმარებლები','cur.evidence.none':'ამ ჯგუფისთვის ციტატები არ შენახულა.',
      'cur.target':'სამიზნე დოკუმენტი','cur.diff':'ცვლილება მიმდინარე ფრაგმენტთან',
      'cur.diff.nochunk':'მიმდინარე ფრაგმენტი მიუწვდომელია — ნაჩვენებია მხოლოდ შემოთავაზებული ტექსტი.','cur.proposed':'შემოთავაზებული ტექსტი',
      'cur.accept':'მიღება','cur.acceptedit':'მიღება რედაქტირებით','cur.decline':'უარყოფა',
      'cur.applied':'ცოდნის ბაზაში დაემატა','cur.declinedok':'უარყოფილია — აღარ გამოჩნდება',
      'cur.edit.heading':'მიღება რედაქტირებით','cur.edit.hint':'დაარედაქტირე ტექსტი ბაზაში შესვლამდე. შენახვისას ხელახლა დაინაწევრება და დაემბედება.',
      'cur.decline.heading':'რატომ უარყოფ?','cur.decline.r.nottrue':'არ არის სიმართლე',
      'cur.decline.r.covered':'უკვე დაფარულია','cur.decline.r.dontsay':'არ მინდა ბოტმა ეს თქვას',
      'cur.decline.r.temporary':'დროებითი / ერთჯერადი','cur.decline.pick':'ჯერ აირჩიე მიზეზი.',
      'cur.bulk.accept':'არჩეულის მიღება','cur.bulk.note':'მასობრივი მიღება მოქმედებს მხოლოდ დამატებასა და განახლებაზე — დამალვა ცალკე მოწმდება.',
      'cur.remove.heading':'დაადასტურე დამალვა','cur.remove.word':'დამალვა','cur.remove.confirm':'დასადასტურებლად აკრიფე {word}.',
      'cur.remove.mismatch':'არ ემთხვევა — ცვლილება არ შესულა.',
      'cur.remove.note':'ეს დამალავს ტექსტს პასუხებიდან. არაფერი იშლება; ოპერატორს შეუძლია ხელით წაშალოს.',
      'cur.run':'კურაციის გაშვება','cur.run.started':'კურაცია რიგში დადგა',
      'cur.st.pending':'მოლოდინში','cur.st.accepted':'მიღებული','cur.st.declined':'უარყოფილი','cur.st.superseded':'ჩანაცვლებული','cur.st.apply_failed':'გამოყენება ჩავარდა',
      'cur.filter.state':'სტატუსი','cur.openjob':'ზარის გახსნა','cur.opensource':'საუბრის გახსნა',
      'cur.foreign':'⚠ იყენებს სხვა ტენანტის მტკიცებულებას',
      'tab.bot':'ბოტი','bot.heading':'საჯარო ბოტი','bot.desc':'როგორ ესაუბრება ბოტი შენს კლიენტებს და რის თქმის უფლება აქვს. ყველა პარამეტრი ამ ტენანტისაა — სხვა სამუშაო სივრცეს არაფერი ეზიარება.',
      'bot.autopilot':'ავტოპილოტი — ბოტი პასუხობს კლიენტს ადამიანის ჩარევის გარეშე',
      'bot.autopilot.hint':'გამორთული ავტოპილოტის დროს ბოტი მხოლოდ ამზადებს პასუხის მონახაზს, რომელსაც ოპერატორი ამოწმებს და აგზავნის. კლიენტამდე წაუკითხავი არაფერი მიდის.',
      'bot.state.live':'პასუხობს კლიენტებს','bot.state.off':'მხოლოდ მონახაზები — პასუხს ადამიანი აგზავნის','bot.state.killed':'შეჩერებულია CommuniQ-ის მიერ',
      'bot.killed.note':'CommuniQ-ის მხარდაჭერამ ავტოპილოტი შეაჩერა. პარამეტრები შენახულია; აღდგენამდე ბოტი ყველა საუბარს ადამიანს გადასცემს.',
      'bot.needpublic.title':'ავტოპილოტს სჭირდება მინიმუმ ერთი გამოქვეყნებული დოკუმენტი',
      'bot.needpublic.body':'ცოდნის ბაზის დოკუმენტები ნაგულისხმევად შიდაა და საჯარო ბოტს მხოლოდ გამოქვეყნებულის ციტირება შეუძლია. თუ არცერთი არ არის გამოქვეყნებული, ბოტი ყველა კითხვაზე უარს იტყვის. გამოაქვეყნე ის დოკუმენტები, რომელთა წაკითხვის უფლებაც კლიენტს აქვს, შემდეგ ჩართე ავტოპილოტი.',
      'bot.needpublic.link':'ცოდნის ბაზის გახსნა',
      'bot.persona':'პერსონა','bot.persona.ph':'შენ ხარ … მხარდაჭერის ასისტენტი. იყავი მოკლე, თბილი და კონკრეტული.',
      'bot.greeting':'მისალმება','bot.refusal':'უარის ტექსტი — რას ამბობს ბოტი, როცა ცოდნის ბაზაში პასუხი არ არის',
      'bot.refusal.hint':'ეს არის წინადადება, რომელსაც კლიენტი ყველაზე ხშირად ხედავს. დაწერე ყველა ენაზე, რომელზეც ბოტი პასუხობს; მან ადამიანი უნდა შესთავაზოს, ორჯერ ბოდიში კი არა.',
      'bot.refusal.missing':'ავტოპილოტის ჩართვამდე დაწერე უარის ტექსტი {lang} ენაზე.',
      'bot.lang.en':'ინგლისური','bot.lang.ka':'ქართული','bot.lang.ru':'რუსული',
      'bot.languages':'ენები, რომლებზეც ბოტი პასუხობს','bot.languages.pickone':'აირჩიე მინიმუმ ერთი ენა.',
      'bot.escalation':'ესკალაციის საკვანძო სიტყვები','bot.escalation.ph':'ადვოკატი, საჩივარი, თანხის დაბრუნება',
      'bot.escalation.hint':'მძიმით გამოყოფილი. დამთხვევისას საუბარი მაშინვე ადამიანს გადაეცემა, პასუხის გენერაციამდე.',
      'bot.retrieval':'ძიება და პასუხის ლიმიტები','bot.minscore':'მინიმალური ქულა','bot.minhits':'მინიმალური დამთხვევები','bot.topk':'Top-k','bot.suggestions':'შემოთავაზება ერთ სვლაზე','bot.maxchars':'პასუხის მაქს. სიმბოლოები',
      'bot.caps':'სიხშირის ლიმიტები','bot.cap.tenant':'სვლა / წუთში (მთელი სივრცე)','bot.cap.enduser':'სვლა / საათში (ერთი კლიენტი)',
      'bot.general':'უპასუხოს ზოგადი ცოდნით, როცა ცოდნის ბაზაში არაფერია',
      'bot.general.risk':'რისკის არჩევანი, ნაგულისხმევად გამორთული. გამორთულია — ბოტი ზემოთ დაწერილი ტექსტით უარს ამბობს და ადამიანს სთავაზობს; ის მხოლოდ იმას იმეორებს, რაც შენ გამოაქვეყნე. ჩართვისას მან შეიძლება მოდელის საკუთარი ცოდნით უპასუხოს — ეს არ არის შენი პოლიტიკა, არ არის აუდიტირებადი და შეიძლება დარწმუნებით შეცდეს შენს ფასებში, წესებსა და ვადებში.',
      'bot.general.confirm':'ნება დართო ბოტს, უპასუხოს მოდელის ზოგადი ცოდნით? მაშინ ის იტყვის რაღაცებს, რაც შენს ცოდნის ბაზაში არ არის და რომელიც შენს კომპანიაში არავის დაუმტკიცებია.',
      'bot.general.on':'ჩართვა',
      'bot.handoff':'დაწეროს მოკლე შეჯამება ადამიანისთვის, რომელიც საუბარს გადაიბარებს','bot.handoff.hint':'ღირს ერთი დამატებითი მოდელის გამოძახება, მხოლოდ გადაცემისას. გამორთულია — ოპერატორი ცივ საუბარს ხსნის.',
      'bot.save':'ბოტის პარამეტრების შენახვა','bot.saved':'ბოტის პარამეტრები შენახულია','bot.version':'ვერსია',
      'bot.loadfail':'ბოტის პარამეტრები ვერ ჩაიტვირთა.','bot.unavailable':'ბოტის პარამეტრები ამ სერვერზე ჯერ ხელმისაწვდომი არ არის.',
      'adm.bot':'ბოტის კონტროლი','kill.heading':'ავტოპილოტის გამორთვის ღილაკი',
      'kill.desc':'სამუხრუჭე. აჩერებს საჯარო ბოტის პასუხებს; საუბრები ადამიანებს გადაეცემა. ტენანტის პარამეტრები ხელუხლებელია, ამიტომ აღდგენა ერთი დაწკაპუნებაა.',
      'kill.global':'ავტოპილოტის შეჩერება ყველა ტენანტისთვის','kill.global.on':'შეჩერებულია ყველგან','kill.global.off':'მუშაობს ნორმალურად',
      'kill.tenants':'ტენანტების მიხედვით','kill.stop':'შეჩერება','kill.resume':'აღდგენა',
      'kill.confirm.global':'შევაჩერო ავტოპილოტი ყველა ტენანტისთვის? ყველა ბოტი ადამიანს გადასცემს საუბარს აღდგენამდე.',
      'kill.confirm.resume.global':'აღვადგინო ავტოპილოტი ყველა ტენანტისთვის, ვისაც ის ჩართული აქვს?',
      'kill.confirm.tenant':'შევაჩერო ავტოპილოტი „{name}“-სთვის?','kill.confirm.resume':'აღვადგინო ავტოპილოტი „{name}“-სთვის?',
      'kill.state.live':'აქტიური','kill.state.stopped':'შეჩერებული','kill.state.off':'ავტოპილოტი გამორთულია',
      'kill.saved':'გამორთვის ღილაკი განახლდა','kill.loadfail':'გამორთვის ღილაკის მდგომარეობა ვერ წაიკითხა.',
      'kill.unavailable':'გამორთვის ღილაკი ამ სერვერზე ჯერ არ არის განთავსებული.','kill.overviewfail':'ტენანტების ავტოპილოტის მდგომარეობა ვერ წაიკითხა.',
      'th.autopilot':'ავტოპილოტი',
      'vis.col':'ხილვადობა','vis.all':'ყველა','vis.public':'საჯარო','vis.internal':'შიდა',
      'vis.publish':'გამოქვეყნება','vis.unpublish':'გამოქვეყნების გაუქმება',
      'vis.bulk.publish':'არჩეულის გამოქვეყნება','vis.bulk.unpublish':'არჩეულის დამალვა',
      'vis.stat.public':'გამოქვეყნებული',
      'vis.confirm.publish':'გამოქვეყნდეს {n} დოკუმენტი? საჯარო ბოტს შეუძლია გამოქვეყნებული დოკუმენტი სიტყვასიტყვით მოუყვეს თქვენს კლიენტს.',
      'vis.confirm.unpublish':'გაუქმდეს {n} დოკუმენტის გამოქვეყნება? საჯარო ბოტი მათ აღარ გამოიყენებს.',
      'vis.confirm.publish.one':'გამოქვეყნდეს „{title}“? საჯარო ბოტს შეუძლია იგი სიტყვასიტყვით მოუყვეს თქვენს კლიენტს.',
      'vis.confirm.unpublish.one':'გაუქმდეს „{title}“-ის გამოქვეყნება? საჯარო ბოტი მას აღარ გამოიყენებს.',
      'vis.done.publish':'გამოქვეყნდა','vis.done.unpublish':'გამოქვეყნება გაუქმდა',
      'tkb.tab.maint':'მოვლა','tkb.overview.heading':'ცოდნის ბაზის მდგომარეობა',
      'tkb.params.hint':'პარამეტრები, რომლითაც ძიება რეალურად მუშაობს. თუ კონფიგურაციის განზომილება და ბაზაში შენახული არ ემთხვევა, ახალი ემბედინგები ვერ იქმნება და ძიებამ უხმაუროდ მუშაობა შეწყვიტა.',
      'tkb.params.columndim':'განზომილება (შენახული)','tkb.params.chunk':'ფრაგმენტის ზომა / გადაფარვა',
      'tkb.params.threshold':'მოძიების ზღვარი','tkb.params.topk':'ნაგულისხმევი top-k',
      'tkb.params.metric':'მანძილის მეტრიკა','tkb.params.index':'ინდექსის ტიპი','tkb.params.noembed':'ფრაგმენტები ემბედინგის გარეშე',
      'tkb.loadfail':'ცოდნის ბაზა ვერ ჩაიტვირთა.',
      'tkb.th.source':'წყარო','tkb.docs.none':'დოკუმენტები ჯერ არ არის — დაამატე „იმპორტის“ ტაბში.',
      'tkb.del.confirm':'წაიშალოს „{title}“? მისი ფრაგმენტები მაშინვე გაქრება ყველა პასუხიდან და მოქმედება შეუქცევადია.',
      'tkb.bulk.delete.confirm':'წაიშალოს {n} დოკუმენტი? მათი ფრაგმენტები მაშინვე გაქრება ყველა პასუხიდან და მოქმედება შეუქცევადია.',
      'tkb.bulk.reembed.confirm':'ხელახლა დაემბედოს {n} დოკუმენტი ახლავე? შესრულდება მაშინვე და მოკლედ დაუკავებს რესურსს ცოცხალ ძიებას.',
      'tkb.edit.warn':'ახალი ტექსტის შენახვა ხელახლა დაანაწევრებს და დაემბედავს ამ დოკუმენტს. ძიება ახალ ტექსტზე გადავა დასრულებისთანავე; წარუმატებლობისას დოკუმენტი შეცდომით მოინიშნება და ნახევრად განახლებული არ დარჩება.',
      'tkb.badjson':'მეტამონაცემები უნდა იყოს ვალიდური JSON.','tkb.reembed.done':'{n} ფრაგმენტი ხელახლა დაემბედა',
      'tkb.chunks.pick':'დოკუმენტი','tkb.chunks.none':'ამ დოკუმენტს ფრაგმენტები ჯერ არ აქვს.',
      'tkb.chunks.pickone':'აირჩიე დოკუმენტი ფრაგმენტების სანახავად.',
      'tkb.chunks.hint':'ძიება ემთხვევა ფრაგმენტებს და არა დოკუმენტებს. ერთის რედაქტირება მაშინვე ხელახლა ემბედავს მას; წაშლა კი მას ყველა პასუხიდან შლის.',
      'tkb.chunk.noembed':'ემბედინგი არ აქვს',
      'tkb.chunk.del.confirm':'წაიშალოს ეს ფრაგმენტი? ის მაშინვე გაქრება ყველა პასუხიდან.',
      'tkb.chunk.edit.hint':'შენახვა მაშინვე ხელახლა ემბედავს ამ ფრაგმენტს. დოკუმენტის დანარჩენი ნაწილი ხელუხლებელია.',
      'tkb.pg.heading':'მოძიების ტესტ-სივრცე',
      'tkb.pg.hint':'უშვებს ზუსტად იმ მოძიებას, რომელსაც შენი ბოტი და ზარების ანალიზი იყენებს, და აჩვენებს რომელმა მეთოდმა უპასუხა — ვექტორულმა ინდექსმა თუ საკვანძო სიტყვების სარეზერვო ძიებამ — და ყოველი ფრაგმენტის ქულას. მოდელი არ გამოიძახება.',
      'tkb.dup.hint':'დუბლიკატები ხარჯავს მოძიების ადგილებს და ბოტს იმეორებინებს; ურთიერთსაწინააღმდეგო ასლები კი ერთსა და იმავე კითხვაზე სხვადასხვა პასუხს აჩენს.',
      'tkb.dup.identical':'დოკუმენტს იდენტური შიგთავსი აქვს','tkb.dup.keep':'რჩება',
      'tkb.dup.skipped':'მსგავსი დუბლიკატების სკანირება გამოტოვებულია — ამ ცოდნის ბაზაში ძალიან ბევრი ფრაგმენტია ყველა წყვილის შესადარებლად.',
      'tkb.act.filter':'მოქმედება','tkb.act.filter.ph':'იმპორტი, რედაქტირება, წაშლა, ემბედინგი…',
      'tkb.act.method':'მეთოდი','tkb.act.detail':'დეტალი','tkb.act.actor':'ვინ',
      'tkb.exp.hint':'ჩამოტვირთავს ამ ცოდნის ბაზის ყველა დოკუმენტს, შიდას ჩათვლით. თავად ექსპორტი აქტივობის ჟურნალში ფიქსირდება.',
      'tkb.reembed.heading':'მთელი ცოდნის ბაზის ხელახლა ემბედინგი',
      'tkb.reembed.desc':'ხელახლა აშენებს ვექტორს ყოველი ფრაგმენტისთვის — საჭიროა ემბედინგის მოდელის ან განზომილების შეცვლის შემდეგ. ეშვება ფონურ დამმუშავებელში შეზღუდული სიჩქარით, ამიტომ დიდ ბაზაზე შეიძლება დიდხანს გაგრძელდეს; ძიება მთელი ამ დროის განმავლობაში მუშაობს. ერთდროულად მხოლოდ ერთი პროცესი მიმდინარეობს.',
      'tkb.reembed.start':'რიგში დაყენება',
      'tkb.reembed.confirm':'დადგეს რიგში ყველა დოკუმენტის ხელახლა ემბედინგი? შესრულდება ფონურად და შეიძლება დიდხანს გაგრძელდეს. დასრულებამდე ახლის დაწყება ვერ მოხერხდება.',
      'tkb.reembed.queued':'ხელახლა ემბედინგი რიგში დადგა','tkb.reembed.busy':'ხელახლა ემბედინგი უკვე რიგშია ან მიმდინარეობს.',
      'tkb.reembed.none':'ხელახლა ემბედინგი ჯერ არ გაშვებულა.',
      'tkb.reembed.progress':'{done} / {total} დოკუმენტი','tkb.reembed.failed':'{n} ჩავარდა',
      'tkb.reembed.state.queued':'რიგში','tkb.reembed.state.running':'მიმდინარეობს','tkb.reembed.state.done':'დასრულდა',
      'tkb.reembed.state.error':'ჩავარდა','tkb.reembed.state.cancelled':'გაუქმდა',
      'sc.readonly':'მხოლოდ სანახავად — შეფასების რუბრიკის რედაქტირება მხოლოდ სამუშაო სივრცის მფლობელს შეუძლია.',
    },
    ru: {
      'nav.public':'Публичное приложение','nav.signin':'Войти','nav.logout':'Выйти','nav.kb':'База знаний',
      'f.username':'Имя пользователя','f.password':'Пароль','f.language':'Язык','f.voice':'Голос','f.text':'Текст',
      'f.category':'Категория','f.title':'Заголовок','f.tags':'Теги (через запятую)','f.name':'Название','f.industry':'Отрасль','f.region':'Регион',
      'f.audiofile':'Аудиофайл','f.provider':'Провайдер','f.dimension':'Размерность','f.model':'Модель','f.baseurl':'Базовый URL',
      'f.anthropic':'API-ключ Anthropic (Claude)','f.eleven':'API-ключ ElevenLabs','f.claudemodel':'Модель Claude','f.sttmodel':'Модель Scribe (STT)','f.ttsmodel':'Модель TTS','f.voiceid':'ID голоса TTS','f.openaikey':'API-ключ (только openai)',
      'btn.signin':'Войти','btn.save':'Сохранить','btn.savesettings':'Сохранить настройки','btn.savelimits':'Сохранить лимиты','btn.refresh':'Обновить','btn.delete':'Удалить','btn.cancel':'Отмена','btn.test':'Тест','btn.testconn':'Проверить подключения','btn.testdeep':'Полная проверка (глубокая)',
      'cap.database':'База данных','cap.ffmpeg':'Перекодирование аудио (ffmpeg)','cap.voices':'ElevenLabs · список голосов','cap.stt':'ElevenLabs · речь в текст','cap.tts':'ElevenLabs · текст в речь','cap.ttska':'ElevenLabs · грузинский TTS','cap.embeddings':'Эмбеддинги','cap.claude':'Claude · анализ','cap.factcheck':'Claude · проверка фактов','cap.scoring':'Claude · оценка',
      'cap.fixscope':'Как исправить: в ElevenLabs откройте Settings → API Keys → Edit для этого ключа, включите разрешение «{scope}», сохраните и повторите проверку.',
      'adm.testnote':'Каждая возможность проверяется по-настоящему — тест расходует доли секунды распознавания речи и несколько символов синтеза, поскольку ElevenLabs не позволяет прочитать разрешения ключа. «Глубокая» проверка дополнительно задействует инструменты проверки фактов и оценки.','btn.search':'Поиск','btn.import':'Импорт','btn.analyze':'Анализ','btn.synth':'Синтез речи','btn.create':'Создать арендатора','btn.adduser':'Добавить пользователя','btn.rotate':'Обновить','btn.remove':'Удалить','btn.apikey':'API-ключ','btn.users':'Пользователи','btn.chunks':'Фрагменты',
      'hero.eyebrow':'CommuniQ Голосовой AI','hero.title':'Говорите и понимайте каждый звонок.','hero.sub':'Превратите текст в естественную речь — включая грузинский — или загрузите запись и получите мгновенный AI-анализ.',
      'tab.tts':'Текст в речь','tab.analyze':'Анализ аудио','tab.kb':'База знаний','tab.history':'История','tab.scoring':'Оценка',
      'tts.heading':'Генерация речи из текста','tts.text_ph':'Введите текст… (английский, русский или грузинский)',
      'an.heading':'Загрузите запись для анализа','an.heading_kb':'Анализ звонка — использует вашу базу знаний',
      'drop.title':'Перетащите аудиофайл сюда или нажмите для выбора','drop.sub':'Любой аудио- или видеофайл — расшифровка ElevenLabs Scribe, анализ Claude','drop.sub_kb':'Сначала расшифровка, затем анализ по вашей базе знаний',
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
      'err.toolarge':'Файл слишком большой для загрузки. Используйте более короткую или маленькую запись.','err.timeout':'Анализ занял слишком много времени и превысил лимит. Попробуйте более короткую запись.','err.unavailable':'Сервис временно недоступен. Пожалуйста, попробуйте через минуту.','err.http':'Запрос не выполнен (HTTP {status}).','err.badresp':'Сервер вернул неожиданный ответ. Пожалуйста, попробуйте снова.',
      'quota.using':'Вы используете CommuniQ анонимно —','quota.analyses':'анализов','quota.clips':'аудиоклипов','quota.left':'осталось сегодня.','quota.more':'для базы знаний и более высоких лимитов.','quota.disabled':'Анонимный доступ отключён.',
      'fc.title':'Проверка по базе знаний','fc.accuracy':'точность','fc.supported':'подтверждено','fc.contradicted':'опровергнуто','fc.notinkb':'нет в базе','fc.misinfo':'Возможная дезинформация','fc.nochecked':'Проверяемых утверждений не найдено.',
      'adm.voices':'Голоса','adm.voicevis':'Голоса, видимые клиентам','f.restrictvoices':'Показывать клиентам только отмеченные голоса','f.defaultvoice':'Голос по умолчанию','v.hint':'Неотмеченные голоса скрыты из списка для клиентов и отклоняются TTS. Оставьте флажок снятым, чтобы показывать все голоса. Системные (включая грузинский) всегда включены.','v.search':'Поиск голосов…','v.selected':'выбрано','v.system':'Системный','v.nopreview':'Нет образца','v.unavailable':'Нет в этом аккаунте ElevenLabs','v.pickone':'Выберите хотя бы один голос или снимите ограничение.','v.loadfail':'Не удалось загрузить голоса из ElevenLabs. Проверьте API-ключ в «Интеграциях».','msg.voicegone':'Этот голос больше недоступен. Список обновлён.',
      'fc.allclaims':'Все утверждения','pg.tab.retrieval':'Поиск','pg.tab.score':'Оценка ответа','pg.ans.label':'Ответ оператора (на любом языке)','pg.ans.ph':'Вставьте или напишите ответ оператора — он будет оценён по рубрике этого клиента…','pg.ans.run':'Оценить ответ','pg.ans.hint':'Оценивается по активной рубрике клиента; утверждения проверяются по его базе знаний.','pg.ans.norubric':'Нет активной рубрики — сначала задайте её во вкладке «Оценка».','pg.ans.empty':'Введите ответ для оценки.','pg.ans.usingv':'версия рубрики',
      'tab.playground':'Песочница','pg.heading':'Оцените звонок или ответ','pg.hint':'Оцените письменный ответ или загрузите запись звонка (аудио или видео) — она будет расшифрована и оценена по вашей рубрике и базе знаний.','pg.mode.text':'Текст','pg.mode.audio':'Аудио / Видео','pg.audiolabel':'Запись звонка (аудио или видео)','pg.run':'Оценить','pg.audioempty':'Выберите аудио- или видеофайл.',
      'kba.title':'Управление базой знаний','kba.tenant':'Арендатор','kba.selecttenant':'Выберите арендатора для управления его базой знаний.',
      'kba.tab.overview':'Обзор','kba.tab.documents':'Документы','kba.tab.import':'Импорт','kba.tab.playground':'Песочница','kba.tab.duplicates':'Дубликаты','kba.tab.activity':'Активность',
      'kba.stat.documents':'Документы','kba.stat.chunks':'Фрагменты','kba.stat.coverage':'Покрытие эмбеддингами','kba.stat.failed':'Ошибки импорта','kba.stat.tokens':'Прибл. токены','kba.stat.lastupd':'Обновлено','kba.stat.inprogress':'В процессе',
      'kba.params':'Активная конфигурация','kba.export':'Экспорт','kba.exportcsv':'Экспорт CSV','kba.reembedall':'Переэмбеддинг','kba.refresh':'Обновить',
      'kba.f.status':'Статус','kba.f.type':'Тип','kba.f.tag':'Тег','kba.f.search':'Поиск по заголовку/тексту','kba.f.all':'Все',
      'kba.selected':'выбрано','kba.bulk.delete':'Удалить','kba.bulk.reembed':'Переэмбеддинг','kba.bulk.retag':'Изменить теги','kba.selectall':'Выбрать все',
      'kba.edit':'Редактировать','kba.chunks':'Фрагменты','kba.reembed':'Переэмбеддинг','kba.delete':'Удалить','kba.save':'Сохранить','kba.nodocs':'Нет документов. Импортируйте ниже.',
      'kba.doc.title':'Заголовок','kba.doc.type':'Категория','kba.doc.tags':'Теги','kba.doc.meta':'Метаданные (JSON)','kba.doc.content':'Текст (редактирование пере-разбивает и пере-эмбеддит)',
      'kba.pg.query':'Запрос (на любом языке)','kba.pg.topk':'Top-k','kba.pg.threshold':'Порог','kba.pg.run':'Выполнить поиск','kba.pg.method':'метод','kba.pg.nohits':'Фрагменты не найдены.',
      'retr.m.vector':'семантический','retr.m.keyword':'текстовое совпадение','retr.m.none':'нет',
      'retr.top':'лучший балл','retr.spread':'разброс','retr.margin':'отрыв от 2-го',
      'retr.level.high':'уверенно','retr.level.medium':'средне','retr.level.low':'слабо','retr.level.none':'нет совпадений',
      'retr.opendoc':'Открыть документ',
      'retr.flag.shown':'Ниже всё равно показаны ближайшие фрагменты, лучшие сверху.',
      'retr.flag.empty_kb':'Искать пока не в чем',
      'retr.flag.empty_kb.b':'База знаний пуста, поэтому ответ в ней найти невозможно. Откройте вкладку «Импорт» и сначала добавьте регламенты, частые вопросы или скрипты разговоров.',
      'retr.flag.unavailable':'Поиск не выполнился',
      'retr.flag.unavailable.b':'Этот поиск не был завершён, поэтому он ничего не говорит о вашей базе знаний — не читайте это как «ответ не найден». Повторите попытку через минуту; если повторяется, сообщите оператору CommuniQ.',
      'retr.flag.no_hits':'Совпадений нет',
      'retr.flag.no_hits.b':'На этот вопрос не вернулось ни одного фрагмента. Попробуйте сформулировать вопрос так, как задал бы его клиент, или импортируйте документ, который на него отвечает.',
      'retr.flag.keyword_fallback':'Резервный поиск — смысловой поиск недоступен',
      'retr.flag.keyword_fallback.b':'Сервис эмбеддингов недоступен, поэтому фрагменты подобраны по совпадению текста, а не по смыслу, и эти баллы нельзя сравнивать с обычными. Пока он не восстановлен, результаты будут хуже обычного — сообщите оператору CommuniQ.',
      'retr.flag.flat_distribution':'Ничего явно не совпало',
      'retr.flag.flat_distribution.b':'Все фрагменты набрали почти одинаковый балл — обычно это значит, что в базе знаний нет ответа на этот вопрос, и ниже показан просто ближайший текст, а не настоящее совпадение. Добавьте документ по этой теме или используйте формулировки из ваших документов.',
      'retr.flag.low_score':'Только слабые совпадения',
      'retr.flag.low_score.b':'Ближайшие фрагменты связаны с вопросом лишь отдалённо. Прежде чем полагаться на них, проверьте, покрывает ли эту тему хоть один документ, и при необходимости добавьте его.',
      'retr.flag.generic':'Результат с низкой уверенностью',
      'retr.flag.generic.b':'Этот поиск не нашёл в базе знаний ничего уверенно подходящего.',
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
      'sc.normalize':'Привести к 100%','sc.mustbe100':'Сумма весов должна быть 100% (сейчас {total}%).',
      'cur.tab':'Здоровье базы','cur.heading':'Пробелы в базе знаний',
      'cur.desc':'Предложения, собранные из диалогов и звонков, на которые бот не смог ответить как следует. Принятие сразу обновляет базу знаний.',
      'cur.none':'Нечего проверять — очередь пуста.','cur.loadfail':'Не удалось загрузить очередь проверки.',
      'cur.op.add':'Добавить','cur.op.update':'Обновить','cur.op.remove':'Скрыть',
      'cur.priority':'приоритет','cur.asked':'спросили {n}×','cur.sources':'источников: {n}',
      'cur.confidence':'уверенность','cur.risk':'риск','cur.window':'период',
      'cur.evidence':'Что говорили клиенты','cur.evidence.none':'Цитаты для этой группы не сохранены.',
      'cur.target':'Целевой документ','cur.diff':'Изменение относительно текущего фрагмента',
      'cur.diff.nochunk':'Текущий фрагмент недоступен — показан только предлагаемый текст.','cur.proposed':'Предлагаемый текст',
      'cur.accept':'Принять','cur.acceptedit':'Принять с правками','cur.decline':'Отклонить',
      'cur.applied':'Добавлено в базу знаний','cur.declinedok':'Отклонено — больше не появится',
      'cur.edit.heading':'Принять с правками','cur.edit.hint':'Отредактируйте текст перед добавлением в базу. При сохранении он будет заново разбит и переэмбеддён.',
      'cur.decline.heading':'Почему отклоняете?','cur.decline.r.nottrue':'Неправда',
      'cur.decline.r.covered':'Уже покрыто','cur.decline.r.dontsay':'Не хочу, чтобы бот это говорил',
      'cur.decline.r.temporary':'Временное / разовое','cur.decline.pick':'Сначала выберите причину.',
      'cur.bulk.accept':'Принять выбранные','cur.bulk.note':'Массовое принятие работает только для добавлений и обновлений — скрытие проверяется по одному.',
      'cur.remove.heading':'Подтвердите скрытие','cur.remove.word':'СКРЫТЬ','cur.remove.confirm':'Введите {word} для подтверждения.',
      'cur.remove.mismatch':'Не совпадает — ничего не изменено.',
      'cur.remove.note':'Текст будет скрыт из ответов. Ничего не удаляется; оператор может удалить вручную.',
      'cur.run':'Запустить курацию','cur.run.started':'Курация поставлена в очередь',
      'cur.st.pending':'Ожидает','cur.st.accepted':'Принято','cur.st.declined':'Отклонено','cur.st.superseded':'Заменено','cur.st.apply_failed':'Ошибка применения',
      'cur.filter.state':'Статус','cur.openjob':'Открыть звонок','cur.opensource':'Открыть диалог',
      'cur.foreign':'⚠ ссылается на данные другого клиента',
      'tab.bot':'Бот','bot.heading':'Публичный бот','bot.desc':'Как бот разговаривает с вашими клиентами и что ему разрешено говорить. Все настройки относятся только к этому клиенту — ничего не передаётся другим рабочим пространствам.',
      'bot.autopilot':'Автопилот — бот отвечает клиентам без участия человека',
      'bot.autopilot.hint':'При выключенном автопилоте бот только готовит черновики ответов, которые оператор проверяет и отправляет. Клиенту не уходит ни одного непрочитанного сообщения.',
      'bot.state.live':'Отвечает клиентам','bot.state.off':'Только черновики — ответ отправляет человек','bot.state.killed':'Остановлен CommuniQ',
      'bot.killed.note':'Поддержка CommuniQ остановила автопилот. Настройки сохранены; до возобновления бот передаёт все диалоги человеку.',
      'bot.needpublic.title':'Автопилоту нужен хотя бы один опубликованный документ',
      'bot.needpublic.body':'Документы базы знаний по умолчанию внутренние, а публичный бот может цитировать только опубликованные. Если не опубликовано ничего, бот откажет на любой вопрос. Опубликуйте документы, которые разрешено читать клиенту, и затем включите автопилот.',
      'bot.needpublic.link':'Открыть базу знаний',
      'bot.persona':'Персона','bot.persona.ph':'Вы — ассистент поддержки … Отвечайте кратко, доброжелательно и конкретно.',
      'bot.greeting':'Приветствие','bot.refusal':'Текст отказа — что бот говорит, когда в базе знаний нет ответа',
      'bot.refusal.hint':'Эту фразу клиент видит чаще всего. Напишите её на всех языках, на которых отвечает бот; она должна предлагать человека, а не извиняться дважды.',
      'bot.refusal.missing':'Напишите текст отказа на языке «{lang}» перед включением автопилота.',
      'bot.lang.en':'Английский','bot.lang.ka':'Грузинский','bot.lang.ru':'Русский',
      'bot.languages':'Языки, на которых отвечает бот','bot.languages.pickone':'Выберите хотя бы один язык.',
      'bot.escalation':'Ключевые слова эскалации','bot.escalation.ph':'юрист, жалоба, возврат платежа',
      'bot.escalation.hint':'Через запятую. Совпадение сразу передаёт диалог человеку, до генерации любого ответа.',
      'bot.retrieval':'Поиск и лимиты ответа','bot.minscore':'Минимальный score','bot.minhits':'Минимум совпадений','bot.topk':'Top-k','bot.suggestions':'Подсказок на ход','bot.maxchars':'Макс. символов в ответе',
      'bot.caps':'Лимиты частоты','bot.cap.tenant':'Ходов / мин (всё пространство)','bot.cap.enduser':'Ходов / час (один клиент)',
      'bot.general':'Отвечать из общих знаний, когда в базе знаний ничего нет',
      'bot.general.risk':'Рискованный выбор, по умолчанию выключен. Выключено — бот отказывает вашим текстом выше и предлагает человека; он способен повторить только то, что вы опубликовали. Включено — он может ответить из собственных знаний модели: это не ваша политика, это не проверяемо и он может уверенно ошибиться в ваших ценах, правилах и сроках.',
      'bot.general.confirm':'Разрешить боту отвечать из общих знаний модели? Тогда он будет говорить то, чего нет в вашей базе знаний и что никто в вашей компании не утверждал.',
      'bot.general.on':'Включить',
      'bot.handoff':'Писать короткое резюме для человека, который перехватывает диалог','bot.handoff.hint':'Один дополнительный вызов модели, только при передаче. Выключено — оператор открывает диалог с нуля.',
      'bot.save':'Сохранить настройки бота','bot.saved':'Настройки бота сохранены','bot.version':'Версия',
      'bot.loadfail':'Не удалось загрузить настройки бота.','bot.unavailable':'Настройки бота пока недоступны на этом сервере.',
      'adm.bot':'Управление ботом','kill.heading':'Аварийное отключение автопилота',
      'kill.desc':'Тормоз. Останавливает ответы публичного бота; диалоги передаются людям. Настройки клиентов не меняются, поэтому возобновление — один клик.',
      'kill.global':'Остановить автопилот для всех клиентов','kill.global.on':'Остановлен везде','kill.global.off':'Работает нормально',
      'kill.tenants':'По клиентам','kill.stop':'Остановить','kill.resume':'Возобновить',
      'kill.confirm.global':'Остановить автопилот для всех клиентов? Все боты будут передавать диалоги людям до возобновления.',
      'kill.confirm.resume.global':'Возобновить автопилот для всех клиентов, у кого он включён?',
      'kill.confirm.tenant':'Остановить автопилот для «{name}»?','kill.confirm.resume':'Возобновить автопилот для «{name}»?',
      'kill.state.live':'Активен','kill.state.stopped':'Остановлен','kill.state.off':'Автопилот выключен',
      'kill.saved':'Аварийный выключатель обновлён','kill.loadfail':'Не удалось прочитать состояние выключателя.',
      'kill.unavailable':'Аварийный выключатель ещё не развёрнут на этом сервере.','kill.overviewfail':'Не удалось прочитать состояние автопилота по клиентам.',
      'th.autopilot':'Автопилот',
      'vis.col':'Видимость','vis.all':'Все','vis.public':'Публичный','vis.internal':'Внутренний',
      'vis.publish':'Опубликовать','vis.unpublish':'Снять с публикации',
      'vis.bulk.publish':'Опубликовать выбранные','vis.bulk.unpublish':'Снять выбранные',
      'vis.stat.public':'Опубликовано',
      'vis.confirm.publish':'Опубликовать {n} документ(ов)? Публичный бот может дословно цитировать опубликованные документы вашим клиентам.',
      'vis.confirm.unpublish':'Снять с публикации {n} документ(ов)? Публичный бот перестанет их цитировать.',
      'vis.confirm.publish.one':'Опубликовать «{title}»? Публичный бот сможет дословно цитировать его вашим клиентам.',
      'vis.confirm.unpublish.one':'Снять с публикации «{title}»? Публичный бот перестанет его цитировать.',
      'vis.done.publish':'Опубликовано','vis.done.unpublish':'Снято с публикации',
      'tkb.tab.maint':'Обслуживание','tkb.overview.heading':'Состояние базы знаний',
      'tkb.params.hint':'Настройки, с которыми поиск работает на самом деле. Если заданная размерность и размерность в базе не совпадают, новые эмбеддинги не создаются и поиск тихо перестал работать.',
      'tkb.params.columndim':'Размерность (в базе)','tkb.params.chunk':'Размер фрагмента / перекрытие',
      'tkb.params.threshold':'Порог поиска','tkb.params.topk':'Top-k по умолчанию',
      'tkb.params.metric':'Метрика расстояния','tkb.params.index':'Тип индекса','tkb.params.noembed':'Фрагменты без эмбеддинга',
      'tkb.loadfail':'Не удалось загрузить базу знаний.',
      'tkb.th.source':'Источник','tkb.docs.none':'Документов пока нет — добавьте их во вкладке «Импорт».',
      'tkb.del.confirm':'Удалить «{title}»? Его фрагменты сразу исчезнут из всех ответов, и отменить это нельзя.',
      'tkb.bulk.delete.confirm':'Удалить {n} документ(ов)? Их фрагменты сразу исчезнут из всех ответов, и отменить это нельзя.',
      'tkb.bulk.reembed.confirm':'Переэмбеддить {n} документ(ов) сейчас? Это выполнится немедленно и ненадолго займёт ресурсы живого поиска.',
      'tkb.edit.warn':'Сохранение нового текста заново разобьёт документ на фрагменты и переэмбеддит его. Поиск перейдёт на новый текст сразу после этого; при ошибке документ помечается как ошибочный, а не остаётся наполовину обновлённым.',
      'tkb.badjson':'Метаданные должны быть корректным JSON.','tkb.reembed.done':'Переэмбеддено фрагментов: {n}',
      'tkb.chunks.pick':'Документ','tkb.chunks.none':'У этого документа пока нет фрагментов.',
      'tkb.chunks.pickone':'Выберите документ, чтобы увидеть его фрагменты.',
      'tkb.chunks.hint':'Поиск сопоставляет фрагменты, а не документы. Правка фрагмента сразу его переэмбеддит; удаление убирает его из всех ответов.',
      'tkb.chunk.noembed':'нет эмбеддинга',
      'tkb.chunk.del.confirm':'Удалить этот фрагмент? Он сразу исчезнет из всех ответов.',
      'tkb.chunk.edit.hint':'Сохранение сразу переэмбеддит этот фрагмент. Остальной документ не затрагивается.',
      'tkb.pg.heading':'Песочница поиска',
      'tkb.pg.hint':'Выполняет ровно тот поиск, который используют ваш бот и анализ звонков, и показывает, какой метод ответил — векторный индекс или запасной поиск по ключевым словам — вместе с оценкой каждого фрагмента. Модель не вызывается.',
      'tkb.dup.hint':'Дубликаты занимают места в выдаче и заставляют бота повторяться; противоречащие друг другу копии — отвечать по-разному на один и тот же вопрос.',
      'tkb.dup.identical':'документ(ов) с одинаковым содержимым','tkb.dup.keep':'оставляем',
      'tkb.dup.skipped':'Поиск похожих дубликатов пропущен — в этой базе знаний слишком много фрагментов, чтобы сравнить все пары.',
      'tkb.act.filter':'Действие','tkb.act.filter.ph':'импорт, правка, удаление, переэмбеддинг…',
      'tkb.act.method':'Метод','tkb.act.detail':'Детали','tkb.act.actor':'Кто',
      'tkb.exp.hint':'Скачивает все документы этой базы знаний, включая внутренние. Сам экспорт записывается в журнал активности.',
      'tkb.reembed.heading':'Переэмбеддинг всей базы знаний',
      'tkb.reembed.desc':'Пересобирает вектор для каждого фрагмента — нужно после смены модели эмбеддингов или её размерности. Задача ставится в очередь фоновому обработчику и выполняется с ограничением скорости, поэтому на большой базе может занять много времени; поиск при этом продолжает работать. Одновременно выполняется только один переэмбеддинг.',
      'tkb.reembed.start':'Поставить в очередь',
      'tkb.reembed.confirm':'Поставить в очередь переэмбеддинг всех документов? Он выполняется в фоне и может занять много времени. Запустить следующий можно будет только после его завершения.',
      'tkb.reembed.queued':'Переэмбеддинг поставлен в очередь','tkb.reembed.busy':'Переэмбеддинг уже в очереди или выполняется.',
      'tkb.reembed.none':'Переэмбеддинг ещё ни разу не запускался.',
      'tkb.reembed.progress':'{done} из {total} документов','tkb.reembed.failed':'с ошибкой: {n}',
      'tkb.reembed.state.queued':'В очереди','tkb.reembed.state.running':'Выполняется','tkb.reembed.state.done':'Завершён',
      'tkb.reembed.state.error':'Ошибка','tkb.reembed.state.cancelled':'Отменён',
      'sc.readonly':'Только просмотр — редактировать рубрику оценки может только владелец рабочего пространства.',
    },
  };
  let LANG = (() => { try { return localStorage.getItem('cq_lang') || (navigator.language||'en').slice(0,2); } catch { return 'en'; } })();
  if (!DICT[LANG]) LANG = 'en';
  function t(key) { return (DICT[LANG] && DICT[LANG][key]) || DICT.en[key] || key; }
  function lang() { return LANG; }

  /* Safely read a fetch Response as JSON. If the server (or a reverse proxy like nginx)
     returns a non-JSON error page — e.g. a 413/502/504 HTML page — this throws a clean,
     localized Error with the real cause instead of a cryptic "Unexpected token '<'". */
  async function readResp(r) {
    const text = await r.text().catch(() => '');
    let data = null;
    const s = (text || '').trimStart();
    if (s && (s[0] === '{' || s[0] === '[')) { try { data = JSON.parse(s); } catch {} }
    if (!r.ok) {
      const detail = data && (data.detail || data.message || data.error);
      if (typeof detail === 'string' && detail) throw new Error(detail);
      if (r.status === 413) throw new Error(t('err.toolarge'));
      if (r.status === 504) throw new Error(t('err.timeout'));
      if (r.status === 502 || r.status === 503) throw new Error(t('err.unavailable'));
      throw new Error(t('err.http').replace('{status}', r.status));
    }
    if (data === null) throw new Error(t('err.badresp'));
    return data;
  }
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
           scorecardHTML, factcheckHTML, readResp };
})();
