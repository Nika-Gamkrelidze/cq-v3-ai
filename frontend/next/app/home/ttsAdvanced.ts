/* Advanced voice settings — the rules, with no React and no DOM.
   =============================================================
   A port of `index.html`'s `advLoad` / `advCaps` / `advEffectiveModel` / `advBody`, kept as
   plain functions so the two things most likely to regress can be tested directly:

     * the STORED blob is sanitised field by field. A stale or hand-edited `cq_tts_adv` must
       never put an out-of-range number into a request — the server 422s every clip, and the
       visitor has no way to see why, because the offending value is in localStorage.
     * the REQUEST carries only the controls the resolved model actually honours. A speed
       remembered from Multilingual v2 sent to Eleven v3 is a field v3 rejects; a `style`
       remembered the same way is silently ignored. Sending only what is on screen is what
       makes "the panel shows what the model takes" true rather than decorative.

   MIRRORED: the legacy block is identical in `index.html` and `account.html` — when the
   account page ports, it should import THIS file rather than copy it again. */

export const ADV_KEY = 'cq_tts_adv';

export interface AdvState {
  /** Whether the <details> is open. A view preference, remembered like the rest. */
  open: boolean;
  /** Explicit model pick; '' means Auto (resolve from the selected language). */
  model: string;
  /** Off means "the voice speaks with the settings its creator chose" — nothing is sent. */
  custom: boolean;
  /** v3's three-way stability, already in the 0 / 0.5 / 1 the API takes. */
  preset: number;
  /** Everything else is a percent (0..100) or, for speed, a multiplier. */
  stability: number;
  similarity: number;
  style: number;
  boost: boolean;
  speed: number;
  forcelang: boolean;
}

export const ADV_DEFAULTS: AdvState = {
  open: false, model: '', custom: false, preset: 0.5, stability: 50,
  similarity: 75, style: 0, boost: true, speed: 1, forcelang: true,
};

/** What one model accepts, as `GET /tts/models` reports it (`supports`). `language_code` is a
    word, not a boolean: 'enforced' (Flash/Turbo — the only family that honours it), 'rejected'
    (v3 400s on one) or 'ignored'. */
export interface ModelCaps {
  presets: boolean;
  style: boolean;
  speaker_boost: boolean;
  speed: boolean;
  language_code: string;
}

export interface TtsModel {
  model_id: string;
  name?: string;
  description?: string;
  max_chars?: number;
  languages?: string[];
  supports?: ModelCaps;
}

/** The v3 family, by id prefix. The presets, the absent speed control and the language_code
    rejection are documented v3 behaviour that the model record does not advertise per model,
    which is why this is a prefix rule and not a field lookup. */
export const isV3 = (id: string | null | undefined): boolean => /^eleven_v3/.test(id || '');

function advNum(v: unknown, lo: number, hi: number, def: number): number {
  const n = Number(v);
  return Number.isFinite(n) && n >= lo && n <= hi ? n : def;
}

/** Read the remembered panel, sanitising every field. Never throws: a corrupt blob, a private
    window with no storage at all and a first visit are all "the defaults". */
export function advLoad(raw?: string | null): AdvState {
  const d: AdvState = { ...ADV_DEFAULTS };
  try {
    const text = raw === undefined ? localStorage.getItem(ADV_KEY) : raw;
    const s = JSON.parse(text || 'null') as Partial<AdvState> | null;
    if (s && typeof s === 'object') {
      d.open = !!s.open;
      d.custom = !!s.custom;
      d.model = typeof s.model === 'string' ? s.model : '';
      // The preset is snapped, not clamped: v3 rejects anything but 0 / 0.5 / 1, so a slider
      // value left over from a non-v3 model has to land on one of the three.
      const p = advNum(s.preset, 0, 1, 0.5);
      d.preset = p < 0.25 ? 0 : p > 0.75 ? 1 : 0.5;
      d.stability = advNum(s.stability, 0, 100, 50);
      d.similarity = advNum(s.similarity, 0, 100, 75);
      d.style = advNum(s.style, 0, 100, 0);
      d.speed = advNum(s.speed, 0.7, 1.2, 1);
      d.boost = s.boost !== false;
      d.forcelang = s.forcelang !== false;
    }
  } catch { /* unparseable, or no storage: the defaults are the answer */ }
  return d;
}

