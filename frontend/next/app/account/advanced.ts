/* Advanced voice settings — the arithmetic, with no React and no DOM in it.
   =======================================================================
   MIRRORED: this logic is identical on the public page and on this one — change both. It
   lives beside the page rather than in `lib/` for exactly that reason: two forms are its only
   callers, and `brand.js` deliberately never grew a home for it.

   The Model dropdown is filled from `GET /tts/models`. The EFFECTIVE model is the explicit
   pick or, on Auto, the one the server resolves to for the selected language (the `model`
   field of `GET /languages`) — and only the controls that model honours are rendered: Eleven
   v3 has a three-way stability preset and no style or speed at all, and only the Flash family
   enforces `language_code`. Everything sits behind "Customise" because a voice's own settings
   are the ones its creator tuned: until that is ticked the request carries no `voice_settings`,
   so the default clip is exactly what it was before. The panel's state is remembered per
   browser under `cq_tts_adv`. */

/** localStorage key, shared with the legacy pages and with the public page's port. */
export const ADV_KEY = 'cq_tts_adv';

export interface AdvState {
  /** Whether the <details> is open. A view preference, not a setting. */
  open: boolean;
  /** Explicit model id, or '' for Auto. A choice, not a setting — `reset` leaves it alone. */
  model: string;
  /** Whether the voice settings below are sent at all. */
  custom: boolean;
  /** v3's three-way stability: 0 creative, 0.5 natural, 1 robust. */
  preset: number;
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

/** Only the VOICE values — what the "reset" link puts back. The model pick and the Customise
    switch are choices the visitor made about this form, not settings about a voice. */
export const ADV_VOICE_KEYS = ['preset', 'stability', 'similarity', 'style', 'boost', 'speed', 'forcelang'] as const;

/** A number inside its range, or the default. */
function advNum(v: unknown, lo: number, hi: number, def: number): number {
  const n = Number(v);
  return Number.isFinite(n) && n >= lo && n <= hi ? n : def;
}

/** Read the remembered panel out of a raw localStorage string.

    Sanitised FIELD BY FIELD, not merged wholesale: a stale or hand-edited blob must never put
    an out-of-range number into a request, because the server would then 422 every clip and the
    visitor would have no way to discover why. Takes the raw string so it is testable without a
    browser — `advLoadFrom(localStorage.getItem(ADV_KEY))` at the call site. */
export function advLoadFrom(raw: string | null): AdvState {
  const d: AdvState = { ...ADV_DEFAULTS };
  try {
    const s = JSON.parse(raw || 'null') as Partial<Record<keyof AdvState, unknown>> | null;
    if (s && typeof s === 'object') {
      d.open = !!s.open;
      d.custom = !!s.custom;
      d.model = typeof s.model === 'string' ? s.model : '';
      // The preset is a THREE-VALUE control, so a stored 0.61 has to land on one of them
      // rather than on a slider position the segmented buttons cannot show.
      const p = advNum(s.preset, 0, 1, 0.5);
      d.preset = p < 0.25 ? 0 : p > 0.75 ? 1 : 0.5;
      d.stability = advNum(s.stability, 0, 100, 50);
      d.similarity = advNum(s.similarity, 0, 100, 75);
      d.style = advNum(s.style, 0, 100, 0);
      d.speed = advNum(s.speed, 0.7, 1.2, 1);
      d.boost = s.boost !== false;
      d.forcelang = s.forcelang !== false;
    }
  } catch { /* nothing stored, or not JSON: the defaults are the answer */ }
  return d;
}

/** Whether a model id belongs to the Eleven v3 family. */
export const isV3 = (id: string | null | undefined): boolean => /^eleven_v3/.test(id || '');

/** What one model accepts, as `GET /tts/models` reports it. */
export interface ModelCaps {
  presets: boolean;
  style: boolean;
  speaker_boost: boolean;
  speed: boolean;
  /** 'enforced' is the only value that puts the language checkbox on screen. */
  language_code: string;
}

export interface TtsModel {
  model_id: string;
  name?: string;
  supports?: Partial<ModelCaps>;
}

/** The model Auto resolves to for a language, or the explicit pick.

    The API's answer first; the hardcoded pair only covers a `/languages` without the `model`
    field and mirrors what the server has always done for Auto. */
export function effectiveModel(
  pick: string,
  langCode: string,
  langModel: Readonly<Record<string, string>>,
): string {
  if (pick) return pick;
  return langModel[langCode] || (langCode === 'ka' ? 'eleven_v3' : 'eleven_multilingual_v2');
}

/** Which controls a model honours. */
export function advCaps(models: readonly TtsModel[], id: string): ModelCaps {
  const hit = models.find(m => m && m.model_id === id);
  if (hit && hit.supports) {
    return {
      presets: !!hit.supports.presets,
      style: !!hit.supports.style,
      speaker_boost: !!hit.supports.speaker_boost,
      speed: !!hit.supports.speed,
      language_code: String(hit.supports.language_code ?? ''),
    };
  }
  // The list failed or the id is not in it: assume the documented shape of the family.
  return isV3(id)
    ? { presets: true, style: false, speaker_boost: true, speed: false, language_code: 'rejected' }
    : { presets: false, style: true, speaker_boost: true, speed: true, language_code: 'ignored' };
}

export interface VoiceSettings {
  stability: number;
  similarity_boost: number;
  style?: number;
  use_speaker_boost?: boolean;
  speed?: number;
}

export interface AdvBody {
  model_id?: string;
  voice_settings?: VoiceSettings;
  enforce_language?: boolean;
}

/** The fields the panel contributes to a `POST /tts` body.

    Slider percentages become 0..1, and ONLY the controls currently on screen are included —
    so a value remembered for another model (say, speed while v3 is in play) is never sent to
    one that rejects it. */
export function advBody(adv: AdvState, caps: ModelCaps): AdvBody {
  const out: AdvBody = {};
  if (adv.model) out.model_id = adv.model;
  if (!adv.custom) return out;          // voice defaults: nothing the panel owns is sent
  const vs: VoiceSettings = {
    stability: caps.presets ? adv.preset : adv.stability / 100,
    similarity_boost: adv.similarity / 100,
  };
  if (caps.style) vs.style = adv.style / 100;
  if (caps.speaker_boost) vs.use_speaker_boost = !!adv.boost;
  if (caps.speed) vs.speed = Math.round(adv.speed * 100) / 100;
  out.voice_settings = vs;
  if (caps.language_code === 'enforced') out.enforce_language = !!adv.forcelang;
  return out;
}

/** How a control's current value is written next to it. */
export function advFmt(key: string, v: number): string {
  return key === 'speed' ? `${Number(v).toFixed(2)}×` : `${Math.round(v)}%`;
}
