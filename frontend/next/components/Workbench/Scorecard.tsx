'use client';
/* The scorecard, and the reviewer's right to disagree with it.
   ===========================================================
   A reviewer overruling the model is the NORMAL case, not an exception, so the numbers are
   editable in place, beside the evidence they were judged on — a separate "edit mode" would
   hide the quotes the reviewer is judging against.

   THE WEIGHTED TOTAL IS NOT COMPUTED HERE. The browser sends the dimension scores and the
   server recomputes the total (`apply_manual_scores`), keeps the model's own scorecard as
   revision 1, and answers with the new card. A score somebody is assessed on has to be
   auditable, which means it is computed once, server-side, and never in a page that could be
   running last week's arithmetic. `lib/rubricMath.ts` exists for the rubric EDITOR's preview;
   the only thing borrowed from it here is `normalizeScore`, so a typed number is clamped and
   rounded exactly the way the server will round it. */

import { useCallback, useEffect, useState } from 'react';
import { type ScoreBands } from '@/lib/aiShapes';
import { normalizeScore } from '@/lib/rubricMath';
import { apiGet, apiMessage, type Scope } from '@/lib/session';
import { useI18n } from '@/lib/useI18n';
import { isUnauthorized, patchJson } from './api';
import {
  asArray, numOrNull,
  type ScoreDimension, type ScoreEvidence, type ScoreResult, type ScoreRevision,
} from './logic';
import { bandClass, ScoreBar, TimeBadge } from './parts';
import { seekProps, type SeekTarget } from './seek';
import { type T } from './strings';

export interface ScorecardProps {
  data: ScoreResult;
  bands: ScoreBands;
  /** The recording this card belongs to. Empty means no tools: without an id there is
      nothing to PATCH and no history to read. */
  jobId: string;
  editable: boolean;
  scope: Scope;
  callIndex: number | null;
  onSeek: (target: SeekTarget) => void;
  /** The saved card, straight from the server — the parent stores it and repaints the lanes,
      which carry the score colour. */
  onSaved: (saved: ScoreResult) => void;
  onUnauthorized?: () => void;
}

