/* The knowledge base — importing it, browsing it, and sharing it with the bot.

   Five prefixes, one vocabulary, because they are five views of the same objects and the
   words have to agree across them:
     kb.*    the import surface (file / paste / CSV) and its progress
     tkb.*   the tenant-facing document list
     kba.*   the operator's KB console — stats, chunk editing, duplicates, the retrieval
             playground — which since the act-as-tenant work lives inside `tenant.html`
             rather than in a console of its own
     vis.*   the per-document 'share with the bot' state; `internal` is the default and the
             public bot reads ONLY documents flipped to public, so these labels are the whole
             UI for a safety property
     bulk.*  the results line for a bulk action over selected documents
     imp.*   the three import method names

   `kb.nomatch` and `kba.edit` are already read by `admin.html` as well as `tenant.html`,
   which is the argument against splitting this per page. */

import type { Dict } from '../index';

export const en: Dict = {
  'kb.import': 'Import knowledge',

  'imp.file': 'Upload file',
  'imp.paste': 'Paste text',
  'imp.csv': 'CSV (Q&A / key-value)',

  'kb.filelabel': 'Files (PDF / DOCX / XLSX / CSV / TXT / MD — several at once is fine)',
  'kb.csvlabel': 'CSV file (first row = header)',
  'kb.searchlabel': 'Search knowledge base',
  'kb.search_ph': 'Ask a question…',
  'kb.documents': 'Documents',
  'kb.none': 'No documents yet. Import some knowledge above.',
  'kb.processing': 'processing…',
  'kb.nomatch': 'No matches.',

  'kba.title': 'Knowledge Base Management',
  'kba.tenant': 'Tenant',
  'kba.selecttenant': 'Select a tenant to manage its knowledge base.',

  'kba.tab.overview': 'Overview',
  'kba.tab.documents': 'Documents',
  'kba.tab.import': 'Import',
  'kba.tab.playground': 'Playground',
  'kba.tab.duplicates': 'Duplicates',
  'kba.tab.activity': 'Activity',

  'kba.stat.documents': 'Documents',
  'kba.stat.chunks': 'Chunks',
  'kba.stat.coverage': 'Embedding coverage',
  'kba.stat.failed': 'Failed imports',
  'kba.stat.tokens': 'Approx. tokens',
  'kba.stat.lastupd': 'Last updated',
  'kba.stat.inprogress': 'In progress',

  'kba.params': 'Active configuration',
  'kba.export': 'Export',
  'kba.exportcsv': 'Export CSV',
  'kba.reembedall': 'Rebuild search index',
  'kba.refresh': 'Refresh',

  'kba.f.status': 'Status',
  'kba.f.type': 'Type',
  'kba.f.tag': 'Tag',
  'kba.f.search': 'Search title/content',
  'kba.f.all': 'All',

  'kba.selected': 'selected',

  'kba.bulk.delete': 'Delete',
  'kba.bulk.reembed': 'Rebuild search',
  'kba.bulk.retag': 'Retag',

  'kba.selectall': 'Select all',
  'kba.edit': 'Edit',
  'kba.chunks': 'Chunks',
  'kba.reembed': 'Re-embed',
  'kba.delete': 'Delete',
  'kba.save': 'Save',
  'kba.nodocs': 'No documents. Import some below.',

  'kba.doc.title': 'Title',
  'kba.doc.type': 'Category',
  'kba.doc.tags': 'Tags',
  'kba.doc.meta': 'Metadata (JSON, optional)',
  'kba.doc.content': 'Content (editing re-chunks & re-embeds)',

  'kba.pg.query': 'Query (any language)',
  'kba.pg.topk': 'Results to return',
  'kba.pg.threshold': 'Minimum match score (0–1)',
  'kba.pg.run': 'Run retrieval',
  'kba.pg.method': 'method',
  'kba.pg.nohits': 'No chunks retrieved.',

  'kba.dup.exact': 'Exact duplicates',
  'kba.dup.near': 'Near-duplicates',
  'kba.dup.none': 'No duplicates found.',
  'kba.dup.sim': 'similarity',

  'kba.act.none': 'No activity yet.',

  'kba.chunk.edit': 'Edit chunk',
  'kba.chunk.delete': 'Delete chunk',

  'kba.warn.mismatch': 'Embedding dimension mismatch — re-embed required',

  'kba.tab.scoring': 'Scoring',

  'vis.col': 'Visibility',
  'vis.all': 'All',
  'vis.public': 'Shared with bot',
  'vis.internal': 'Internal',
  'vis.publish': 'Share with bot',
  'vis.unpublish': 'Stop sharing',

  'vis.bulk.publish': 'Share selected with bot',
  'vis.bulk.unpublish': 'Stop sharing selected',

  'vis.stat.public': 'Shared with bot',

  'vis.confirm.publish': 'Share {n} document(s) with your bot? The bot may quote shared documents word for word to your customers. Nothing is made public on the internet — your data stays inside your workspace.',
  'vis.confirm.unpublish': 'Stop sharing {n} document(s)? The bot will no longer use them.',
  'vis.confirm.publish.one': 'Share “{title}” with your bot? The bot may quote it word for word to your customers. Nothing is made public on the internet.',
  'vis.confirm.unpublish.one': 'Stop sharing “{title}”? The bot will no longer use it.',

  'vis.done.publish': 'Shared with bot',
  'vis.done.unpublish': 'Sharing stopped',

  'tkb.tab.maint': 'Maintenance',

  'tkb.overview.heading': 'Knowledge base health',

  'tkb.params.hint': 'The settings retrieval actually runs with. If the configured dimension and the stored column disagree, new embeddings are failing and search has quietly stopped working.',
  'tkb.params.columndim': 'Dimension (stored)',
  'tkb.params.chunk': 'Chunk size / overlap',
  'tkb.params.threshold': 'Retrieval threshold',
  'tkb.params.topk': 'Default top-k',
  'tkb.params.metric': 'Distance metric',
  'tkb.params.index': 'Index type',
  'tkb.params.noembed': 'Chunks without an embedding',

  'tkb.loadfail': 'Could not load the knowledge base.',

  'tkb.th.source': 'Source',

  'tkb.docs.none': 'No documents here yet — add some under Import.',

  'tkb.del.confirm': 'Delete “{title}”? Its chunks disappear from every answer immediately, and this cannot be undone.',

  'tkb.bulk.delete.confirm': 'Delete {n} document(s)? Their chunks disappear from every answer immediately, and this cannot be undone.',
  'tkb.bulk.reembed.confirm': 'Re-embed {n} document(s) now? This runs immediately and briefly competes with live search.',

  'tkb.edit.warn': 'Saving new text re-chunks and re-embeds this document. Retrieval switches to the new text as soon as that finishes; if it fails the document is marked with an error rather than left half-updated.',

  'tkb.badjson': 'Metadata must be valid JSON.',

  'tkb.reembed.done': 'Search rebuilt for {n} chunks',

  'tkb.chunks.pick': 'Document',
  'tkb.chunks.none': 'This document has no chunks yet.',
  'tkb.chunks.pickone': 'Choose a document to see its chunks.',
  'tkb.chunks.hint': 'Chunks — not documents — are what retrieval matches against. Editing one re-embeds that chunk on the spot; deleting one removes it from every answer.',

  'tkb.chunk.noembed': 'no embedding',
  'tkb.chunk.del.confirm': 'Delete this chunk? It disappears from every answer immediately.',
  'tkb.chunk.edit.hint': 'Saving re-embeds this chunk immediately. The rest of the document is untouched.',

  'tkb.pg.heading': 'Test what search finds',

  'tkb.dup.identical': 'documents with identical content',
  'tkb.dup.keep': 'keeping',
  'tkb.dup.skipped': 'Near-duplicate scan skipped — this knowledge base has too many chunks to compare every pair.',

  'tkb.act.filter': 'Action',
  'tkb.act.filter.ph': 'import, edit, delete, reembed…',
  'tkb.act.method': 'Method',
  'tkb.act.detail': 'Detail',
  'tkb.act.actor': 'Who',

  'tkb.exp.hint': 'Downloads every document in this knowledge base, including the internal ones. The export itself is recorded in the activity log.',

  'tkb.reembed.heading': 'Rebuild the search index',
  'tkb.reembed.desc': 'Rebuilds the search data for every chunk — needed after the embedding model or its dimension changes. Runs in the background worker at a limited rate, so a large knowledge base can take a while; search keeps working the whole time. Only one rebuild runs at a time.',
  'tkb.reembed.start': 'Queue rebuild',
  'tkb.reembed.confirm': 'Queue a full search-index rebuild? It runs in the background and can take a while. A new one cannot start until it finishes.',
  'tkb.reembed.queued': 'Search rebuild queued',
  'tkb.reembed.busy': 'A rebuild is already queued or running.',
  'tkb.reembed.none': 'No rebuild has run yet.',
  'tkb.reembed.progress': '{done} of {total} documents',
  'tkb.reembed.failed': '{n} failed',
  'tkb.reembed.state.queued': 'Queued',
  'tkb.reembed.state.running': 'Running',
  'tkb.reembed.state.done': 'Finished',
  'tkb.reembed.state.error': 'Failed',
  'tkb.reembed.state.cancelled': 'Cancelled',

  'bulk.done.delete': 'Deleted {n} documents',
  'bulk.done.reembed': 'Rebuilding search for {n} documents',
  'bulk.done.retag': 'Updated tags on {n} documents',
  'bulk.done.publish': 'Shared {n} documents with the bot',
  'bulk.done.unpublish': 'Stopped sharing {n} documents',

  'kb.csvhint': 'First row must be column headers. A two-column file is imported as question & answer (or key & value) pairs; files with more columns are imported one entry per row.',
  'kb.needfile': 'Choose a file first.',

  'kba.notenants': 'No organizations yet — create one in the Console.',

  'kb.templates': 'Sample files to copy:',
  'kb.restr': 'My file does not follow the template — restructure it with AI',
  'kb.restr.done': 'AI restructuring finished — the document is ready.',
  'kb.restr.fail': 'AI restructuring failed.',

  'kb.files.progress': 'Importing file {done} of {total}…',
  'kb.files.done': '{n} file(s) imported.',
  'kb.files.failed': 'Failed:',

  'kb.restr.hint': 'During import, Claude reads the document and rewrites it as clean, self-contained entries — every amount, term and number is kept exactly as written. The import takes a little longer.',
};

