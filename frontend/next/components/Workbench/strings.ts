/* The workbench's words: the helpers that need the dictionary or a formatter.
   ==========================================================================
   Split from `logic.ts` only because that file has to stay import-free to run under the
   Node test runner (see its header). Everything here is still pure — `t` is passed in, never
   read from a hook — so a renderer can call it during render without an effect. */

import { capFirst } from '@/lib/format';
import { noVoiceKey, type Call, type SemanticResult } from './logic';

/** The `t` a component gets from `useI18n`. */
export type T = (key: string, vars?: Record<string, string | number>) => string;

/** A dictionary word whose KEY is data — a tone, a role, a voice label.

    The lookup can miss legitimately: the model may return a label this build has no word for,
    and `translate` answers with the key itself in that case. Showing the raw value beats
    showing `wb.tone.sarcastic` to a customer. */
export function word(t: T, prefix: string, value: unknown, fallback?: string): string {
  if (!value) return fallback == null ? '—' : fallback;
  const key = prefix + String(value).toLowerCase();
  const v = t(key);
  return v === key ? String(value) : v;
}

/** An ISO-639 code as the language's own name, or the code when it is one we do not name. */
export function langName(t: T, code: unknown): string {
  if (!code) return '';
  const key = `lang.${String(code).toLowerCase().slice(0, 2)}`;
  const v = t(key);
  return v === key ? String(code) : v;
}

/** A speaker id → what to call them on screen.

    Three shapes arrive here and all three are normal: a role the caller has already resolved
    (`labels`), a diarised `speaker_0`, and — for a pasted transcript — the label the person
    typed, which IS the id (§2). */
export function speakerName(t: T, id: unknown, labels?: Record<string, string> | null): string {
  if (!id) return '';
  const key = String(id);
  if (labels && labels[key]) return labels[key];
  const m = /^speaker_(\d+)$/.exec(key);
  if (m) return t('wb.speaker', { n: Number(m[1]) + 1 });
  const v = word(t, 'wb.role.', key, key);
  return v === key ? capFirst(key) : v;
}

/** A call's speaker id → display label map, in the CURRENT language.

    Roles are STORED and labels are DERIVED, which is what makes a language switch relabel
    every chip on the timeline and in the transcript rather than leaving the first language's
    words behind. */
export function speakerLabels(t: T, call: Call): Record<string, string> {
  const out: Record<string, string> = {};
  for (const id of Object.keys(call.roles || {})) {
    const role = call.roles[id];
    const v = word(t, 'wb.role.', role, role);
    out[id] = v === role ? capFirst(role) : v;
  }
  return out;
}

/** The sentence explaining why there is no voice half.

    `noVoiceKey` picks the specific one; this checks that the key resolves — a result carrying
    a status this build has no wording for renders the key itself, which is worse than the
    generic line it falls back to. */
export function noVoiceText(t: T, sm: SemanticResult | null | undefined): string {
  const key = noVoiceKey(sm?.voice_status);
  const v = t(key);
  return v === key ? t('wb.sem.novoice') : v;
}

/** The turn count of a pasted transcript, for the collapsed source line. */
export function turnsLabel(t: T, call: Call): string {
  return call.source === 'text' && call.segments.length
    ? t('wb.turns', { n: call.segments.length })
    : '';
}
