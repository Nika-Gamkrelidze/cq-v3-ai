'use client';
/* Retrieval confidence — shared by the playground and by plain KB search.
   ======================================================================
   WHY: a BGE-M3 cosine score is NOT calibrated in absolute terms — only the ranking is
   meaningful. Unrelated same-language text still scores ~0.30–0.45, so a screen full of
   0.36–0.40 rows is the encoder saying "none of these match" while looking exactly like a set
   of matches. A real tenant searched for a word that appears nowhere in their KB and got every
   document back inside a 0.037-wide band. `confidence`
   (services/retrieval.py::assess_confidence) names that; these two components are what make it
   visible instead of silent.

   The flag is a BANNER, not a toast: it has to stay on screen while the passages it is warning
   about are read. The passages are still rendered underneath — an operator may legitimately
   want the best-effort ranking, and hiding them would make a populated KB look empty. */

import type { T } from '../ctx';
import s from '../workspace.module.css';

export interface Confidence {
  level?: string;
  confident?: boolean;
  reason?: string;
  top_score?: number | null;
  spread?: number | null;
  margin?: number | null;
}

export interface RetrievalBody {
  method?: string;
  top_score?: number | null;
  confidence?: Confidence | null;
  results?: unknown;
}

const LEVELS = ['high', 'medium', 'low', 'none'];
const KNOWN_REASONS = ['empty_kb', 'unavailable', 'no_hits', 'keyword_fallback',
  'flat_distribution', 'low_score'];

const num = (v: number | null | undefined) => (v == null ? '—' : Number(v).toFixed(4));

function method(t: T, m: string | undefined) {
  return (m === 'vector' || m === 'keyword' || m === 'none') ? t('retr.m.' + m) : (m || '—');
}

function conf(d: RetrievalBody | null): Confidence | null {
  return (d && typeof d.confidence === 'object' && d.confidence) || null;
}

/** Always shown, compact: the score the user is judging must never be invisible, and
    "semantic" vs "text match" changes what that score even means. */
export function RetrMeta({ t, d, n }: { t: T; d: RetrievalBody | null; n: number }) {
  const c = conf(d);
  const top = (d && d.top_score != null) ? d.top_score : (c ? c.top_score : null);
  const lvl = c && LEVELS.indexOf(String(c.level)) >= 0 ? String(c.level) : null;
  const pill = lvl === 'high' ? 'ready' : lvl === 'medium' ? 'processing' : 'notinkb';
  return (
    <div className="inline hint" style={{ gap: 8, flexWrap: 'wrap', marginBottom: 8 }}>
      <span>{t('kba.pg.method')}: <b>{method(t, d?.method)}</b></span>
      <span>· {t('retr.top')} <b>{num(top)}</b></span>
      <span>· {n}</span>
      {lvl ? <span className={`pill ${pill}`}>{t('retr.level.' + lvl)}</span> : null}
    </div>
  );
}

/** Renders NOTHING when retrieval was confident — or when the field is absent (an older API
    build): silence is the right default, the results speak for themselves. */
export function RetrFlag({ t, d, n }: { t: T; d: RetrievalBody | null; n: number }) {
  const c = conf(d);
  if (!c || c.confident !== false) return null;
  const k = KNOWN_REASONS.indexOf(String(c.reason)) >= 0 ? 'retr.flag.' + c.reason : 'retr.flag.generic';
  const nums: string[] = [];
  if (c.top_score != null) nums.push(`${t('retr.top')} ${num(c.top_score)}`);
  if (c.spread != null) nums.push(`${t('retr.spread')} ${num(c.spread)}`);
  if (c.margin != null) nums.push(`${t('retr.margin')} ${num(c.margin)}`);
  if (n) nums.push(t('retr.flag.shown'));
  /* Red for the two that are a fact about our machinery rather than about this tenant's KB —
     `keyword_fallback` (the encoder is unreachable, so every tenant is quietly getting worse
     answers) and `unavailable` (the search did not run at all, so nothing on screen is a
     statement about the KB). Both are the operator's to act on TODAY. The rest say "your KB
     has a gap", which is amber, not red. */
  const hard = c.reason === 'keyword_fallback' || c.reason === 'unavailable';
  return (
    <div className={`${s.retrFlag}${hard ? ' ' + s.hard : ''}`}>
      <b>{t(k)}</b>{t(k + '.b')}
      {nums.length ? <span className={s.retrNums}>{nums.join(' · ')}</span> : null}
    </div>
  );
}