export const ka: Dict = {
  'kb.import': 'ცოდნის იმპორტი',

  'imp.file': 'ფაილის ატვირთვა',
  'imp.paste': 'ტექსტის ჩასმა',
  'imp.csv': 'CSV (კითხვა-პასუხი / გასაღები-მნიშვნელობა)',

  'kb.filelabel': 'ფაილები (PDF / DOCX / XLSX / CSV / TXT / MD — შეგიძლიათ რამდენიმეც ერთად)',
  'kb.csvlabel': 'CSV ფაილი (პირველი სტრიქონი = სათაურები)',
  'kb.searchlabel': 'ცოდნის ბაზაში ძებნა',
  'kb.search_ph': 'დასვით კითხვა…',
  'kb.documents': 'დოკუმენტები',
  'kb.none': 'დოკუმენტები ჯერ არ არის. დაამატეთ ცოდნა ზემოთ.',
  'kb.processing': 'მუშავდება…',
  'kb.nomatch': 'შედეგი ვერ მოიძებნა.',

  'kba.title': 'ცოდნის ბაზის მართვა',
  'kba.tenant': 'ორგანიზაცია',
  'kba.selecttenant': 'აირჩიეთ ორგანიზაცია მისი ცოდნის ბაზის სამართავად.',

  'kba.tab.overview': 'მიმოხილვა',
  'kba.tab.documents': 'დოკუმენტები',
  'kba.tab.import': 'იმპორტი',
  'kba.tab.playground': 'ტესტირება',
  'kba.tab.duplicates': 'დუბლიკატები',
  'kba.tab.activity': 'აქტივობა',

  'kba.stat.documents': 'დოკუმენტები',
  'kba.stat.chunks': 'ფრაგმენტები',
  'kba.stat.coverage': 'ემბედინგებით დაფარვა',
  'kba.stat.failed': 'წარუმატებელი იმპორტები',
  'kba.stat.tokens': 'დაახლ. ტოკენები',
  'kba.stat.lastupd': 'ბოლო განახლება',
  'kba.stat.inprogress': 'მიმდინარე',

  'kba.params': 'აქტიური კონფიგურაცია',
  'kba.export': 'ექსპორტი',
  'kba.exportcsv': 'CSV ექსპორტი',
  'kba.reembedall': 'ძიების ინდექსის განახლება',
  'kba.refresh': 'განახლება',

  'kba.f.status': 'სტატუსი',
  'kba.f.type': 'ტიპი',
  'kba.f.tag': 'ტეგი',
  'kba.f.search': 'ძებნა სათაურში/კონტენტში',
  'kba.f.all': 'ყველა',

  'kba.selected': 'მონიშნული',

  'kba.bulk.delete': 'წაშლა',
  'kba.bulk.reembed': 'ძიების განახლება',
  'kba.bulk.retag': 'ტეგების შეცვლა',

  'kba.selectall': 'ყველას მონიშვნა',
  'kba.edit': 'რედაქტირება',
  'kba.chunks': 'ფრაგმენტები',
  'kba.reembed': 'ხელახალი ემბედინგი',
  'kba.delete': 'წაშლა',
  'kba.save': 'შენახვა',
  'kba.nodocs': 'დოკუმენტები არ არის. დაამატეთ ქვემოთ.',

  'kba.doc.title': 'სათაური',
  'kba.doc.type': 'კატეგორია',
  'kba.doc.tags': 'ტეგები',
  'kba.doc.meta': 'მეტამონაცემები (JSON, არასავალდებულო)',
  'kba.doc.content': 'კონტენტი (რედაქტირებისას ფრაგმენტები და ემბედინგები ხელახლა შეიქმნება)',

  'kba.pg.query': 'მოთხოვნა (ნებისმიერ ენაზე)',
  'kba.pg.topk': 'შედეგების რაოდენობა',
  'kba.pg.threshold': 'დამთხვევის მინიმალური ქულა (0–1)',
  'kba.pg.run': 'მოძიების გაშვება',
  'kba.pg.method': 'მეთოდი',
  'kba.pg.nohits': 'ფრაგმენტები ვერ მოიძებნა.',

  'kba.dup.exact': 'ზუსტი დუბლიკატები',
  'kba.dup.near': 'მსგავსი დუბლიკატები',
  'kba.dup.none': 'დუბლიკატები არ მოიძებნა.',
  'kba.dup.sim': 'მსგავსება',

  'kba.act.none': 'აქტივობა ჯერ არ არის.',

  'kba.chunk.edit': 'ფრაგმენტის რედაქტირება',
  'kba.chunk.delete': 'ფრაგმენტის წაშლა',

  'kba.warn.mismatch': 'ემბედინგის განზომილება არ ემთხვევა — საჭიროა ხელახალი ემბედინგი',

  'kba.tab.scoring': 'შეფასება',

  'vis.col': 'ხილვადობა',
  'vis.all': 'ყველა',
  'vis.public': 'ბოტისთვის დაშვებული',
  'vis.internal': 'შიდა',
  'vis.publish': 'ბოტისთვის დაშვება',
  'vis.unpublish': 'დაშვების მოხსნა',

  'vis.bulk.publish': 'მონიშნულის დაშვება ბოტისთვის',
  'vis.bulk.unpublish': 'მონიშნულისთვის დაშვების მოხსნა',

  'vis.stat.public': 'ბოტისთვის დაშვებული',

  'vis.confirm.publish': 'დაეშვას {n} დოკუმენტი ბოტისთვის? ბოტი დაშვებულ დოკუმენტებს თქვენს კლიენტებთან სიტყვასიტყვით ციტირებს. ინტერნეტში არაფერი ქვეყნდება — მონაცემები თქვენს სამუშაო სივრცეში რჩება.',
  'vis.confirm.unpublish': 'მოეხსნას დაშვება {n} დოკუმენტს? ბოტი მათ აღარ გამოიყენებს.',
  'vis.confirm.publish.one': 'დაეშვას „{title}“ ბოტისთვის? ბოტი მას თქვენს კლიენტებთან სიტყვასიტყვით ციტირებს. ინტერნეტში არაფერი ქვეყნდება.',
  'vis.confirm.unpublish.one': 'მოეხსნას დაშვება „{title}“-ს? ბოტი მას აღარ გამოიყენებს.',

  'vis.done.publish': 'დაშვებულია ბოტისთვის',
  'vis.done.unpublish': 'დაშვება მოხსნილია',

  'tkb.tab.maint': 'მოვლა',

  'tkb.overview.heading': 'ცოდნის ბაზის მდგომარეობა',

  'tkb.params.hint': 'პარამეტრები, რომლებითაც მოძიება რეალურად მუშაობს. თუ კონფიგურაციის განზომილება და ბაზაში შენახული ერთმანეთს არ ემთხვევა, ახალი ემბედინგები ვერ იქმნება და მოძიება უხმაუროდ აღარ მუშაობს.',
  'tkb.params.columndim': 'განზომილება (შენახული)',
  'tkb.params.chunk': 'ფრაგმენტის ზომა / გადაფარვა',
  'tkb.params.threshold': 'მოძიების ზღვარი',
  'tkb.params.topk': 'ნაგულისხმევი top-k',
  'tkb.params.metric': 'მანძილის მეტრიკა',
  'tkb.params.index': 'ინდექსის ტიპი',
  'tkb.params.noembed': 'ფრაგმენტები ემბედინგის გარეშე',

  'tkb.loadfail': 'ცოდნის ბაზა ვერ ჩაიტვირთა.',

  'tkb.th.source': 'წყარო',

  'tkb.docs.none': 'დოკუმენტები ჯერ არ არის — დაამატეთ „იმპორტის“ ჩანართში.',

  'tkb.del.confirm': 'წაიშალოს „{title}“? მისი ფრაგმენტები მაშინვე გაქრება ყველა პასუხიდან და მოქმედება შეუქცევადია.',

  'tkb.bulk.delete.confirm': 'წაიშალოს {n} დოკუმენტი? მათი ფრაგმენტები მაშინვე გაქრება ყველა პასუხიდან და მოქმედება შეუქცევადია.',
  'tkb.bulk.reembed.confirm': 'გაუკეთდეს {n} დოკუმენტს ხელახალი ემბედინგი ახლავე? პროცესი მაშინვე შესრულდება და მცირე ხნით რესურსებს გაინაწილებს მიმდინარე მოძიებასთან.',

  'tkb.edit.warn': 'ახალი ტექსტის შენახვისას ეს დოკუმენტი ხელახლა დანაწევრდება და მისი ემბედინგები განახლდება. მოძიება ახალ ტექსტზე დასრულებისთანავე გადავა; თუ პროცესი ვერ შესრულდა, დოკუმენტი შეცდომის სტატუსით მოინიშნება და ნახევრად განახლებული არ დარჩება.',

  'tkb.badjson': 'მეტამონაცემები უნდა იყოს ვალიდური JSON.',

  'tkb.reembed.done': '{n} ფრაგმენტის საძიებო მონაცემები განახლდა',

  'tkb.chunks.pick': 'დოკუმენტი',
  'tkb.chunks.none': 'ამ დოკუმენტს ფრაგმენტები ჯერ არ აქვს.',
  'tkb.chunks.pickone': 'აირჩიეთ დოკუმენტი ფრაგმენტების სანახავად.',
  'tkb.chunks.hint': 'მოძიება ფრაგმენტებს ადარებს და არა დოკუმენტებს. ფრაგმენტის რედაქტირებისას მისი ემბედინგი მაშინვე განახლდება; წაშლა კი მას ყველა პასუხიდან შლის.',

  'tkb.chunk.noembed': 'ემბედინგი არ აქვს',
  'tkb.chunk.del.confirm': 'წაიშალოს ეს ფრაგმენტი? ის მაშინვე გაქრება ყველა პასუხიდან.',
  'tkb.chunk.edit.hint': 'შენახვისას ამ ფრაგმენტის ემბედინგი მაშინვე განახლდება. დოკუმენტის დანარჩენი ნაწილი უცვლელი რჩება.',

  'tkb.pg.heading': 'შეამოწმეთ, რას პოულობს ძიება',

  'tkb.dup.identical': 'დოკუმენტს იდენტური შიგთავსი აქვს',
  'tkb.dup.keep': 'რჩება',
  'tkb.dup.skipped': 'მსგავსი დუბლიკატების სკანირება გამოტოვებულია — ამ ცოდნის ბაზაში ძალიან ბევრი ფრაგმენტია ყველა წყვილის შესადარებლად.',

  'tkb.act.filter': 'მოქმედება',
  'tkb.act.filter.ph': 'იმპორტი, რედაქტირება, წაშლა, ემბედინგი…',
  'tkb.act.method': 'მეთოდი',
  'tkb.act.detail': 'დეტალი',
  'tkb.act.actor': 'ვინ',

  'tkb.exp.hint': 'ჩამოტვირთავს ამ ცოდნის ბაზის ყველა დოკუმენტს, შიდას ჩათვლით. თავად ექსპორტი აქტივობის ჟურნალში ფიქსირდება.',

  'tkb.reembed.heading': 'ძიების ინდექსის განახლება',
  'tkb.reembed.desc': 'ხელახლა აგებს საძიებო მონაცემებს ყოველი ფრაგმენტისთვის — საჭიროა ემბედინგის მოდელის ან განზომილების შეცვლის შემდეგ. ეშვება ფონურად, შეზღუდული სიჩქარით, ამიტომ დიდ ბაზაზე შეიძლება დიდხანს გაგრძელდეს; ძიება მთელი ამ დროის განმავლობაში მუშაობს. ერთდროულად მხოლოდ ერთი განახლება მიმდინარეობს.',
  'tkb.reembed.start': 'განახლების დაწყება',
  'tkb.reembed.confirm': 'დადგეს რიგში ძიების ინდექსის სრული განახლება? შესრულდება ფონურად და შეიძლება დიდხანს გაგრძელდეს. დასრულებამდე ახლის დაწყება ვერ მოხერხდება.',
  'tkb.reembed.queued': 'ძიების ინდექსის განახლება რიგში დადგა',
  'tkb.reembed.busy': 'განახლება უკვე რიგშია ან მიმდინარეობს.',
  'tkb.reembed.none': 'განახლება ჯერ არ გაშვებულა.',
  'tkb.reembed.progress': '{done} / {total} დოკუმენტი',
  'tkb.reembed.failed': '{n} ჩავარდა',
  'tkb.reembed.state.queued': 'რიგში',
  'tkb.reembed.state.running': 'მიმდინარეობს',
  'tkb.reembed.state.done': 'დასრულდა',
  'tkb.reembed.state.error': 'ჩავარდა',
  'tkb.reembed.state.cancelled': 'გაუქმდა',

  'bulk.done.delete': 'წაიშალა {n} დოკუმენტი',
  'bulk.done.reembed': '{n} დოკუმენტის საძიებო მონაცემები ახლდება',
  'bulk.done.retag': 'ტეგები განახლდა {n} დოკუმენტზე',
  'bulk.done.publish': '{n} დოკუმენტი დაეშვა ბოტისთვის',
  'bulk.done.unpublish': '{n} დოკუმენტს დაშვება მოეხსნა',

  'kb.csvhint': 'პირველი რიგი სვეტების სათაურები უნდა იყოს. ორსვეტიანი ფაილი შემოდის კითხვა-პასუხის (ან გასაღები-მნიშვნელობის) წყვილებად; მეტსვეტიანი ფაილიდან თითო რიგი თითო ჩანაწერად შემოდის.',
  'kb.needfile': 'ჯერ აირჩიეთ ფაილი.',

  'kba.notenants': 'ორგანიზაციები ჯერ არ არის — შექმენით კონსოლში.',

  'kb.templates': 'ნიმუშის ფაილები:',
  'kb.restr': 'ფაილი შაბლონს არ მიჰყვება — AI-მ გადააწყოს იმპორტისას',
  'kb.restr.done': 'AI-გადაწყობა დასრულდა — დოკუმენტი მზადაა.',
  'kb.restr.fail': 'AI-გადაწყობა ვერ შესრულდა.',

  'kb.files.progress': 'იმპორტდება ფაილი {done} / {total}…',
  'kb.files.done': 'დაიმპორტდა {n} ფაილი.',
  'kb.files.failed': 'ვერ დაიმპორტდა:',

  'kb.restr.hint': 'იმპორტისას Claude წაიკითხავს დოკუმენტს და გადააწყობს მას მკაფიო, დამოუკიდებელ ჩანაწერებად — ყველა თანხა, ვადა და რიცხვი ზუსტად ისე რჩება, როგორც წერია. იმპორტს ცოტა მეტი დრო სჭირდება.',
};

