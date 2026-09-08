# Frontend migration: vanilla JS → Next.js

The record of the port, kept after it finished. It exists because the migration was large
enough that the reasoning could not live in commit messages alone: ~12,400 lines of legacy
frontend across six pages and six shared modules, calling **155 backend routes**. The legacy
stack is deleted; what survives here is why the new one is shaped the way it is, which is
still the first thing to read before changing any of these pages.

## Status

| Page | Legacy | Route | State |
|---|---|---|---|
| AI usage | *(new)* | `/usage` | ✅ ported |
| AI setup | *(new)* | `/ai-config` | ✅ ported |
| Public app | `index.html` | `/` | ✅ ported |
| Audio editor | `editor.html` | `/editor` | ✅ ported |
| Account | `account.html` | `/account` | ✅ ported |
| Console | `admin.html` | `/console` | ✅ ported |
| Workspace | `tenant.html` | `/workspace` | ✅ ported |
| Copilot demo | `copilot-demo.html` | `/copilot` | ✅ ported |

**The migration is finished.** Every page is ported, deployed, and the legacy stack has been
deleted. What follows is the record of how it was done and why the result looks the way it
does — the two lists below are the load-bearing part, and they are why this file is kept.

## Cutover — done

The vanilla stack is gone from the tree. Three things happened, in one commit:

**1. The files were deleted.** Six pages (`index`, `tenant`, `admin`, `account`, `editor`,
`copilot-demo`, plus the `kb-admin` redirect stub) and the six shared modules — `brand.js`
(2018 lines), `brand.css`, `workbench.js` (1235), `timeline.js` (1032), `audio-edit-core.js` +
`audio-editor.js` (856). ~12,400 lines.

`frontend/public/` still exists and is still copied into the image, because it is now
**static assets only**: `favicon.png`, the two logos, `guides/` (the KB import templates the
workspace links by absolute path, and the operator/tenant guide docs) and `guides.zip`. None
of it is emitted by `next build`, and none of it can collide with the export any more — so
the copy order in `frontend/Dockerfile`, which used to decide which stack won a filename,
now decides nothing.

**2. The retired URLs became 301s**, in BOTH `deploy/nginx.conf` and `deploy/tls-ssl.conf`:

| Old URL | → |
|---|---|
| `/tenant.html` | `/workspace` |
| `/admin.html` | `/console` |
| `/kb-admin.html` | `/workspace` |
| `/copilot-demo.html` | `/copilot` |

Three details in those blocks are deliberate and easy to undo by accident:

- **Both files, always.** They are separate `server` blocks — 80 and 443 — and production
  serves 443. A redirect added to `nginx.conf` alone is written, committed, deployed and
  never executed.
- **`location =`, not a prefix.** Exact matches are resolved before the
  `~* \.(html|css|js)$` regex block, which ends in `try_files $uri =404`. A prefix location
  would lose to the regex and the old URL would 404 instead of redirecting.
- **`$is_args$args`.** Unlike `rewrite`, `return 301 <uri>` does **not** carry the query
  string. The console's sign-in link is literally `/tenant.html?next=/console`
  (`app/console/api.ts`), and `/workspace` reads `?next=` to decide where to send an operator
  after login — so dropping it would sign them in and strand them in the workspace.

`index.html`, `account.html` and `editor.html` need no redirect: the export owns those three
filenames (`trailingSlash: false` emits one flat `.html` per route), so those URLs already
serve the ported page.

**3. `scripts/check_i18n.py` reports the finish line instead of dying at it.** Its
"shared between the stacks" count was the migration's own definition of done: it counted the
key definitions that had to be kept in step by hand, peaked at 3258, and reads **0** now.
The script no longer requires `brand.js` to exist — but it still *looks* for it, and still
parses a legacy dictionary if it finds one. That is the point: zero is only meaningful while
something is still counting. A vanilla page reappearing puts the number back above zero and
says so in the output. The migrated stack's own checks (parity, one-owner-per-key,
unparseable module, unwired module) are untouched.

Frontend routes today, all served from one docroot by `frontend/Dockerfile`:
`/`, `/account`, `/workspace`, `/console`, `/editor`, `/copilot`, `/usage`, `/ai-config`.

