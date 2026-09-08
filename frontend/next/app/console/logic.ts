/* The console's decisions, with no React and no DOM around them.
   =============================================================
   Everything here was a few lines inside a render function in `admin.html`, which is why none
   of it was ever tested: `killRowState` read two module-level variables, and the default-bot
   form's fill/save pair read and wrote twenty DOM ids. Pulled out, they are ordinary functions
   over ordinary values — and the rules inside them are the ones that must not drift, because
   each is a safe-direction default that reads like an arbitrary choice.

   `lib/__tests__/console.test.mts` covers them. */

/* ---------------------------------------------------------------- kill switch */

export interface KillState {
  global_disabled: boolean;
  /** Client ids, as strings — the ids arrive as uuids and are compared against `String(x.id)`. */
  disabled_clients: string[];
}

export interface KillRow {
  id: string;
  name: string;
  /** The workspace's OWN `autopilot_enabled`, read from its chat config. */
  autopilot: boolean;
  /** False when that config could not be read — the row is shown, flagged, and still stoppable. */
  reachable: boolean;
}

/** The three states a row can be in, as a pill class plus the key that names it.

    Order matters and is the point of the function: a workspace whose own autopilot is OFF reads
    as "off" whatever the brake says, because telling an operator a bot is "stopped" when the
    customer never switched it on invites them to resume something that was never running. Only
    once the workspace has it on does the brake — global or per-client — become the answer. */
export function killRowState(row: KillRow, state: KillState): { cls: string; key: string } {
  if (!row.autopilot) return { cls: 'notinkb', key: 'kill.state.off' };
  if (state.global_disabled || state.disabled_clients.includes(row.id)) {
    return { cls: 'error', key: 'kill.state.stopped' };
  }
  return { cls: 'ready', key: 'kill.state.live' };
}

/** Add or remove one client from the brake list, without duplicating an id already on it. */
export function killListAfter(state: KillState, id: string, stop: boolean): string[] {
  const set = new Set(state.disabled_clients);
  if (stop) set.add(id); else set.delete(id);
  return [...set];
}

/* ------------------------------------------------------------- chat key state */

/** A secret has three terminal looks and one ordinary one.

    `expires` is the interesting one: it is the ROTATED-OUT key during its overlap window, still
    verifying, and it is the row an operator watches while the chat service switches over. Both
    `revoked` and an elapsed `expires_at` render as dead, but they are separate keys because
    "deactivated" and "the overlap ran out" are different things to have happened. */
export type KeyState = 'revoked' | 'expired' | 'expires' | 'live';

export function keyState(
  secret: { revoked_at?: string | null; expires_at?: string | null },
  now: number = Date.now(),
): KeyState {
  if (secret.revoked_at) return 'revoked';
  const exp = secret.expires_at ? new Date(secret.expires_at) : null;
  if (!exp || Number.isNaN(exp.getTime())) return 'live';
  return exp.getTime() <= now ? 'expired' : 'expires';
}

/* --------------------------------------------------- per-account limit overrides */

/** The four caps a registered account may have overridden, with the label each is shown under.
    Order is the order they appear in the dialog. */
export const OVERRIDE_KEYS: readonly (readonly [string, string])[] = [
  ['max_analyses_per_day', 'adm.maxanalyses'],
  ['max_audio_mb', 'adm.maxmb'],
  ['max_tts_per_day', 'adm.maxtts'],
  ['max_conversions_per_day', 'pb.reg.maxconv'],
] as const;

/** Read the override dialog back.

    THE PUT REPLACES THE BLOB, so this form is the whole truth for one account: a field left
    empty is not "unchanged", it is "use the tier's number", and the key is simply absent from
    the result. Anything that is neither empty nor a non-negative whole number fails the whole
    submit rather than being coerced — `services/limits.py` IGNORES a cap it cannot parse, which
    would leave the operator believing in a limit they never got. */
export function parseOverrides(raw: Readonly<Record<string, string>>): {
  ok: boolean;
  limits: Record<string, number>;
} {
  const limits: Record<string, number> = {};
  for (const [key] of OVERRIDE_KEYS) {
    const value = (raw[key] || '').trim();
    if (value === '') continue;                 // no override — the tier decides
    const n = parseInt(value, 10);
    if (!Number.isFinite(n) || n < 0) return { ok: false, limits: {} };
    limits[key] = n;
  }
  return { ok: true, limits };
}

