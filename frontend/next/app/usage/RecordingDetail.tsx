'use client';
import type { JSX } from 'react';
import { CallsTable, modelLabel } from './CallsTable';
import { BackButton, KV_GRID, Kv, LoadError, Loading, NUM, TotalsStats, useUsageGet } from './listKit';
import { duration, fmt, when } from './logic';
import { Consumed, groupLabel } from './parts';
import type { RecordingDetail as Detail, T } from './types';

/* One recording, analyser by analyser: what transcription, analysis, fact-check, sentiment,
   the score and the summary each spent on it, and every call behind those figures. The page
   answers "why did THIS call cost that much", so the calls are shown, not just the sums. */

export default function RecordingDetail({ jobId, t, onBack, backLabel }: {
  jobId: string;
  t: T;
  onBack: () => void;
  /** Which list Back returns to; the call log opens this panel too. */
  backLabel?: string;
}): JSX.Element {
  const { data, error, retry } = useUsageGet<Detail>(`/admin/usage/recordings/${encodeURIComponent(jobId)}`);
  // One tree shape for loading, failed and loaded, so the Back button (which takes focus on
  // mount) is not remounted — and refocused — when the answer arrives.
  const back = <BackButton label={backLabel || t('ul.back.recordings')} onBack={onBack} />;
  if (error || !data) {
    return (
      <>
        <div className="card">
          {back}
          {error ? <LoadError error={error} onRetry={retry} t={t} /> : <Loading t={t} />}
        </div>
      </>
    );
  }

  const { recording: rec, total, groups, calls, summaries } = data;
  return (
    <>
      <div className="card">
        {back}
        <div className="eyebrow">{t('ul.th.recording')}</div>
        <h3 style={{ margin: '0 0 4px', overflowWrap: 'anywhere' }}>{rec.filename || t('usage.purged')}</h3>
        <div style={KV_GRID}>
          <Kv label={t('usage.th.tenant')}>{rec.tenant_name || t('usage.unattributed')}</Kv>
          <Kv label={t('ul.kv.recorded')}>{when(rec.created_at)}</Kv>
          <Kv label={t('ul.th.duration')}>{duration(rec.duration_s)}</Kv>
          <Kv label={t('ul.kv.language')}>{rec.language || '—'}</Kv>
        </div>
        <TotalsStats total={total} t={t} />
      </div>

      {!groups.length ? <div className="card"><div className="empty">{t('ul.rec.nocalls')}</div></div> : null}

      {groups.map(g => {
        const own = calls.filter(c => c.group === g.group);
        return (
          <div className="card" key={g.group}>
            <div className="inline wrap" style={{ justifyContent: 'space-between', alignItems: 'baseline', gap: '4px 14px' }}>
              <h3 style={{ margin: 0 }}>{groupLabel(t, g.group)}</h3>
              <span className="inline wrap" style={{ gap: '2px 12px', alignItems: 'baseline' }}>
                <b><Consumed tokens={g.total_tokens} audioSeconds={g.audio_seconds} characters={g.characters} t={t} /></b>
                <span className="muted" style={{ whiteSpace: 'nowrap' }}>
                  {t('ul.calls.n', { n: fmt(g.calls) })}
                  {g.failed ? <> · <span className="warn-flag">{t('ul.failed.n', { n: fmt(g.failed) })}</span></> : null}
                </span>
              </span>
            </div>
            {g.models.length ? (
              <div style={{ marginTop: 6 }}>
                {g.models.map(m => <span className="chip" key={m}>{modelLabel(m)}</span>)}
              </div>
            ) : null}
            {own.length ? (
              <div style={{ marginTop: 12 }}>
                <CallsTable
                  rows={own}
                  cols={['time', 'feature', 'model', 'in', 'out', 'cached', 'consumed', 'latency', 'status']}
                  t={t}
                  compact
                />
              </div>
            ) : null}
          </div>
        );
      })}

      {summaries.length ? (
        <div className="card">
          <h3 style={{ marginBottom: 4 }}>{t('ul.summaries.title')}</h3>
          <p className="hint" style={{ margin: '0 0 12px' }}>{t('ul.summaries.hint')}</p>
          <div className="table-wrap">
            <table style={{ fontSize: 12.5 }}>
              <thead>
                <tr>
                  <th>{t('ul.th.summary')}</th>
                  <th style={NUM}>{t('ul.th.recordings')}</th>
                  <th style={NUM}>{t('usage.th.total')}</th>
                  <th style={NUM}>{t('usage.th.calls')}</th>
                  <th>{t('usage.th.last')}</th>
                </tr>
              </thead>
              <tbody>
                {summaries.map(s => (
                  <tr key={s.summary_id}>
                    <td>
                      <div style={{ whiteSpace: 'nowrap' }}>{when(s.created_at)}</div>
                      <div className="hint" title={s.summary_id}>{s.summary_id.slice(0, 8)}</div>
                    </td>
                    <td style={NUM}>{fmt(s.job_count)}</td>
                    <td style={NUM}><Consumed tokens={s.total_tokens} audioSeconds={s.audio_seconds} characters={s.characters} t={t} /></td>
                    <td style={NUM}>
                      {fmt(s.calls)}
                      {s.failed ? <> <span className="warn-flag" title={t('ul.failed.n', { n: fmt(s.failed) })}>{fmt(s.failed)}</span></> : null}
                    </td>
                    <td style={{ whiteSpace: 'nowrap' }}>{when(s.last_used)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}
    </>
  );
}
