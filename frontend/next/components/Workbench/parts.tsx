'use client';
/* The small pieces every result pane repeats: a timestamp badge, a score bar, a list. */

import { bandVar, scoreBand, type ScoreBands } from '@/lib/aiShapes';
import { duration } from '@/lib/format';
import { numOrNull, pct } from './logic';

/** `2:07–2:19`, or nothing at all for a finding the model could not place in time.

    Nothing, rather than a `—`: a pasted transcript has no timestamps anywhere, and a column
    of dashes down every card says "broken" where the truth is "this source has no clock". */
export function TimeBadge({ start, end }: { start?: number | null; end?: number | null }) {
  const s = numOrNull(start);
  if (s == null) return null;
  const e = numOrNull(end);
  return <span className="wb-time">{duration(s)}{e != null ? `–${duration(e)}` : ''}</span>;
}

/** The 0-100 bar under a score, coloured by the WORKSPACE's bands. */
export function ScoreBar({ value, bands }: { value: number | null; bands: ScoreBands }) {
  const band = scoreBand(value, bands);
  return (
    <div className={`sc-bar ${band === 'none' ? '' : band}`.trim()}>
      <span style={{ width: `${pct(value)}%` }} />
    </div>
  );
}

/** A bar with a colour that is not a score — the calm/tense shares of a voice read. */
export function FixedBar({ value, tone }: { value: number; tone: 'good' | 'bad' }) {
  return <div className={`sc-bar ${tone}`}><span style={{ width: `${value}%` }} /></div>;
}

/** The class that colours a number by band: `.wb-band-ok` and friends, never an inline
    colour — the light theme overrides these and would need `!important` against a style
    attribute. */
export function bandClass(value: number | null, bands: ScoreBands): string {
  return `wb-band-${bandVar(scoreBand(value, bands))}`;
}

/** A model list (key points, action items) — or an em dash when it returned none. */
export function TightList({ items }: { items: string[] }) {
  if (!items.length) return <span className="muted">—</span>;
  return <ul className="tight">{items.map((x, i) => <li key={i}>{x}</li>)}</ul>;
}