export const ru: Dict = {
  'kb.import': 'Импорт знаний',

  'imp.file': 'Загрузить файл',
  'imp.paste': 'Вставить текст',
  'imp.csv': 'CSV (вопрос-ответ)',

  'kb.filelabel': 'Файлы (PDF / DOCX / XLSX / CSV / TXT / MD — можно несколько сразу)',
  'kb.csvlabel': 'CSV-файл (первая строка = заголовок)',
  'kb.searchlabel': 'Поиск по базе знаний',
  'kb.search_ph': 'Задайте вопрос…',
  'kb.documents': 'Документы',
  'kb.none': 'Документов пока нет. Импортируйте знания выше.',
  'kb.processing': 'обработка…',
  'kb.nomatch': 'Совпадений нет.',

  'kba.title': 'Управление базой знаний',
  'kba.tenant': 'Организация',
  'kba.selecttenant': 'Выберите организацию, чтобы управлять её базой знаний.',

  'kba.tab.overview': 'Обзор',
  'kba.tab.documents': 'Документы',
  'kba.tab.import': 'Импорт',
  'kba.tab.playground': 'Песочница',
  'kba.tab.duplicates': 'Дубликаты',
  'kba.tab.activity': 'Активность',

  'kba.stat.documents': 'Документы',
  'kba.stat.chunks': 'Фрагменты',
  'kba.stat.coverage': 'Покрытие эмбеддингами',
  'kba.stat.failed': 'Ошибки импорта',
  'kba.stat.tokens': 'Прибл. токены',
  'kba.stat.lastupd': 'Обновлено',
  'kba.stat.inprogress': 'В процессе',

  'kba.params': 'Активная конфигурация',
  'kba.export': 'Экспорт',
  'kba.exportcsv': 'Экспорт CSV',
  'kba.reembedall': 'Перестроить поисковый индекс',
  'kba.refresh': 'Обновить',

  'kba.f.status': 'Статус',
  'kba.f.type': 'Тип',
  'kba.f.tag': 'Тег',
  'kba.f.search': 'Поиск по заголовку/тексту',
  'kba.f.all': 'Все',

  'kba.selected': 'выбрано',

  'kba.bulk.delete': 'Удалить',
  'kba.bulk.reembed': 'Перестроить поиск',
  'kba.bulk.retag': 'Изменить теги',

  'kba.selectall': 'Выбрать все',
  'kba.edit': 'Редактировать',
  'kba.chunks': 'Фрагменты',
  'kba.reembed': 'Переэмбеддинг',
  'kba.delete': 'Удалить',
  'kba.save': 'Сохранить',
  'kba.nodocs': 'Нет документов. Импортируйте ниже.',

  'kba.doc.title': 'Заголовок',
  'kba.doc.type': 'Категория',
  'kba.doc.tags': 'Теги',
  'kba.doc.meta': 'Метаданные (JSON, необязательно)',
  'kba.doc.content': 'Текст (при редактировании документ заново разбивается и переэмбеддится)',

  'kba.pg.query': 'Запрос (на любом языке)',
  'kba.pg.topk': 'Количество результатов',
  'kba.pg.threshold': 'Минимальный балл совпадения (0–1)',
  'kba.pg.run': 'Выполнить поиск',
  'kba.pg.method': 'метод',
  'kba.pg.nohits': 'Фрагменты не найдены.',

  'kba.dup.exact': 'Точные дубликаты',
  'kba.dup.near': 'Похожие дубликаты',
  'kba.dup.none': 'Дубликаты не найдены.',
  'kba.dup.sim': 'сходство',

  'kba.act.none': 'Активности пока нет.',

  'kba.chunk.edit': 'Редактировать фрагмент',
  'kba.chunk.delete': 'Удалить фрагмент',

  'kba.warn.mismatch': 'Несовпадение размерности эмбеддинга — требуется переэмбеддинг',

  'kba.tab.scoring': 'Оценка',

  'vis.col': 'Видимость',
  'vis.all': 'Все',
  'vis.public': 'Доступен боту',
  'vis.internal': 'Внутренний',
  'vis.publish': 'Открыть боту',
  'vis.unpublish': 'Закрыть от бота',

  'vis.bulk.publish': 'Открыть боту выбранные',
  'vis.bulk.unpublish': 'Закрыть от бота выбранные',

  'vis.stat.public': 'Доступны боту',

  'vis.confirm.publish': 'Открыть боту {n} документ(ов)? Бот может дословно цитировать доступные документы вашим клиентам. В интернете ничего не публикуется — данные остаются в вашем рабочем пространстве.',
  'vis.confirm.unpublish': 'Закрыть от бота {n} документ(ов)? Бот перестанет их использовать.',
  'vis.confirm.publish.one': 'Открыть боту «{title}»? Бот может дословно цитировать его вашим клиентам. В интернете ничего не публикуется.',
  'vis.confirm.unpublish.one': 'Закрыть от бота «{title}»? Бот перестанет его использовать.',

  'vis.done.publish': 'Доступен боту',
  'vis.done.unpublish': 'Закрыт от бота',

  'tkb.tab.maint': 'Обслуживание',

  'tkb.overview.heading': 'Состояние базы знаний',

  'tkb.params.hint': 'Настройки, с которыми поиск работает на самом деле. Если заданная размерность и размерность в базе не совпадают, новые эмбеддинги не создаются и поиск тихо перестал работать.',
  'tkb.params.columndim': 'Размерность (в базе)',
  'tkb.params.chunk': 'Размер фрагмента / перекрытие',
  'tkb.params.threshold': 'Порог поиска',
  'tkb.params.topk': 'Top-k по умолчанию',
  'tkb.params.metric': 'Метрика расстояния',
  'tkb.params.index': 'Тип индекса',
  'tkb.params.noembed': 'Фрагменты без эмбеддинга',

  'tkb.loadfail': 'Не удалось загрузить базу знаний.',

  'tkb.th.source': 'Источник',

  'tkb.docs.none': 'Документов пока нет — добавьте их во вкладке «Импорт».',

  'tkb.del.confirm': 'Удалить «{title}»? Его фрагменты сразу исчезнут из всех ответов, и отменить это нельзя.',

  'tkb.bulk.delete.confirm': 'Удалить {n} документ(ов)? Их фрагменты сразу исчезнут из всех ответов, и отменить это нельзя.',
  'tkb.bulk.reembed.confirm': 'Переэмбеддить {n} документ(ов) сейчас? Это выполнится немедленно и ненадолго займёт ресурсы живого поиска.',

  'tkb.edit.warn': 'Сохранение нового текста заново разобьёт документ на фрагменты и переэмбеддит его. Поиск перейдёт на новый текст сразу после этого; при ошибке документ помечается как ошибочный, а не остаётся наполовину обновлённым.',

  'tkb.badjson': 'Метаданные должны быть корректным JSON.',

  'tkb.reembed.done': 'Поисковые данные обновлены для {n} фрагментов',

  'tkb.chunks.pick': 'Документ',
  'tkb.chunks.none': 'У этого документа пока нет фрагментов.',
  'tkb.chunks.pickone': 'Выберите документ, чтобы увидеть его фрагменты.',
  'tkb.chunks.hint': 'Поиск сопоставляет фрагменты, а не документы. Правка фрагмента сразу его переэмбеддит; удаление убирает его из всех ответов.',

  'tkb.chunk.noembed': 'нет эмбеддинга',
  'tkb.chunk.del.confirm': 'Удалить этот фрагмент? Он сразу исчезнет из всех ответов.',
  'tkb.chunk.edit.hint': 'Сохранение сразу переэмбеддит этот фрагмент. Остальной документ не затрагивается.',

  'tkb.pg.heading': 'Проверьте, что находит поиск',

  'tkb.dup.identical': 'документ(ов) с одинаковым содержимым',
  'tkb.dup.keep': 'оставляем',
  'tkb.dup.skipped': 'Поиск похожих дубликатов пропущен — в этой базе знаний слишком много фрагментов, чтобы сравнить все пары.',

  'tkb.act.filter': 'Действие',
  'tkb.act.filter.ph': 'импорт, правка, удаление, переэмбеддинг…',
  'tkb.act.method': 'Метод',
  'tkb.act.detail': 'Детали',
  'tkb.act.actor': 'Кто',

  'tkb.exp.hint': 'Скачивает все документы этой базы знаний, включая внутренние. Сам экспорт записывается в журнал активности.',

  'tkb.reembed.heading': 'Перестроить поисковый индекс',
  'tkb.reembed.desc': 'Заново строит поисковые данные для каждого фрагмента — нужно после смены модели эмбеддингов или её размерности. Выполняется в фоне с ограниченной скоростью, поэтому на большой базе может занять время; поиск всё это время работает. Одновременно выполняется только одно перестроение.',
  'tkb.reembed.start': 'Запустить перестроение',
  'tkb.reembed.confirm': 'Поставить в очередь полное перестроение поискового индекса? Выполнится в фоне и может занять время. До завершения новое запустить нельзя.',
  'tkb.reembed.queued': 'Перестроение поискового индекса поставлено в очередь',
  'tkb.reembed.busy': 'Перестроение уже в очереди или выполняется.',
  'tkb.reembed.none': 'Перестроение ещё не запускалось.',
  'tkb.reembed.progress': '{done} из {total} документов',
  'tkb.reembed.failed': 'с ошибкой: {n}',
  'tkb.reembed.state.queued': 'В очереди',
  'tkb.reembed.state.running': 'Выполняется',
  'tkb.reembed.state.done': 'Завершён',
  'tkb.reembed.state.error': 'Ошибка',
  'tkb.reembed.state.cancelled': 'Отменён',

  'bulk.done.delete': 'Удалено документов: {n}',
  'bulk.done.reembed': 'Перестраивается поиск для {n} документов',
  'bulk.done.retag': 'Теги обновлены у {n} документов',
  'bulk.done.publish': 'Открыто боту документов: {n}',
  'bulk.done.unpublish': 'Закрыто от бота документов: {n}',

  'kb.csvhint': 'Первая строка — заголовки столбцов. Файл с двумя столбцами импортируется как пары вопрос-ответ (или ключ-значение); файлы с большим числом столбцов — по одной записи на строку.',
  'kb.needfile': 'Сначала выберите файл.',

  'kba.notenants': 'Организаций пока нет — создайте в Консоли.',

  'kb.templates': 'Файлы-образцы:',
  'kb.restr': 'Файл не соответствует шаблону — переструктурировать с помощью ИИ',
  'kb.restr.done': 'ИИ-переструктурирование завершено — документ готов.',
  'kb.restr.fail': 'Не удалось переструктурировать документ с помощью ИИ.',

  'kb.files.progress': 'Импорт файла {done} из {total}…',
  'kb.files.done': 'Импортировано файлов: {n}.',
  'kb.files.failed': 'Не удалось:',

  'kb.restr.hint': 'При импорте Claude читает документ и переписывает его в виде отдельных, понятных записей — все суммы, сроки и числа сохраняются ровно как написано. Импорт занимает немного больше времени.',
};
