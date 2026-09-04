/* CommuniQ shared UI: API, theme, i18n, header, toasts, confirm, custom <select>, audio player. */
const CQ = (() => {
  const API = (location.port === '' || location.port === '80')
    ? '/api' : `${location.protocol}//${location.hostname}:8000`;

  /* The official CommuniQ logo lockup (communiq.io) — mark + wordmark as ONE image,
     exactly the asset the brand site ships, not an app-font approximation. Two official
     variants: the colour lockup for the light theme, the all-white "logotetri" for dark.
     CSS picks one per theme, so both are in the markup and only one is ever painted.
     alt="CommuniQ" because the wordmark now lives inside the image. */
  const LOGO = `<img class="brand-logo on-light" src="cq-logo.png" alt="CommuniQ" width="256" height="120" />`
             + `<img class="brand-logo on-dark" src="cq-logo-on-dark.png" alt="CommuniQ" width="256" height="120" />`;

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
      'f.audiofile':'Audio or video file','f.provider':'Provider','f.dimension':'Dimension','f.model':'Model','f.baseurl':'Base URL',
      'f.anthropic':'Anthropic (Claude) API key','f.eleven':'ElevenLabs API key','f.claudemodel':'Claude model','f.sttmodel':'Scribe (STT) model','f.ttsmodel':'TTS model','f.voiceid':'TTS voice ID','f.openaikey':'API key (openai only)',
      'btn.signin':'Sign in','btn.save':'Save','btn.savesettings':'Save settings','btn.savelimits':'Save limits','btn.refresh':'Refresh','btn.delete':'Delete','btn.cancel':'Cancel','btn.test':'Test','btn.testconn':'Test connections','btn.testdeep':'Test everything (deep)',
      'cap.database':'Database','cap.ffmpeg':'Audio transcoding (ffmpeg)','cap.voices':'ElevenLabs · list voices','cap.stt':'ElevenLabs · speech-to-text','cap.tts':'ElevenLabs · text-to-speech','cap.ttska':'ElevenLabs · Georgian TTS','cap.embeddings':'Embeddings','cap.claude':'Claude · analysis','cap.factcheck':'Claude · fact-check tools','cap.scoring':'Claude · scoring tool',
      'cap.fixscope':'Fix: in ElevenLabs open Settings → API Keys → Edit on this key, enable the “{scope}” permission, save, then re-test.',
      'adm.testnote':'Each capability is probed for real — the connection test spends a fraction of a second of speech-to-text and a few text-to-speech characters, because ElevenLabs offers no way to read a key’s permissions. “Deep” additionally exercises the fact-check and scoring tools.','btn.search':'Search','btn.import':'Import','btn.analyze':'Analyze','btn.synth':'Generate speech','btn.create':'Create tenant','btn.adduser':'Add user','btn.rotate':'Rotate','btn.remove':'Remove','btn.apikey':'API key','btn.users':'Users','btn.chunks':'Chunks',
      'hero.eyebrow':'CommuniQ Voice AI','hero.title':'Speak & understand every call.',
      'tab.tts':'Text to Speech','tab.analyze':'Analyze a Call','tab.kb':'Knowledge Base','tab.history':'History','tab.scoring':'Rubric',
      'tts.heading':'Generate speech from text','tts.text_ph':'Type something to say… (English, Russian or Georgian)',
      'an.heading':'Upload a recording to analyze','an.heading_kb':'Analyze a call — uses your knowledge base',
      'drop.title':'Drop an audio or video file here, or click to browse','drop.sub':'Any audio or video file — transcribed with ElevenLabs Scribe, analyzed by Claude','drop.sub_kb':'Transcribed, then analyzed against your knowledge base',
      'rec.or':'or','rec.record':'Record','rec.stop':'Stop','rec.recording':'Recording','rec.ready':'Recorded — ready to analyze','rec.unsupported':'Recording needs HTTPS or localhost','rec.denied':'Microphone access denied',
      'res.analysis':'Analysis','res.language':'Language','res.sentiment':'Sentiment','res.topics':'Topics','res.time':'Time','res.quality':'Quality','res.summary':'Summary','res.keypoints':'Key points','res.actions':'Action items','res.transcript':'Transcript','res.kbused':'Knowledge base used','res.nokb':'No knowledge base context matched.','res.empty':'(empty)','res.done':'Analysis complete',
      'login.heading':'Sign in',
      'kb.import':'Import knowledge','imp.file':'Upload file','imp.paste':'Paste text','imp.csv':'CSV (Q&A / key-value)','kb.filelabel':'Files (PDF / DOCX / XLSX / CSV / TXT / MD — several at once is fine)','kb.csvlabel':'CSV file (first row = header)','kb.searchlabel':'Search knowledge base','kb.search_ph':'Ask a question…','kb.documents':'Documents','kb.none':'No documents yet. Import some knowledge above.','kb.processing':'processing…','kb.nomatch':'No matches.',
      'th.title':'Title','th.category':'Category','th.status':'Status','th.chunks':'Chunks','th.file':'File','th.lang':'Language','th.when':'When','th.name':'Name','th.slug':'Slug','th.industry':'Industry','th.active':'Active','th.users':'Users','th.docs':'Docs',
      'hist.heading':'Recent analyses','hist.none':'No analyses yet.',
      'adm.tenants':'Tenants','adm.embeddings':'Embeddings','adm.anon':'Anonymous limits','adm.integrations':'Integrations',
      'adm.createtenant':'Create tenant','adm.embprov':'Embeddings provider','adm.embnote':'Changing the dimension requires re-embedding the KB (documents must be re-imported).',
      'adm.anonheading':'Anonymous (no-login) user limits','adm.allowanon':'Allow anonymous users','adm.maxanalyses':'Max analyses / day','adm.maxmb':'Max audio MB','adm.maxtts':'Max TTS / day','adm.features':'Features allowed','feat.analyze':'Analyze','feat.tts':'Text to Speech',
      'adm.intkeys':'Integration keys','adm.models':'Models & voice','adm.instructions':'Analysis instructions',
      'toast.saved':'Settings saved','toast.imported':'Import started','toast.deleted':'Deleted','toast.created':'Created','toast.welcome':'Welcome','toast.error':'Something went wrong',
      'tip.label':'More information',
      'err.toolarge':'That file is too large to upload. Please use a shorter or smaller recording.','err.timeout':'The analysis took too long and timed out. Try a shorter recording.','err.unavailable':'The service is temporarily unavailable. Please try again in a moment.','err.http':'Something went wrong on our side. Please try again in a moment — if it keeps happening, contact support. (Code {status})','err.badresp':'The server returned an unexpected response. Please try again.',
      'quota.using':"You're using CommuniQ anonymously —",'quota.analyses':'transcriptions','quota.clips':'speech clips','quota.left':'left today.','quota.more':'for a knowledge base and higher limits.','quota.disabled':'Anonymous access is disabled.',
      'fc.title':'Knowledge base fact-check','fc.accuracy':'accuracy','fc.supported':'supported','fc.contradicted':'contradicted','fc.notinkb':'not in KB','fc.partial':'partly correct','fc.misinfo':'Possible misinformation','fc.nochecked':'No verifiable claims were found.',
      'adm.voices':'Voices','adm.voicevis':'Customer-visible voices','f.restrictvoices':'Show only the ticked voices to customers','f.defaultvoice':'Default voice','v.hint':'Unticked voices are hidden from the customer voice list and rejected by the TTS API. Leave the box unticked to show every voice. System defaults (incl. the Georgian voice) are always on.','v.search':'Search voices…','v.selected':'selected','v.system':'System default','v.nopreview':'No preview','v.unavailable':'Not in this ElevenLabs account','v.pickone':'Select at least one voice, or untick the restriction.','v.loadfail':'Could not load voices from ElevenLabs. Check the API key in Integrations.','msg.voicegone':'That voice is no longer available. The list has been refreshed.',
      'fc.allclaims':'All claims','pg.tab.retrieval':'Test search','pg.tab.score':'Answer scoring','pg.tab.sentiment':'Sentiment','pg.ans.label':'Operator answer (any language)','pg.ans.ph':'Paste or type what the operator said or replied — it will be scored against this tenant’s rubric…','pg.ans.run':'Score answer','pg.ans.norubric':'No active rubric — define one in the Scoring tab first.','pg.ans.empty':'Enter an answer to score.','pg.ans.usingv':'rubric version',
      'tab.playground':'Score a Call','pg.heading':'Score a call or answer','pg.mode.text':'Text','pg.mode.audio':'Audio / Video','pg.mode.sentiment':'Sentiment','pg.audiolabel':'Call recording (audio or video)','pg.run':'Score','pg.audioempty':'Choose an audio or video file.',
      'kba.title':'Knowledge Base Management','kba.tenant':'Tenant','kba.selecttenant':'Select a tenant to manage its knowledge base.',
      'kba.tab.overview':'Overview','kba.tab.documents':'Documents','kba.tab.import':'Import','kba.tab.playground':'Playground','kba.tab.duplicates':'Duplicates','kba.tab.activity':'Activity',
      'kba.stat.documents':'Documents','kba.stat.chunks':'Chunks','kba.stat.coverage':'Embedding coverage','kba.stat.failed':'Failed imports','kba.stat.tokens':'Approx. tokens','kba.stat.lastupd':'Last updated','kba.stat.inprogress':'In progress',
      'kba.params':'Active configuration','kba.export':'Export','kba.exportcsv':'Export CSV','kba.reembedall':'Rebuild search index','kba.refresh':'Refresh',
      'kba.f.status':'Status','kba.f.type':'Type','kba.f.tag':'Tag','kba.f.search':'Search title/content','kba.f.all':'All',
      'kba.selected':'selected','kba.bulk.delete':'Delete','kba.bulk.reembed':'Rebuild search','kba.bulk.retag':'Retag','kba.selectall':'Select all',
      'kba.edit':'Edit','kba.chunks':'Chunks','kba.reembed':'Re-embed','kba.delete':'Delete','kba.save':'Save','kba.nodocs':'No documents. Import some below.',
      'kba.doc.title':'Title','kba.doc.type':'Category','kba.doc.tags':'Tags','kba.doc.meta':'Metadata (JSON, optional)','kba.doc.content':'Content (editing re-chunks & re-embeds)',
      'kba.pg.query':'Query (any language)','kba.pg.topk':'Results to return','kba.pg.threshold':'Minimum match score (0–1)','kba.pg.run':'Run retrieval','kba.pg.method':'method','kba.pg.nohits':'No chunks retrieved.',
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
      'sc.heading':'Scoring rubric',
      'sc.rubric':'Overall rubric / guidance','sc.rubric.ph':'Optional overall guidance for the evaluator (tone, what matters most, how strict to be)…',
      'sc.adddim':'+ Add dimension','sc.dname':'Dimension name','sc.dname.ph':'e.g. Greeting & identification',
      'sc.ddesc':'Short description','sc.dweight':'Weight','sc.dguide':'Scoring guidance',
      'sc.dguide.ph':'How to score this dimension: what earns a high vs low score…',
      'sc.remove':'Remove','sc.save':'Save rubric','sc.saved':'Rubric saved','sc.sum':'Total weight',
      'sc.nodims':'No dimensions yet — add one to start.','sc.version':'Version','sc.none':'No active rubric for this tenant yet.',
      'sc.needname':'Every dimension needs a name.','sc.needone':'Add at least one dimension.',
      'sc.normalize':'Normalize to 100%','sc.mustbe100':'Weights must total 100% (they total {total}%).',
      'cur.tab':'Review Queue','cur.heading':'Knowledge gaps to review',
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
      'cur.remove.heading':'Hide this content?','cur.remove.word':'HIDE','cur.remove.confirm':'Type {word} to confirm.',
      'cur.remove.mismatch':'That does not match — nothing was changed.',
      'cur.remove.note':'This hides the content from answers. Nothing is deleted; an operator can still delete it by hand.',
      'cur.run':'Run curation now','cur.run.started':'Curation run queued',
      'cur.st.pending':'Pending','cur.st.accepted':'Accepted','cur.st.declined':'Declined','cur.st.superseded':'Superseded','cur.st.apply_failed':'Apply failed',
      'cur.filter.state':'State','cur.openjob':'Open call','cur.opensource':'Open conversation',
      'cur.foreign':'⚠ cites evidence from another tenant',
      'tab.bot':'Bot','bot.heading':'Public bot',
      'bot.autopilot':'Autopilot — the bot answers customers with no human in the loop',
      'bot.autopilot.hint':'With autopilot off the bot only drafts replies for an operator to review and send. Nothing reaches a customer unread.',
      'bot.state.live':'Answering customers','bot.state.off':'Drafts only — a human sends every reply','bot.state.killed':'Stopped by CommuniQ',
      'bot.killed.note':'CommuniQ support has stopped autopilot. Your settings are kept; the bot hands every conversation to a human until it is resumed.',
      'bot.needpublic.title':'Autopilot needs at least one document shared with the bot',
      'bot.needpublic.body':'Knowledge base documents are internal by default, and the bot may only quote documents you have shared with it. If none are shared, the bot refuses every question. Share the documents your customers are allowed to read, then enable autopilot. Nothing is made public on the internet.',
      'bot.needpublic.link':'Open the knowledge base',
      'bot.persona':'Persona','bot.persona.ph':'You are the support assistant for … Be brief, warm and concrete.',
      'bot.greeting':'Greeting','bot.refusal':'Refusal copy — what the bot says when your knowledge base has no answer',
      'bot.refusal.hint':'This is the sentence a customer sees most often. Write it in every language the bot answers in; it should offer a human, not apologise twice.',
      'bot.refusal.missing':'Write the refusal copy in {lang} before turning autopilot on.',
      'bot.lang.en':'English','bot.lang.ka':'Georgian','bot.lang.ru':'Russian',
      'bot.languages':'Languages the bot answers in','bot.languages.pickone':'Pick at least one language.',
      'bot.escalation':'Escalation keywords','bot.escalation.ph':'lawyer, complaint, chargeback',
      'bot.escalation.hint':'Comma-separated. A match hands the conversation to a human immediately, before any answer is generated.',
      'bot.retrieval':'Retrieval & reply limits','bot.minscore':'Minimum match score before the bot answers (0–1)','bot.minhits':'Minimum matching passages before the bot answers','bot.topk':'Passages retrieved per question','bot.suggestions':'Suggested replies per turn (drafts mode)','bot.maxchars':'Max reply characters',
      'bot.caps':'Rate caps','bot.cap.tenant':'Turns / minute (whole workspace)','bot.cap.enduser':'Turns / hour (one customer)',
      'bot.general':'Answer from general knowledge when the knowledge base has nothing',
      'bot.general.risk':'Risk choice, off by default. Left off, the bot refuses with your copy above and offers a human — it can only ever repeat what you published. Turned on, it may answer from the model’s own knowledge, which is not your policy, is not auditable, and can be confidently wrong about your prices, rules and deadlines.',
      'bot.general.confirm':'Let the bot answer from the model’s general knowledge? It will then say things that are not in your knowledge base and that nobody at your company approved.',
      'bot.general.on':'Turn it on',
      'bot.handoff':'Write a short summary for the human who takes over','bot.handoff.hint':'Costs one extra model call, only on handoffs. Off means the operator opens a cold conversation.',
      'bot.save':'Save bot settings','bot.saved':'Bot settings saved','bot.version':'Version',
      'bot.loadfail':'Could not load the bot settings.','bot.unavailable':'Bot settings are not available on this server yet.',
      'bot.soon.title':'Coming soon','bot.soon.desc':'The customer bot is still being finished. As soon as it launches, its settings will appear on this page.',
      'bot.soon.desc.admin':'The customer bot has not launched yet — tenants currently see a "Coming soon" page. The controls below will take effect once it goes live.',
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
      'vis.col':'Visibility','vis.all':'All','vis.public':'Shared with bot','vis.internal':'Internal',
      'vis.publish':'Share with bot','vis.unpublish':'Stop sharing',
      'vis.bulk.publish':'Share selected with bot','vis.bulk.unpublish':'Stop sharing selected',
      'vis.stat.public':'Shared with bot',
      'vis.confirm.publish':'Share {n} document(s) with your bot? The bot may quote shared documents word for word to your customers. Nothing is made public on the internet — your data stays inside your workspace.',
      'vis.confirm.unpublish':'Stop sharing {n} document(s)? The bot will no longer use them.',
      'vis.confirm.publish.one':'Share “{title}” with your bot? The bot may quote it word for word to your customers. Nothing is made public on the internet.',
      'vis.confirm.unpublish.one':'Stop sharing “{title}”? The bot will no longer use it.',
      'vis.done.publish':'Shared with bot','vis.done.unpublish':'Sharing stopped',
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
      'tkb.badjson':'Metadata must be valid JSON.','tkb.reembed.done':'Search rebuilt for {n} chunks',
      'tkb.chunks.pick':'Document','tkb.chunks.none':'This document has no chunks yet.',
      'tkb.chunks.pickone':'Choose a document to see its chunks.',
      'tkb.chunks.hint':'Chunks — not documents — are what retrieval matches against. Editing one re-embeds that chunk on the spot; deleting one removes it from every answer.',
      'tkb.chunk.noembed':'no embedding',
      'tkb.chunk.del.confirm':'Delete this chunk? It disappears from every answer immediately.',
      'tkb.chunk.edit.hint':'Saving re-embeds this chunk immediately. The rest of the document is untouched.',
      'tkb.pg.heading':'Test what search finds',
      'tkb.dup.identical':'documents with identical content','tkb.dup.keep':'keeping',
      'tkb.dup.skipped':'Near-duplicate scan skipped — this knowledge base has too many chunks to compare every pair.',
      'tkb.act.filter':'Action','tkb.act.filter.ph':'import, edit, delete, reembed…',
      'tkb.act.method':'Method','tkb.act.detail':'Detail','tkb.act.actor':'Who',
      'tkb.exp.hint':'Downloads every document in this knowledge base, including the internal ones. The export itself is recorded in the activity log.',
      'tkb.reembed.heading':'Rebuild the search index',
      'tkb.reembed.desc':'Rebuilds the search data for every chunk — needed after the embedding model or its dimension changes. Runs in the background worker at a limited rate, so a large knowledge base can take a while; search keeps working the whole time. Only one rebuild runs at a time.',
      'tkb.reembed.start':'Queue rebuild',
      'tkb.reembed.confirm':'Queue a full search-index rebuild? It runs in the background and can take a while. A new one cannot start until it finishes.',
      'tkb.reembed.queued':'Search rebuild queued','tkb.reembed.busy':'A rebuild is already queued or running.',
      'tkb.reembed.none':'No rebuild has run yet.',
      'tkb.reembed.progress':'{done} of {total} documents','tkb.reembed.failed':'{n} failed',
      'tkb.reembed.state.queued':'Queued','tkb.reembed.state.running':'Running','tkb.reembed.state.done':'Finished',
      'tkb.reembed.state.error':'Failed','tkb.reembed.state.cancelled':'Cancelled',
      'sc.readonly':'View only — only workspace owners can edit the scoring rubric.',
      'adm.retention':'Keep anonymous data (days)','adm.retention.hint':'How long an unregistered visitor’s IP, audio and text are kept before the worker deletes them. 0 keeps them indefinitely.',
      'adm.sentiment.heading':'Public sentiment analysis',
      'tab.stt':'Speech to Text','btn.transcribe':'Transcribe',
      'stt.heading':'Turn a recording into text','stt.nofile':'Choose an audio file first.',
      'drop.sub_stt':'Any audio or video file — transcribed with ElevenLabs Scribe',
      'sn.title':'Sentiment','sn.text':'What was said','sn.voice':'How it sounded','sn.arousal':'Energy','sn.valence':'Positivity','sn.unavailable':'Not available for this recording.','sn.conflict':'The words and the tone of voice disagree — worth listening to.',
      'tab.sentiment':'Sentiment','sn.heading':'How did the speaker sound?','sn.run':'Analyze sentiment','sn.none':'No sentiment could be determined for this recording.','sn.config':'Sentiment settings','sn.enabled':'Enable sentiment analysis','sn.guidance':'Guidance for the text judge (optional)','sn.guidance.ph':'e.g. Treat any complaint as at least mildly negative, even if phrased politely…','sn.save':'Save sentiment settings','sn.saved':'Saved','sn.readonly':'View only — only workspace owners can edit sentiment settings.','sn.audiolabel':'Audio to analyze','sn.mode':'Sentiment','sn.disabled':'Sentiment analysis is turned off for this workspace.',
      'lang.en':'English','lang.ru':'Russian','lang.ka':'Georgian',
      'lang.note.ka':'Georgian uses the eleven_v3 model with a Georgian-capable voice for correct pronunciation. Leave the voice on default for best results.',
      'tts.needtext':'Enter some text.','tts.pickvoice':'Pick a specific voice to preview.','tts.previewtitle':'Preview voice (free sample)',
      'session.expired':'Your session has expired — please sign in again.',
      'login.failed':'Could not sign in. Check your username and password.',
      'login.empty':'Enter your username and password.',
      'login.checking':'Restoring your session…',
      'login.showpw':'Show password',
      'nav.console':'Console',
      'btn.retry':'Retry',
      'adm.deltenant.confirm':'Delete “{name}”? This permanently removes the organization, all of its users, its knowledge base and its call history. This cannot be undone.',
      'adm.rotate.confirm':'Generate a new API key for this organization? The current key stops working immediately — any integration using it must be updated.',
      'adm.rotate.done':'New API key generated',
      'adm.rmuser.confirm':'Remove user “{u}”? They lose access immediately.',
      'bulk.done.delete':'Deleted {n} documents',
      'bulk.done.reembed':'Rebuilding search for {n} documents',
      'bulk.done.retag':'Updated tags on {n} documents',
      'bulk.done.publish':'Shared {n} documents with the bot',
      'bulk.done.unpublish':'Stopped sharing {n} documents',
      'kb.csvhint':'First row must be column headers. A two-column file is imported as question & answer (or key & value) pairs; files with more columns are imported one entry per row.',
      'kb.needfile':'Choose a file first.',
      'tts.previewfail':'Could not play the preview. Try again.',
      'drop.sub_sn':'Transcribed, then given a sentiment read — your knowledge base is not used.',
      'stt.done':'Transcript ready',
      'sn.done':'Sentiment ready',
      'pg.done':'Score ready',
      'cur.bulk.confirm':'Accept {n} suggestions? They are applied to the knowledge base immediately.',
      'kba.notenants':'No organizations yet — create one in the Console.',
      'nav.workspace':'My workspace',
      'login.asadmin':'You are signed in as administrator.',
      'f.role':'Role',
      'role.member':'Member',
      'role.owner':'Owner',
      'adm.user.newpw':'New password (leave empty to keep)',
      'adm.user.saved':'User updated',
      'kb.templates':'Sample files to copy:',
      'kb.restr':'My file does not follow the template — restructure it with AI',
      'kb.restr.done':'AI restructuring finished — the document is ready.',
      'sc.import':'Import from file (AI)',
      'sc.import.loading':'Reading the scoring standard…',
      'sc.import.loaded':'Draft loaded from the file — review the criteria and weights, then save.',
      'sc.import.fail':'Rubric import failed.',
      'sc.import.stage.upload':'Uploading the file…',
      'sc.import.stage.queued':'Uploaded — waiting for the server…',
      'sc.import.stage.extracting':'Reading the document…',
      'sc.import.stage.analyzing':'AI is mapping the criteria…',
      'sc.import.cancelled':'Import cancelled.',
      'sc.import.netfail':'The connection to the server was lost. Please try again.',
      'kb.restr.fail':'AI restructuring failed.',
      'kb.files.progress':'Importing file {done} of {total}…',
      'kb.files.done':'{n} file(s) imported.',
      'kb.files.failed':'Failed:',
      'kb.restr.hint':'During import, Claude reads the document and rewrites it as clean, self-contained entries — every amount, term and number is kept exactly as written. The import takes a little longer.',
      /* ---- Audio converter (public page) ----
         Telephony formats are an expert vocabulary sold to non-experts: a call-centre
         manager knows what a greeting should sound like, not what alaw is. So every format
         carries a one-line description inside the dropdown, and the labels are translated
         here rather than passed through from the API in English. The catalog itself still
         comes from GET /convert/formats — a format id with no key here falls back to
         whatever the server called it, so a new format is never invisible.
         Keep this comment free of apostrophes, quotes and backticks: scripts/check_i18n.py
         walks this file as a character stream. */
      'tab.convert':'Audio Converter',
      'cv.heading':'Convert audio for your phone system',
      'cv.files':'Files to convert',
      'cv.drop.title':'Drop audio or video files here, or click to browse',
      'cv.drop.sub':'Several files at once — audio or video. Only the sound is kept; every telephony format is mono.',
      'cv.format':'Output format',
      'cv.run':'Convert',
      'cv.download':'Download ZIP',
      'cv.clear':'Clear list',
      'cv.nofiles':'Add at least one file to convert.',
      'cv.toomany':'Too many files — {max} at a time.',
      'cv.toobig':'“{name}” is larger than {max}.',
      'cv.batchtoobig':'These files add up to more than {max}. Remove a few and convert the rest in a second batch.',
      'cv.stage.upload':'Uploading your files…',
      'cv.stage.queued':'Uploaded — waiting for the server…',
      'cv.stage.converting':'Converting — {done} of {total} done…',
      'cv.st.queued':'Queued','cv.st.converting':'Converting','cv.st.done':'Done','cv.st.failed':'Failed',
      'cv.done.all':'All {n} file(s) converted.',
      'cv.done.some':'{ok} of {total} converted — {fail} failed.',
      'cv.done.none':'Nothing could be converted.',
      'cv.cancelled':'Conversion cancelled.',
      'cv.fail':'Conversion failed.',
      'cv.unavailable':'Audio conversion is not available on this server.',
      'cv.ttl':'The download stays available for {n} more hour(s).',
      'cv.anon':'Without an account you can convert {max} files a day — {left} left today.',
      'cv.anon.more':'for higher limits.',
      'th.size':'Size',
      'quota.conversions':'file conversions',
      'cv.f.wav':'WAV 8 kHz (Asterisk wav)',
      'cv.f.wav.d':'Plays anywhere. The safe default for greetings and voicemail.',
      'cv.f.wav16':'WAV 16 kHz (Asterisk WAV)',
      'cv.f.wav16.d':'Wideband — better sound wherever the call supports it.',
      'cv.f.alaw':'G.711 A-law (.alaw)',
      'cv.f.alaw.d':'The Georgian and European trunk standard — played with no transcoding.',
      'cv.f.ulaw':'G.711 μ-law (.ulaw)',
      'cv.f.ulaw.d':'The North American equivalent of A-law.',
      'cv.f.gsm':'GSM 06.10 (.gsm)',
      'cv.f.gsm.d':'Smallest files, noticeably lower quality — for long prompts.',
      'cv.f.g722':'G.722 wideband (.g722)',
      'cv.f.g722.d':'HD voice. Use it when the whole call path is G.722.',
      'cv.f.sln':'Raw signed linear 8 kHz (.sln)',
      'cv.f.sln.d':'Uncompressed and headerless — what Asterisk mixes internally.',
      'cv.f.sln16':'Raw signed linear 16 kHz (.sln16)',
      'cv.f.sln16.d':'The wideband version of .sln.',
      /* account.html — the registered-user page (design-v2.md 13.5): gate, history,
         personal rubric, profile. Prefix ac. */
      'ac.gate.heading':'Your CommuniQ account',
      'ac.gate.signin':'Sign in','ac.gate.register':'Create account',
      'ac.gate.tenant':'Workspace user? Sign in here →',
      'ac.gate.istenant':'Workspace account — opening your portal…','ac.gate.isadmin':'Operator account — opening the console…',
      'ac.f.email':'Email','ac.f.signinid':'Email or username','ac.f.name':'Display name','ac.f.name.hint':'Optional — the name shown in the header.',
      'ac.f.curpw':'Current password','ac.f.newpw':'New password','ac.f.pw2':'Repeat the new password',
      'ac.reg.pwhint':'At least 8 characters.','ac.reg.bademail':'Enter a valid email address.',
      'ac.reg.shortpw':'The password must be at least 8 characters.',
      'ac.reg.closed':'New sign-ups are closed at the moment.',
      'ac.reg.dup':'An account with this email already exists.','ac.reg.done':'Account created.',
      'ac.disabled':'This account has been switched off. Nothing can be run until an operator turns it back on.',
      'ac.tab.profile':'Profile',
      'ac.hist.recordings':'Recordings','ac.hist.summaries':'Summaries','ac.hist.tts':'Speech clips','ac.hist.conversions':'Conversions',
      'ac.hist.none.rec':'No recordings yet.','ac.hist.none.sum':'No summaries yet.',
      'ac.hist.none.tts':'No speech clips yet.','ac.hist.none.conv':'No conversions yet.',
      'ac.hist.pasted':'Pasted transcript','ac.hist.play':'Play','ac.hist.gone':'No longer stored','ac.hist.expired':'Expired',
      'ac.hist.left.d':'{n} day(s) left','ac.hist.left.h':'{n} hour(s) left','ac.hist.left.m':'{n} minute(s) left',
      'ac.hist.noanalyse':'Analysis is switched off for this account.',
      'ac.src.audio':'Recording','ac.src.text':'Transcript',
      'ac.th.source':'Source','ac.th.duration':'Length','ac.th.ran':'Analysed','ac.th.summary':'Summary',
      'ac.th.calls':'Calls','ac.th.files':'Files','ac.th.expires':'Expires',
      'ac.rub.default':'You are looking at the default rubric. Saving it creates your own copy, which you can then change freely.',
      'ac.rub.defaultver':'Default rubric','ac.rub.reset':'Reset to default',
      'ac.rub.reset.ask':'This replaces your rubric with a copy of the default one. Enter your password to confirm.',
      'ac.rub.reset.needpw':'Enter your password to reset the rubric.',
      'ac.rub.reset.badpw':'That password does not match.','ac.rub.reset.done':'Rubric reset to the default.',
      'ac.pf.heading':'Profile','ac.pf.saved':'Profile saved.','ac.pf.pw':'Change password','ac.pf.pw.change':'Change password',
      'ac.pf.pw.needcur':'Enter your current password.','ac.pf.pw.mismatch':'The two new passwords do not match.',
      'ac.pf.pw.badcur':'The current password is incorrect.','ac.pf.pw.done':'Password changed.',
      'ac.pf.usage':'Today','ac.pf.of':'{used} of {max}','ac.pf.nolimit':'no limit',
      'ac.pf.maxupload':'Largest upload','ac.pf.features':'Available on this account',
      'ac.cv.expires':'Download available — {when}.',
      /* tenant.html (tenant portal v2: Analyse / Rubric / History) — owned by the tenant page. */
      'tn.hist.rec':'Recordings','tn.hist.sum':'Summaries',
      'tn.hist.rec.none':'Nothing here yet — upload a call or paste a transcript in Analyse.',
      'tn.hist.sum.none':'No summaries yet.','tn.hist.open':'Open in Analyse',
      'tn.th.source':'Source','tn.th.length':'Length','tn.th.ran':'Analysed','tn.th.calls':'Calls','tn.th.summary':'Summary',
      'tn.src.audio':'Audio','tn.src.text':'Text',
      'tn.sc.reset':'Reset to default','tn.sc.reset.heading':'Reset this rubric to the default?',
      'tn.sc.reset.warn':'The rubric is replaced by a copy of the shared default, saved as a new version — earlier versions stay in the history. Enter your own password to confirm.',
      'tn.sc.reset.pw':'Your password','tn.sc.reset.needpw':'Enter your password.',
      'tn.sc.reset.bad':'That password does not match.','tn.sc.reset.done':'The rubric was reset to the default.',
      'tn.sc.isdefault':'You are looking at the shared default rubric — this workspace has none of its own yet. Saving creates your own copy, which later changes to the default will not touch.',
      /* ---- public page + admin console (pb.*) — owned by the public+admin page agent ---- */
      'pb.nav.create':'Create account',
      'pb.nav.account':'My account',
      'pb.users':'Users',
      'pb.storage':'Storage',
      'pb.defrubric':'Default rubric',
      'pb.reg.heading':'Registered accounts — daily limits',
      'pb.reg.desc':'Applies to every self-service account that has no override of its own.',
      'pb.reg.signups':'Sign-ups open',
      'pb.reg.signups.hint':'Turning this off closes the public sign-up form. Existing accounts keep working — deactivate one from the table below.',
      'pb.reg.maxconv':'Max conversions / day',
      'pb.feat.convert':'Audio conversion',
      'pb.feat.summarise':'Summarise',
      'pb.feat.score':'Scoring',
      'pb.feat.semantic':'Sentiment analysis',
      'pb.users.heading':'Accounts',
      'pb.users.search':'Search email or name',
      'pb.users.none':'No registered accounts yet.',
      'pb.users.nomatch':'No account matches that search.',
      'pb.users.legend':'“Today” counts analyses · TTS clips · conversions used since midnight.',
      'pb.th.email':'Email',
      'pb.th.name':'Name',
      'pb.th.created':'Created',
      'pb.th.lastlogin':'Last login',
      'pb.th.today':'Today',
      'pb.never':'Never',
      'pb.act.activate':'Activate',
      'pb.act.deactivate':'Deactivate',
      'pb.act.limits':'Limits',
      'pb.act.resetpw':'Reset password',
      'pb.user.saved':'Account updated',
      'pb.lim.title':'Per-account limits',
      'pb.lim.note':'An empty field means this account uses the tier’s number. Saving replaces every override this account has.',
      'pb.lim.saved':'Limits saved',
      'pb.pw.title':'New password',
      'pb.pw.once':'Shown once. Only its hash is stored, so it cannot be shown again — pass it to the user now.',
      'pb.pw.confirm':'Generate a new password for {email}? Their current password stops working immediately.',
      'pb.copy':'Copy',
      'pb.copied':'Copied',
      'pb.copyfail':'Copy failed — select the password and copy it by hand.',
      'pb.close':'Close',
      'pb.del.confirm':'Delete the account {email}? Their recordings, summaries and TTS clips are NOT deleted — those stay until the storage retention period removes the files.',
      'pb.del.done':'Account deleted. Their recordings stay until the storage retention removes the files.',
      'pb.storage.heading':'Stored recordings',
      'pb.storage.desc':'One retention period for every stored recording and TTS clip — anonymous visitors, tenants and registered accounts alike.',
      'pb.storage.days':'Keep recordings for (days)',
      'pb.storage.hint':'0 keeps them indefinitely. An anonymous visitor’s row is stripped in full; a tenant’s or an account’s row keeps its transcript and results and loses only the audio file.',
      'pb.storage.moved':'Retention moved to the Storage tab: one number now covers every stored recording, not only anonymous ones.',
      'pb.defrubric.heading':'Default rubric',
      'pb.defrubric.desc':'Scored against by every tenant and registered account that has not saved a rubric of its own.',
      'pb.src.stored':'Saved by an operator',
      'pb.src.demo':'Seeded from the demo tenant — not saved yet',
      'pb.src.builtin':'Built-in starter — not saved yet',
      'pb.defrubric.updated':'Updated {when} by {who}',
      'pb.defrubric.saved':'Default rubric saved',
    },
    ka: {
      'nav.public':'საჯარო აპლიკაცია','nav.signin':'შესვლა','nav.logout':'გასვლა','nav.kb':'ცოდნის ბაზა',
      'f.username':'მომხმარებლის სახელი','f.password':'პაროლი','f.language':'ენა','f.voice':'ხმა','f.text':'ტექსტი',
      'f.category':'კატეგორია','f.title':'სათაური','f.tags':'ტეგები (მძიმით გამოყოფილი)','f.name':'სახელი','f.industry':'ინდუსტრია','f.region':'რეგიონი',
      'f.audiofile':'აუდიო ან ვიდეო ფაილი','f.provider':'პროვაიდერი','f.dimension':'განზომილება','f.model':'მოდელი','f.baseurl':'საბაზო URL',
      'f.anthropic':'Anthropic (Claude) API გასაღები','f.eleven':'ElevenLabs API გასაღები','f.claudemodel':'Claude მოდელი','f.sttmodel':'Scribe (STT) მოდელი','f.ttsmodel':'TTS მოდელი','f.voiceid':'TTS ხმის ID','f.openaikey':'API გასაღები (მხოლოდ openai)',
      'btn.signin':'შესვლა','btn.save':'შენახვა','btn.savesettings':'პარამეტრების შენახვა','btn.savelimits':'ლიმიტების შენახვა','btn.refresh':'განახლება','btn.delete':'წაშლა','btn.cancel':'გაუქმება','btn.test':'ტესტი','btn.testconn':'კავშირების ტესტი','btn.testdeep':'სრული ტესტი (ღრმა)',
      'cap.database':'მონაცემთა ბაზა','cap.ffmpeg':'აუდიოს გარდაქმნა (ffmpeg)','cap.voices':'ElevenLabs · ხმების სია','cap.stt':'ElevenLabs · მეტყველება ტექსტად','cap.tts':'ElevenLabs · ტექსტი მეტყველებად','cap.ttska':'ElevenLabs · ქართული TTS','cap.embeddings':'ემბედინგები','cap.claude':'Claude · ანალიზი','cap.factcheck':'Claude · ფაქტების შემოწმება','cap.scoring':'Claude · შეფასება',
      'cap.fixscope':'გამოსწორება: ElevenLabs-ში გახსენით Settings → API Keys → Edit ამ გასაღებზე, ჩართეთ ნებართვა „{scope}“, შეინახეთ და ხელახლა შეამოწმეთ.',
      'adm.testnote':'თითოეული შესაძლებლობა რეალურად მოწმდება — ტესტი ხარჯავს წამის მცირე ნაწილს მეტყველების ამოცნობაზე და რამდენიმე სიმბოლოს ხმის სინთეზზე, რადგან ElevenLabs არ იძლევა გასაღების ნებართვების წაკითხვის საშუალებას. „ღრმა“ დამატებით ამოწმებს ფაქტების შემოწმებისა და შეფასების ხელსაწყოებს.','btn.search':'ძებნა','btn.import':'იმპორტი','btn.analyze':'ანალიზი','btn.synth':'მეტყველების გენერაცია','btn.create':'ორგანიზაციის შექმნა','btn.adduser':'მომხმარებლის დამატება','btn.rotate':'როტაცია','btn.remove':'წაშლა','btn.apikey':'API გასაღები','btn.users':'მომხმარებლები','btn.chunks':'ფრაგმენტები',
      'hero.eyebrow':'CommuniQ ხმოვანი AI','hero.title':'ისაუბრეთ და გაიგეთ ყველა ზარი.',
      'tab.tts':'ტექსტი მეტყველებად','tab.analyze':'ზარის ანალიზი','tab.kb':'ცოდნის ბაზა','tab.history':'ისტორია','tab.scoring':'რუბრიკა',
      'tts.heading':'ტექსტიდან მეტყველების გენერაცია','tts.text_ph':'აკრიფეთ სათქმელი… (ინგლისურად, რუსულად ან ქართულად)',
      'an.heading':'ატვირთეთ ჩანაწერი ანალიზისთვის','an.heading_kb':'გააანალიზეთ ზარი — იყენებს თქვენს ცოდნის ბაზას',
      'drop.title':'ჩააგდეთ აუდიო ან ვიდეო ფაილი აქ, ან დააჭირეთ ასარჩევად','drop.sub':'ნებისმიერი აუდიო ან ვიდეო ფაილი — ტრანსკრიფცია ElevenLabs Scribe-ით, ანალიზი Claude-ით','drop.sub_kb':'ჯერ ტრანსკრიფცია, შემდეგ ანალიზი თქვენი ცოდნის ბაზის მიხედვით',
      'rec.or':'ან','rec.record':'ჩაწერა','rec.stop':'გაჩერება','rec.recording':'მიმდინარეობს ჩაწერა','rec.ready':'ჩაწერილია — მზადაა ანალიზისთვის','rec.unsupported':'ჩაწერა საჭიროებს HTTPS-ს ან localhost-ს','rec.denied':'მიკროფონზე წვდომა უარყოფილია',
      'res.analysis':'ანალიზი','res.language':'ენა','res.sentiment':'განწყობა','res.topics':'თემები','res.time':'დრო','res.quality':'ხარისხი','res.summary':'შეჯამება','res.keypoints':'ძირითადი პუნქტები','res.actions':'სამოქმედო პუნქტები','res.transcript':'ტრანსკრიფცია','res.kbused':'გამოყენებული ცოდნის ბაზა','res.nokb':'ცოდნის ბაზაში შესაბამისი კონტექსტი ვერ მოიძებნა.','res.empty':'(ცარიელი)','res.done':'ანალიზი დასრულდა',
      'login.heading':'შესვლა',
      'kb.import':'ცოდნის იმპორტი','imp.file':'ფაილის ატვირთვა','imp.paste':'ტექსტის ჩასმა','imp.csv':'CSV (კითხვა-პასუხი / გასაღები-მნიშვნელობა)','kb.filelabel':'ფაილები (PDF / DOCX / XLSX / CSV / TXT / MD — შეგიძლიათ რამდენიმეც ერთად)','kb.csvlabel':'CSV ფაილი (პირველი სტრიქონი = სათაურები)','kb.searchlabel':'ცოდნის ბაზაში ძებნა','kb.search_ph':'დასვით კითხვა…','kb.documents':'დოკუმენტები','kb.none':'დოკუმენტები ჯერ არ არის. დაამატეთ ცოდნა ზემოთ.','kb.processing':'მუშავდება…','kb.nomatch':'შედეგი ვერ მოიძებნა.',
      'th.title':'სათაური','th.category':'კატეგორია','th.status':'სტატუსი','th.chunks':'ფრაგმენტები','th.file':'ფაილი','th.lang':'ენა','th.when':'როდის','th.name':'სახელი','th.slug':'იდენტიფ.','th.industry':'ინდუსტრია','th.active':'აქტიური','th.users':'მომხმ.','th.docs':'დოკ.',
      'hist.heading':'ბოლო ანალიზები','hist.none':'ჯერ არ არის ანალიზი.',
      'adm.tenants':'ორგანიზაციები','adm.embeddings':'ემბედინგები','adm.anon':'ანონიმური ლიმიტები','adm.integrations':'ინტეგრაციები',
      'adm.createtenant':'ორგანიზაციის შექმნა','adm.embprov':'ემბედინგების პროვაიდერი','adm.embnote':'განზომილების შეცვლა მოითხოვს ცოდნის ბაზის ხელახალ ემბედინგს (საჭიროა დოკუმენტების ხელახალი იმპორტი).',
      'adm.anonheading':'ანონიმური მომხმარებლების (ავტორიზაციის გარეშე) ლიმიტები','adm.allowanon':'ანონიმური მომხმარებლების დაშვება','adm.maxanalyses':'მაქს. ანალიზი / დღე','adm.maxmb':'მაქს. აუდიო MB','adm.maxtts':'მაქს. TTS / დღე','adm.features':'დაშვებული ფუნქციები','feat.analyze':'ანალიზი','feat.tts':'ტექსტი მეტყველებად',
      'adm.intkeys':'ინტეგრაციის გასაღებები','adm.models':'მოდელები და ხმა','adm.instructions':'ანალიზის ინსტრუქციები',
      'toast.saved':'პარამეტრები შენახულია','toast.imported':'იმპორტი დაიწყო','toast.deleted':'წაიშალა','toast.created':'შეიქმნა','toast.welcome':'კეთილი იყოს თქვენი მობრძანება','toast.error':'რაღაც ვერ მოხერხდა',
      'tip.label':'დამატებითი ინფორმაცია',
      'err.toolarge':'ფაილი ძალიან დიდია ასატვირთად. გამოიყენეთ უფრო მოკლე ან პატარა ჩანაწერი.','err.timeout':'ანალიზმა დიდი დრო წაიღო და ვადა ამოიწურა. სცადეთ უფრო მოკლე ჩანაწერი.','err.unavailable':'სერვისი დროებით მიუწვდომელია. გთხოვთ სცადოთ ცოტა ხანში.','err.http':'შეცდომა მოხდა ჩვენს მხარეს. სცადეთ ცოტა ხანში — თუ განმეორდება, მიმართეთ მხარდაჭერას. (კოდი {status})','err.badresp':'სერვერმა მოულოდნელი პასუხი დააბრუნა. გთხოვთ სცადოთ თავიდან.',
      'quota.using':'თქვენ იყენებთ CommuniQ-ს ანონიმურად —','quota.analyses':'ტრანსკრიფცია','quota.clips':'აუდიო კლიპი','quota.left':'დარჩა დღეს.','quota.more':'ცოდნის ბაზისა და გაზრდილი ლიმიტებისთვის.','quota.disabled':'ანონიმური წვდომა გათიშულია.',
      'fc.title':'ფაქტების შემოწმება ცოდნის ბაზასთან','fc.accuracy':'სიზუსტე','fc.supported':'დადასტურებული','fc.contradicted':'უარყოფილი','fc.notinkb':'ბაზაში არ არის','fc.partial':'ნაწილობრივ სწორი','fc.misinfo':'შესაძლო მცდარი ინფორმაცია','fc.nochecked':'შესამოწმებელი მტკიცება ვერ მოიძებნა.',
      'adm.voices':'ხმები','adm.voicevis':'მომხმარებლისთვის ხილული ხმები','f.restrictvoices':'მომხმარებლისთვის მხოლოდ მონიშნული ხმების ჩვენება','f.defaultvoice':'ნაგულისხმევი ხმა','v.hint':'მოუნიშნავი ხმები იმალება მომხმარებლის სიიდან და TTS API მათ უარყოფს. თუ ველი მოუნიშნავია, ყველა ხმა ჩანს. სისტემური ნაგულისხმევი ხმები (მათ შორის ქართული) ყოველთვის ჩართულია.','v.search':'ხმების ძებნა…','v.selected':'მონიშნული','v.system':'სისტემური','v.nopreview':'ნიმუში არ არის','v.unavailable':'ამ ElevenLabs ანგარიშში არ არის','v.pickone':'მონიშნეთ მინიმუმ ერთი ხმა ან მოხსენით შეზღუდვა.','v.loadfail':'ხმების ჩატვირთვა ElevenLabs-იდან ვერ მოხერხდა. შეამოწმეთ API გასაღები ინტეგრაციებში.','msg.voicegone':'ეს ხმა აღარ არის ხელმისაწვდომი. სია განახლდა.',
      'fc.allclaims':'ყველა მტკიცება','pg.tab.retrieval':'ძიების ტესტი','pg.tab.score':'პასუხის შეფასება','pg.tab.sentiment':'განწყობა','pg.ans.label':'ოპერატორის პასუხი (ნებისმიერ ენაზე)','pg.ans.ph':'ჩასვით ან აკრიფეთ ოპერატორის პასუხი — შეფასდება ამ ორგანიზაციის რუბრიკით…','pg.ans.run':'პასუხის შეფასება','pg.ans.norubric':'აქტიური რუბრიკა არ არის — ჯერ განსაზღვრეთ ის „შეფასების“ ჩანართში.','pg.ans.empty':'შეიყვანეთ პასუხი შესაფასებლად.','pg.ans.usingv':'რუბრიკის ვერსია',
      'tab.playground':'ზარის შეფასება','pg.heading':'შეაფასეთ ზარი ან პასუხი','pg.mode.text':'ტექსტი','pg.mode.audio':'აუდიო / ვიდეო','pg.mode.sentiment':'განწყობა','pg.audiolabel':'ზარის ჩანაწერი (აუდიო ან ვიდეო)','pg.run':'შეფასება','pg.audioempty':'აირჩიეთ აუდიო ან ვიდეო ფაილი.',
      'kba.title':'ცოდნის ბაზის მართვა','kba.tenant':'ორგანიზაცია','kba.selecttenant':'აირჩიეთ ორგანიზაცია მისი ცოდნის ბაზის სამართავად.',
      'kba.tab.overview':'მიმოხილვა','kba.tab.documents':'დოკუმენტები','kba.tab.import':'იმპორტი','kba.tab.playground':'ტესტირება','kba.tab.duplicates':'დუბლიკატები','kba.tab.activity':'აქტივობა',
      'kba.stat.documents':'დოკუმენტები','kba.stat.chunks':'ფრაგმენტები','kba.stat.coverage':'ემბედინგებით დაფარვა','kba.stat.failed':'წარუმატებელი იმპორტები','kba.stat.tokens':'დაახლ. ტოკენები','kba.stat.lastupd':'ბოლო განახლება','kba.stat.inprogress':'მიმდინარე',
      'kba.params':'აქტიური კონფიგურაცია','kba.export':'ექსპორტი','kba.exportcsv':'CSV ექსპორტი','kba.reembedall':'ძიების ინდექსის განახლება','kba.refresh':'განახლება',
      'kba.f.status':'სტატუსი','kba.f.type':'ტიპი','kba.f.tag':'ტეგი','kba.f.search':'ძებნა სათაურში/კონტენტში','kba.f.all':'ყველა',
      'kba.selected':'მონიშნული','kba.bulk.delete':'წაშლა','kba.bulk.reembed':'ძიების განახლება','kba.bulk.retag':'ტეგების შეცვლა','kba.selectall':'ყველას მონიშვნა',
      'kba.edit':'რედაქტირება','kba.chunks':'ფრაგმენტები','kba.reembed':'ხელახალი ემბედინგი','kba.delete':'წაშლა','kba.save':'შენახვა','kba.nodocs':'დოკუმენტები არ არის. დაამატეთ ქვემოთ.',
      'kba.doc.title':'სათაური','kba.doc.type':'კატეგორია','kba.doc.tags':'ტეგები','kba.doc.meta':'მეტამონაცემები (JSON, არასავალდებულო)','kba.doc.content':'კონტენტი (რედაქტირებისას ფრაგმენტები და ემბედინგები ხელახლა შეიქმნება)',
      'kba.pg.query':'მოთხოვნა (ნებისმიერ ენაზე)','kba.pg.topk':'შედეგების რაოდენობა','kba.pg.threshold':'დამთხვევის მინიმალური ქულა (0–1)','kba.pg.run':'მოძიების გაშვება','kba.pg.method':'მეთოდი','kba.pg.nohits':'ფრაგმენტები ვერ მოიძებნა.',
      'retr.m.vector':'სემანტიკური','retr.m.keyword':'ტექსტური დამთხვევა','retr.m.none':'არცერთი',
      'retr.top':'საუკეთესო ქულა','retr.spread':'გაბნევა','retr.margin':'სხვაობა მეორესთან',
      'retr.level.high':'სანდო','retr.level.medium':'საშუალო','retr.level.low':'სუსტი','retr.level.none':'დამთხვევის გარეშე',
      'retr.opendoc':'დოკუმენტის გახსნა',
      'retr.flag.shown':'ქვემოთ მაინც ჩამოთვლილია ყველაზე ახლო ფრაგმენტები, საუკეთესოდან დაწყებული.',
      'retr.flag.empty_kb':'ჯერ არაფერია მოსაძებნი',
      'retr.flag.empty_kb.b':'ეს ცოდნის ბაზა ცარიელია, ამიტომ მასში პასუხი ვერ მოიძებნება. გახსენით ჩანართი „იმპორტი“ და ჯერ დაამატეთ თქვენი წესები, ხშირი კითხვები ან სასაუბრო სცენარები.',
      'retr.flag.unavailable':'ძებნა ვერ შესრულდა',
      'retr.flag.unavailable.b':'მოძიება ბოლომდე არ შესრულდა, ამიტომ ეს არაფერს ამბობს თქვენს ცოდნის ბაზაზე — ნუ აღიქვამთ მას როგორც „პასუხი ვერ მოიძებნა“. სცადეთ ხელახლა ცოტა ხანში; თუ პრობლემა განმეორდება, შეატყობინეთ CommuniQ-ის ოპერატორს.',
      'retr.flag.no_hits':'დამთხვევა ვერ მოიძებნა',
      'retr.flag.no_hits.b':'ამ კითხვაზე არცერთი ფრაგმენტი არ დაბრუნდა. სცადეთ კითხვის დასმა ისე, როგორც კლიენტი დასვამდა, ან დაამატეთ დოკუმენტი, რომელიც მას პასუხობს.',
      'retr.flag.keyword_fallback':'სარეზერვო ძებნა — სემანტიკური ძებნა მიუწვდომელია',
      'retr.flag.keyword_fallback.b':'ემბედინგების სერვისთან დაკავშირება ვერ მოხერხდა, ამიტომ ეს ფრაგმენტები შეირჩა ტექსტური მსგავსებით და არა მნიშვნელობით; მათი ქულები ჩვეულებრივ ქულებს ვერ შედარდება. აღდგენამდე შედეგები ჩვეულებრივზე სუსტი იქნება — შეატყობინეთ CommuniQ-ის ოპერატორს.',
      'retr.flag.flat_distribution':'მკაფიო დამთხვევა არ არის',
      'retr.flag.flat_distribution.b':'ყველა ფრაგმენტმა თითქმის ერთნაირი ქულა მიიღო, რაც ჩვეულებრივ ნიშნავს, რომ ცოდნის ბაზაში ამ კითხვაზე პასუხი არ არის — ქვემოთ მხოლოდ ყველაზე ახლო ტექსტია და არა ნამდვილი დამთხვევა. დაამატეთ ამ თემის დოკუმენტი, ან შეკითხვა იმ სიტყვებით დასვით, რომლებიც თქვენს დოკუმენტებშია.',
      'retr.flag.low_score':'მხოლოდ სუსტი დამთხვევები',
      'retr.flag.low_score.b':'ყველაზე ახლო ფრაგმენტები კითხვას მხოლოდ ირიბად უკავშირდება. სანამ დაეყრდნობით, გადაამოწმეთ, ნამდვილად ფარავს თუ არა რომელიმე დოკუმენტი ამ თემას, და თუ არა — დაამატეთ.',
      'retr.flag.generic':'დაბალი სანდოობის შედეგი',
      'retr.flag.generic.b':'ამ ძებნამ ცოდნის ბაზაში დამაჯერებელი დამთხვევა ვერ იპოვა.',
      'kba.dup.exact':'ზუსტი დუბლიკატები','kba.dup.near':'მსგავსი დუბლიკატები','kba.dup.none':'დუბლიკატები არ მოიძებნა.','kba.dup.sim':'მსგავსება',
      'kba.act.none':'აქტივობა ჯერ არ არის.','kba.chunk.edit':'ფრაგმენტის რედაქტირება','kba.chunk.delete':'ფრაგმენტის წაშლა',
      'kba.warn.mismatch':'ემბედინგის განზომილება არ ემთხვევა — საჭიროა ხელახალი ემბედინგი',
      'kba.tab.scoring':'შეფასება',
      'sc.title':'რუბრიკის ქულა','sc.weighted':'შეწონილი','sc.weight':'წონა','sc.contribution':'წვლილი',
      'sc.heading':'შეფასების რუბრიკა',
      'sc.rubric':'ზოგადი რუბრიკა / მითითება','sc.rubric.ph':'არასავალდებულო ზოგადი მითითება შემფასებლისთვის (ტონი, რა არის მთავარი, სიმკაცრე)…',
      'sc.adddim':'+ კრიტერიუმის დამატება','sc.dname':'კრიტერიუმის სახელი','sc.dname.ph':'მაგ. მისალმება და იდენტიფიკაცია',
      'sc.ddesc':'მოკლე აღწერა','sc.dweight':'წონა','sc.dguide':'შეფასების მითითება',
      'sc.dguide.ph':'როგორ შეფასდეს ეს კრიტერიუმი: რა იმსახურებს მაღალ ქულას და რა — დაბალს…',
      'sc.remove':'წაშლა','sc.save':'რუბრიკის შენახვა','sc.saved':'რუბრიკა შენახულია','sc.sum':'ჯამური წონა',
      'sc.nodims':'კრიტერიუმები ჯერ არ არის — დასაწყებად დაამატეთ ერთი.','sc.version':'ვერსია','sc.none':'ამ ორგანიზაციას აქტიური რუბრიკა ჯერ არ აქვს.',
      'sc.needname':'თითოეულ კრიტერიუმს სჭირდება სახელი.','sc.needone':'დაამატეთ ერთი კრიტერიუმი მაინც.',
      'sc.normalize':'100%-მდე მოყვანა','sc.mustbe100':'წონების ჯამი უნდა იყოს 100% (ახლა {total}%-ია).',
      'cur.tab':'განსახილველი','cur.heading':'გადასახედი ხარვეზები ცოდნის ბაზაში',
      'cur.none':'გადასახედი არაფერია — რიგი ცარიელია.','cur.loadfail':'რიგის ჩატვირთვა ვერ მოხერხდა.',
      'cur.op.add':'დამატება','cur.op.update':'განახლება','cur.op.remove':'დამალვა',
      'cur.priority':'პრიორიტეტი','cur.asked':'იკითხეს {n}-ჯერ','cur.sources':'{n} წყარო',
      'cur.confidence':'სანდოობა','cur.risk':'რისკი','cur.window':'პერიოდი',
      'cur.evidence':'რას ამბობდნენ მომხმარებლები','cur.evidence.none':'ამ ჯგუფისთვის ციტატები არ შენახულა.',
      'cur.target':'სამიზნე დოკუმენტი','cur.diff':'ცვლილება მიმდინარე ფრაგმენტთან შედარებით',
      'cur.diff.nochunk':'მიმდინარე ფრაგმენტი მიუწვდომელია — ნაჩვენებია მხოლოდ შემოთავაზებული ტექსტი.','cur.proposed':'შემოთავაზებული ტექსტი',
      'cur.accept':'მიღება','cur.acceptedit':'მიღება რედაქტირებით','cur.decline':'უარყოფა',
      'cur.applied':'ცოდნის ბაზაში აისახა','cur.declinedok':'უარყოფილია — აღარ გამოჩნდება',
      'cur.edit.heading':'მიღება რედაქტირებით','cur.edit.hint':'დაარედაქტირეთ ტექსტი ცოდნის ბაზაში შესვლამდე. შენახვისას ის ხელახლა დანაწევრდება და ემბედინგები განახლდება.',
      'cur.decline.heading':'რატომ უარყოფთ?','cur.decline.r.nottrue':'მცდარია',
      'cur.decline.r.covered':'ბაზაში უკვე არსებობს','cur.decline.r.dontsay':'არ მინდა, რომ ბოტმა ეს თქვას',
      'cur.decline.r.temporary':'დროებითი / ერთჯერადი','cur.decline.pick':'ჯერ აირჩიეთ მიზეზი.',
      'cur.bulk.accept':'მონიშნულის მიღება','cur.bulk.note':'მასობრივი მიღება მხოლოდ დამატებასა და განახლებაზე მოქმედებს — დამალვა თითო-თითოდ განიხილება.',
      'cur.remove.heading':'დაადასტურეთ დამალვა','cur.remove.word':'დამალვა','cur.remove.confirm':'დასადასტურებლად აკრიფეთ {word}.',
      'cur.remove.mismatch':'არ ემთხვევა — არაფერი შეცვლილა.',
      'cur.remove.note':'ეს ტექსტი პასუხებში აღარ გამოჩნდება. არაფერი იშლება — ოპერატორს კვლავ შეუძლია მისი ხელით წაშლა.',
      'cur.run':'კურაციის გაშვება','cur.run.started':'კურაცია რიგში დადგა',
      'cur.st.pending':'მოლოდინში','cur.st.accepted':'მიღებული','cur.st.declined':'უარყოფილი','cur.st.superseded':'ჩანაცვლებული','cur.st.apply_failed':'ვერ აისახა',
      'cur.filter.state':'სტატუსი','cur.openjob':'ზარის გახსნა','cur.opensource':'საუბრის გახსნა',
      'cur.foreign':'⚠ იყენებს სხვა ორგანიზაციის მტკიცებულებას',
      'tab.bot':'ბოტი','bot.heading':'საჯარო ბოტი',
      'bot.autopilot':'ავტოპილოტი — ბოტი პასუხობს კლიენტს ადამიანის ჩარევის გარეშე',
      'bot.autopilot.hint':'გამორთული ავტოპილოტის დროს ბოტი მხოლოდ ამზადებს პასუხის მონახაზს, რომელსაც ოპერატორი ამოწმებს და აგზავნის. კლიენტამდე წაუკითხავი არაფერი მიდის.',
      'bot.state.live':'პასუხობს კლიენტებს','bot.state.off':'მხოლოდ მონახაზები — პასუხს ადამიანი აგზავნის','bot.state.killed':'შეჩერებულია CommuniQ-ის მიერ',
      'bot.killed.note':'CommuniQ-ის მხარდაჭერის გუნდმა ავტოპილოტი შეაჩერა. თქვენი პარამეტრები შენახულია; აღდგენამდე ბოტი ყველა საუბარს ადამიანს გადასცემს.',
      'bot.needpublic.title':'ავტოპილოტს სჭირდება ბოტისთვის დაშვებული მინიმუმ ერთი დოკუმენტი',
      'bot.needpublic.body':'ცოდნის ბაზის დოკუმენტები ნაგულისხმევად შიდაა და ბოტი მხოლოდ დაშვებულ დოკუმენტებს ციტირებს. თუ არცერთი არ არის დაშვებული, ბოტი ყველა კითხვაზე უარს იტყვის. დაუშვით ის დოკუმენტები, რომელთა წაკითხვის უფლებაც კლიენტს აქვს, და შემდეგ ჩართეთ ავტოპილოტი. ინტერნეტში არაფერი ქვეყნდება.',
      'bot.needpublic.link':'ცოდნის ბაზის გახსნა',
      'bot.persona':'პერსონა','bot.persona.ph':'თქვენ ხართ … მხარდაჭერის ასისტენტი. იყავით ლაკონიური, თბილი და კონკრეტული.',
      'bot.greeting':'მისალმება','bot.refusal':'უარის ტექსტი — რას ამბობს ბოტი, როცა ცოდნის ბაზაში პასუხი არ არის',
      'bot.refusal.hint':'ეს ის წინადადებაა, რომელსაც კლიენტი ყველაზე ხშირად ხედავს. დაწერეთ ყველა ენაზე, რომელზეც ბოტი პასუხობს; ტექსტი ადამიანის დახმარებას უნდა სთავაზობდეს და არა ორჯერ ბოდიშობდეს.',
      'bot.refusal.missing':'ავტოპილოტის ჩართვამდე დაწერეთ უარის ტექსტი {lang} ენაზე.',
      'bot.lang.en':'ინგლისური','bot.lang.ka':'ქართული','bot.lang.ru':'რუსული',
      'bot.languages':'ენები, რომლებზეც ბოტი პასუხობს','bot.languages.pickone':'აირჩიეთ მინიმუმ ერთი ენა.',
      'bot.escalation':'ესკალაციის საკვანძო სიტყვები','bot.escalation.ph':'ადვოკატი, საჩივარი, თანხის დაბრუნება',
      'bot.escalation.hint':'მძიმით გამოყოფილი. დამთხვევისას საუბარი მაშინვე ადამიანს გადაეცემა, პასუხის გენერაციამდე.',
      'bot.retrieval':'ძიება და პასუხის ლიმიტები','bot.minscore':'დამთხვევის მინიმალური ქულა პასუხამდე (0–1)','bot.minhits':'დამთხვეული ფრაგმენტების მინიმუმი პასუხამდე','bot.topk':'მოძიებული ფრაგმენტები თითო კითხვაზე','bot.suggestions':'შეთავაზებული პასუხები თითო რეპლიკაზე (დრაფტების რეჟიმი)','bot.maxchars':'პასუხის მაქს. სიმბოლოები',
      'bot.caps':'სიხშირის ლიმიტები','bot.cap.tenant':'სვლა / წუთში (მთელი სამუშაო სივრცე)','bot.cap.enduser':'სვლა / საათში (ერთი კლიენტი)',
      'bot.general':'უპასუხოს ზოგადი ცოდნით, როცა ცოდნის ბაზაში არაფერია',
      'bot.general.risk':'სარისკო არჩევანია, ნაგულისხმევად გამორთული. თუ გამორთულია, ბოტი ზემოთ მითითებული ტექსტით ამბობს უარს და ადამიანს სთავაზობს — ის მხოლოდ იმას იმეორებს, რაც თქვენ ბოტისთვის დაუშვით. თუ ჩართულია, შესაძლოა მოდელის საკუთარი ცოდნით უპასუხოს — ეს არ არის თქვენი პოლიტიკა, არ ექვემდებარება აუდიტს და შეიძლება დარწმუნებით შეცდეს თქვენს ფასებში, წესებსა და ვადებში.',
      'bot.general.confirm':'მივცე ბოტს უფლება, მოდელის ზოგადი ცოდნით უპასუხოს? მაშინ ის იტყვის ისეთ რამეს, რაც თქვენს ცოდნის ბაზაში არ არის და თქვენს კომპანიაში არავის დაუმტკიცებია.',
      'bot.general.on':'ჩართვა',
      'bot.handoff':'დაწეროს მოკლე შეჯამება ადამიანისთვის, რომელიც საუბარს გადაიბარებს','bot.handoff.hint':'საჭიროებს მოდელის ერთ დამატებით გამოძახებას, მხოლოდ გადაცემისას. თუ გამორთულია, ოპერატორი საუბარს კონტექსტის გარეშე იღებს.',
      'bot.save':'ბოტის პარამეტრების შენახვა','bot.saved':'ბოტის პარამეტრები შენახულია','bot.version':'ვერსია',
      'bot.loadfail':'ბოტის პარამეტრები ვერ ჩაიტვირთა.','bot.unavailable':'ბოტის პარამეტრები ამ სერვერზე ჯერ ხელმისაწვდომი არ არის.',
      'bot.soon.title':'მალე დაემატება','bot.soon.desc':'მომხმარებელთა ბოტი ჯერ მზადდება. როგორც კი ამუშავდება, მისი პარამეტრები ამ გვერდზე გამოჩნდება.',
      'bot.soon.desc.admin':'მომხმარებელთა ბოტი ჯერ არ არის გაშვებული — ორგანიზაციები ამ ეტაპზე ხედავენ გვერდს „მალე დაემატება“. ქვემოთ მოცემული მართვა ბოტის ამუშავების შემდეგ იმოქმედებს.',
      'adm.bot':'ბოტის მართვა','kill.heading':'ავტოპილოტის ავარიული გამორთვა',
      'kill.desc':'ეს მუხრუჭია: აჩერებს საჯარო ბოტის პასუხებს და საუბრები ადამიანებს გადაეცემა. ორგანიზაციის პარამეტრები ხელუხლებელი რჩება, ამიტომ აღდგენა ერთი დაწკაპუნებით ხდება.',
      'kill.global':'ავტოპილოტის შეჩერება ყველა ორგანიზაციისთვის','kill.global.on':'შეჩერებულია ყველგან','kill.global.off':'მუშაობს ნორმალურად',
      'kill.tenants':'ორგანიზაციების მიხედვით','kill.stop':'შეჩერება','kill.resume':'აღდგენა',
      'kill.confirm.global':'შევაჩერო ავტოპილოტი ყველა ორგანიზაციისთვის? აღდგენამდე ყველა ბოტი საუბრებს ადამიანებს გადასცემს.',
      'kill.confirm.resume.global':'აღვადგინო ავტოპილოტი ყველა ორგანიზაციისთვის, რომელსაც ის ჩართული აქვს?',
      'kill.confirm.tenant':'შევაჩერო ავტოპილოტი „{name}“-სთვის?','kill.confirm.resume':'აღვადგინო ავტოპილოტი „{name}“-სთვის?',
      'kill.state.live':'აქტიური','kill.state.stopped':'შეჩერებული','kill.state.off':'ავტოპილოტი გამორთულია',
      'kill.saved':'ავარიული გამორთვა განახლდა','kill.loadfail':'ავარიული გამორთვის მდგომარეობის წაკითხვა ვერ მოხერხდა.',
      'kill.unavailable':'ავარიული გამორთვა ამ სერვერზე ჯერ არ არის განთავსებული.','kill.overviewfail':'ორგანიზაციების ავტოპილოტის მდგომარეობის წაკითხვა ვერ მოხერხდა.',
      'th.autopilot':'ავტოპილოტი',
      'vis.col':'ხილვადობა','vis.all':'ყველა','vis.public':'ბოტისთვის დაშვებული','vis.internal':'შიდა',
      'vis.publish':'ბოტისთვის დაშვება','vis.unpublish':'დაშვების მოხსნა',
      'vis.bulk.publish':'მონიშნულის დაშვება ბოტისთვის','vis.bulk.unpublish':'მონიშნულისთვის დაშვების მოხსნა',
      'vis.stat.public':'ბოტისთვის დაშვებული',
      'vis.confirm.publish':'დაეშვას {n} დოკუმენტი ბოტისთვის? ბოტი დაშვებულ დოკუმენტებს თქვენს კლიენტებთან სიტყვასიტყვით ციტირებს. ინტერნეტში არაფერი ქვეყნდება — მონაცემები თქვენს სამუშაო სივრცეში რჩება.',
      'vis.confirm.unpublish':'მოეხსნას დაშვება {n} დოკუმენტს? ბოტი მათ აღარ გამოიყენებს.',
      'vis.confirm.publish.one':'დაეშვას „{title}“ ბოტისთვის? ბოტი მას თქვენს კლიენტებთან სიტყვასიტყვით ციტირებს. ინტერნეტში არაფერი ქვეყნდება.',
      'vis.confirm.unpublish.one':'მოეხსნას დაშვება „{title}“-ს? ბოტი მას აღარ გამოიყენებს.',
      'vis.done.publish':'დაშვებულია ბოტისთვის','vis.done.unpublish':'დაშვება მოხსნილია',
      'tkb.tab.maint':'მოვლა','tkb.overview.heading':'ცოდნის ბაზის მდგომარეობა',
      'tkb.params.hint':'პარამეტრები, რომლებითაც მოძიება რეალურად მუშაობს. თუ კონფიგურაციის განზომილება და ბაზაში შენახული ერთმანეთს არ ემთხვევა, ახალი ემბედინგები ვერ იქმნება და მოძიება უხმაუროდ აღარ მუშაობს.',
      'tkb.params.columndim':'განზომილება (შენახული)','tkb.params.chunk':'ფრაგმენტის ზომა / გადაფარვა',
      'tkb.params.threshold':'მოძიების ზღვარი','tkb.params.topk':'ნაგულისხმევი top-k',
      'tkb.params.metric':'მანძილის მეტრიკა','tkb.params.index':'ინდექსის ტიპი','tkb.params.noembed':'ფრაგმენტები ემბედინგის გარეშე',
      'tkb.loadfail':'ცოდნის ბაზა ვერ ჩაიტვირთა.',
      'tkb.th.source':'წყარო','tkb.docs.none':'დოკუმენტები ჯერ არ არის — დაამატეთ „იმპორტის“ ჩანართში.',
      'tkb.del.confirm':'წაიშალოს „{title}“? მისი ფრაგმენტები მაშინვე გაქრება ყველა პასუხიდან და მოქმედება შეუქცევადია.',
      'tkb.bulk.delete.confirm':'წაიშალოს {n} დოკუმენტი? მათი ფრაგმენტები მაშინვე გაქრება ყველა პასუხიდან და მოქმედება შეუქცევადია.',
      'tkb.bulk.reembed.confirm':'გაუკეთდეს {n} დოკუმენტს ხელახალი ემბედინგი ახლავე? პროცესი მაშინვე შესრულდება და მცირე ხნით რესურსებს გაინაწილებს მიმდინარე მოძიებასთან.',
      'tkb.edit.warn':'ახალი ტექსტის შენახვისას ეს დოკუმენტი ხელახლა დანაწევრდება და მისი ემბედინგები განახლდება. მოძიება ახალ ტექსტზე დასრულებისთანავე გადავა; თუ პროცესი ვერ შესრულდა, დოკუმენტი შეცდომის სტატუსით მოინიშნება და ნახევრად განახლებული არ დარჩება.',
      'tkb.badjson':'მეტამონაცემები უნდა იყოს ვალიდური JSON.','tkb.reembed.done':'{n} ფრაგმენტის საძიებო მონაცემები განახლდა',
      'tkb.chunks.pick':'დოკუმენტი','tkb.chunks.none':'ამ დოკუმენტს ფრაგმენტები ჯერ არ აქვს.',
      'tkb.chunks.pickone':'აირჩიეთ დოკუმენტი ფრაგმენტების სანახავად.',
      'tkb.chunks.hint':'მოძიება ფრაგმენტებს ადარებს და არა დოკუმენტებს. ფრაგმენტის რედაქტირებისას მისი ემბედინგი მაშინვე განახლდება; წაშლა კი მას ყველა პასუხიდან შლის.',
      'tkb.chunk.noembed':'ემბედინგი არ აქვს',
      'tkb.chunk.del.confirm':'წაიშალოს ეს ფრაგმენტი? ის მაშინვე გაქრება ყველა პასუხიდან.',
      'tkb.chunk.edit.hint':'შენახვისას ამ ფრაგმენტის ემბედინგი მაშინვე განახლდება. დოკუმენტის დანარჩენი ნაწილი უცვლელი რჩება.',
      'tkb.pg.heading':'შეამოწმეთ, რას პოულობს ძიება',
      'tkb.dup.identical':'დოკუმენტს იდენტური შიგთავსი აქვს','tkb.dup.keep':'რჩება',
      'tkb.dup.skipped':'მსგავსი დუბლიკატების სკანირება გამოტოვებულია — ამ ცოდნის ბაზაში ძალიან ბევრი ფრაგმენტია ყველა წყვილის შესადარებლად.',
      'tkb.act.filter':'მოქმედება','tkb.act.filter.ph':'იმპორტი, რედაქტირება, წაშლა, ემბედინგი…',
      'tkb.act.method':'მეთოდი','tkb.act.detail':'დეტალი','tkb.act.actor':'ვინ',
      'tkb.exp.hint':'ჩამოტვირთავს ამ ცოდნის ბაზის ყველა დოკუმენტს, შიდას ჩათვლით. თავად ექსპორტი აქტივობის ჟურნალში ფიქსირდება.',
      'tkb.reembed.heading':'ძიების ინდექსის განახლება',
      'tkb.reembed.desc':'ხელახლა აგებს საძიებო მონაცემებს ყოველი ფრაგმენტისთვის — საჭიროა ემბედინგის მოდელის ან განზომილების შეცვლის შემდეგ. ეშვება ფონურად, შეზღუდული სიჩქარით, ამიტომ დიდ ბაზაზე შეიძლება დიდხანს გაგრძელდეს; ძიება მთელი ამ დროის განმავლობაში მუშაობს. ერთდროულად მხოლოდ ერთი განახლება მიმდინარეობს.',
      'tkb.reembed.start':'განახლების დაწყება',
      'tkb.reembed.confirm':'დადგეს რიგში ძიების ინდექსის სრული განახლება? შესრულდება ფონურად და შეიძლება დიდხანს გაგრძელდეს. დასრულებამდე ახლის დაწყება ვერ მოხერხდება.',
      'tkb.reembed.queued':'ძიების ინდექსის განახლება რიგში დადგა','tkb.reembed.busy':'განახლება უკვე რიგშია ან მიმდინარეობს.',
      'tkb.reembed.none':'განახლება ჯერ არ გაშვებულა.',
      'tkb.reembed.progress':'{done} / {total} დოკუმენტი','tkb.reembed.failed':'{n} ჩავარდა',
      'tkb.reembed.state.queued':'რიგში','tkb.reembed.state.running':'მიმდინარეობს','tkb.reembed.state.done':'დასრულდა',
      'tkb.reembed.state.error':'ჩავარდა','tkb.reembed.state.cancelled':'გაუქმდა',
      'sc.readonly':'მხოლოდ სანახავად — შეფასების რუბრიკის რედაქტირება მხოლოდ სამუშაო სივრცის მფლობელს შეუძლია.',
      'adm.retention':'ანონიმური მონაცემების შენახვა (დღე)','adm.retention.hint':'რამდენ ხანს ინახება არარეგისტრირებული მომხმარებლის IP, აუდიო და ტექსტი, სანამ წაიშლება. 0 — უვადოდ.',
      'adm.sentiment.heading':'საჯარო განწყობის ანალიზი',
      'tab.stt':'მეტყველება ტექსტად','btn.transcribe':'ტრანსკრიფცია',
      'stt.heading':'აქციეთ ჩანაწერი ტექსტად','stt.nofile':'ჯერ აირჩიეთ აუდიო ფაილი.',
      'drop.sub_stt':'ნებისმიერი აუდიო ან ვიდეო ფაილი — ტრანსკრიფცია ElevenLabs Scribe-ით',
      'sn.title':'განწყობა','sn.text':'რა ითქვა','sn.voice':'როგორ ჟღერდა','sn.arousal':'ენერგია','sn.valence':'პოზიტიურობა','sn.unavailable':'ამ ჩანაწერისთვის მიუწვდომელია.','sn.conflict':'სიტყვები და ხმის ტონი არ ემთხვევა — ღირს მოსმენა.',
      'tab.sentiment':'განწყობა','sn.heading':'როგორ ჟღერდა მოსაუბრე?','sn.run':'განწყობის ანალიზი','sn.none':'ამ ჩანაწერისთვის განწყობის დადგენა ვერ მოხერხდა.','sn.config':'განწყობის პარამეტრები','sn.enabled':'განწყობის ანალიზის ჩართვა','sn.guidance':'მითითება ტექსტის შემფასებლისთვის (არასავალდებულო)','sn.guidance.ph':'მაგ. ნებისმიერი პრეტენზია ჩაითვალოს მინიმუმ ოდნავ ნეგატიურად, თუნდაც თავაზიანად იყოს ნათქვამი…','sn.save':'განწყობის პარამეტრების შენახვა','sn.saved':'შენახულია','sn.readonly':'მხოლოდ სანახავად — განწყობის პარამეტრების რედაქტირება მხოლოდ სამუშაო სივრცის მფლობელს შეუძლია.','sn.audiolabel':'გასაანალიზებელი აუდიო','sn.mode':'განწყობა','sn.disabled':'განწყობის ანალიზი გამორთულია ამ სამუშაო სივრცისთვის.',
      'lang.en':'ინგლისური','lang.ru':'რუსული','lang.ka':'ქართული',
      'lang.note.ka':'ქართულისთვის გამოიყენება eleven_v3 მოდელი ქართულის მხარდამჭერ ხმასთან ერთად — სწორი გამოთქმისთვის. საუკეთესო შედეგისთვის დატოვეთ ნაგულისხმევი ხმა.',
      'tts.needtext':'შეიყვანეთ ტექსტი.','tts.pickvoice':'მოსასმენად აირჩიეთ კონკრეტული ხმა.','tts.previewtitle':'ხმის მოსმენა (უფასო ნიმუში)',
      'session.expired':'სესიის ვადა ამოიწურა — გთხოვთ, შეხვიდეთ ხელახლა.',
      'login.failed':'შესვლა ვერ მოხერხდა. შეამოწმეთ მომხმარებლის სახელი და პაროლი.',
      'login.empty':'შეიყვანეთ მომხმარებლის სახელი და პაროლი.',
      'login.checking':'სესია აღდგება…',
      'login.showpw':'პაროლის ჩვენება',
      'nav.console':'კონსოლი',
      'btn.retry':'ხელახლა ცდა',
      'adm.deltenant.confirm':'წაიშალოს „{name}“? ეს სამუდამოდ შლის ორგანიზაციას, მის ყველა მომხმარებელს, ცოდნის ბაზასა და ზარების ისტორიას. ამის დაბრუნება შეუძლებელია.',
      'adm.rotate.confirm':'შეიქმნას ახალი API გასაღები ამ ორგანიზაციისთვის? მიმდინარე გასაღები მაშინვე გაითიშება — ყველა ინტეგრაცია, რომელიც მას იყენებს, უნდა განახლდეს.',
      'adm.rotate.done':'ახალი API გასაღები შეიქმნა',
      'adm.rmuser.confirm':'წაიშალოს მომხმარებელი „{u}“? წვდომა მაშინვე გაუუქმდება.',
      'bulk.done.delete':'წაიშალა {n} დოკუმენტი',
      'bulk.done.reembed':'{n} დოკუმენტის საძიებო მონაცემები ახლდება',
      'bulk.done.retag':'ტეგები განახლდა {n} დოკუმენტზე',
      'bulk.done.publish':'{n} დოკუმენტი დაეშვა ბოტისთვის',
      'bulk.done.unpublish':'{n} დოკუმენტს დაშვება მოეხსნა',
      'kb.csvhint':'პირველი რიგი სვეტების სათაურები უნდა იყოს. ორსვეტიანი ფაილი შემოდის კითხვა-პასუხის (ან გასაღები-მნიშვნელობის) წყვილებად; მეტსვეტიანი ფაილიდან თითო რიგი თითო ჩანაწერად შემოდის.',
      'kb.needfile':'ჯერ აირჩიეთ ფაილი.',
      'tts.previewfail':'ნიმუშის დაკვრა ვერ მოხერხდა. სცადეთ ხელახლა.',
      'drop.sub_sn':'ჯერ ტრანსკრიფცია, შემდეგ განწყობის შეფასება — თქვენი ცოდნის ბაზა არ გამოიყენება.',
      'stt.done':'ტრანსკრიფცია მზადაა',
      'sn.done':'განწყობის შეფასება მზადაა',
      'pg.done':'შეფასება მზადაა',
      'cur.bulk.confirm':'მიიღოთ {n} შემოთავაზება? ისინი ცოდნის ბაზაში მაშინვე აისახება.',
      'kba.notenants':'ორგანიზაციები ჯერ არ არის — შექმენით კონსოლში.',
      'nav.workspace':'ჩემი სამუშაო სივრცე',
      'login.asadmin':'თქვენ შესული ხართ როგორც ადმინისტრატორი.',
      'f.role':'როლი',
      'role.member':'წევრი',
      'role.owner':'მფლობელი',
      'adm.user.newpw':'ახალი პაროლი (ცარიელი — უცვლელი)',
      'adm.user.saved':'მომხმარებელი განახლდა',
      'kb.templates':'ნიმუშის ფაილები:',
      'kb.restr':'ფაილი შაბლონს არ მიჰყვება — AI-მ გადააწყოს იმპორტისას',
      'kb.restr.done':'AI-გადაწყობა დასრულდა — დოკუმენტი მზადაა.',
      'sc.import':'ფაილიდან იმპორტი (AI)',
      'sc.import.loading':'შეფასების სტანდარტი იკითხება…',
      'sc.import.loaded':'მონახაზი ჩაიტვირთა ფაილიდან — გადახედეთ კრიტერიუმებსა და წონებს, შემდეგ შეინახეთ.',
      'sc.import.fail':'რუბრიკის იმპორტი ვერ შესრულდა.',
      'sc.import.stage.upload':'ფაილი იტვირთება…',
      'sc.import.stage.queued':'ატვირთულია — სერვერის პასუხს ველოდებით…',
      'sc.import.stage.extracting':'დოკუმენტი იკითხება…',
      'sc.import.stage.analyzing':'AI კრიტერიუმებს ამუშავებს…',
      'sc.import.cancelled':'იმპორტი შეწყდა.',
      'sc.import.netfail':'სერვერთან კავშირი გაწყდა. გთხოვთ სცადოთ თავიდან.',
      'kb.restr.fail':'AI-გადაწყობა ვერ შესრულდა.',
      'kb.files.progress':'იმპორტდება ფაილი {done} / {total}…',
      'kb.files.done':'დაიმპორტდა {n} ფაილი.',
      'kb.files.failed':'ვერ დაიმპორტდა:',
      'kb.restr.hint':'იმპორტისას Claude წაიკითხავს დოკუმენტს და გადააწყობს მას მკაფიო, დამოუკიდებელ ჩანაწერებად — ყველა თანხა, ვადა და რიცხვი ზუსტად ისე რჩება, როგორც წერია. იმპორტს ცოტა მეტი დრო სჭირდება.',
      'tab.convert':'აუდიო კონვერტერი',
      'cv.heading':'გადაიყვანეთ აუდიო თქვენი სატელეფონო სისტემისთვის',
      'cv.files':'გადასაყვანი ფაილები',
      'cv.drop.title':'ჩააგდეთ აუდიო ან ვიდეო ფაილები აქ, ან დააწკაპუნეთ ასარჩევად',
      'cv.drop.sub':'რამდენიმე ფაილი ერთდროულად — აუდიო ან ვიდეო. ინახება მხოლოდ ხმა; ყველა სატელეფონო ფორმატი მონოა.',
      'cv.format':'გამომავალი ფორმატი',
      'cv.run':'კონვერტაცია',
      'cv.download':'ZIP-ის ჩამოტვირთვა',
      'cv.clear':'სიის გასუფთავება',
      'cv.nofiles':'დაამატეთ სულ მცირე ერთი ფაილი.',
      'cv.toomany':'ძალიან ბევრი ფაილია — ერთდროულად {max}.',
      'cv.toobig':'„{name}“ აღემატება {max}-ს.',
      'cv.batchtoobig':'ამ ფაილების ჯამური ზომა აღემატება {max}-ს. წაშალეთ რამდენიმე და დანარჩენი მეორე პარტიად გადაიყვანეთ.',
      'cv.stage.upload':'ფაილები იტვირთება…',
      'cv.stage.queued':'აიტვირთა — სერვერს ველოდებით…',
      'cv.stage.converting':'მიმდინარეობს კონვერტაცია — მზადაა {done} / {total}…',
      'cv.st.queued':'რიგში','cv.st.converting':'მიმდინარეობს','cv.st.done':'მზადაა','cv.st.failed':'ჩაიშალა',
      'cv.done.all':'ყველა {n} ფაილი გადაყვანილია.',
      'cv.done.some':'{total}-იდან გადაყვანილია {ok} — {fail} ჩაიშალა.',
      'cv.done.none':'ვერცერთი ფაილი ვერ გადაკეთდა.',
      'cv.cancelled':'კონვერტაცია გაუქმდა.',
      'cv.fail':'კონვერტაცია ჩაიშალა.',
      'cv.unavailable':'აუდიოს კონვერტაცია ამ სერვერზე ხელმისაწვდომი არ არის.',
      'cv.ttl':'ჩამოტვირთვა ხელმისაწვდომია კიდევ {n} საათის განმავლობაში.',
      'cv.anon':'ანგარიშის გარეშე დღეში {max} ფაილის გადაყვანა შეგიძლიათ — დღეს დარჩა {left}.',
      'cv.anon.more':'უფრო მაღალი ლიმიტებისთვის.',
      'th.size':'ზომა',
      'quota.conversions':'ფაილის კონვერტაცია',
      'cv.f.wav':'WAV 8 kHz (Asterisk wav)',
      'cv.f.wav.d':'იკვრება ყველგან. უსაფრთხო არჩევანი მისალმებებისა და ხმოვანი ფოსტისთვის.',
      'cv.f.wav16':'WAV 16 kHz (Asterisk WAV)',
      'cv.f.wav16.d':'ფართოზოლოვანი — უკეთესი ხმა იქ, სადაც ზარი ამას უჭერს მხარს.',
      'cv.f.alaw':'G.711 A-law (.alaw)',
      'cv.f.alaw.d':'ქართული და ევროპული სტანდარტი — იკვრება გადაკოდირების გარეშე.',
      'cv.f.ulaw':'G.711 μ-law (.ulaw)',
      'cv.f.ulaw.d':'A-law-ის ჩრდილოამერიკული ანალოგი.',
      'cv.f.gsm':'GSM 06.10 (.gsm)',
      'cv.f.gsm.d':'ყველაზე მცირე ზომა, შესამჩნევად დაბალი ხარისხი — გრძელი ჩანაწერებისთვის.',
      'cv.f.g722':'G.722 ფართოზოლოვანი (.g722)',
      'cv.f.g722.d':'HD ხმა. გამოიყენეთ, თუ ზარის მთელი მარშრუტი G.722-ია.',
      'cv.f.sln':'დაუმუშავებელი signed linear 8 kHz (.sln)',
      'cv.f.sln.d':'შეუკუმშავი და უსათაურო — ასე ამუშავებს ხმას Asterisk შიგნით.',
      'cv.f.sln16':'დაუმუშავებელი signed linear 16 kHz (.sln16)',
      'cv.f.sln16.d':'.sln-ის ფართოზოლოვანი ვერსია.',
      /* account.html — the registered-user page (design-v2.md 13.5): gate, history,
         personal rubric, profile. Prefix ac. */
      'ac.gate.heading':'თქვენი CommuniQ ანგარიში',
      'ac.gate.signin':'შესვლა','ac.gate.register':'ანგარიშის შექმნა',
      'ac.gate.tenant':'სამუშაო სივრცის მომხმარებელი ხართ? შედით აქ →',
      'ac.gate.istenant':'სამუშაო სივრცის ანგარიში — იხსნება თქვენი პორტალი…','ac.gate.isadmin':'ოპერატორის ანგარიში — იხსნება კონსოლი…',
      'ac.f.email':'ელფოსტა','ac.f.signinid':'ელფოსტა ან მომხმარებელი','ac.f.name':'საჩვენებელი სახელი','ac.f.name.hint':'არასავალდებულო — სახელი, რომელიც თავსართში გამოჩნდება.',
      'ac.f.curpw':'მიმდინარე პაროლი','ac.f.newpw':'ახალი პაროლი','ac.f.pw2':'გაიმეორეთ ახალი პაროლი',
      'ac.reg.pwhint':'სულ მცირე 8 სიმბოლო.','ac.reg.bademail':'შეიყვანეთ სწორი ელფოსტის მისამართი.',
      'ac.reg.shortpw':'პაროლი უნდა შეიცავდეს სულ მცირე 8 სიმბოლოს.',
      'ac.reg.closed':'ახალი რეგისტრაცია ამჟამად დახურულია.',
      'ac.reg.dup':'ამ ელფოსტით ანგარიში უკვე არსებობს.','ac.reg.done':'ანგარიში შეიქმნა.',
      'ac.disabled':'ეს ანგარიში გამორთულია. ვერაფერს გაუშვებთ, სანამ ოპერატორი ხელახლა არ ჩართავს.',
      'ac.tab.profile':'პროფილი',
      'ac.hist.recordings':'ჩანაწერები','ac.hist.summaries':'შეჯამებები','ac.hist.tts':'გახმოვანებული კლიპები','ac.hist.conversions':'კონვერტაციები',
      'ac.hist.none.rec':'ჯერ არ არის ჩანაწერი.','ac.hist.none.sum':'ჯერ არ არის შეჯამება.',
      'ac.hist.none.tts':'ჯერ არ არის გახმოვანებული კლიპი.','ac.hist.none.conv':'ჯერ არ არის კონვერტაცია.',
      'ac.hist.pasted':'ჩასმული ტრანსკრიპტი','ac.hist.play':'დაკვრა','ac.hist.gone':'აღარ ინახება','ac.hist.expired':'ვადა ამოიწურა',
      'ac.hist.left.d':'დარჩა {n} დღე','ac.hist.left.h':'დარჩა {n} საათი','ac.hist.left.m':'დარჩა {n} წუთი',
      'ac.hist.noanalyse':'ამ ანგარიშისთვის ანალიზი გამორთულია.',
      'ac.src.audio':'ჩანაწერი','ac.src.text':'ტრანსკრიპტი',
      'ac.th.source':'წყარო','ac.th.duration':'ხანგრძლივობა','ac.th.ran':'გაანალიზებულია','ac.th.summary':'შეჯამება',
      'ac.th.calls':'ზარები','ac.th.files':'ფაილები','ac.th.expires':'ვადა',
      'ac.rub.default':'ხედავთ ნაგულისხმევ რუბრიკას. შენახვისას შეიქმნება თქვენი საკუთარი ასლი, რომლის შეცვლაც თავისუფლად შეგიძლიათ.',
      'ac.rub.defaultver':'ნაგულისხმევი რუბრიკა','ac.rub.reset':'ნაგულისხმევზე დაბრუნება',
      'ac.rub.reset.ask':'ეს თქვენს რუბრიკას ჩაანაცვლებს ნაგულისხმევის ასლით. დასადასტურებლად შეიყვანეთ თქვენი პაროლი.',
      'ac.rub.reset.needpw':'რუბრიკის დასაბრუნებლად შეიყვანეთ თქვენი პაროლი.',
      'ac.rub.reset.badpw':'პაროლი არ ემთხვევა.','ac.rub.reset.done':'რუბრიკა დაბრუნდა ნაგულისხმევზე.',
      'ac.pf.heading':'პროფილი','ac.pf.saved':'პროფილი შენახულია.','ac.pf.pw':'პაროლის შეცვლა','ac.pf.pw.change':'პაროლის შეცვლა',
      'ac.pf.pw.needcur':'შეიყვანეთ მიმდინარე პაროლი.','ac.pf.pw.mismatch':'ახალი პაროლები ერთმანეთს არ ემთხვევა.',
      'ac.pf.pw.badcur':'მიმდინარე პაროლი არასწორია.','ac.pf.pw.done':'პაროლი შეიცვალა.',
      'ac.pf.usage':'დღეს','ac.pf.of':'{used} / {max}','ac.pf.nolimit':'ლიმიტის გარეშე',
      'ac.pf.maxupload':'ატვირთვის მაქსიმალური ზომა','ac.pf.features':'ხელმისაწვდომია ამ ანგარიშზე',
      'ac.cv.expires':'ჩამოტვირთვა ხელმისაწვდომია — {when}.',
      /* tenant.html (tenant portal v2: Analyse / Rubric / History) — owned by the tenant page. */
      'tn.hist.rec':'ჩანაწერები','tn.hist.sum':'შეჯამებები',
      'tn.hist.rec.none':'ჯერ არაფერია — ატვირთეთ ზარი ან ჩასვით ტრანსკრიპტი ანალიზის ჩანართში.',
      'tn.hist.sum.none':'შეჯამებები ჯერ არ არის.','tn.hist.open':'გახსნა ანალიზის ჩანართში',
      'tn.th.source':'წყარო','tn.th.length':'ხანგრძლივობა','tn.th.ran':'გაანალიზებულია','tn.th.calls':'ზარები','tn.th.summary':'შეჯამება',
      'tn.src.audio':'აუდიო','tn.src.text':'ტექსტი',
      'tn.sc.reset':'ნაგულისხმევზე დაბრუნება','tn.sc.reset.heading':'დაბრუნდეს რუბრიკა ნაგულისხმევზე?',
      'tn.sc.reset.warn':'რუბრიკა ჩანაცვლდება საერთო ნაგულისხმევის ასლით და შეინახება როგორც ახალი ვერსია — წინა ვერსიები ისტორიაში რჩება. დასადასტურებლად შეიყვანეთ თქვენი პაროლი.',
      'tn.sc.reset.pw':'თქვენი პაროლი','tn.sc.reset.needpw':'შეიყვანეთ თქვენი პაროლი.',
      'tn.sc.reset.bad':'პაროლი არ ემთხვევა.','tn.sc.reset.done':'რუბრიკა დაბრუნდა ნაგულისხმევზე.',
      'tn.sc.isdefault':'ხედავთ საერთო ნაგულისხმევ რუბრიკას — ამ სამუშაო სივრცეს ჯერ საკუთარი არ აქვს. შენახვისას შეიქმნება თქვენი ასლი, რომელსაც ნაგულისხმევის შემდგომი ცვლილებები აღარ შეეხება.',
      /* ---- public page + admin console (pb.*) ---- */
      'pb.nav.create':'ანგარიშის შექმნა',
      'pb.nav.account':'ჩემი ანგარიში',
      'pb.users':'მომხმარებლები',
      'pb.storage':'შენახვა',
      'pb.defrubric':'ნაგულისხმევი რუბრიკა',
      'pb.reg.heading':'რეგისტრირებული ანგარიშები — დღიური ლიმიტები',
      'pb.reg.desc':'ვრცელდება ყველა თვითრეგისტრირებულ ანგარიშზე, რომელსაც საკუთარი გამონაკლისი არ აქვს.',
      'pb.reg.signups':'რეგისტრაცია ღიაა',
      'pb.reg.signups.hint':'გამორთვა ხურავს საჯარო რეგისტრაციის ფორმას. არსებული ანგარიშები აგრძელებენ მუშაობას — კონკრეტული ანგარიში ქვემოთ, ცხრილში გამორთეთ.',
      'pb.reg.maxconv':'მაქს. კონვერტაცია / დღეში',
      'pb.feat.convert':'აუდიოს კონვერტაცია',
      'pb.feat.summarise':'შეჯამება',
      'pb.feat.score':'შეფასება',
      'pb.feat.semantic':'სენტიმენტის ანალიზი',
      'pb.users.heading':'ანგარიშები',
      'pb.users.search':'ძებნა ელფოსტით ან სახელით',
      'pb.users.none':'რეგისტრირებული ანგარიშები ჯერ არ არის.',
      'pb.users.nomatch':'ამ ძებნას ანგარიში არ ემთხვევა.',
      'pb.users.legend':'„დღეს“ ითვლის ანალიზებს · TTS ჩანაწერებს · კონვერტაციებს შუაღამიდან.',
      'pb.th.email':'ელფოსტა',
      'pb.th.name':'სახელი',
      'pb.th.created':'შექმნის თარიღი',
      'pb.th.lastlogin':'ბოლო შესვლა',
      'pb.th.today':'დღეს',
      'pb.never':'არასდროს',
      'pb.act.activate':'ჩართვა',
      'pb.act.deactivate':'გამორთვა',
      'pb.act.limits':'ლიმიტები',
      'pb.act.resetpw':'პაროლის განულება',
      'pb.user.saved':'ანგარიში განახლდა',
      'pb.lim.title':'ანგარიშის ინდივიდუალური ლიმიტები',
      'pb.lim.note':'ცარიელი ველი ნიშნავს, რომ ანგარიში საერთო ტარიფის რიცხვს იყენებს. შენახვა ჩაანაცვლებს ამ ანგარიშის ყველა გამონაკლისს.',
      'pb.lim.saved':'ლიმიტები შენახულია',
      'pb.pw.title':'ახალი პაროლი',
      'pb.pw.once':'ჩანს მხოლოდ ერთხელ. ინახება მხოლოდ მისი ჰეში, ამიტომ ხელახლა ვერ გამოჩნდება — გადაეცით მომხმარებელს ახლავე.',
      'pb.pw.confirm':'შევქმნათ ახალი პაროლი {email}-სთვის? მისი მიმდინარე პაროლი მაშინვე შეწყვეტს მუშაობას.',
      'pb.copy':'კოპირება',
      'pb.copied':'დაკოპირდა',
      'pb.copyfail':'კოპირება ვერ მოხერხდა — მონიშნეთ პაროლი და ხელით დააკოპირეთ.',
      'pb.close':'დახურვა',
      'pb.del.confirm':'წავშალოთ ანგარიში {email}? მისი ჩანაწერები, შეჯამებები და TTS ფაილები არ წაიშლება — ისინი დარჩება, სანამ შენახვის ვადა არ წაშლის ფაილებს.',
      'pb.del.done':'ანგარიში წაიშალა. მისი ჩანაწერები დარჩება, სანამ შენახვის ვადა არ წაშლის ფაილებს.',
      'pb.storage.heading':'შენახული ჩანაწერები',
      'pb.storage.desc':'ერთი შენახვის ვადა ყველა შენახული ჩანაწერისა და TTS ფაილისთვის — ანონიმური ვიზიტორების, ტენანტებისა და რეგისტრირებული ანგარიშებისთვის ერთნაირად.',
      'pb.storage.days':'ჩანაწერების შენახვა (დღე)',
      'pb.storage.hint':'0 ნიშნავს უვადოდ შენახვას. ანონიმური ვიზიტორის ჩანაწერი მთლიანად იშლება; ტენანტისა და ანგარიშის ჩანაწერს რჩება ტრანსკრიპტი და შედეგები, კარგავს მხოლოდ აუდიოფაილს.',
      'pb.storage.moved':'შენახვის ვადა გადავიდა ჩანართში „შენახვა“: ერთი რიცხვი ახლა ყველა შენახულ ჩანაწერზე ვრცელდება, არა მხოლოდ ანონიმურზე.',
      'pb.defrubric.heading':'ნაგულისხმევი რუბრიკა',
      'pb.defrubric.desc':'ამით ფასდება ყველა ტენანტი და რეგისტრირებული ანგარიში, რომელსაც საკუთარი რუბრიკა არ შეუნახავს.',
      'pb.src.stored':'ოპერატორის მიერ შენახული',
      'pb.src.demo':'აღებულია demo ტენანტიდან — ჯერ არ არის შენახული',
      'pb.src.builtin':'ჩაშენებული საწყისი — ჯერ არ არის შენახული',
      'pb.defrubric.updated':'განახლდა {when}, ავტორი: {who}',
      'pb.defrubric.saved':'ნაგულისხმევი რუბრიკა შენახულია',
    },
    ru: {
      'nav.public':'Публичное приложение','nav.signin':'Войти','nav.logout':'Выйти','nav.kb':'База знаний',
      'f.username':'Имя пользователя','f.password':'Пароль','f.language':'Язык','f.voice':'Голос','f.text':'Текст',
      'f.category':'Категория','f.title':'Заголовок','f.tags':'Теги (через запятую)','f.name':'Название','f.industry':'Отрасль','f.region':'Регион',
      'f.audiofile':'Аудио- или видеофайл','f.provider':'Провайдер','f.dimension':'Размерность','f.model':'Модель','f.baseurl':'Базовый URL',
      'f.anthropic':'API-ключ Anthropic (Claude)','f.eleven':'API-ключ ElevenLabs','f.claudemodel':'Модель Claude','f.sttmodel':'Модель Scribe (STT)','f.ttsmodel':'Модель TTS','f.voiceid':'ID голоса TTS','f.openaikey':'API-ключ (только openai)',
      'btn.signin':'Войти','btn.save':'Сохранить','btn.savesettings':'Сохранить настройки','btn.savelimits':'Сохранить лимиты','btn.refresh':'Обновить','btn.delete':'Удалить','btn.cancel':'Отмена','btn.test':'Тест','btn.testconn':'Проверить подключения','btn.testdeep':'Полная проверка (глубокая)',
      'cap.database':'База данных','cap.ffmpeg':'Перекодирование аудио (ffmpeg)','cap.voices':'ElevenLabs · список голосов','cap.stt':'ElevenLabs · речь в текст','cap.tts':'ElevenLabs · текст в речь','cap.ttska':'ElevenLabs · грузинский TTS','cap.embeddings':'Эмбеддинги','cap.claude':'Claude · анализ','cap.factcheck':'Claude · проверка фактов','cap.scoring':'Claude · оценка',
      'cap.fixscope':'Как исправить: в ElevenLabs откройте Settings → API Keys → Edit для этого ключа, включите разрешение «{scope}», сохраните и повторите проверку.',
      'adm.testnote':'Каждая возможность проверяется по-настоящему — тест расходует доли секунды распознавания речи и несколько символов синтеза, поскольку ElevenLabs не позволяет прочитать разрешения ключа. «Глубокая» проверка дополнительно задействует инструменты проверки фактов и оценки.','btn.search':'Поиск','btn.import':'Импорт','btn.analyze':'Анализ','btn.synth':'Сгенерировать речь','btn.create':'Создать организацию','btn.adduser':'Добавить пользователя','btn.rotate':'Перевыпустить','btn.remove':'Удалить','btn.apikey':'API-ключ','btn.users':'Пользователи','btn.chunks':'Фрагменты',
      'hero.eyebrow':'Голосовой ИИ CommuniQ','hero.title':'Озвучивайте текст и понимайте каждый звонок.',
      'tab.tts':'Текст в речь','tab.analyze':'Анализ звонка','tab.kb':'База знаний','tab.history':'История','tab.scoring':'Рубрика',
      'tts.heading':'Генерация речи из текста','tts.text_ph':'Введите текст… (английский, русский или грузинский)',
      'an.heading':'Загрузите запись для анализа','an.heading_kb':'Анализ звонка — использует вашу базу знаний',
      'drop.title':'Перетащите аудио- или видеофайл сюда или нажмите для выбора','drop.sub':'Любой аудио- или видеофайл — расшифровка ElevenLabs Scribe, анализ Claude','drop.sub_kb':'Сначала расшифровка, затем анализ по вашей базе знаний',
      'rec.or':'или','rec.record':'Записать','rec.stop':'Стоп','rec.recording':'Идёт запись','rec.ready':'Записано — готово к анализу','rec.unsupported':'Для записи нужен HTTPS или localhost','rec.denied':'Доступ к микрофону запрещён',
      'res.analysis':'Анализ','res.language':'Язык','res.sentiment':'Тональность','res.topics':'Темы','res.time':'Время','res.quality':'Качество','res.summary':'Резюме','res.keypoints':'Ключевые моменты','res.actions':'Действия','res.transcript':'Расшифровка','res.kbused':'Использованная база знаний','res.nokb':'Совпадений в базе знаний не найдено.','res.empty':'(пусто)','res.done':'Анализ завершён',
      'login.heading':'Войти',
      'kb.import':'Импорт знаний','imp.file':'Загрузить файл','imp.paste':'Вставить текст','imp.csv':'CSV (вопрос-ответ)','kb.filelabel':'Файлы (PDF / DOCX / XLSX / CSV / TXT / MD — можно несколько сразу)','kb.csvlabel':'CSV-файл (первая строка = заголовок)','kb.searchlabel':'Поиск по базе знаний','kb.search_ph':'Задайте вопрос…','kb.documents':'Документы','kb.none':'Документов пока нет. Импортируйте знания выше.','kb.processing':'обработка…','kb.nomatch':'Совпадений нет.',
      'th.title':'Заголовок','th.category':'Категория','th.status':'Статус','th.chunks':'Фрагменты','th.file':'Файл','th.lang':'Язык','th.when':'Когда','th.name':'Название','th.slug':'Идент.','th.industry':'Отрасль','th.active':'Активен','th.users':'Польз.','th.docs':'Док.',
      'hist.heading':'Недавние анализы','hist.none':'Анализов пока нет.',
      'adm.tenants':'Организации','adm.embeddings':'Эмбеддинги','adm.anon':'Лимиты для анонимных пользователей','adm.integrations':'Интеграции',
      'adm.createtenant':'Создать организацию','adm.embprov':'Провайдер эмбеддингов','adm.embnote':'Изменение размерности требует переэмбеддинга базы знаний (документы нужно импортировать заново).',
      'adm.anonheading':'Лимиты анонимных пользователей','adm.allowanon':'Разрешить анонимных пользователей','adm.maxanalyses':'Макс. анализов / день','adm.maxmb':'Макс. аудио МБ','adm.maxtts':'Макс. TTS / день','adm.features':'Разрешённые функции','feat.analyze':'Анализ','feat.tts':'Текст в речь',
      'adm.intkeys':'Ключи интеграций','adm.models':'Модели и голос','adm.instructions':'Инструкции анализа',
      'toast.saved':'Настройки сохранены','toast.imported':'Импорт начат','toast.deleted':'Удалено','toast.created':'Создано','toast.welcome':'Добро пожаловать','toast.error':'Что-то пошло не так',
      'tip.label':'Дополнительная информация',
      'err.toolarge':'Файл слишком большой для загрузки. Используйте более короткую запись или файл меньшего размера.','err.timeout':'Анализ занял слишком много времени и превысил лимит. Попробуйте более короткую запись.','err.unavailable':'Сервис временно недоступен. Пожалуйста, попробуйте через минуту.','err.http':'Что-то пошло не так на нашей стороне. Попробуйте ещё раз через минуту — если повторится, обратитесь в поддержку. (Код {status})','err.badresp':'Сервер вернул неожиданный ответ. Пожалуйста, попробуйте снова.',
      'quota.using':'Вы используете CommuniQ анонимно —','quota.analyses':'расшифровок','quota.clips':'аудиоклипов','quota.left':'осталось сегодня.','quota.more':'для базы знаний и более высоких лимитов.','quota.disabled':'Анонимный доступ отключён.',
      'fc.title':'Проверка по базе знаний','fc.accuracy':'точность','fc.supported':'подтверждено','fc.contradicted':'опровергнуто','fc.notinkb':'нет в базе','fc.partial':'частично верно','fc.misinfo':'Возможно, недостоверная информация','fc.nochecked':'Проверяемых утверждений не найдено.',
      'adm.voices':'Голоса','adm.voicevis':'Голоса, видимые клиентам','f.restrictvoices':'Показывать клиентам только отмеченные голоса','f.defaultvoice':'Голос по умолчанию','v.hint':'Неотмеченные голоса скрыты из списка для клиентов и отклоняются TTS. Оставьте флажок снятым, чтобы показывать все голоса. Системные (включая грузинский) всегда включены.','v.search':'Поиск голосов…','v.selected':'выбрано','v.system':'Системный','v.nopreview':'Нет образца','v.unavailable':'Нет в этом аккаунте ElevenLabs','v.pickone':'Выберите хотя бы один голос или снимите ограничение.','v.loadfail':'Не удалось загрузить голоса из ElevenLabs. Проверьте API-ключ в «Интеграциях».','msg.voicegone':'Этот голос больше недоступен. Список обновлён.',
      'fc.allclaims':'Все утверждения','pg.tab.retrieval':'Тест поиска','pg.tab.score':'Оценка ответа','pg.tab.sentiment':'Тональность','pg.ans.label':'Ответ оператора (на любом языке)','pg.ans.ph':'Вставьте или напишите ответ оператора — он будет оценён по рубрике этой организации…','pg.ans.run':'Оценить ответ','pg.ans.norubric':'Нет активной рубрики — сначала задайте её во вкладке «Оценка».','pg.ans.empty':'Введите ответ для оценки.','pg.ans.usingv':'версия рубрики',
      'tab.playground':'Оценка звонка','pg.heading':'Оцените звонок или ответ','pg.mode.text':'Текст','pg.mode.audio':'Аудио / Видео','pg.mode.sentiment':'Тональность','pg.audiolabel':'Запись звонка (аудио или видео)','pg.run':'Оценить','pg.audioempty':'Выберите аудио- или видеофайл.',
      'kba.title':'Управление базой знаний','kba.tenant':'Организация','kba.selecttenant':'Выберите организацию, чтобы управлять её базой знаний.',
      'kba.tab.overview':'Обзор','kba.tab.documents':'Документы','kba.tab.import':'Импорт','kba.tab.playground':'Песочница','kba.tab.duplicates':'Дубликаты','kba.tab.activity':'Активность',
      'kba.stat.documents':'Документы','kba.stat.chunks':'Фрагменты','kba.stat.coverage':'Покрытие эмбеддингами','kba.stat.failed':'Ошибки импорта','kba.stat.tokens':'Прибл. токены','kba.stat.lastupd':'Обновлено','kba.stat.inprogress':'В процессе',
      'kba.params':'Активная конфигурация','kba.export':'Экспорт','kba.exportcsv':'Экспорт CSV','kba.reembedall':'Перестроить поисковый индекс','kba.refresh':'Обновить',
      'kba.f.status':'Статус','kba.f.type':'Тип','kba.f.tag':'Тег','kba.f.search':'Поиск по заголовку/тексту','kba.f.all':'Все',
      'kba.selected':'выбрано','kba.bulk.delete':'Удалить','kba.bulk.reembed':'Перестроить поиск','kba.bulk.retag':'Изменить теги','kba.selectall':'Выбрать все',
      'kba.edit':'Редактировать','kba.chunks':'Фрагменты','kba.reembed':'Переэмбеддинг','kba.delete':'Удалить','kba.save':'Сохранить','kba.nodocs':'Нет документов. Импортируйте ниже.',
      'kba.doc.title':'Заголовок','kba.doc.type':'Категория','kba.doc.tags':'Теги','kba.doc.meta':'Метаданные (JSON, необязательно)','kba.doc.content':'Текст (при редактировании документ заново разбивается и переэмбеддится)',
      'kba.pg.query':'Запрос (на любом языке)','kba.pg.topk':'Количество результатов','kba.pg.threshold':'Минимальный балл совпадения (0–1)','kba.pg.run':'Выполнить поиск','kba.pg.method':'метод','kba.pg.nohits':'Фрагменты не найдены.',
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
      'sc.title':'Оценка по рубрике','sc.weighted':'с учётом весов','sc.weight':'вес','sc.contribution':'вклад',
      'sc.heading':'Рубрика оценки',
      'sc.rubric':'Общая рубрика / указания','sc.rubric.ph':'Необязательные общие указания для оценщика (тон, что важнее всего, насколько строго)…',
      'sc.adddim':'+ Добавить критерий','sc.dname':'Название критерия','sc.dname.ph':'напр. Приветствие и идентификация',
      'sc.ddesc':'Краткое описание','sc.dweight':'Вес','sc.dguide':'Указания по оценке',
      'sc.dguide.ph':'Как оценивать этот критерий: что даёт высокий или низкий балл…',
      'sc.remove':'Удалить','sc.save':'Сохранить рубрику','sc.saved':'Рубрика сохранена','sc.sum':'Общий вес',
      'sc.nodims':'Пока нет критериев — добавьте один, чтобы начать.','sc.version':'Версия','sc.none':'У этой организации ещё нет активной рубрики.',
      'sc.needname':'У каждого критерия должно быть название.','sc.needone':'Добавьте хотя бы один критерий.',
      'sc.normalize':'Привести к 100%','sc.mustbe100':'Сумма весов должна быть 100% (сейчас {total}%).',
      'cur.tab':'На проверку','cur.heading':'Пробелы в базе знаний',
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
      'cur.remove.heading':'Скрыть этот текст?','cur.remove.word':'СКРЫТЬ','cur.remove.confirm':'Введите {word} для подтверждения.',
      'cur.remove.mismatch':'Не совпадает — ничего не изменено.',
      'cur.remove.note':'Текст будет скрыт из ответов. Ничего не удаляется; оператор может удалить вручную.',
      'cur.run':'Запустить курацию','cur.run.started':'Курация поставлена в очередь',
      'cur.st.pending':'Ожидает','cur.st.accepted':'Принято','cur.st.declined':'Отклонено','cur.st.superseded':'Заменено','cur.st.apply_failed':'Ошибка применения',
      'cur.filter.state':'Статус','cur.openjob':'Открыть звонок','cur.opensource':'Открыть диалог',
      'cur.foreign':'⚠ ссылается на данные другой организации',
      'tab.bot':'Бот','bot.heading':'Публичный бот',
      'bot.autopilot':'Автопилот — бот отвечает клиентам без участия человека',
      'bot.autopilot.hint':'При выключенном автопилоте бот только готовит черновики ответов, которые оператор проверяет и отправляет. Клиенту не уходит ни одного непрочитанного сообщения.',
      'bot.state.live':'Отвечает клиентам','bot.state.off':'Только черновики — ответ отправляет человек','bot.state.killed':'Остановлен CommuniQ',
      'bot.killed.note':'Поддержка CommuniQ остановила автопилот. Настройки сохранены; до возобновления бот передаёт все диалоги человеку.',
      'bot.needpublic.title':'Автопилоту нужен хотя бы один документ, доступный боту',
      'bot.needpublic.body':'Документы базы знаний по умолчанию внутренние, и бот может цитировать только документы, которые вы ему открыли. Если не открыт ни один, бот отказывает на каждый вопрос. Откройте боту документы, которые вашим клиентам разрешено читать, затем включите автопилот. В интернете ничего не публикуется.',
      'bot.needpublic.link':'Открыть базу знаний',
      'bot.persona':'Персона','bot.persona.ph':'Вы — ассистент поддержки … Отвечайте кратко, доброжелательно и конкретно.',
      'bot.greeting':'Приветствие','bot.refusal':'Текст отказа — что бот говорит, когда в базе знаний нет ответа',
      'bot.refusal.hint':'Эту фразу клиент видит чаще всего. Напишите её на всех языках, на которых отвечает бот; она должна предлагать человека, а не извиняться дважды.',
      'bot.refusal.missing':'Напишите текст отказа на языке «{lang}» перед включением автопилота.',
      'bot.lang.en':'Английский','bot.lang.ka':'Грузинский','bot.lang.ru':'Русский',
      'bot.languages':'Языки, на которых отвечает бот','bot.languages.pickone':'Выберите хотя бы один язык.',
      'bot.escalation':'Ключевые слова эскалации','bot.escalation.ph':'юрист, жалоба, возврат платежа',
      'bot.escalation.hint':'Через запятую. Совпадение сразу передаёт диалог человеку, до генерации любого ответа.',
      'bot.retrieval':'Поиск и лимиты ответа','bot.minscore':'Минимальный балл совпадения до ответа (0–1)','bot.minhits':'Минимум совпавших фрагментов до ответа','bot.topk':'Фрагментов на вопрос','bot.suggestions':'Подсказок на реплику (режим черновиков)','bot.maxchars':'Макс. символов в ответе',
      'bot.caps':'Лимиты частоты','bot.cap.tenant':'Реплик / мин (всё рабочее пространство)','bot.cap.enduser':'Реплик / час (один клиент)',
      'bot.general':'Отвечать из общих знаний, когда в базе знаний ничего нет',
      'bot.general.risk':'Рискованный выбор, по умолчанию выключен. Выключено — бот отказывает вашим текстом выше и предлагает человека; он способен повторить только то, что вы опубликовали. Включено — он может ответить из собственных знаний модели: это не ваша политика, это не проверяемо и он может уверенно ошибиться в ваших ценах, правилах и сроках.',
      'bot.general.confirm':'Разрешить боту отвечать из общих знаний модели? Тогда он будет говорить то, чего нет в вашей базе знаний и что никто в вашей компании не утверждал.',
      'bot.general.on':'Включить',
      'bot.handoff':'Писать короткое резюме для человека, который перехватывает диалог','bot.handoff.hint':'Один дополнительный вызов модели, только при передаче. Выключено — оператор открывает диалог с нуля.',
      'bot.save':'Сохранить настройки бота','bot.saved':'Настройки бота сохранены','bot.version':'Версия',
      'bot.loadfail':'Не удалось загрузить настройки бота.','bot.unavailable':'Настройки бота пока недоступны на этом сервере.',
      'bot.soon.title':'Скоро','bot.soon.desc':'Клиентский бот ещё готовится. Как только он заработает, его настройки появятся на этой странице.',
      'bot.soon.desc.admin':'Клиентский бот ещё не запущен — организации сейчас видят страницу «Скоро». Управление ниже заработает после запуска бота.',
      'adm.bot':'Управление ботом','kill.heading':'Аварийное отключение автопилота',
      'kill.desc':'Тормоз. Останавливает ответы публичного бота; диалоги передаются людям. Настройки клиентов не меняются, поэтому возобновление — один клик.',
      'kill.global':'Остановить автопилот для всех организаций','kill.global.on':'Остановлен везде','kill.global.off':'Работает нормально',
      'kill.tenants':'По организациям','kill.stop':'Остановить','kill.resume':'Возобновить',
      'kill.confirm.global':'Остановить автопилот для всех организаций? Все боты будут передавать диалоги людям до возобновления.',
      'kill.confirm.resume.global':'Возобновить автопилот для всех организаций, у которых он включён?',
      'kill.confirm.tenant':'Остановить автопилот для «{name}»?','kill.confirm.resume':'Возобновить автопилот для «{name}»?',
      'kill.state.live':'Активен','kill.state.stopped':'Остановлен','kill.state.off':'Автопилот выключен',
      'kill.saved':'Аварийный выключатель обновлён','kill.loadfail':'Не удалось прочитать состояние выключателя.',
      'kill.unavailable':'Аварийный выключатель ещё не развёрнут на этом сервере.','kill.overviewfail':'Не удалось прочитать состояние автопилота по клиентам.',
      'th.autopilot':'Автопилот',
      'vis.col':'Видимость','vis.all':'Все','vis.public':'Доступен боту','vis.internal':'Внутренний',
      'vis.publish':'Открыть боту','vis.unpublish':'Закрыть от бота',
      'vis.bulk.publish':'Открыть боту выбранные','vis.bulk.unpublish':'Закрыть от бота выбранные',
      'vis.stat.public':'Доступны боту',
      'vis.confirm.publish':'Открыть боту {n} документ(ов)? Бот может дословно цитировать доступные документы вашим клиентам. В интернете ничего не публикуется — данные остаются в вашем рабочем пространстве.',
      'vis.confirm.unpublish':'Закрыть от бота {n} документ(ов)? Бот перестанет их использовать.',
      'vis.confirm.publish.one':'Открыть боту «{title}»? Бот может дословно цитировать его вашим клиентам. В интернете ничего не публикуется.',
      'vis.confirm.unpublish.one':'Закрыть от бота «{title}»? Бот перестанет его использовать.',
      'vis.done.publish':'Доступен боту','vis.done.unpublish':'Закрыт от бота',
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
      'tkb.badjson':'Метаданные должны быть корректным JSON.','tkb.reembed.done':'Поисковые данные обновлены для {n} фрагментов',
      'tkb.chunks.pick':'Документ','tkb.chunks.none':'У этого документа пока нет фрагментов.',
      'tkb.chunks.pickone':'Выберите документ, чтобы увидеть его фрагменты.',
      'tkb.chunks.hint':'Поиск сопоставляет фрагменты, а не документы. Правка фрагмента сразу его переэмбеддит; удаление убирает его из всех ответов.',
      'tkb.chunk.noembed':'нет эмбеддинга',
      'tkb.chunk.del.confirm':'Удалить этот фрагмент? Он сразу исчезнет из всех ответов.',
      'tkb.chunk.edit.hint':'Сохранение сразу переэмбеддит этот фрагмент. Остальной документ не затрагивается.',
      'tkb.pg.heading':'Проверьте, что находит поиск',
      'tkb.dup.identical':'документ(ов) с одинаковым содержимым','tkb.dup.keep':'оставляем',
      'tkb.dup.skipped':'Поиск похожих дубликатов пропущен — в этой базе знаний слишком много фрагментов, чтобы сравнить все пары.',
      'tkb.act.filter':'Действие','tkb.act.filter.ph':'импорт, правка, удаление, переэмбеддинг…',
      'tkb.act.method':'Метод','tkb.act.detail':'Детали','tkb.act.actor':'Кто',
      'tkb.exp.hint':'Скачивает все документы этой базы знаний, включая внутренние. Сам экспорт записывается в журнал активности.',
      'tkb.reembed.heading':'Перестроить поисковый индекс',
      'tkb.reembed.desc':'Заново строит поисковые данные для каждого фрагмента — нужно после смены модели эмбеддингов или её размерности. Выполняется в фоне с ограниченной скоростью, поэтому на большой базе может занять время; поиск всё это время работает. Одновременно выполняется только одно перестроение.',
      'tkb.reembed.start':'Запустить перестроение',
      'tkb.reembed.confirm':'Поставить в очередь полное перестроение поискового индекса? Выполнится в фоне и может занять время. До завершения новое запустить нельзя.',
      'tkb.reembed.queued':'Перестроение поискового индекса поставлено в очередь','tkb.reembed.busy':'Перестроение уже в очереди или выполняется.',
      'tkb.reembed.none':'Перестроение ещё не запускалось.',
      'tkb.reembed.progress':'{done} из {total} документов','tkb.reembed.failed':'с ошибкой: {n}',
      'tkb.reembed.state.queued':'В очереди','tkb.reembed.state.running':'Выполняется','tkb.reembed.state.done':'Завершён',
      'tkb.reembed.state.error':'Ошибка','tkb.reembed.state.cancelled':'Отменён',
      'sc.readonly':'Только просмотр — редактировать рубрику оценки может только владелец рабочего пространства.',
      'adm.retention':'Хранить анонимные данные (дней)','adm.retention.hint':'Сколько хранятся IP, аудио и текст незарегистрированного посетителя до удаления. 0 — бессрочно.',
      'adm.sentiment.heading':'Публичный анализ тональности',
      'tab.stt':'Речь в текст','btn.transcribe':'Расшифровать',
      'stt.heading':'Превратите запись в текст','stt.nofile':'Сначала выберите аудиофайл.',
      'drop.sub_stt':'Любой аудио- или видеофайл — расшифровка через ElevenLabs Scribe',
      'sn.title':'Тональность','sn.text':'Что было сказано','sn.voice':'Как это прозвучало','sn.arousal':'Энергия','sn.valence':'Позитивность','sn.unavailable':'Недоступно для этой записи.','sn.conflict':'Слова и тон голоса расходятся — стоит послушать.',
      'tab.sentiment':'Тональность','sn.heading':'Как звучал говорящий?','sn.run':'Анализировать тональность','sn.none':'Для этой записи не удалось определить тональность.','sn.config':'Настройки тональности','sn.enabled':'Включить анализ тональности','sn.guidance':'Указания для текстового анализатора (необязательно)','sn.guidance.ph':'напр. Любую жалобу считать как минимум слегка негативной, даже если она вежливо сформулирована…','sn.save':'Сохранить настройки тональности','sn.saved':'Сохранено','sn.readonly':'Только просмотр — настройки тональности может редактировать только владелец рабочего пространства.','sn.audiolabel':'Аудио для анализа','sn.mode':'Тональность','sn.disabled':'Анализ тональности отключён для этого рабочего пространства.',
      'lang.en':'Английский','lang.ru':'Русский','lang.ka':'Грузинский',
      'lang.note.ka':'Для грузинского используется модель eleven_v3 с голосом, поддерживающим грузинский, — для правильного произношения. Для лучшего результата оставьте голос по умолчанию.',
      'tts.needtext':'Введите текст.','tts.pickvoice':'Выберите конкретный голос для прослушивания.','tts.previewtitle':'Прослушать голос (бесплатный образец)',
      'session.expired':'Сессия истекла — войдите снова.',
      'login.failed':'Не удалось войти. Проверьте имя пользователя и пароль.',
      'login.empty':'Введите имя пользователя и пароль.',
      'login.checking':'Восстановление сессии…',
      'login.showpw':'Показать пароль',
      'nav.console':'Консоль',
      'btn.retry':'Повторить',
      'adm.deltenant.confirm':'Удалить «{name}»? Это навсегда удалит организацию, всех её пользователей, базу знаний и историю звонков. Отменить это нельзя.',
      'adm.rotate.confirm':'Выпустить новый API-ключ для этой организации? Текущий ключ сразу перестанет работать — все интеграции, использующие его, нужно обновить.',
      'adm.rotate.done':'Новый API-ключ выпущен',
      'adm.rmuser.confirm':'Удалить пользователя «{u}»? Доступ будет закрыт сразу.',
      'bulk.done.delete':'Удалено документов: {n}',
      'bulk.done.reembed':'Перестраивается поиск для {n} документов',
      'bulk.done.retag':'Теги обновлены у {n} документов',
      'bulk.done.publish':'Открыто боту документов: {n}',
      'bulk.done.unpublish':'Закрыто от бота документов: {n}',
      'kb.csvhint':'Первая строка — заголовки столбцов. Файл с двумя столбцами импортируется как пары вопрос-ответ (или ключ-значение); файлы с большим числом столбцов — по одной записи на строку.',
      'kb.needfile':'Сначала выберите файл.',
      'tts.previewfail':'Не удалось воспроизвести образец. Попробуйте ещё раз.',
      'drop.sub_sn':'Сначала расшифровка, затем оценка тональности — ваша база знаний не используется.',
      'stt.done':'Расшифровка готова',
      'sn.done':'Оценка тональности готова',
      'pg.done':'Оценка готова',
      'cur.bulk.confirm':'Принять предложений: {n}? Они сразу применяются к базе знаний.',
      'kba.notenants':'Организаций пока нет — создайте в Консоли.',
      'nav.workspace':'Моё пространство',
      'login.asadmin':'Вы вошли как администратор.',
      'f.role':'Роль',
      'role.member':'Участник',
      'role.owner':'Владелец',
      'adm.user.newpw':'Новый пароль (пусто — не менять)',
      'adm.user.saved':'Пользователь обновлён',
      'kb.templates':'Файлы-образцы:',
      'kb.restr':'Файл не соответствует шаблону — переструктурировать с помощью ИИ',
      'kb.restr.done':'ИИ-переструктурирование завершено — документ готов.',
      'sc.import':'Импорт из файла (ИИ)',
      'sc.import.loading':'Читаем стандарт оценки…',
      'sc.import.loaded':'Черновик загружен из файла — проверьте критерии и веса, затем сохраните.',
      'sc.import.fail':'Не удалось импортировать рубрику.',
      'sc.import.stage.upload':'Загрузка файла…',
      'sc.import.stage.queued':'Файл загружен — ждём ответ сервера…',
      'sc.import.stage.extracting':'Читаем документ…',
      'sc.import.stage.analyzing':'ИИ разбирает критерии…',
      'sc.import.cancelled':'Импорт отменён.',
      'sc.import.netfail':'Соединение с сервером потеряно. Пожалуйста, попробуйте снова.',
      'kb.restr.fail':'Не удалось переструктурировать документ с помощью ИИ.',
      'kb.files.progress':'Импорт файла {done} из {total}…',
      'kb.files.done':'Импортировано файлов: {n}.',
      'kb.files.failed':'Не удалось:',
      'kb.restr.hint':'При импорте Claude читает документ и переписывает его в виде отдельных, понятных записей — все суммы, сроки и числа сохраняются ровно как написано. Импорт занимает немного больше времени.',
      'tab.convert':'Конвертер аудио',
      'cv.heading':'Конвертация аудио для телефонной системы',
      'cv.files':'Файлы для конвертации',
      'cv.drop.title':'Перетащите аудио- или видеофайлы сюда или нажмите для выбора',
      'cv.drop.sub':'Сразу несколько файлов — аудио или видео. Сохраняется только звук; все телефонные форматы моно.',
      'cv.format':'Выходной формат',
      'cv.run':'Конвертировать',
      'cv.download':'Скачать ZIP',
      'cv.clear':'Очистить список',
      'cv.nofiles':'Добавьте хотя бы один файл.',
      'cv.toomany':'Слишком много файлов — не больше {max} за раз.',
      'cv.toobig':'«{name}» больше, чем {max}.',
      'cv.batchtoobig':'Суммарный размер этих файлов больше {max}. Удалите несколько, а остальные конвертируйте второй партией.',
      'cv.stage.upload':'Загрузка файлов…',
      'cv.stage.queued':'Загружено — ждём сервер…',
      'cv.stage.converting':'Конвертация — готово {done} из {total}…',
      'cv.st.queued':'В очереди','cv.st.converting':'Конвертация','cv.st.done':'Готово','cv.st.failed':'Ошибка',
      'cv.done.all':'Сконвертированы все файлы: {n}.',
      'cv.done.some':'Сконвертировано {ok} из {total} — с ошибкой: {fail}.',
      'cv.done.none':'Не удалось сконвертировать ни одного файла.',
      'cv.cancelled':'Конвертация отменена.',
      'cv.fail':'Конвертация не удалась.',
      'cv.unavailable':'Конвертация аудио на этом сервере недоступна.',
      'cv.ttl':'Ссылка на скачивание действует ещё {n} ч.',
      'cv.anon':'Без учётной записи можно конвертировать {max} файлов в день — сегодня осталось {left}.',
      'cv.anon.more':'для более высоких лимитов.',
      'th.size':'Размер',
      'quota.conversions':'конвертаций файлов',
      'cv.f.wav':'WAV 8 кГц (Asterisk wav)',
      'cv.f.wav.d':'Воспроизводится везде. Безопасный вариант для приветствий и голосовой почты.',
      'cv.f.wav16':'WAV 16 кГц (Asterisk WAV)',
      'cv.f.wav16.d':'Широкополосный — лучше звучит там, где звонок это поддерживает.',
      'cv.f.alaw':'G.711 A-law (.alaw)',
      'cv.f.alaw.d':'Европейский и грузинский стандарт — воспроизводится без перекодирования.',
      'cv.f.ulaw':'G.711 μ-law (.ulaw)',
      'cv.f.ulaw.d':'Североамериканский аналог A-law.',
      'cv.f.gsm':'GSM 06.10 (.gsm)',
      'cv.f.gsm.d':'Самые маленькие файлы, заметно ниже качество — для длинных записей.',
      'cv.f.g722':'G.722 широкополосный (.g722)',
      'cv.f.g722.d':'HD-звук. Используйте, если весь маршрут звонка на G.722.',
      'cv.f.sln':'Raw signed linear 8 кГц (.sln)',
      'cv.f.sln.d':'Без сжатия и без заголовка — так Asterisk микширует звук внутри.',
      'cv.f.sln16':'Raw signed linear 16 кГц (.sln16)',
      'cv.f.sln16.d':'Широкополосная версия .sln.',
      /* account.html — the registered-user page (design-v2.md 13.5): gate, history,
         personal rubric, profile. Prefix ac. */
      'ac.gate.heading':'Ваш аккаунт CommuniQ',
      'ac.gate.signin':'Вход','ac.gate.register':'Создать аккаунт',
      'ac.gate.tenant':'Пользователь рабочего пространства? Войдите здесь →',
      'ac.gate.istenant':'Рабочее пространство — открываем ваш портал…','ac.gate.isadmin':'Учётная запись оператора — открываем консоль…',
      'ac.f.email':'Электронная почта','ac.f.signinid':'Эл. почта или имя пользователя','ac.f.name':'Отображаемое имя','ac.f.name.hint':'Необязательно — имя, которое видно в шапке.',
      'ac.f.curpw':'Текущий пароль','ac.f.newpw':'Новый пароль','ac.f.pw2':'Повторите новый пароль',
      'ac.reg.pwhint':'Не менее 8 символов.','ac.reg.bademail':'Введите корректный адрес электронной почты.',
      'ac.reg.shortpw':'Пароль должен содержать не менее 8 символов.',
      'ac.reg.closed':'Регистрация сейчас закрыта.',
      'ac.reg.dup':'Аккаунт с такой почтой уже существует.','ac.reg.done':'Аккаунт создан.',
      'ac.disabled':'Этот аккаунт отключён. Ничего запустить не получится, пока оператор не включит его снова.',
      'ac.tab.profile':'Профиль',
      'ac.hist.recordings':'Записи','ac.hist.summaries':'Сводки','ac.hist.tts':'Озвученные фрагменты','ac.hist.conversions':'Конвертации',
      'ac.hist.none.rec':'Записей пока нет.','ac.hist.none.sum':'Сводок пока нет.',
      'ac.hist.none.tts':'Озвученных фрагментов пока нет.','ac.hist.none.conv':'Конвертаций пока нет.',
      'ac.hist.pasted':'Вставленный транскрипт','ac.hist.play':'Воспроизвести','ac.hist.gone':'Больше не хранится','ac.hist.expired':'Срок истёк',
      'ac.hist.left.d':'Осталось {n} дн.','ac.hist.left.h':'Осталось {n} ч.','ac.hist.left.m':'Осталось {n} мин.',
      'ac.hist.noanalyse':'Анализ для этого аккаунта отключён.',
      'ac.src.audio':'Запись','ac.src.text':'Транскрипт',
      'ac.th.source':'Источник','ac.th.duration':'Длительность','ac.th.ran':'Проанализировано','ac.th.summary':'Сводка',
      'ac.th.calls':'Звонки','ac.th.files':'Файлы','ac.th.expires':'Срок',
      'ac.rub.default':'Вы смотрите рубрику по умолчанию. При сохранении создастся ваша собственная копия, которую можно менять как угодно.',
      'ac.rub.defaultver':'Рубрика по умолчанию','ac.rub.reset':'Вернуть к умолчанию',
      'ac.rub.reset.ask':'Ваша рубрика будет заменена копией рубрики по умолчанию. Введите пароль для подтверждения.',
      'ac.rub.reset.needpw':'Введите пароль, чтобы вернуть рубрику по умолчанию.',
      'ac.rub.reset.badpw':'Пароль не совпадает.','ac.rub.reset.done':'Рубрика возвращена к умолчанию.',
      'ac.pf.heading':'Профиль','ac.pf.saved':'Профиль сохранён.','ac.pf.pw':'Смена пароля','ac.pf.pw.change':'Сменить пароль',
      'ac.pf.pw.needcur':'Введите текущий пароль.','ac.pf.pw.mismatch':'Новые пароли не совпадают.',
      'ac.pf.pw.badcur':'Текущий пароль неверен.','ac.pf.pw.done':'Пароль изменён.',
      'ac.pf.usage':'Сегодня','ac.pf.of':'{used} из {max}','ac.pf.nolimit':'без лимита',
      'ac.pf.maxupload':'Максимальный размер загрузки','ac.pf.features':'Доступно в этом аккаунте',
      'ac.cv.expires':'Ссылка на скачивание — {when}.',
      /* tenant.html (tenant portal v2: Analyse / Rubric / History) — owned by the tenant page. */
      'tn.hist.rec':'Записи','tn.hist.sum':'Сводки',
      'tn.hist.rec.none':'Здесь пока пусто — загрузите звонок или вставьте транскрипт на вкладке анализа.',
      'tn.hist.sum.none':'Сводок пока нет.','tn.hist.open':'Открыть на вкладке анализа',
      'tn.th.source':'Источник','tn.th.length':'Длительность','tn.th.ran':'Проанализировано','tn.th.calls':'Звонки','tn.th.summary':'Сводка',
      'tn.src.audio':'Аудио','tn.src.text':'Текст',
      'tn.sc.reset':'Сбросить к умолчанию','tn.sc.reset.heading':'Сбросить рубрику к значению по умолчанию?',
      'tn.sc.reset.warn':'Рубрика будет заменена копией общей рубрики по умолчанию и сохранена как новая версия — прежние версии останутся в истории. Для подтверждения введите свой пароль.',
      'tn.sc.reset.pw':'Ваш пароль','tn.sc.reset.needpw':'Введите свой пароль.',
      'tn.sc.reset.bad':'Пароль не совпадает.','tn.sc.reset.done':'Рубрика сброшена к значению по умолчанию.',
      'tn.sc.isdefault':'Перед вами общая рубрика по умолчанию — у этого рабочего пространства пока нет своей. При сохранении будет создана ваша копия, и дальнейшие изменения умолчания её не затронут.',
      /* ---- public page + admin console (pb.*) ---- */
      'pb.nav.create':'Создать аккаунт',
      'pb.nav.account':'Мой аккаунт',
      'pb.users':'Пользователи',
      'pb.storage':'Хранение',
      'pb.defrubric':'Рубрика по умолчанию',
      'pb.reg.heading':'Зарегистрированные аккаунты — дневные лимиты',
      'pb.reg.desc':'Действуют для каждого самостоятельно созданного аккаунта, у которого нет персональных исключений.',
      'pb.reg.signups':'Регистрация открыта',
      'pb.reg.signups.hint':'Отключение закрывает публичную форму регистрации. Существующие аккаунты продолжают работать — отдельный аккаунт отключается в таблице ниже.',
      'pb.reg.maxconv':'Макс. конвертаций / день',
      'pb.feat.convert':'Конвертация аудио',
      'pb.feat.summarise':'Резюме',
      'pb.feat.score':'Оценка',
      'pb.feat.semantic':'Семантический анализ',
      'pb.users.heading':'Аккаунты',
      'pb.users.search':'Поиск по почте или имени',
      'pb.users.none':'Зарегистрированных аккаунтов пока нет.',
      'pb.users.nomatch':'Ни один аккаунт не подходит под запрос.',
      'pb.users.legend':'«Сегодня» — анализы · клипы TTS · конвертации с полуночи.',
      'pb.th.email':'Эл. почта',
      'pb.th.name':'Имя',
      'pb.th.created':'Создан',
      'pb.th.lastlogin':'Последний вход',
      'pb.th.today':'Сегодня',
      'pb.never':'Никогда',
      'pb.act.activate':'Включить',
      'pb.act.deactivate':'Отключить',
      'pb.act.limits':'Лимиты',
      'pb.act.resetpw':'Сбросить пароль',
      'pb.user.saved':'Аккаунт обновлён',
      'pb.lim.title':'Лимиты аккаунта',
      'pb.lim.note':'Пустое поле означает, что аккаунт использует значение тарифа. Сохранение заменяет все исключения этого аккаунта.',
      'pb.lim.saved':'Лимиты сохранены',
      'pb.pw.title':'Новый пароль',
      'pb.pw.once':'Показывается один раз. Хранится только его хеш, поэтому повторно показать нельзя — передайте его пользователю сейчас.',
      'pb.pw.confirm':'Создать новый пароль для {email}? Текущий пароль перестанет работать немедленно.',
      'pb.copy':'Копировать',
      'pb.copied':'Скопировано',
      'pb.copyfail':'Не удалось скопировать — выделите пароль и скопируйте вручную.',
      'pb.close':'Закрыть',
      'pb.del.confirm':'Удалить аккаунт {email}? Его записи, резюме и клипы TTS НЕ удаляются — они останутся, пока срок хранения не удалит файлы.',
      'pb.del.done':'Аккаунт удалён. Его записи останутся, пока срок хранения не удалит файлы.',
      'pb.storage.heading':'Сохранённые записи',
      'pb.storage.desc':'Один срок хранения для всех сохранённых записей и клипов TTS — и для анонимных посетителей, и для тенантов, и для зарегистрированных аккаунтов.',
      'pb.storage.days':'Хранить записи (дней)',
      'pb.storage.hint':'0 — хранить бессрочно. Запись анонимного посетителя удаляется целиком; у тенанта и у аккаунта остаются расшифровка и результаты, теряется только аудиофайл.',
      'pb.storage.moved':'Срок хранения перенесён во вкладку «Хранение»: одно число теперь охватывает все сохранённые записи, а не только анонимные.',
      'pb.defrubric.heading':'Рубрика по умолчанию',
      'pb.defrubric.desc':'По ней оцениваются все тенанты и зарегистрированные аккаунты, у которых нет собственной рубрики.',
      'pb.src.stored':'Сохранена оператором',
      'pb.src.demo':'Взята у тенанта demo — ещё не сохранена',
      'pb.src.builtin':'Встроенная стартовая — ещё не сохранена',
      'pb.defrubric.updated':'Обновлена {when}, автор: {who}',
      'pb.defrubric.saved':'Рубрика по умолчанию сохранена',
    },
  };
  let LANG = (() => { try { return localStorage.getItem('cq_lang') || (navigator.language||'en').slice(0,2); } catch { return 'en'; } })();
  if (!DICT[LANG]) LANG = 'en';
  function t(key) { return (DICT[LANG] && DICT[LANG][key]) || DICT.en[key] || key; }
  /* Module-local dictionaries. timeline.js / workbench.js carry their own strings and register
     them here at load time, so a feature's copy lives next to its code instead of inside one
     thousand-line block. scripts/check_i18n.py scans every extendDict({...}) call literal in
     frontend/public/*.js with the same parity rules as DICT (and fails on a key defined in two
     places), so the three languages stay in sync there too. Call it at load, before mountHeader. */
  function extendDict(ext) {
    Object.keys(ext || {}).forEach(code => { if (DICT[code]) Object.assign(DICT[code], ext[code]); });
  }
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
    // An icon-only control's accessible NAME is an aria-label, and a stale English one on a
    // Georgian page is the exact bug check_i18n.py exists to stop. The ⓘ tip trigger is the
    // first such control; anything else icon-only should use this too.
    root.querySelectorAll('[data-i18n-aria]').forEach(el => { el.setAttribute('aria-label', t(el.getAttribute('data-i18n-aria'))); });
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
    // The current page gets an .active pill so the header tells you where you are —
    // the tiny brand tag used to be the only page identity.
    const here = (location.pathname.split('/').pop() || 'index.html');
    const nav = (opts.nav || []).map(n => {
      const cls = [n.cls, n.href === here ? 'active' : ''].filter(Boolean).join(' ');
      return `<a href="${n.href}"${n.id ? ` id="${n.id}"` : ''}${cls ? ` class="${cls}"` : ''}${n.i18n ? ` data-i18n="${n.i18n}"` : ''}>${n.label || ''}</a>`;
    }).join('');
    const who = opts.who ? `<span class="who" id="${opts.who}"></span>` : '';
    const langSwitch = `<div class="lang-switch" role="group" aria-label="Language">
      ${['en','ka','ru'].map(c => `<button data-lang-btn="${c}">${c.toUpperCase()}</button>`).join('')}</div>`;
    const theme = opts.theme === false ? '' :
      `<button class="icon-btn" data-theme-btn title="Toggle light/dark" aria-label="Toggle theme"></button>`;
    return `<header class="app-header">
      <a class="brand" href="index.html" aria-label="CommuniQ home">${LOGO}
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
    // Every page mounts the header from a script that runs after its static markup, so this
    // is the one call that wires up all the page's declarative ⓘ tips. Panels rendered later
    // with innerHTML must call CQ.mountTips(container) themselves.
    mountTips(document);
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
      // The message is ALWAYS plain text (tenant names, document titles and usernames flow
      // into it) — textContent, never innerHTML, or a hostile title executes in this origin.
      bg.innerHTML = `<div class="cq-modal" role="dialog" aria-modal="true">
        <p></p>
        <div class="actions"><button class="ghost" data-c>${cancel}</button>
        <button class="${danger ? 'danger' : 'primary'}" data-o>${ok}</button></div></div>`;
      bg.querySelector('p').textContent = message;
      document.body.appendChild(bg);
      const done = v => { bg.remove(); resolve(v); };
      bg.querySelector('[data-o]').addEventListener('click', () => done(true));
      bg.querySelector('[data-c]').addEventListener('click', () => done(false));
      bg.addEventListener('click', e => { if (e.target === bg) done(false); });
      bg.querySelector('[data-o]').focus();
    });
  }

  /* ---------------- Info tips (the ⓘ next to a label) ----------------
     The pages carry no standing prose any more, so this is where a description that is
     still worth having goes: attached to the control it is about, out of the way until
     someone asks. It deliberately opens three ways — hover, keyboard focus and tap —
     because hover-only would have shipped a hint that no phone and no keyboard can reach,
     and half this app's staff are on a phone.

     Markup form (preferred — CQ.mountHeader mounts these for you):
         <button class="tip" data-tip-i18n="bot.general.risk"></button>
         <button class="tip" data-tip="already-localised literal"></button>
     The button must be EMPTY: the "i" glyph is drawn by CSS.
     Add data-tip-for="id id" when the warning is about controls the ⓘ merely sits beside
     (the Test buttons) rather than the label it hangs off: those ids get the same
     aria-describedby, so the warning is announced on the control it costs money on.

     Wiring, per trigger: aria-label names the button, and the sentence itself lives in a
     clipped .cq-sr span next to it, referenced by aria-describedby, so a screen reader
     reads it on focus whether or not the bubble ever paints. The bubble is decorative
     (aria-hidden) — it never carries anything the AT tree does not already have.
     The description span carries data-i18n, so applyI18n retranslates it on a language
     switch for free; an open bubble is repainted by the cq:lang listener below. */
  let TIP_SEQ = 0, tipBox = null, tipOwner = null, tipSrc = '', tipRaf = 0;
  // Elements whose own text IS a name for something else — the description span is placed
  // outside all of them (see wireTip). Table cells are deliberately absent: a <span> after
  // a <th> is not valid inside a <tr>, and no tip lives in a table.
  const TIP_NAMERS = 'label,h1,h2,h3,h4,h5,h6,legend,summary,figcaption';

  function tipBubble() {
    if (tipBox) return tipBox;
    tipBox = document.createElement('div');
    tipBox.id = 'cq-tip';
    tipBox.setAttribute('aria-hidden', 'true');   // the text is already on the trigger
    document.body.appendChild(tipBox);
    return tipBox;
  }

  /* Fixed-position placement, measured from the trigger every time. Prefers below, flips
     above when the room is not there, and clamps to the viewport — at 375px a tip on a
     right-hand field is ALWAYS clamped, so the caret is moved to stay under its ⓘ. */
  function tipPlace() {
    if (!tipOwner || !tipBox) return;
    const r = tipOwner.getBoundingClientRect();
    const vw = window.innerWidth, vh = window.innerHeight;
    // Trigger scrolled out of its container (or off screen): a bubble pointing at nothing
    // is worse than no bubble. A 0×0 rect is the same case wearing a disguise — an element
    // in a display:none subtree reports one, and it slips through the bounds test below
    // because a zero rect at the origin is technically "on screen".
    if (!r.width && !r.height) { tipHide(); return; }
    if (r.bottom < 0 || r.top > vh || r.right < 0 || r.left > vw) { tipHide(); return; }
    const b = tipBox.getBoundingClientRect();
    const GAP = 8, EDGE = 10;
    const place = (r.bottom + GAP + b.height <= vh - EDGE) ? 'below'
                : (r.top - GAP - b.height >= EDGE) ? 'above' : 'below';
    let top = place === 'below' ? r.bottom + GAP : r.top - GAP - b.height;
    top = Math.max(EDGE, Math.min(top, vh - b.height - EDGE));
    let left = Math.max(EDGE, Math.min(r.left + r.width / 2 - b.width / 2, vw - b.width - EDGE));
    tipBox.style.top = Math.round(top) + 'px';
    tipBox.style.left = Math.round(left) + 'px';
    tipBox.dataset.place = place;
    tipBox.style.setProperty('--cq-tip-ax',
      Math.round(Math.max(12, Math.min(r.left + r.width / 2 - left, b.width - 12))) + 'px');
  }

  function tipShow(el, src) {
    // Read the sentence off the description span, never off a cached string: that span is
    // what applyI18n rewrites, so the bubble can never fall behind the current language.
    const text = ((el._cqTipDesc && el._cqTipDesc.textContent) || '').trim();
    if (!text) return;
    if (tipOwner && tipOwner !== el) tipOwner.classList.remove('on');
    const box = tipBubble();
    box.textContent = text;
    tipOwner = el; tipSrc = src;
    el.classList.add('on');
    box.classList.add('open');
    tipPlace();
    tipListen(true);
  }

  function tipHide() {
    if (tipOwner) tipOwner.classList.remove('on');
    if (tipBox) tipBox.classList.remove('open');
    tipOwner = null; tipSrc = '';
    tipListen(false);
  }

  /* Global listeners live only while a tip is open. addEventListener de-dupes an identical
     (fn, capture) pair, so re-binding on every show is safe. */
  function tipListen(on) {
    const m = on ? 'addEventListener' : 'removeEventListener';
    document[m]('pointerdown', tipOutside, true);
    document[m]('keydown', tipEsc, true);
    window[m]('scroll', tipReflow, true);   // capture: also catches nested scroll containers
    window[m]('resize', tipReflow);
  }
  function tipOutside(e) { if (tipOwner && !tipOwner.contains(e.target)) tipHide(); }
  function tipEsc(e) { if (e.key === 'Escape') tipHide(); }
  function tipReflow() {
    if (tipRaf) return;
    tipRaf = requestAnimationFrame(() => { tipRaf = 0; tipPlace(); });
  }

  /* Turn one element into a live trigger. Idempotent — pages re-render panels and call
     mountTips again over markup that is already wired. */
  function wireTip(el) {
    if (!el) return null;
    if (el._cqTipDesc) return el;                       // already wired
    const key = el.getAttribute('data-tip-i18n');
    const text = key ? t(key) : (el.getAttribute('data-tip') || '');

    if (el.tagName === 'BUTTON') el.setAttribute('type', 'button');   // never submit a form
    else { el.setAttribute('role', 'button'); if (!el.hasAttribute('tabindex')) el.setAttribute('tabindex', '0'); }
    el.classList.add('tip');
    el.setAttribute('aria-label', t('tip.label'));
    el.setAttribute('data-i18n-aria', 'tip.label');

    const desc = document.createElement('span');
    desc.className = 'cq-sr';
    desc.id = 'cq-tip-d' + (++TIP_SEQ);
    if (key) desc.setAttribute('data-i18n', key);       // applyI18n keeps it in language
    desc.textContent = text;
    // A trigger with nothing to say is hidden rather than left as a dead ⓘ that opens an
    // empty box. This is what lets a tip come and go with the state it describes (the
    // Georgian voice note only applies while Georgian is the selected language).
    el.hidden = !text;
    /* The span must NOT land inside the <label> (or heading) the ⓘ sits in. Those elements
       ARE a text alternative: everything inside a <label> becomes part of the accessible
       NAME of the control it labels, so a span parked there would make a screen reader
       announce the Voice field as "Voice, More information, Georgian uses the eleven_v3
       model…" on every focus — the whole paragraph read as the field's name, which is
       exactly the noise a tip exists to avoid. aria-describedby resolves by id anywhere in
       the document, so the span is parked after the outermost thing that names something;
       still inside the same panel, so a re-render disposes of the pair together. */
    let anchor = el;
    for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
      if (p.matches(TIP_NAMERS)) anchor = p;
    }
    anchor.insertAdjacentElement('afterend', desc);
    el.setAttribute('aria-describedby', desc.id);
    el._cqTipDesc = desc;
    /* A tip whose warning belongs to controls that are not its own neighbours names them
       here. The admin Test buttons spend real ElevenLabs credit on every click, and a
       keyboard user who never Tabs one step further would otherwise meet the cost note
       only by accident. Ids, because the span's own id is generated at runtime. */
    (el.getAttribute('data-tip-for') || '').split(/\s+/).forEach(id => {
      const target = id && document.getElementById(id);
      if (!target) return;
      const on = (target.getAttribute('aria-describedby') || '').split(/\s+/).filter(Boolean);
      if (on.indexOf(desc.id) < 0) on.push(desc.id);
      target.setAttribute('aria-describedby', on.join(' '));
    });

    el.addEventListener('mouseenter', () => tipShow(el, 'hover'));
    el.addEventListener('mouseleave', () => { if (tipSrc === 'hover') tipHide(); });
    // Focus opens it so a keyboard reaches the same text a mouse does.
    el.addEventListener('focus', () => { if (tipOwner !== el) tipShow(el, 'focus'); });
    el.addEventListener('blur', () => { if (tipOwner === el) tipHide(); });
    // Tap is the only way in on a touch screen, and there a click arrives with no focus
    // before it. On a mouse the focus above already opened it, so the FIRST click must not
    // close it again — only a click on an already-clicked tip toggles off.
    el.addEventListener('click', e => {
      e.preventDefault();
      if (tipOwner === el && tipSrc === 'click') tipHide(); else tipShow(el, 'click');
    });
    return el;
  }

  /* Wire every declarative trigger under `root`. Safe to call repeatedly and on a subtree
     that was just written with innerHTML — which is how the JS-rendered panels adopt it. */
  function mountTips(root = document) {
    const scope = (root && root.querySelectorAll) ? root : document;
    scope.querySelectorAll('[data-tip-i18n],[data-tip]').forEach(wireTip);
    // A root that is itself a trigger (mountTips(btn)) would be missed by the query above.
    if (scope.matches && scope.matches('[data-tip-i18n],[data-tip]')) wireTip(scope);
  }

  /* Attach a tip programmatically to an element you hold a reference to. The ⓘ is appended
     INSIDE `host` (put it on the <label> or the <h3>, not on the input), and a second call
     on the same host updates the sentence instead of hanging a second ⓘ off it.
     Pass { i18n: 'some.key' } to have it follow language switches; a bare string will not.
     Passing an empty string hides the ⓘ again, so a tip can track the state it explains. */
  function tip(host, text, { i18n } = {}) {
    if (!host) return null;
    const btn = (host.classList && host.classList.contains('tip')) ? host
      : (host.querySelector && host.querySelector(':scope > .tip'))
      || host.appendChild(Object.assign(document.createElement('button'), { className: 'tip' }));
    if (i18n) { btn.setAttribute('data-tip-i18n', i18n); btn.removeAttribute('data-tip'); }
    else { btn.setAttribute('data-tip', text == null ? '' : String(text)); btn.removeAttribute('data-tip-i18n'); }
    if (btn._cqTipDesc) {                                // re-point an already-wired trigger
      const d = btn._cqTipDesc;
      if (i18n) { d.setAttribute('data-i18n', i18n); d.textContent = t(i18n); }
      else { d.removeAttribute('data-i18n'); d.textContent = btn.getAttribute('data-tip'); }
      btn.hidden = !d.textContent;
      if (tipOwner === btn) { if (btn.hidden) tipHide(); else tipShow(btn, tipSrc); }
      return btn;
    }
    return wireTip(btn);
  }

  // applyI18n has already rewritten the description spans by the time this fires; a bubble
  // that happens to be open still holds the old sentence, so repaint it.
  document.addEventListener('cq:lang', () => { if (tipOwner) tipShow(tipOwner, tipSrc); });

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

  /* Download glyph as SVG, not a character. It used to be U+2B73 (⭳), which is absent from
     the Georgian and Russian font stacks this app ships — so on a Georgian page the download
     control rendered as an empty tofu box. An inline SVG has no font dependency at all and
     inherits currentColor, so it follows the theme like the text around it. */
  const ICON_DL = '<svg class="cq-i" viewBox="0 0 16 16" width="15" height="15" aria-hidden="true" focusable="false"'
    + ' fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">'
    + '<path d="M8 2.5v7.5m0 0L5.2 7.2M8 10l2.8-2.8"/><path d="M2.8 12.2v.8a1.2 1.2 0 0 0 1.2 1.2h8a1.2 1.2 0 0 0 1.2-1.2v-.8"/></svg>';

  /* ---------------- Audio player ---------------- */
  function fmt(s) { s = Math.floor(s || 0); return `${Math.floor(s/60)}:${String(s%60).padStart(2,'0')}`; }
  function player(container, src, { name = 'audio', autoplay = true } = {}) {
    container.innerHTML = `<div class="cq-player">
      <button class="cq-play" aria-label="Play/pause">▶</button>
      <input class="cq-seek" type="range" min="0" max="100" value="0" step="0.1" aria-label="Seek" />
      <span class="cq-time">0:00</span>
      <a class="cq-dl icon-btn" title="Download" download="${name}">${ICON_DL}</a>
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

  /* Sentiment card: the words and the voice, side by side.
     They are NOT averaged into one number. When they disagree — positive words in a negative
     voice — that disagreement is the finding a reviewer wants, and a mean would hide it, so
     a conflict is called out explicitly instead. */
  function sentimentHTML(sn) {
    if (!sn || (!sn.text && !sn.prosody)) return '';
    const cls = p => p === 'positive' ? 'ok' : p === 'negative' ? 'bad' : '';
    const esc2 = v => (v ?? '').toString().replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
    const pct = v => v == null ? null : Math.round(v * 100);
    const meter = (labelKey, v) => {
      const n = pct(v); if (n == null) return '';
      return `<div style="margin-top:8px"><div class="sc-meta">${t(labelKey)} · ${n}%</div>
        <div class="sc-bar"><span style="width:${n}%"></span></div></div>`;
    };
    const half = (titleKey, part) => {
      if (!part) return `<div style="flex:1"><b style="color:var(--mist)">${t(titleKey)}</b>
        <div class="muted" style="margin-top:6px">${t('sn.unavailable')}</div></div>`;
      return `<div style="flex:1"><b style="color:var(--mist)">${t(titleKey)}</b>
        <div style="margin-top:6px"><span class="pill ${cls(part.polarity)}">${esc2(part.label)}</span></div>
        ${meter('sn.arousal', part.arousal)}${meter('sn.valence', part.valence)}</div>`;
    };
    const agree = sn.agreement === 'conflict'
      ? `<div class="msg err" style="margin-top:12px">${t('sn.conflict')}</div>` : '';
    return `<div class="card">
      <div class="inline" style="justify-content:space-between; align-items:baseline">
        <h3 style="margin:0">${t('sn.title')}</h3>
        <span class="pill ${cls(sn.overall)}">${esc2(sn.overall || '—')}</span>
      </div>
      <div class="row" style="margin-top:10px">
        ${half('sn.text', sn.text)}
        ${half('sn.voice', sn.prosody)}
      </div>${agree}
    </div>`;
  }

  /* Full /analyze result card (language/sentiment/quality/topics/summary/key points/actions)
     + the transcript. Shared by tenant.html's own Analyze/Playground results and the KB-admin
     console's new Playground audio-score mode — both call the same pipeline and want the
     same rendering; this used to be duplicated per page. */
  function analysisHTML(d) {
    const a = d.analysis || {};
    const list = v => { const arr = _arr(v); return arr.length
      ? '<ul class="tight">' + arr.map(i => `<li>${_esc(i)}</li>`).join('') + '</ul>'
      : '<span class="muted">—</span>'; };
    const topics = _arr(a.topics).map(x => `<span class="chip">${_esc(x)}</span>`).join('')
      || '<span class="muted">—</span>';
    const kb = (d.kb_used || []).length
      ? d.kb_used.map(k => `<span class="chip">${_esc(k.title || k.doc_type || 'KB')}${k.score != null ? ` · ${k.score}` : ''}</span>`).join('')
      : `<span class="muted">${t('res.nokb')}</span>`;
    return `<div class="card">
      <h3>${t('res.analysis')}</h3>
      <div class="kv"><b>${t('res.language')}</b> ${_esc(a.language || d.language || '—')}</div>
      <div class="kv"><b>${t('res.sentiment')}</b> ${_esc(a.sentiment || '—')} &nbsp; <b>${t('res.quality')}</b> ${a.quality_score ?? '—'}/100</div>
      <div class="kv"><b>${t('res.topics')}</b> ${topics}</div>
      <div style="margin-top:8px"><b style="color:var(--mist)">${t('res.kbused')}</b><div style="margin-top:4px">${kb}</div></div>
      <div style="margin-top:10px"><b style="color:var(--mist)">${t('res.summary')}</b><p>${_esc(a.summary)}</p></div>
      <div class="row"><div><b style="color:var(--mist)">${t('res.keypoints')}</b>${list(a.key_points)}</div><div><b style="color:var(--mist)">${t('res.actions')}</b>${list(a.action_items)}</div></div>
    </div>
    <div class="card"><h3>${t('res.transcript')}</h3><pre class="tx">${_esc(d.transcript) || t('res.empty')}</pre></div>`;
  }

  function scorecardHTML(sc) {
    if (!sc || !Array.isArray(sc.dimensions) || !sc.dimensions.length) return '';
    const total = sc.weighted_total;
    const band = v => v == null ? 'muted' : v >= 80 ? 'ok' : v >= 50 ? 'pending' : 'alert';
    const barcls = v => v == null ? '' : v >= 80 ? 'good' : v >= 50 ? 'mid' : 'bad';
    // Evidence used to be a list of strings; the workbench's scoring now returns
    // {quote, segments, start, end} objects so a finding can be placed on the timeline.
    // BOTH shapes are in the database (old jobs keep the strings), so this renders either —
    // _arr alone would stringify an object to "[object Object]".
    const quote = e => (e && typeof e === 'object' ? e.quote : e);
    const dimRow = d => {
      const s = d.score, ev = _arr((d.evidence || []).map ? d.evidence.map(quote) : d.evidence);
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
    // PARTIALLY_SUPPORTED is a real verdict, not an unknown one: the substance is right but a
    // detail is wrong. Mapping it to 'notinkb' told a reviewer the knowledge base had nothing to
    // say about a claim it in fact contradicted in part.
    const vcls = v => ({SUPPORTED:'supported', PARTIALLY_SUPPORTED:'partial',
                        CONTRADICTED:'contradicted', NOT_IN_KB:'notinkb'}[v] || 'notinkb');
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
        ${c.partially_supported ? `<span class="pill partial">${c.partially_supported} ${t('fc.partial')}</span>` : ''}
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
    label();
    document.addEventListener('cq:lang', label);
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


  // ---- Auto-growing textareas -------------------------------------------------------
  // A scoring dimension's guidance is the text the model actually reads when scoring, and
  // AI rubric import fills it with that section's complete criteria verbatim — thousands
  // of characters for a real call-centre standard. A fixed 54px box showed two lines of
  // it, so the field people most need to READ was the one they could see least of.
  //
  // Capped rather than unbounded: seven dimensions each grown to full height would push
  // Save several screens down, so past the cap the textarea scrolls internally instead.
  function autogrow(el, cap) {
    if (!el) return;
    cap = cap || Math.max(240, Math.round(window.innerHeight * 0.45));
    // Inside a hidden panel a textarea measures scrollHeight 0 — sizing it there would
    // collapse it to nothing. Leave it; it gets sized when its tab is shown.
    if (!el.offsetHeight && !el.getClientRects().length) return;
    el.style.height = 'auto';
    const natural = el.scrollHeight;          // read BEFORE clamping, or the cap hides it
    el.style.height = Math.min(natural, cap) + 'px';
    el.style.overflowY = natural > cap ? 'auto' : 'hidden';
  }

  // Size every match now and keep each in step as it is typed into.
  function autogrowBind(root, sel, cap) {
    (root || document).querySelectorAll(sel).forEach(el => {
      autogrow(el, cap);
      el.addEventListener('input', () => autogrow(el, cap));
    });
  }

  return { API, LOGO, ICON_DL, t, lang, setLang, extendDict, applyI18n, toggleTheme, currentTheme, header, mountHeader,
           toast, confirm, tip, mountTips, select, enhanceSelects, syncSelect, player, attachRecorder,
           analysisHTML, scorecardHTML, factcheckHTML, sentimentHTML, readResp,
           autogrow, autogrowBind };
})();