/* -------------------------------------------------------------- the default bot */

export const BOT_LANGS = ['en', 'ka', 'ru'] as const;
export type BotLang = (typeof BOT_LANGS)[number];

/** The four rate caps, as [form field, the key they are stored under in `settings.limits`]. */
export const BOT_CAPS: readonly (readonly [string, string])[] = [
  ['tenant', 'tenant_per_minute'],
  ['enduser', 'enduser_per_hour'],
  ['answer_tenant', 'answer_tenant_per_minute'],
  ['answer_enduser', 'answer_enduser_per_hour'],
] as const;

export type DisclosureMode = 'first' | 'always' | 'off';
const DISCLOSURE_MODES: readonly string[] = ['first', 'always', 'off'];

type LangText = Record<string, string>;

/** The form, as the operator holds it: every number is the string that is in the input, because
    an emptied box has to stay empty rather than becoming a zero on the next render. */
export interface BotForm {
  persona: string;
  greeting: LangText;
  refusal: LangText;
  disclosure: LangText;
  languages: string[];
  escalation: string;
  minScore: string;
  minHits: string;
  topK: string;
  suggestions: string;
  maxChars: string;
  caps: Record<string, string>;
  disclosureMode: DisclosureMode;
  allowGeneral: boolean;
  handoffSummary: boolean;
}

/** What the route returns. Everything is optional because a server older than this console may
    not send a field, and `settings` is a free-form blob by design. */
export interface BotConfig {
  persona?: string | null;
  greeting?: unknown;
  refusal_copy?: unknown;
  languages?: unknown;
  canned?: unknown;
  min_score?: unknown;
  min_hits?: unknown;
  top_k?: unknown;
  suggestion_count?: unknown;
  settings?: unknown;
  source?: string;
  updated_at?: string | null;
  updated_by?: string | null;
}

const obj = (v: unknown): Record<string, unknown> =>
  v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : {};

const text = (v: unknown): string => (typeof v === 'string' ? v : '');

/** A number field's initial value: the stored one, or the built-in default spelled out.

    `??`, exactly as the legacy `$('db_minscore').value = dbCfg.min_score ?? 0.35` — so a stored
    **0** stays 0 rather than being replaced by the default a truthiness test would restore. */
const numText = (v: unknown, fallback: number): string =>
  v === null || v === undefined ? String(fallback) : String(v);

/** Fill the form from a config.

    Four defaults here are safe-direction choices, not conveniences:

      * an UNKNOWN disclosure mode falls back to disclosing (`first`), never to silence;
      * `allow_general_knowledge` is true only when it is literally `true` — a missing key means
        false, and `??`-defaulting it would let an absent field switch the risky behaviour on;
      * `handoff_summary` is the mirror image: on unless it is literally `false`;
      * a cap that is not set stays an EMPTY string, which is what "use the built-in" looks like
        in the box, rather than a zero that would read as "none allowed".

    `min_score` and friends are read from the TOP LEVEL and written back into `settings` — the
    server lifts those four knobs out of the blob on the way out (`chat_store._LIFTED_KNOBS`),
    so the flat field is the current value and the blob is where it lives. */
export function formFromConfig(cfg: BotConfig | null | undefined): BotForm {
  const c = obj(cfg);
  const s = obj(c.settings);
  const greeting = obj(c.greeting);
  const refusal = obj(c.refusal_copy);
  const disclosure = obj(s.disclosure);
  const limits = obj(s.limits);
  const langs = Array.isArray(c.languages) && c.languages.length
    ? (c.languages as unknown[]).map(String)
    : [...BOT_LANGS];

  const perLang = (src: Record<string, unknown>): LangText =>
    Object.fromEntries(BOT_LANGS.map(l => [l, text(src[l])]));

  const kw = (s as { escalation_keywords?: unknown }).escalation_keywords;

  return {
    persona: text(c.persona),
    greeting: perLang(greeting),
    refusal: perLang(refusal),
    disclosure: perLang(disclosure),
    languages: langs,
    escalation: Array.isArray(kw) ? kw.join(', ') : text(kw),
    minScore: numText(c.min_score, 0.35),
    minHits: numText(c.min_hits, 1),
    topK: numText(c.top_k, 8),
    suggestions: numText(c.suggestion_count, 2),
    maxChars: numText(s.max_reply_chars, 1200),
    caps: Object.fromEntries(BOT_CAPS.map(([field, key]) => {
      const v = limits[key];
      return [field, v === null || v === undefined ? '' : String(v)];
    })),
    disclosureMode: DISCLOSURE_MODES.includes(text(s.disclosure_mode))
      ? (s.disclosure_mode as DisclosureMode)
      : 'first',
    allowGeneral: s.allow_general_knowledge === true,
    handoffSummary: s.handoff_summary !== false,
  };
}