export function Scorecard(p: ScorecardProps) {
  const { t } = useI18n();
  const { data, bands, jobId, editable } = p;
  const dimensions = asArray<ScoreDimension>(data.dimensions);

  const [edits, setEdits] = useState<Record<string, string>>({});
  const [note, setNote] = useState('');
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState('');
  const [revisions, setRevisions] = useState<ScoreRevision[] | null>(null);
  const [histOpen, setHistOpen] = useState(false);
  const [histBusy, setHistBusy] = useState(false);

  /* A new card — a save landed, or the rubric was run again — replaces what was being typed.
     Keeping the old edits would show a reviewer their draft over somebody else's saved
     numbers and let them save it back without ever seeing what they overwrote.

     This is also what ends the save: `save()` deliberately does not clear `saving` on the
     success path, because the button must keep spinning until the SERVER's recomputed card is
     on screen — clearing it there flashes "Save scores" over the old numbers for a frame. */
  useEffect(() => {
    setEdits({});
    setNote('');
    setMsg('');
    setSaving(false);
    setRevisions(null);
    setHistOpen(false);
  }, [data]);

  const save = useCallback(async () => {
    // Every dimension with a number in its box, not only the ones that were touched: that is
    // what the legacy panel sent, and the server treats the list as the reviewer's card.
    const scores: { key: string; score: number }[] = [];
    for (const d of dimensions) {
      const raw = d.key in edits ? edits[d.key] : (d.score == null ? '' : String(d.score));
      if (String(raw).trim() === '') continue;
      const v = normalizeScore(raw);
      if (v != null) scores.push({ key: d.key, score: v });
    }
    if (!scores.length) return;

    setSaving(true);
    setMsg('');
    try {
      const saved = await patchJson<ScoreResult>(
        `/recordings/${encodeURIComponent(jobId)}/score`, { scores, note }, p.scope,
      );
      p.onSaved(saved);
    } catch (e) {
      setMsg(apiMessage(e, t));
      if (isUnauthorized(e)) p.onUnauthorized?.();
      setSaving(false);
    }
    // No `finally`: on success the parent hands down a new `data`, which remounts this state
    // through the effect above. Clearing `saving` here would flash the button back to
    // "Save scores" a frame before the new card arrives.
  }, [dimensions, edits, note, jobId, p, t]);

  const toggleHistory = useCallback(async () => {
    if (histOpen) { setHistOpen(false); return; }
    setHistBusy(true);
    setMsg('');
    try {
      const d = await apiGet<{ revisions?: ScoreRevision[] }>(
        `/recordings/${encodeURIComponent(jobId)}/score/revisions`, { scope: p.scope },
      );
      setRevisions(asArray<ScoreRevision>(d?.revisions));
      setHistOpen(true);
    } catch (e) {
      setMsg(apiMessage(e, t));
      if (isUnauthorized(e)) p.onUnauthorized?.();
    } finally {
      setHistBusy(false);
    }
  }, [histOpen, jobId, p, t]);

  if (!dimensions.length) return null;

  const total = numOrNull(data.weighted_total);
  const version = (data.is_default || data.version === 0)
    ? t('wb.sc.default')
    : (data.version != null ? `${t('sc.version')} ${data.version}` : '');

  return (
    <div className="wb-res">
      <div className="wb-res-head">
        <div>
          <h3>{t('sc.title')}</h3>
          {version ? <div className="sc-meta">{version}</div> : null}
          {data.manually_edited && data.edited_by
            ? <div className="sc-meta">{t('wb.sc.editedby')} {data.edited_by}</div>
            : null}
        </div>
        <div className="sc-total">
          <div className={`num ${bandClass(total, bands)}`}>{total == null ? '—' : total}</div>
          <span className="muted">{t('sc.weighted')} / {data.max_total || 100}</span>
        </div>
      </div>

      {dimensions.map((d, i) => (
        <Dimension
          key={d.key || i}
          t={t} dim={d} bands={bands} editable={editable}
          value={d.key in edits ? edits[d.key] : (d.score == null ? '' : String(d.score))}
          onChange={v => setEdits(prev => ({ ...prev, [d.key]: v }))}
          callIndex={p.callIndex} onSeek={p.onSeek}
        />
      ))}

      {(editable || jobId) && (
        <>
          <div className="wb-sc-tools">
            {editable && (
              <>
                <input
                  className="wb-sc-note" type="text" value={note}
                  placeholder={t('wb.sc.whynote')} onChange={e => setNote(e.target.value)}
                />
                <button type="button" className="primary wb-sc-save" disabled={saving} onClick={() => void save()}>
                  {saving ? <span className="spinner" /> : t('wb.sc.save')}
                </button>
              </>
            )}
            {jobId && (
              <button type="button" className="ghost wb-sc-hist" disabled={histBusy} onClick={() => void toggleHistory()}>
                {histOpen ? t('wb.sc.hide') : t('wb.sc.history')}
              </button>
            )}
          </div>
          {histOpen && <div className="wb-sc-histbox"><History t={t} revisions={revisions || []} /></div>}
          <div className={`msg wb-sc-msg${msg ? ' err' : ''}`}>{msg}</div>
        </>
      )}
    </div>
  );
}

