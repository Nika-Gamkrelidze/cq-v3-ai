'use client';
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { AudioPlayer, type PlayerHandle } from '@/components/AudioPlayer';
import Header from '@/components/Header';
import { Recorder } from '@/components/Recorder';
import { showModal } from '@/components/ui/Modal';
import { Select } from '@/components/ui/Select';
import { toast } from '@/components/ui/Toast';
import { ApiError, apiBase, apiMessage, keyForStatus } from '@/lib/session';
import { readSseBody, type SseData } from '@/lib/sse';
import { useI18n } from '@/lib/useI18n';
import styles from './copilot.module.css';
import {
  BLANK_TURN, absorbAnswer, absorbTurn, appendDelta, blockedKey, citationNumbers, envelopeOf,
  reasonKey, retryDelay, spentTokens, stateOf, upsertVariant,
  type CiteRef, type Envelope, type Grounding, type Passage, type TurnState, type Wrapper,
} from './logic';

/* ============================================================================================
   CQ v3 AI — operator copilot reference harness.

   WHY THIS PAGE EXISTS
   This is the artefact the chat-site team copies. Production callers are server-to-server: the
   integration key lives in THEIR backend and never reaches a browser. Here an operator pastes a
   key for local testing and it is held in React state only — never localStorage, never
   sessionStorage, never a query string. Nothing in the product links here.

   It deliberately renders the tier ladder honestly, in the order the architecture produces it:
      POST /v1/chat/turns  ->  grounding gate (deterministic, no LLM)
                           ->  tier-1 KB passage cards   (NO model call, 0 tokens)
                           ->  streamed draft variants   (the only tokens spent)
   …with a measured millisecond readout per stage, because "the tier-1 cards land before the
   model does" is the product claim and a demo that hides it proves nothing.

   ── CREDENTIALS: WHY THIS PAGE DOES NOT USE `apiGet`/`apiSend` ──────────────────────────────
   Every other ported page calls the API through `lib/session`, which chooses ONE credential
   from the tab's session by scope. This surface has no session credential at all: `/v1/chat/*`
   authenticates a chat INTEGRATION (`X-CQ-Key: cqi_….…` + `X-CQ-Tenant`), which is a
   server-side secret and is not — must not be — anything the browser holds. There is no
   operator or admin path into these routes to port instead; the legacy page asks a human to
   paste the key for a local test, and this one does the same, unchanged.

   `session.ts` agrees, from the other side: `x-cq-key` is in its CREDENTIAL_HEADERS list, so
   `scopedHeaders()` REFUSES to carry it (it throws in development). That is correct and is not
   worked around here — these calls are plain `fetch`, with `apiBase()` for the origin and
   `readResp` below for the error shape, exactly as the legacy page used `CQ.readResp`.

   TWO credentials, deliberately kept apart. The mic posts to `/v1/transcriptions`, which
   authenticates as the TENANT (`X-API-Key`), not as a chat integration — hence the separate
   optional field rather than a silent 401. And `/v1/tts` is anonymous-capable and is sent NO
   credential at all: attaching whatever token happens to be in the tab is the public-surface
   mistake `docs/MIGRATION.md` calls out under "the public page sends the registered-user token
   only".

   STREAMING, two transports, one vocabulary:
     * copilot — a ticketed GET, read with the platform's own `EventSource`. That is why
       `/v1/chat/stream-tickets` exists at all: `EventSource` cannot set headers, so a 60 s
       single-use ticket goes in the query string instead of the integration credential landing
       in nginx access logs and browser history.
     * autopilot — a POST, which `EventSource` cannot do, so the frames are read off the fetch
       body with `lib/sse`'s `readSseBody`. Same wire format, same event names, and the terminal
       `done` payload is byte-identical to the blocking envelope — which is why `absorbAnswer`
       is the single consumer of both.
   ============================================================================================ */

type Mode = 'assist' | 'autopilot';
type Role = 'customer' | 'operator' | 'bot';
interface Msg { role: Role; text: string }

const LOCALES = ['ka', 'ru', 'en'].map(v => ({ value: v, label: v }));
const CHANNELS = ['web', 'instagram', 'messenger', 'whatsapp'].map(v => ({ value: v, label: v }));

const uuid = (): string => (typeof crypto !== 'undefined' && crypto.randomUUID
  ? crypto.randomUUID()
  : 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
    const r = Math.random() * 16 | 0;
    return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16);
  }));

const sleep = (ms: number) => new Promise<void>(res => { setTimeout(res, ms); });

function safeJson(s: string): unknown {
  try { return JSON.parse(s); } catch { return null; }
}