export function advSave(adv: AdvState): void {
  try { localStorage.setItem(ADV_KEY, JSON.stringify(adv)); } catch { /* private mode */ }
}

/** Which model this request will really use: the explicit pick, or what Auto resolves to for
    the selected language.

    `langModel` is `GET /languages`' own `model` field — the API's answer first. The hardcoded
    pair below only covers a `/languages` that predates that field, and mirrors what the server
    has always done for Auto. */
export function advEffectiveModel(
  pick: string,
  langCode: string,
  langModel: Record<string, string>,
): string {
  if (pick) return pick;
  return langModel[langCode] || (langCode === 'ka' ? 'eleven_v3' : 'eleven_multilingual_v2');
}

/** What the effective model accepts. Falls back to the documented shape of the family when the
    model list failed to load or the id is not in it — the form still renders the right controls
    for the two models this product actually synthesizes with. */
export function advCaps(id: string, models: TtsModel[]): ModelCaps {
  const hit = models.find(m => m && m.model_id === id);
  if (hit && hit.supports) return hit.supports;
  return isV3(id)
    ? { presets: true, style: false, speaker_boost: true, speed: false, language_code: 'rejected' }
    : { presets: false, style: true, speaker_boost: true, speed: true, language_code: 'ignored' };
}

export interface AdvBody {
  model_id?: string;
  voice_settings?: {
    stability: number;
    similarity_boost: number;
    style?: number;
    use_speaker_boost?: boolean;
    speed?: number;
  };
  enforce_language?: boolean;
}

/** The fields the panel contributes to a `POST /tts` body.

    Slider percentages become 0..1, and ONLY the controls currently on screen are included —
    so a value remembered for another model (say, speed while v3 is in play) is never sent to
    one that rejects it. With "Customise" off nothing the panel owns is sent at all, which is
    what makes the default clip byte-for-byte the request this page made before the panel
    existed. */
export function advBody(adv: AdvState, caps: ModelCaps): AdvBody {
  const out: AdvBody = {};
  if (adv.model) out.model_id = adv.model;
  if (!adv.custom) return out;              // voice defaults: nothing the panel owns is sent
  out.voice_settings = {
    stability: caps.presets ? adv.preset : adv.stability / 100,
    similarity_boost: adv.similarity / 100,
  };
  if (caps.style) out.voice_settings.style = adv.style / 100;
  if (caps.speaker_boost) out.voice_settings.use_speaker_boost = !!adv.boost;
  if (caps.speed) out.voice_settings.speed = Math.round(adv.speed * 100) / 100;
  if (caps.language_code === 'enforced') out.enforce_language = !!adv.forcelang;
  return out;
}

/** The value badge beside a slider: a multiplier for speed, a percentage for everything else. */
export function advFmt(key: string, v: number): string {
  return key === 'speed' ? `${Number(v).toFixed(2)}×` : `${Math.round(v)}%`;
}

/** Reset returns only the VOICE values. The model pick, the Customise switch and whether the
    panel is open are CHOICES, not settings — resetting them would move the visitor off the
    model they deliberately picked and fold the panel they deliberately opened. */
export function advReset(adv: AdvState): AdvState {
  return {
    ...adv,
    preset: ADV_DEFAULTS.preset,
    stability: ADV_DEFAULTS.stability,
    similarity: ADV_DEFAULTS.similarity,
    style: ADV_DEFAULTS.style,
    boost: ADV_DEFAULTS.boost,
    speed: ADV_DEFAULTS.speed,
    forcelang: ADV_DEFAULTS.forcelang,
  };
}