## The rule the port runs on

**Faithful first, better second.** A survey of the legacy code found 56 places where the
existing behaviour is either a deliberate decision that reads like an accident, or an actual
defect. Both are dangerous to a rewrite, in opposite directions: "cleaning up" the first
ships a regression, and faithfully reproducing the second ships a known bug into new code.

So every one of them is listed below and decided EXPLICITLY. Nothing in this list gets
resolved by a porter's judgement in the moment.

## Pre-existing defects found while surveying

These were bugs in the legacy code, not port hazards. Each is fixed in the port and was
called out in its own commit, so the fix was reviewable as a fix rather than hidden inside a
2,000-line diff. Kept because each one describes behaviour the new code deliberately does
NOT reproduce — read before "restoring" anything to match how the old page behaved.

1. **The editor's "Convert & download" is broken.** `editor.html` does
   `POST /api/convert` (non-stream) then `await r.blob()` and saves it as `<name>-edited.zip`.
   The non-stream route returns a **JSON summary**, not a ZIP — so the file that lands on
   disk is a JSON body with a `.zip` extension. The real archive is behind a second call.
2. **The editor's convert sends no auth headers**, on either the POST or the download.
   Ownership is keyed on the principal, so a signed-in user's batch is owned by their IP
   instead of their account — and the two calls must be made as the *same* principal or the
   download 404s.
3. **AudioContext leak in the editor.** `start()` constructs a new `AudioContext` on every
   play and nothing ever closes it. Browsers cap live contexts per document (historically ~6
   in Chrome), so playback dies silently after a handful of plays.
4. **The editor reallocates an AudioBuffer per layer per animation frame when zoomed in.**
   `draw()` runs in the playback rAF loop at 60fps and calls `trim()`, which builds an
   `OfflineAudioContext` and copies the slice.
5. **The copilot page reads the wrong envelope level.** `GET /v1/chat/suggestions/{ref}`
   returns `{state, turn:<envelope>}`, but `absorbTurn`/`absorbAnswer` read `d.grounding`,
   `d.tier1`, `d.citations`, `d.suggestions`, `d.usage`, `d.handoff`, `d.reply`, `d.stages`
   from the top level, where none of them exist.
6. **The console races itself after adding a tenant user.** `tenantAction('users', id, name);
   loadTenants();` — neither awaited. `loadTenants()` rebuilds the whole tbody and destroys
   the `#sub-<id>` node `tenantAction` is concurrently writing into.

## Deliberate decisions the port must preserve

Not bugs. Each was argued for in the source, several fix a previously-shipped bug, and each
is the kind of thing a rewrite silently "improves" into a regression.

- **The public page sends the registered-user token only.** `pubAuth()` attaches
  `Authorization: Bearer <cq_user_token>` and nothing else. Reusing the shared `authHeaders()`
  would send `X-Admin-Token` or a tenant Bearer to `/tts`, `/transcribe`, `/sentiment` and
  `/limits`, silently promoting an operator to superadmin scope on the public surface.
- **`kbJson()` returns three values** — STALE, `null`, or the body — and callers distinguish
  `null` ("the request failed") from `[]` ("the workspace genuinely has none"). Collapsing
  them tells a customer their knowledge base is empty when the server is merely down. This
  was a shipped QA bug once already.
- **`tenant.html`'s 401 funnel uses `else if` deliberately**, and its admin branch is guarded
  on `TID`. `sessionExpired()` clears the token, which flips `adminMode()` true inside the
  same call — an `if`/`if` would then tear down a valid operator session too.
- **Never a native `prompt()`/`confirm()`.** Every dialog is a promise-returning modal. A
  native prompt leaking into bulk retag was a fixed QA bug.
- **One audio player per surface, reused via `load(url)`.** Creating a second player leaves a
  second play bar on the page — also a previously fixed bug.
- **The select panel escapes its stacking context.** `.card` uses `backdrop-filter`, which
  creates a stacking context that traps a child's `z-index`, so no `z-index` on the panel can
  fix it. This was the reported "workspace picker hidden behind KB health" bug.