function isObject(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

/** `CQ.readResp`, for the one page that cannot go through `lib/session`'s transports.

    Same contract as `session.ts`'s `readBody`, and it throws the same `ApiError` — so
    `apiMessage(e, t)` renders these failures in the visitor's language with the server's own
    `detail` preferred, exactly as everywhere else. The 204 exemption is not decoration:
    `POST /v1/chat/feedback` is declared `status_code=204`, and without it every fire-and-forget
    feedback call would succeed server-side and then throw "unexpected response" at the operator
    who clicked. */
async function readResp<T>(r: Response): Promise<T> {
  const text = await r.text().catch(() => '');
  const trimmed = text.trimStart();
  const data = trimmed && (trimmed[0] === '{' || trimmed[0] === '[') ? safeJson(trimmed) : null;
  if (!r.ok) {
    const d = isObject(data) ? data : null;
    const raw = d ? (d.detail ?? d.message ?? d.error) : null;
    const detail = typeof raw === 'string' && raw ? raw
      : raw != null ? JSON.stringify(raw) : null;
    throw new ApiError({ status: r.status, detail, ...keyForStatus(r.status) });
  }
  if (r.status === 204 || r.status === 205) return undefined as T;
  if (data === null) throw new ApiError({ status: r.status, detail: null, i18nKey: 'err.badresp' });
  return data as T;
}

/** State that async code has to read back BEFORE React has re-rendered.

    The legacy page kept one mutable `S` object and re-rendered by hand, so every handler read
    the current value for free. Here the value is React state — but half the flow is a stream:
    `sendShownOnce()` fires feedback for the drafts that `absorbTurn` just stored, and the poll
    loop asks "do I already have grounding?" between two `await`s. Both would read a stale
    closure. So each of these hooks keeps a ref in step with its state, updated inside the
    setter rather than in an effect: an effect runs after the commit, which is one tick too
    late for code that is already running. Render reads the state; async reads the ref. */
type Updater<T> = T | ((prev: T) => T);
function useTracked<T>(initial: T): [T, (next: Updater<T>) => void, { current: T }] {
  const [value, setValue] = useState<T>(initial);
  const ref = useRef<T>(initial);
  const set = useCallback((next: Updater<T>) => {
    ref.current = typeof next === 'function' ? (next as (prev: T) => T)(ref.current) : next;
    setValue(ref.current);
  }, []);
  return [value, set, ref];
}

export default function CopilotPage() {
  const { t } = useI18n();

  /* ---- credentials. In memory for this tab only: no storage, no URL, no logging. ---- */
  const [fKey, setFKey] = useState('');
  const [fTenant, setFTenant] = useState('');
  const [fExpect, setFExpect] = useState('');
  const [fLocale, setFLocale] = useState('ka');
  const [fChannel, setFChannel] = useState('web');
  const [fVoiceKey, setFVoiceKey] = useState('');

  const [mode, setModeState] = useState<Mode>('assist');
  const [convRef, setConvRef] = useState('');
  const [thread, setThread] = useState<Msg[]>([]);
  const [inbound, setInbound] = useState('');
  const [sendErr, setSendErr] = useState('');
  const [reopening, setReopening] = useState(false);
  const [editText, setEditText] = useState('');
  const [stt, setStt] = useState<{ text: string; cls: string }>({ text: '', cls: '' });
  const [ttsVisible, setTtsVisible] = useState(false);

  const [busy, setBusy, busyRef] = useTracked(false);
  const [speaking, setSpeaking, speakingRef] = useTracked(false);
  const [turn, setTurn, turnRef] = useTracked<TurnState>(BLANK_TURN);

  const t0Ref = useRef(0);
  const esRef = useRef<EventSource | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const playerRef = useRef<PlayerHandle>(null);
  const threadBoxRef = useRef<HTMLDivElement>(null);
  const endUserRef = useRef<string | null>(null);
  const mountedRef = useRef(true);

  /* The conversation ref is random, so it is minted after mount rather than during render: this
     page is prerendered at BUILD time and a value invented during render would differ between
     the prerendered markup and the browser's first render. */
  useEffect(() => { setConvRef(`demo-${uuid().slice(0, 8)}`); }, []);

  /* Leaving the page must not leave anything running. Under the legacy stack every navigation
     was a full page load, so none of this needed saying; under client-side routing an open
     `EventSource` reconnects on its own, an in-flight answer keeps the model running on
     someone's money, and the 20 s warm-read loop below would keep polling a route nobody is
     looking at. All three stop here. */
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      esRef.current?.close();
      esRef.current = null;
      abortRef.current?.abort();
      abortRef.current = null;
    };
  }, []);

  useEffect(() => {
    const box = threadBoxRef.current;
    if (box) box.scrollTop = box.scrollHeight;
  }, [thread]);

  /* ---------------------------------------------------------------------------------------
     Transport helpers
     --------------------------------------------------------------------------------------- */

  // Metering only, NEVER authorization — and opaque, because it is stored as-is on the
  // conversation row. Minted lazily so nothing random happens during render.
  const endUser = () => (endUserRef.current ??= `demo-operator-${Math.random().toString(36).slice(2, 8)}`);

  function headers(extra: Record<string, string> = {}): Record<string, string> {
    const tenant = fTenant.trim();
    return {
      'X-CQ-Key': fKey.trim(),
      'X-CQ-Tenant': tenant,
      // MANDATORY on writes — it makes a caller's tenant-mapping bug loud (403) instead of
      // letting one tenant's message land in another's conversation.
      'X-CQ-Expect-Tenant': fExpect.trim() || tenant,
      'X-CQ-End-User': endUser(),
      ...extra,
    };
  }

  const mark = (name: string) => {
    const ms = Math.round(performance.now() - t0Ref.current);
    setTurn(prev => ({ ...prev, marks: { ...prev.marks, [name]: ms } }));
  };

  /* Everything a turn produces, cleared in one place so a mode switch or a new conversation
     cannot leave a stale answer sitting under a fresh question. `suggestRef` goes with it: it
     belongs to the turn that produced it, and keeping it across a mode switch would leave the
     copilot rail rendering an autopilot turn's ref (and the reverse). */
  function resetTurn() {
    esRef.current?.close();
    esRef.current = null;
    abortRef.current?.abort();
    abortRef.current = null;
    setTurn({ ...BLANK_TURN, marks: {}, tier1: [], citations: [], variants: [] });
    setEditText('');
    // The player is mounted once and re-pointed; there is no second one to tear down. Stopping
    // it and hiding its host is what the legacy `ttsHost.innerHTML = ''` amounted to.
    playerRef.current?.pause();
    setTtsVisible(false);
    setSpeaking(false);
  }

  function newConversation() {
    resetTurn();
    setConvRef(`demo-${uuid().slice(0, 8)}`);
    setThread([]);
  }

  function setMode(next: Mode) {
    setModeState(next);
    resetTurn();
  }

  const sendLabel = t(mode === 'autopilot' ? 'cd.send.auto' : 'cd.send');

  /* ---------------------------------------------------------------------------------------
     1) POST /v1/chat/turns — the single ingest. 202 in ~15 ms, precompute fires behind it.
     --------------------------------------------------------------------------------------- */
  async function send() {
    const text = inbound.trim();
    setSendErr('');
    if (!fKey.trim() || !fTenant.trim()) { setSendErr(t('cd.needkey')); return; }
    if (!text) { setSendErr(t('cd.needtext')); return; }
    if (busyRef.current) return;

    resetTurn();
    setBusy(true);
    setThread(prev => [...prev, { role: 'customer', text }]);
    setInbound('');
    t0Ref.current = performance.now();

    if (mode === 'autopilot') {
      try { await answerTurn(text); } finally { setBusy(false); }
      return;
    }

    try {
      const r = await fetch(`${apiBase()}/v1/chat/turns`, {
        method: 'POST',
        headers: headers({ 'Content-Type': 'application/json', 'Idempotency-Key': uuid() }),
        body: JSON.stringify({
          conversation_ref: convRef,
          turn_ref: `t-${uuid()}`,
          role: 'customer',
          content: text,
          channel: fChannel,
          locale: fLocale,
          mode: 'assist',
        }),
      });
      /* The 202 is an ACKNOWLEDGEMENT, not a turn: it carries the ids, `precompute` and
         `retry_after_ms`, and nothing else (docs/CHAT_INTEGRATION.md §5.3). The legacy page
         also read `grounding`, `tier1` and `citations` off it — three reads that could never
         fire, and the first sign of the envelope-level confusion that §5.4 made real. */
      const d = await readResp<{ suggest_ref?: string | null; suggestion_ref?: string | null }>(r);
      mark('ingest');
      const ref = d.suggest_ref || d.suggestion_ref || null;
      setTurn(prev => ({ ...prev, suggestRef: ref }));
      if (!ref) throw new Error(`${t('cd.err')}: no suggest_ref returned`);
      await streamSuggestion(ref);
    } catch (e) {
      const message = apiMessage(e, t);
      setTurn(prev => ({ ...prev, err: message }));
      toast(message, 'err');
    } finally {
      setBusy(false);
    }
  }

  /* ---------------------------------------------------------------------------------------
     2+3+4) stream-ticket -> EventSource.

     NOTE on the 'error' event: a server-sent event NAMED "error" is dispatched on EventSource
     as an event of type "error" — the same type the browser uses for transport failures. They
     are told apart by `e.data`: a transport error carries none.
     --------------------------------------------------------------------------------------- */
  async function streamSuggestion(ref: string): Promise<void> {
    let ticket: string | null = null;
    try {
      const r = await fetch(`${apiBase()}/v1/chat/stream-tickets`, {
        method: 'POST',
        headers: headers({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ suggest_ref: ref }),
      });
      const d = await readResp<{ ticket?: string | null; stream_ticket?: string | null }>(r);
      ticket = d.ticket || d.stream_ticket || null;
      mark('ticket');
    } catch { /* fall through to the warm-path poll below */ }

    if (!ticket) {
      toast(t('cd.streamfail'), 'err');
      await pollSuggestion(ref);
      return;
    }

    const url = `${apiBase()}/v1/chat/stream?ticket=${encodeURIComponent(ticket)}`;
    await new Promise<void>(resolve => {
      const es = new EventSource(url);
      esRef.current = es;
      const finish = () => {
        es.close();
        if (esRef.current === es) esRef.current = null;
        resolve();
      };
      const payload = (ev: Event): unknown => safeJson(String((ev as MessageEvent).data ?? ''));

      es.addEventListener('grounding', ev => {
        const d = payload(ev);
        if (!isObject(d)) return;
        setTurn(prev => ({ ...prev, grounding: (isObject(d.grounding) ? d.grounding : d) as Grounding }));
        mark('grounding');
      });

      es.addEventListener('tier1', ev => {
        const d = payload(ev);
        if (d == null) return;
        // The wire name is `cards` (engine + replay agree); `tier1` is the envelope's name and
        // is kept as a fallback so this handler also works if the page is fed a Turn object.
        const bag = isObject(d) ? d : {};
        const cards = (Array.isArray(d) ? d : (bag.cards ?? bag.tier1 ?? [])) as Passage[];
        const cites = Array.isArray(bag.citations) ? bag.citations : null;
        setTurn(prev => ({
          ...prev,
          tier1: Array.isArray(cards) ? cards : [],
          ...(cites ? { citations: cites } : {}),
        }));
        mark('tier1');
      });

      es.addEventListener('delta', ev => {
        const d = payload(ev);
        if (!isObject(d)) return;
        if (turnRef.current.marks.first == null) mark('first');
        const index = typeof d.index === 'number' ? d.index : 0;
        const text = String(d.text ?? d.delta ?? '');
        setTurn(prev => ({ ...prev, variants: appendDelta(prev.variants, index, text) }));
      });

      es.addEventListener('suggestion', ev => {
        const d = payload(ev);
        if (!isObject(d)) return;
        setTurn(prev => ({
          ...prev,
          variants: upsertVariant(prev.variants, {
            index: typeof d.index === 'number' ? d.index : prev.variants.length,
            kind: typeof d.kind === 'string' ? d.kind : 'answer',
            text: typeof d.text === 'string' ? d.text : '',
            citations: Array.isArray(d.citations) ? d.citations as CiteRef[] : [],
            streaming: false,
          }),
        }));
      });

      es.addEventListener('done', ev => {
        const d = payload(ev);
        mark('done');
        setTurn(prev => absorbTurn(prev, envelopeOf(d)));
        sendShownOnce();
        finish();
      });

      es.addEventListener('error', ev => {
        // Named server "error" event (has data) vs. transport failure (does not).
        const raw = (ev as MessageEvent).data;
        const d = raw ? safeJson(String(raw)) : null;
        if (isObject(d)) {
          const message = String(d.detail ?? d.error ?? t('cd.err'));
          setTurn(prev => ({ ...prev, err: message }));
          toast(message, 'err');
          finish();
          return;
        }
        if (es.readyState === EventSource.CLOSED) { finish(); return; }
        // Transient: let EventSource retry, but stop it from reconnecting forever on a dead route.
        es.close();
        if (esRef.current === es) esRef.current = null;
        toast(t('cd.streamfail'), 'err');
        pollSuggestion(ref).then(resolve, resolve);
      });
    });
  }

  /* Warm path / fallback: GET /v1/chat/suggestions/{ref}. `state: running` carries
     retry_after_ms. This is also exactly what a reconnecting client does — there is no
     stream-resume protocol.

     ── THE FIX (docs/MIGRATION.md, defect 5) ────────────────────────────────────────────────
     The response is `{state, turn: <envelope>}`. The legacy loop read `d.grounding`, `d.tier1`,
     `d.citations` and `d.suggestions` off the TOP level and then handed the whole WRAPPER to
     `absorbTurn`, which read them off the top level again — so the warm path rendered an empty
     rail for a turn the server had answered in full, and `reopen` (the "25 ms warm read" the
     demo exists to show) displayed nothing at all. `envelopeOf` opens the wrapper once, here,
     and everything downstream sees only an envelope. */
  async function pollSuggestion(ref: string, { once = false }: { once?: boolean } = {}): Promise<void> {
    const deadline = performance.now() + 20000;
    for (;;) {
      const r = await fetch(`${apiBase()}/v1/chat/suggestions/${encodeURIComponent(ref)}`, {
        headers: headers(),
      });
      const body = await readResp<Wrapper>(r);
      const envelope = envelopeOf(body);
      const seen = turnRef.current;

      if (envelope?.grounding && !seen.grounding) {
        setTurn(prev => ({ ...prev, grounding: envelope.grounding as Grounding }));
        mark('grounding');
      }
      if (envelope?.tier1?.length && !seen.tier1.length) {
        setTurn(prev => ({ ...prev, tier1: envelope.tier1 as Passage[] }));
        mark('tier1');
      }
      if (envelope?.citations?.length) {
        setTurn(prev => ({ ...prev, citations: envelope.citations! }));
      }

      const state = stateOf(body, envelope);
      /* `state: "error"` is a documented terminal answer (a generation the reaper marked dead),
         and it carries the server's own `detail`. The legacy loop treated it as "not running",
         absorbed a wrapper with nothing in it and showed "No draft was generated" — which is
         the same silence as a healthy refusal. */
      if (state === 'error') {
        const message = body.detail || t('cd.err');
        setTurn(prev => ({ ...prev, err: message }));
        return;
      }
      if (once || state !== 'running' || performance.now() > deadline) {
        setTurn(prev => absorbTurn(prev, envelope));
        if (turnRef.current.marks.done == null) mark('done');
        sendShownOnce();
        return;
      }
      await sleep(retryDelay(body));
      if (!mountedRef.current) return;
    }
  }

  /* Re-open the conversation: one indexed SELECT off Postgres. This is the 25 ms number,
     measured in the browser rather than asserted in a slide. */
  async function reopen() {
    setSendErr('');
    const ref = turnRef.current.suggestRef;
    if (!ref) { setSendErr(t('cd.needturn')); return; }
    setReopening(true);
    t0Ref.current = performance.now();
    // `shownSent` stays true: these drafts were already counted as shown when they arrived.
    setTurn(prev => ({ ...prev, marks: {}, srvStages: null, shownSent: true }));
    try {
      await pollSuggestion(ref, { once: true });
      const warm = Math.round(performance.now() - t0Ref.current);
      setTurn(prev => {
        const marks: Record<string, number> = { ...prev.marks, warm };
        delete marks.done;                      // the warm read is the only timing on show here
        return { ...prev, marks };
      });
    } catch (e) {
      setSendErr(apiMessage(e, t));
    } finally {
      setReopening(false);
    }
  }

  /* =========================================================================================
     AUTOPILOT — POST /v1/chat/answer?stream=1

     The safety story this renders, honestly:
       * grounding block         — the gate's decision, not a vibe: method / top_score / hits.
       * refusal + 0 tokens      — an out-of-scope question costs nothing at all. The badge is
                                   derived from the SERVER's stage timings (no `llm` stage ⇒ no
                                   model call), not from a hardcoded string.
       * handoff summary         — what the operator inherits when the bot steps aside.
       * citation chips          — resolved against the server-held citations list.
     ========================================================================================= */
  async function answerTurn(text: string) {
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    let r: Response;
    try {
      r = await fetch(`${apiBase()}/v1/chat/answer?stream=1`, {
        method: 'POST',
        signal: ctrl.signal,
        headers: headers({
          'Content-Type': 'application/json',
          Accept: 'text/event-stream',
          'Idempotency-Key': uuid(),
        }),
        body: JSON.stringify({
          conversation_ref: convRef,
          turn_ref: `t-${uuid()}`,
          role: 'customer',
          content: text,
          channel: fChannel,
          locale: fLocale,
          mode: 'autopilot',
        }),
      });
    } catch (e) {
      const message = apiMessage(e, t);
      setTurn(prev => ({ ...prev, err: message }));
      toast(message, 'err');
      return;
    }
    mark('ingest');

    // A 4xx here is a configuration answer, not a crash: autopilot off, no public documents,
    // a key without chat:answer. Render it as the sentence an operator can act on.
    if (!r.ok) {
      let detail = '';
      try {
        const body = await r.text();
        const d = body.trimStart()[0] === '{' ? safeJson(body) : null;
        const raw = isObject(d) ? (d.detail ?? d.message ?? d.error) : null;
        detail = typeof raw === 'string' ? raw : raw != null ? JSON.stringify(raw) : body || '';
      } catch { detail = ''; }
      setTurn(prev => ({ ...prev, blocked: { status: r.status, detail: String(detail || '') } }));
      return;
    }

    const ct = (r.headers.get('content-type') || '').toLowerCase();
    if (!ct.includes('text/event-stream')) {
      // Blocking adapter (or a proxy that buffered the stream away). Same wrapper as the warm
      // read — `{state, turn}` — and the legacy page handed the whole thing to `absorbAnswer`,
      // which is the other half of defect 5: a non-streaming answer rendered a blank panel.
      const body = await r.text();
      const d = body.trimStart()[0] === '{' ? safeJson(body) : null;
      if (!d) {
        setTurn(prev => ({ ...prev, err: t('err.badresp') }));
        return;
      }
      mark('done');
      applyAnswer(envelopeOf(d));
      return;
    }

    await readSseBody(r, onAnswerEvent);
    abortRef.current = null;
  }

  /** The terminal envelope. Same object the blocking call returns and the same one the store
      files, so there is exactly one shape to trust. */
  function applyAnswer(envelope: Envelope | null) {
    const { next, botReply } = absorbAnswer(turnRef.current, envelope);
    setTurn(next);
    if (botReply) setThread(prev => [...prev, { role: 'bot', text: botReply }]);
  }

  function onAnswerEvent(name: string, d: SseData | null) {
    if (!d) return;
    if (name === 'open') {
      if (typeof d.suggest_ref === 'string' && d.suggest_ref) {
        const ref = d.suggest_ref;
        setTurn(prev => ({ ...prev, suggestRef: ref }));
      }
      return;
    }
    if (name === 'grounding') {
      setTurn(prev => ({ ...prev, grounding: (isObject(d.grounding) ? d.grounding : d) as Grounding }));
      mark('grounding');
      return;
    }
    if (name === 'delta') {
      if (turnRef.current.marks.first == null) mark('first');
      // Deltas are RAW model text — the authoritative, sanitized, citation-resolved text
      // arrives on `done` and REPLACES this. Never treat a delta as final.
      const chunk = String(d.text ?? d.delta ?? '');
      setTurn(prev => ({
        ...prev,
        answer: {
          text: (prev.answer?.text || '') + chunk,
          citations: prev.answer?.citations || [],
          answered_from_kb: prev.answer?.answered_from_kb || false,
          streaming: true,
        },
      }));
      return;
    }
    if (name === 'error') {
      const message = String(d.message ?? d.detail ?? t('cd.err'));
      setTurn(prev => ({ ...prev, err: message }));
      toast(message, 'err');
      return;   // a non-fatal error is still followed by `done`
    }
    if (name === 'done') {
      mark('done');
      applyAnswer(envelopeOf(d));
    }
  }

  /* ---------------------------------------------------------------------------------------
     Voice OUT — the EXISTING /v1/tts route (anonymous-capable). Georgian is handled entirely
     server-side there (eleven_v3 + a Georgian-capable voice), which is exactly why this page
     sends the locale and nothing else: no model id, no voice id, no client-side special case.
     One player, re-pointed with `load()`, so a second play bar can never appear.
     --------------------------------------------------------------------------------------- */
  async function speakAnswer() {
    const a = turnRef.current.answer;
    if (!a || !a.text || speakingRef.current) return;
    setSpeaking(true);
    try {
      const r = await fetch(`${apiBase()}/v1/tts`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: a.text.slice(0, 5000), language_code: fLocale }),
      });
      if (!r.ok) {
        const body = await r.text();
        const d = body.trimStart()[0] === '{' ? safeJson(body) : null;
        const detail = isObject(d) && typeof d.detail === 'string' ? d.detail : '';
        throw new Error(detail || t('cd.tts.fail'));
      }
      const url = URL.createObjectURL(await r.blob());
      setTtsVisible(true);
      playerRef.current?.load(url, 'reply.mp3');
    } catch (e) {
      toast(e instanceof Error && e.message ? e.message : t('cd.tts.fail'), 'err');
    } finally {
      setSpeaking(false);
    }
  }

  /* ---------------------------------------------------------------------------------------
     Voice IN — the shared <Recorder> records, then the clip goes to the EXISTING
     POST /v1/transcriptions and the transcript lands in the inbound box as if it had been
     typed. That route authenticates as the TENANT (X-API-Key), not as a chat integration,
     which is why there is a separate optional key field rather than a silent 401.
     --------------------------------------------------------------------------------------- */
  async function transcribeClip(file: File) {
    const key = fVoiceKey.trim();
    if (!key) { setStt({ text: t('cd.stt.needkey'), cls: ' err' }); return; }
    setStt({ text: t('cd.stt'), cls: '' });
    try {
      const fd = new FormData();
      fd.append('file', file, file.name);
      const r = await fetch(`${apiBase()}/v1/transcriptions`, {
        method: 'POST',
        headers: { 'X-API-Key': key },
        body: fd,
      });
      const d = await readResp<{ transcript?: string; language?: string }>(r);
      const text = (d.transcript || '').trim();
      if (!text) { setStt({ text: t('cd.stt.empty'), cls: ' err' }); return; }
      setInbound(text);
      setStt({ text: `${d.language ? `${d.language} · ` : ''}${text.length} chars`, cls: ' ok' });
    } catch (e) {
      setStt({ text: apiMessage(e, t), cls: ' err' });
    }
  }

  /* ---------------------------------------------------------------------------------------
     6) POST /v1/chat/feedback — 204. The curation loop is trained on exactly this signal, so
     every button here really fires; nothing is faked for the demo.
     --------------------------------------------------------------------------------------- */
  async function feedback(action: string, variantIndex: number, finalText: string | null) {
    const ref = turnRef.current.suggestRef;
    if (!ref) return;
    try {
      const r = await fetch(`${apiBase()}/v1/chat/feedback`, {
        method: 'POST',
        headers: headers({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({
          suggest_ref: ref,
          variant_index: variantIndex,
          // The envelope calls it `index`, the store `variant_index`, the column
          // `suggestion_index` — send both, and the spare is ignored.
          suggestion_index: variantIndex,
          action,
          final_text: finalText ?? null,
        }),
      });
      await readResp<void>(r);
    } catch (e) {
      toast(apiMessage(e, t), 'err');
    }
  }

  function sendShownOnce() {
    const s = turnRef.current;
    if (s.shownSent || !s.variants.length) return;
    setTurn(prev => ({ ...prev, shownSent: true }));
    s.variants.forEach(v => { void feedback('shown', v.index, null); });
  }

  async function variantAction(act: string, index: number) {
    const v = turnRef.current.variants.find(x => x.index === index);
    if (!v) return;
    if (act === 'insert') {
      setThread(prev => [...prev, { role: 'operator', text: v.text }]);
      await feedback('inserted', index, v.text);
      await feedback('sent_asis', index, v.text);
      toast(t('cd.fb.inserted'), 'ok');
      return;
    }
    if (act === 'edit') {
      setEditText(v.text);
      setTurn(prev => ({ ...prev, editing: index }));
      return;
    }
    if (act === 'cancel-edit') {
      setTurn(prev => ({ ...prev, editing: null }));
      setEditText('');
      return;
    }
    if (act === 'send-edit') {
      const final = editText;
      setTurn(prev => ({ ...prev, editing: null }));
      setEditText('');
      setThread(prev => [...prev, { role: 'operator', text: final }]);
      await feedback('edited_sent', index, final);
      toast(t('cd.fb.edited'), 'ok');
      return;
    }
    if (act === 'ignore') {
      await feedback('ignored', index, null);
      toast(t('cd.fb.ignored'), 'ok');
    }
  }

  /* Citation chips resolve against the SERVER-HELD hits list echoed in the envelope — a forged
     `[n]` in model text can only produce a wrong index, never a spoofed grounding state. */
  function showCitation(n: number) {
    const c = turn.citations.find(x => x.n === n) || {};
    const p = turn.tier1.find(x => x.n === n || (!!c.chunk_id && x.chunk_id === c.chunk_id)) || {};
    void showModal(close => (
      <div style={{ textAlign: 'left' }}>
        <h3 style={{ margin: '0 0 8px' }}>[{n}] {c.title || p.title || t('cd.cite')}</h3>
        <p className={styles.mono}>
          {t('cd.cite.doc')}: {c.document_id || '—'}<br />
          {t('cd.cite.chunk')}: {c.chunk_id || p.chunk_id || '—'}<br />
          {t('cd.cite.score')}: {c.score != null ? Number(c.score).toFixed(3) : '—'}
        </p>
        <p>
          {p.snippet || p.content
            || t(mode === 'autopilot' ? 'cd.cite.customer' : 'cd.cite.nosnip')}
        </p>
        <div className="actions">
          <button className="primary" type="button" data-autofocus onClick={() => close()}>
            {t('cd.close')}
          </button>
        </div>
      </div>
    ), { maxWidth: '560px' });
  }

  /* ---------------------------------------------------------------------------------------
     Rendering
     --------------------------------------------------------------------------------------- */

  // A stable ref callback: an inline one is a new identity every render, so React would detach
  // and re-attach it (and re-focus the box) on every keystroke.
  const focusEditor = useCallback((el: HTMLTextAreaElement | null) => { el?.focus(); }, []);

  const reasonLabel = (reason?: string | null) => {
    const key = reasonKey(reason);
    const value = t(key);
    return value === key ? (reason || '—') : value;   // no translation yet: show the raw reason
  };

  function stageChips(): ReactNode[] {
    const m = turn.marks;
    const out: ReactNode[] = [];
    const chip = (key: string, ms: number, extra = '') => (
      <span key={key} className={extra ? `${styles.stage} ${extra}` : styles.stage}>
        {t(`cd.stage.${key}`)} <b>{ms} ms</b>
      </span>
    );
    if (m.ingest != null) out.push(chip('ingest', m.ingest));
    if (m.ticket != null) out.push(chip('ticket', m.ticket));
    if (m.grounding != null) out.push(chip('grounding', m.grounding));
    if (m.tier1 != null) out.push(chip('tier1', m.tier1, styles.first));   // the claim: BEFORE any token
    if (m.first != null) out.push(chip('first', m.first));
    if (m.done != null) out.push(chip('done', m.done));
    if (m.warm != null) out.push(chip('warm', m.warm, styles.warm));
    if (turn.srvStages) {
      for (const [k, v] of Object.entries(turn.srvStages)) {
        if (typeof v !== 'number') continue;
        out.push(
          <span key={`srv-${k}`} className={styles.stage}>
            {t('cd.stage.srv')} · {k} <b>{Math.round(v)} ms</b>
          </span>,
        );
      }
    }
    return out;
  }

  function groundBanner(): ReactNode {
    const g = turn.grounding;
    if (!g) return null;
    const ok = !!g.grounded;
    return (
      <div className={`${styles.ground} ${ok ? styles.yes : styles.no}`}>
        <span className={`dot ${ok ? 'ok' : 'bad'}`} />
        <b style={{ color: 'var(--paper)' }}>{ok ? t('cd.grounded') : t('cd.ungrounded')}</b>
        <span className={styles.num}>{t('cd.method')}: {g.method || '—'}</span>
        <span className={styles.num}>
          {t('cd.top')}: {g.top_score != null ? Number(g.top_score).toFixed(3) : '—'}
        </span>
        <span className={styles.num}>{t('cd.hits')}: {g.hit_count ?? 0}</span>
        {g.kb_present === false ? <span className="pill error">{t('cd.kbempty')}</span> : null}
      </div>
    );
  }

  function citeChips(refs: CiteRef[] | null | undefined): ReactNode {
    const ns = citationNumbers(refs);
    if (!ns.length) return null;
    return (
      <div className={styles.cites}>
        {ns.map(n => {
          const c = turn.citations.find(x => x.n === n);
          return (
            <button key={n} type="button" className={styles.cite} onClick={() => showCitation(n)}>
              [{n}] {c?.title || t('cd.cite')}
            </button>
          );
        })}
      </div>
    );
  }

  function tier1Section(): ReactNode {
    const zero = !turn.tier1.length;
    return (
      <div className={styles.sec}>
        <div className={styles.secHead}>
          <h4>{t('cd.tier1')}</h4>
          <span className={zero ? `${styles.badge} ${styles.zero}` : styles.badge}>
            {t('cd.tier1.badge')}
          </span>
          {turn.marks.tier1 != null
            ? <span className={`${styles.stage} ${styles.first}`}>{turn.marks.tier1} ms</span>
            : null}
        </div>
        {zero
          ? <div className="empty">{t('cd.tier1.none')}</div>
          : turn.tier1.map((p, i) => (
            <div key={p.chunk_id || `${p.n ?? i}`} className={styles.pass}>
              <div className={styles.passT}>
                <span className={styles.passN}>[{p.n ?? ''}]</span>{p.title || '—'}
              </div>
              <div className={styles.passS}>{p.snippet || p.content || ''}</div>
            </div>
          ))}
      </div>
    );
  }

  function tokenLine(): ReactNode {
    const u = turn.usage || {};
    const inTok = u.input_tokens ?? 0;
    const outTok = u.output_tokens ?? 0;
    if (inTok === 0 && outTok === 0) {
      return <span className={`${styles.badge} ${styles.zero}`}>{t('cd.notokens')}</span>;
    }
    return <span className={styles.stage}>{t('cd.tokens')} <b>{inTok} / {outTok}</b></span>;
  }

  function variantsSection(): ReactNode {
    if (!turn.variants.length) {
      if (turn.grounding && turn.grounding.grounded === false) {
        const h = turn.handoff || {};
        return (
          <div className={styles.sec}>
            <div className={styles.secHead}><h4>{t('cd.refusal')}</h4>{tokenLine()}</div>
            <div className={styles.refusal}>
              <div className={styles.rH}>{t('cd.handoff')}</div>
              {turn.refusal ? <div>{turn.refusal}</div> : null}
              {h.reason ? <div className="hint">{t('cd.reason')}: {h.reason}</div> : null}
              {h.summary ? <div style={{ marginTop: 6 }}>{h.summary}</div> : null}
            </div>
          </div>
        );
      }
      return (
        <div className={styles.sec}>
          <div className={styles.secHead}><h4>{t('cd.variants')}</h4></div>
          <div className="empty">{busy ? t('cd.waiting') : t('cd.variants.none')}</div>
        </div>
      );
    }
    return (
      <div className={styles.sec}>
        <div className={styles.secHead}>
          <h4>{t('cd.variants')}</h4>
          {turn.usage ? tokenLine() : null}
        </div>
        {turn.variants.map(v => {
          const editing = turn.editing === v.index;
          return (
            <div key={v.index} className={styles.var}>
              <div className={styles.varHead}><span className={styles.varKind}>{v.kind}</span></div>
              <div className={v.streaming ? `${styles.varText} ${styles.streaming}` : styles.varText}>
                {v.text}
              </div>
              {citeChips(v.citations)}
              {editing
                ? (
                  <textarea
                    ref={focusEditor}
                    value={editText}
                    onChange={e => setEditText(e.target.value)}
                    aria-label={t('cd.edit')}
                  />
                )
                : null}
              <div className="actions">
                {editing
                  ? (
                    <>
                      <button className="primary" type="button" onClick={() => void variantAction('send-edit', v.index)}>
                        {t('cd.sendedit')}
                      </button>
                      <button className="ghost" type="button" onClick={() => void variantAction('cancel-edit', v.index)}>
                        {t('cd.cancelEdit')}
                      </button>
                    </>
                  )
                  : (
                    <>
                      <button className="primary" type="button" onClick={() => void variantAction('insert', v.index)}>
                        {t('cd.insert')}
                      </button>
                      <button className="ghost" type="button" onClick={() => void variantAction('edit', v.index)}>
                        {t('cd.edit')}
                      </button>
                      <button className="ghost" type="button" onClick={() => void variantAction('ignore', v.index)}>
                        {t('cd.ignore')}
                      </button>
                    </>
                  )}
              </div>
            </div>
          );
        })}
      </div>
    );
  }

  function handoffPanel(): ReactNode {
    const h = turn.handoff;
    if (!h || !h.recommended) return null;
    return (
      <div className={styles.handoff}>
        <div className={styles.hH}>
          {t('cd.handoff.h')}{h.reason ? ` · ${reasonLabel(h.reason)}` : ''}
        </div>
        <div className={styles.note} style={{ marginBottom: 4 }}>{t('cd.handoff.summary')}</div>
        <div>{h.summary || <span className={styles.note}>{t('cd.handoff.none')}</span>}</div>
        {h.goal ? <div className={styles.hGoal}>{t('cd.handoff.goal')}: {h.goal}</div> : null}
      </div>
    );
  }

  function answerSection(): ReactNode {
    const a = turn.answer;
    const grounded = !!turn.grounding?.grounded;
    const refused = !!a && !a.answered_from_kb && !grounded;
    if (!a) {
      return (
        <div className={styles.sec}>
          <div className={styles.secHead}><h4>{t('cd.answer')}</h4></div>
          <div className="empty">{busy ? t('cd.waiting') : t('cd.auto.empty')}</div>
        </div>
      );
    }
    return (
      <div className={styles.sec}>
        <div className={styles.secHead}>
          {refused
            ? (
              <>
                <h4>{t('cd.refuse')}</h4>
                {spentTokens(turn.srvStages)
                  ? null
                  : <span className={`${styles.badge} ${styles.zero}`}>{t('cd.notokens')}</span>}
              </>
            )
            : (
              <>
                <h4>{t('cd.answer')}</h4>
                <span className={a.answered_from_kb ? styles.badge : `${styles.badge} ${styles.zero}`}>
                  {t(a.answered_from_kb ? 'cd.answer.kb' : 'cd.answer.nokb')}
                </span>
              </>
            )}
        </div>
        <div className={refused || !a.answered_from_kb ? `${styles.answer} ${styles.ungrounded}` : styles.answer}>
          <div className={a.streaming ? `${styles.answerText} ${styles.streaming}` : styles.answerText}>
            {a.text}
          </div>
          {citeChips(a.citations)}
          <div className={styles.answerFoot}>
            <span className={styles.ai}>{t('cd.answer.ai')}</span>
            <button className="ghost" type="button" disabled={speaking} onClick={() => void speakAnswer()}>
              {speaking ? t('cd.speaking') : t('cd.speak')}
            </button>
          </div>
        </div>
        {refused
          ? <div className={styles.note} style={{ marginTop: 8 }}>{t('cd.refuse.note')}</div>
          : null}
        {handoffPanel()}
      </div>
    );
  }

  function blockedPanel(): ReactNode {
    const b = turn.blocked;
    if (!b) return null;
    return (
      <div className={styles.blocked}>
        <div className={styles.bH}>{t('cd.blocked')}</div>
        <div>{t(blockedKey(b))}</div>
        {b.detail
          ? (
            <div className={styles.mono} style={{ marginTop: 8 }}>
              {b.status ? `HTTP ${b.status} · ` : ''}{b.detail}
            </div>
          )
          : null}
      </div>
    );
  }

  function suggestRefLine(): ReactNode {
    if (!turn.suggestRef) return null;
    return (
      <div className={styles.note} style={{ marginTop: 14 }}>
        suggest_ref <span className={styles.mono}>{turn.suggestRef}</span>
      </div>
    );
  }

  function rail(): ReactNode {
    if (mode === 'autopilot') {
      if (turn.blocked) return blockedPanel();
      if (!turn.grounding && !turn.answer && !turn.err && !busy) {
        return <div className="empty">{t('cd.auto.empty')}</div>;
      }
      return (
        <>
          {turn.err ? <div className="msg err">{turn.err}</div> : null}
          {groundBanner()}
          {answerSection()}
          {suggestRefLine()}
        </>
      );
    }
    if (!turn.suggestRef && !turn.grounding && !turn.tier1.length && !turn.err) {
      return <div className="empty">{t('cd.rail.empty')}</div>;
    }
    return (
      <>
        {turn.err ? <div className="msg err">{turn.err}</div> : null}
        {groundBanner()}
        {tier1Section()}
        {variantsSection()}
        {suggestRefLine()}
      </>
    );
  }

  return (
    <>
      <Header tag="Copilot" />
      <main className={styles.main}>
        {/* ---------------- credentials + conversation ---------------- */}
        <div className="card">
          <div className={styles.head}>
            <h3>{t('cd.creds')}</h3>
            <span className={styles.note}>{t('cd.note')}</span>
          </div>
          <div className="row">
            <div style={{ flex: 2 }}>
              <label htmlFor="f_key">{t('cd.f.key')}</label>
              <input
                id="f_key" type="password" autoComplete="off" spellCheck={false} placeholder="cqi_…"
                value={fKey} onChange={e => setFKey(e.target.value)}
              />
            </div>
            <div>
              <label htmlFor="f_tenant">{t('cd.f.tenant')}</label>
              <input
                id="f_tenant" autoComplete="off" spellCheck={false}
                value={fTenant} onChange={e => setFTenant(e.target.value)}
              />
            </div>
            <div>
              <label htmlFor="f_expect">{t('cd.f.expect')}</label>
              <input
                id="f_expect" autoComplete="off" spellCheck={false}
                value={fExpect} onChange={e => setFExpect(e.target.value)}
              />
            </div>
          </div>
          <div className="row" style={{ marginTop: 12 }}>
            <div style={{ maxWidth: 150 }}>
              <label htmlFor="f_locale">{t('cd.f.locale')}</label>
              <Select
                id="f_locale" value={fLocale} onChange={setFLocale}
                options={LOCALES} ariaLabel={t('cd.f.locale')}
              />
            </div>
            <div style={{ maxWidth: 190 }}>
              <label htmlFor="f_channel">{t('cd.f.channel')}</label>
              <Select
                id="f_channel" value={fChannel} onChange={setFChannel}
                options={CHANNELS} ariaLabel={t('cd.f.channel')}
              />
            </div>
            <div>
              <label>{t('cd.conv')}</label>
              <div className={styles.mono} style={{ paddingTop: 9 }}>{convRef}</div>
            </div>
          </div>
          <div className="row" style={{ marginTop: 12 }}>
            <div>
              <label htmlFor="f_voicekey">{t('cd.f.voicekey')}</label>
              <input
                id="f_voicekey" type="password" autoComplete="off" spellCheck={false}
                placeholder="X-API-Key"
                value={fVoiceKey} onChange={e => setFVoiceKey(e.target.value)}
              />
            </div>
          </div>
          <div className="actions">
            <button className="ghost" type="button" onClick={newConversation}>{t('cd.newconv')}</button>
            {/* The warm read is a copilot affordance — it re-reads a cached *suggestion* row.
                There is no equivalent for an answer that has already been said to a customer,
                so the button is absent in autopilot rather than disabled. (The legacy page
                toggled `style.display` for this: the `hidden` ATTRIBUTE would have lost to the
                explicit `display` brand.css gives buttons. Not rendering it at all sidesteps
                that entirely.) */}
            {mode === 'autopilot'
              ? null
              : (
                <button className="ghost" type="button" disabled={reopening} onClick={() => void reopen()}>
                  {t('cd.reopen')}
                </button>
              )}
          </div>
          <div className="hint">{t('cd.creds.hint')}</div>
          <div className="hint">{t('cd.voicekey.hint')}</div>
        </div>

        {/* ---------------- mode switch: copilot vs autopilot ---------------- */}
        <div className="card">
          <div className={styles.head}><h3>{t('cd.mode')}</h3></div>
          <div className={styles.mode}>
            <div className="tabs" style={{ margin: 0 }}>
              <button
                type="button" className={mode === 'assist' ? 'tab active' : 'tab'}
                onClick={() => setMode('assist')}
              >
                {t('cd.mode.assist')}
              </button>
              <button
                type="button" className={mode === 'autopilot' ? 'tab active' : 'tab'}
                onClick={() => setMode('autopilot')}
              >
                {t('cd.mode.autopilot')}
              </button>
            </div>
            <span className={styles.modeNote}>{t(`cd.mode.note.${mode}`)}</span>
          </div>
        </div>

        <div className={styles.grid}>
          {/* ---------------- thread ---------------- */}
          <section className="card">
            <div className={styles.head}><h3>{t('cd.thread')}</h3></div>
            <div className={styles.threadList} ref={threadBoxRef}>
              {!thread.length
                ? <div className="empty">{t('cd.thread.empty')}</div>
                : thread.map((m, i) => (
                  <div
                    key={i}
                    className={`${styles.msg} ${m.role === 'customer' ? styles.customer : styles.operator}`}
                  >
                    <span className={styles.who}>{t(`cd.role.${m.role}`)}</span>{m.text}
                  </div>
                ))}
            </div>
            <div style={{ marginTop: 14 }}>
              <label htmlFor="inbound">{t('cd.inbound')}</label>
              <textarea
                id="inbound"
                value={inbound}
                placeholder={t('cd.inbound.ph')}
                style={{ minHeight: 88 }}
                onChange={e => setInbound(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) void send(); }}
              />
            </div>
            {/* Voice IN: the shared recorder, then the EXISTING /v1/transcriptions. The recorder
                owns its own status line ("recording 0:04"); the second one below it is this
                page's, and carries what the transcription came back with. */}
            <div className={styles.voice}>
              <Recorder onReady={file => void transcribeClip(file)} />
              <span className={`rec-status hint${stt.cls}`}>{stt.text}</span>
            </div>
            <div className="actions">
              <button className="primary" type="button" disabled={busy} onClick={() => void send()}>
                {busy
                  ? <><span className="spinner" />{`${sendLabel}…`}</>
                  : sendLabel}
              </button>
            </div>
            {sendErr ? <div className="msg err">{sendErr}</div> : null}
          </section>

          {/* ---------------- copilot rail / autopilot answer ---------------- */}
          <section className="card">
            <div className={styles.head}>
              <h3>{t(mode === 'autopilot' ? 'cd.rail.auto' : 'cd.rail')}</h3>
            </div>
            <div className={styles.stages}>{stageChips()}</div>
            <div>{rail()}</div>
            {/* Voice OUT lives OUTSIDE the rail. The legacy reason was that `renderRail()`
                replaced that subtree on every event and would have torn down a playing
                <audio>; React would not, but the rule it protects still holds — ONE player per
                surface, re-pointed with `load()`, because a second one leaves a second play bar
                on the page (docs/MIGRATION.md, "Deliberate decisions"). It is hidden rather
                than unmounted so the handle survives a turn reset. */}
            <div className={styles.ttsHost} style={ttsVisible ? undefined : { display: 'none' }}>
              <AudioPlayer ref={playerRef} />
            </div>
          </section>
        </div>
      </main>
    </>
  );
}