const num = (raw: string, fallback: number): number => {
  const v = parseFloat(raw);
  return Number.isFinite(v) ? v : fallback;
};

const optInt = (raw: string): number | undefined => {
  const v = parseInt(raw, 10);
  return Number.isFinite(v) ? v : undefined;
};

/** Build the PUT body.

    Two rules that look like omissions and are not:

      * ONLY LANGUAGES WITH TEXT ARE SENT. An empty greeting/refusal/disclosure box means "use
        the built-in wording", and sending `''` would shadow that wording with silence for every
        workspace inheriting this default — which is never what an empty box means.
      * THE CAPS BLOB IS REBUILT, not spread from the previous one. A cap the operator cleared
        must go back to the built-in default, and spreading `prev.limits` would keep it alive as
        the number it used to be.

    Everything ELSE in `settings` IS spread from the previous blob, so a knob this form does not
    know about (a newer server's, or one only the tenant form writes) survives a save here. */
export function payloadFromForm(form: BotForm, cfg: BotConfig | null | undefined) {
  const c = obj(cfg);
  const prev = obj(c.settings);

  const nonEmpty = (src: LangText): LangText => {
    const out: LangText = {};
    for (const l of BOT_LANGS) {
      const v = (src[l] || '').trim();
      if (v) out[l] = v;
    }
    return out;
  };

  const limits: Record<string, number> = {};
  for (const [field, key] of BOT_CAPS) {
    const v = optInt(form.caps[field] || '');
    if (v !== undefined) limits[key] = v;
  }

  return {
    persona: form.persona.trim() || null,
    greeting: nonEmpty(form.greeting),
    refusal_copy: nonEmpty(form.refusal),
    languages: form.languages,
    canned: Array.isArray(c.canned) ? c.canned : [],
    settings: {
      ...prev,
      min_score: num(form.minScore, 0.35),
      min_hits: num(form.minHits, 1),
      top_k: num(form.topK, 8),
      suggestion_count: num(form.suggestions, 2),
      max_reply_chars: num(form.maxChars, 1200),
      escalation_keywords: form.escalation.split(',').map(s => s.trim()).filter(Boolean),
      allow_general_knowledge: form.allowGeneral,
      handoff_summary: form.handoffSummary,
      disclosure_mode: form.disclosureMode,
      disclosure: nonEmpty(form.disclosure),
      limits,
    },
  };
}

/* ------------------------------------------------------------------- the source pill

   'demo' and 'builtin' both mean NOTHING IS SAVED HERE YET — the editor is showing a proposal
   (the demo tenant's rubric, or the code's own starter). Saying so in the pill is the difference
   between an operator editing the default and an operator believing they already have one. */

export const RUBRIC_SOURCE: Record<string, readonly [string, string]> = {
  stored: ['pb.src.stored', 'ready'],
  demo: ['pb.src.demo', 'processing'],
  builtin: ['pb.src.builtin', 'notinkb'],
};

export const BOT_SOURCE: Record<string, readonly [string, string]> = {
  stored: ['pb.defbot.source.stored', 'ready'],
  builtin: ['pb.defbot.source.builtin', 'notinkb'],
};

export function sourcePill(
  table: Record<string, readonly [string, string]>,
  source: string | undefined,
  fallback: string,
): { key: string; cls: string } {
  const [key, cls] = table[source || ''] || table[fallback];
  return { key, cls };
}