function Dimension({
  t, dim, bands, editable, value, onChange, callIndex, onSeek,
}: {
  t: T;
  dim: ScoreDimension;
  bands: ScoreBands;
  editable: boolean;
  value: string;
  onChange: (v: string) => void;
  callIndex: number | null;
  onSeek: (target: SeekTarget) => void;
}) {
  const score = numOrNull(dim.score);
  const evidence = asArray<string | ScoreEvidence>(dim.evidence);

  return (
    <div className="sc-dim">
      <div className="sc-dim-head">
        <span className="sc-dim-name">
          {dim.name}
          {/* An edited dimension says so and keeps the model's number beside it: the POINT of
              an override is the disagreement, and hiding the original hides that. */}
          {dim.edited && dim.ai_score != null
            ? <span className="pill" style={{ marginLeft: 6 }}>{t('wb.sc.edited')} · {t('wb.sc.was')} {dim.ai_score}</span>
            : null}
        </span>
        {editable ? (
          <span className="sc-dim-score wb-sc-editcell">
            <input
              type="number" className="wb-sc-in" min={0} max={100}
              value={value} aria-label={dim.name}
              onChange={e => onChange(e.target.value)}
            />
            <span className="sc-meta">/100</span>
          </span>
        ) : (
          <span className={`sc-dim-score ${bandClass(score, bands)}`}>
            {score == null ? '—' : score}<span className="sc-meta">/100</span>
          </span>
        )}
      </div>
      <div className="sc-meta">
        {t('sc.weight')} {dim.weight ?? '—'}% · {t('sc.contribution')} {dim.contribution ?? '—'}
      </div>
      <ScoreBar value={score} bands={bands} />
      {dim.rationale ? <div className="hint" style={{ marginTop: 6 }}>{dim.rationale}</div> : null}
      {evidence.length > 0 && (
        <div className="sc-evid">
          {evidence.map((e, i) => {
            // A job scored before the workbench existed stored plain strings; the current one
            // stores objects that can be placed on the timeline. Both are in the database.
            if (typeof e === 'string') return <q key={i}>{e}</q>;
            if (!e || typeof e !== 'object') return null;
            return (
              <q
                key={i} className="wb-q"
                {...seekProps(onSeek, { start: e.start, seg: asArray<number>(e.segments)[0] ?? null, call: callIndex }, t('wb.seek'))}
              >
                {e.quote || e.text || ''}
                <TimeBadge start={e.start} end={e.end} />
              </q>
            );
          })}
        </div>
      )}
    </div>
  );
}

/** Oldest first: the model's own numbers, then what each person changed them to.

    Fewer than two revisions means nobody has changed anything — the card on screen IS
    revision 1, and a one-row "history" of it reads as a change that never happened. */
function History({ t, revisions }: { t: T; revisions: ScoreRevision[] }) {
  if (revisions.length < 2) return <div className="hint">{t('wb.sc.nohistory')}</div>;

  return (
    <>
      {revisions.map((r, n) => {
        const sc = r.scoring || {};
        const who = r.revision === 1 ? t('wb.sc.themodel') : (r.edited_by || '—');
        const previous = n > 0 ? asArray<ScoreDimension>(revisions[n - 1].scoring?.dimensions) : null;
        const wasBy: Record<string, number | null | undefined> = {};
        for (const d of previous || []) wasBy[d.key] = d.score;

        return (
          <div className="wb-rev" key={r.revision ?? n}>
            <div className="wb-inline" style={{ justifyContent: 'space-between' }}>
              <b>{t('wb.sc.rev')} {r.revision}{r.revision === 1 ? ` · ${t('wb.sc.original')}` : ''}</b>
              <span className="sc-meta">
                {who} · {r.created_at ? new Date(r.created_at).toLocaleString() : '—'}
              </span>
            </div>
            <div className="sc-meta" style={{ margin: '4px 0' }}>
              <b>{sc.weighted_total == null ? '—' : sc.weighted_total}</b> / {sc.max_total || 100}
            </div>
            {asArray<ScoreDimension>(sc.dimensions).map((d, i) => {
              const was = previous && wasBy[d.key] != null && wasBy[d.key] !== d.score
                ? <span className="muted"> ({t('wb.sc.was')} {wasBy[d.key]})</span>
                : null;
              return (
                <div className="sc-meta" key={d.key || i}>
                  {d.name}: <b>{d.score == null ? '—' : d.score}</b>{was}
                </div>
              );
            })}
            {r.note ? <div className="hint" style={{ marginTop: 4 }}>{r.note}</div> : null}
          </div>
        );
      })}
    </>
  );
}