- **`computePeaks` yields with `MessageChannel`, not `setTimeout`**, and only after holding
  the main thread >8ms. Both are deliberate: `setTimeout` is clamped to 1s+ under background
  throttling and rAF does not fire in a hidden tab at all.
- **The editor's selection semantics.** An empty selection (`|to-from| <= 1e-4`) means THE
  WHOLE TIMELINE, not nothing. Undo/redo restore layers only — restoring `sel` would point at
  samples that no longer exist.
- **`mixdown()` resamples UP to the highest input rate** and scales by exactly `1/peak` only
  when the peak exceeds 1.0. Resampling down would destroy a music bed under a telephony
  capture; dividing by N would make a single quiet layer inexplicably quieter.
- **Fact-check verdict mapping** must keep `PARTIALLY_SUPPORTED` distinct from `NOT_IN_KB`.
  Collapsing them told a reviewer the knowledge base had nothing to say about a claim it in
  fact partly contradicted — a correctness bug in a compliance feature.
- **The console's clipboard fallback order is load-bearing.** Production is reachable over
  plain HTTP, where `navigator.clipboard` does not exist, and `document.execCommand('copy')`
  only works inside the click's own gesture — so any `await` before it breaks the fallback.

## Migration-specific hazards

Every one of these was hit and handled; they are listed because each names a trap that is
still live for anyone touching this code, not a to-do.

- **`authHeaders()` has no act-as-tenant support.** `tenant.html` is two consoles behind one
  URL; porting it onto the existing `apiGet`/`apiSend` would make every operator request run
  unscoped. `lib/session.ts` has to grow this before that page moves.
- **`cq:lang` is dispatched on `window` by the new stack and listened for on `document` by
  `timeline.js`.** Window events do not propagate down to document, so a naive port stops
  re-translating on language change, silently.
- **Imperative modules own their host DOM.** `timeline.js` does `container.innerHTML = ''` on
  construction and `root.remove()` on destroy; the audio editor writes `host.innerHTML` and
  registers window/document listeners with no teardown. Dropped into React unchanged they
  delete nodes React believes it owns. `reactStrictMode` double-mounts in dev, which surfaces
  this immediately — and doubles every leaked listener.
- **Toasts, the confirm modal and the tip bubble are appended to `document.body` and never
  torn down.** Harmless under full-page navigation; under client-side routing they survive it.
- **The API base differs.** The legacy pages compute `/api` or `http://<host>:8000` from
  `location.port`; `lib/session.ts` hardcodes `/api`, so `npm run dev` on :3000 404s every
  call. Affects the already-ported pages too.
- **Four places parse SSE out of `XMLHttpRequest.responseText`**, not `fetch`, not
  `EventSource` — uploads need progress, and the parser is stateful across `progress` events
  (it keeps the partial tail and consumes only complete `\n\n` frames). Whether a response is
  a stream or a plain JSON refusal is decided ONCE, off `Content-Type`, at `readyState === 2`.
- **`admin.html`'s address is hardcoded in three places** that must move with it, including
  `tenant.html`'s `NEXT_OK = ['admin.html','kb-admin.html']` post-login redirect allowlist.
- **~110 lines of page-scoped `.cd-*` CSS** live in `copilot-demo.html` and exist nowhere in
  the shared sheet. Appending them to `globals.css` would leak that page's narrower `main`
  width onto every other page.

## i18n

~1,000 keys lived in one `DICT` in `brand.js`, and ownership was not clean: `cv.`, `tts.`,
`sc.`, `quota.`, `lang.`, `login.`, `tab.` and `sn.` were each shared by two to four pages, so
"a key travels with its page" needed a shared tier underneath it. That tier is
`lib/i18n/chrome.ts`; the rest is one module per owner under `lib/i18n/features/` and
`lib/i18n/pages/`, assembled in `lib/i18n/index.ts`.

While both stacks were in the tree a key shared with an unmigrated page had to be COPIED, not
moved, and `scripts/check_i18n.py` counted those copies — 3258 definitions at the peak. **It
reads 0.** The script still runs on every change and still enforces the rules that outlived
the migration: three-language parity, exactly one module owning each key, and the two ways a
module can be linted green while contributing nothing at runtime (blocks it cannot parse, and
a module missing from `MODULES`).
