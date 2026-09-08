'use client';
/* Summarise: one or several related calls, read together.
   ======================================================
   The only analyser that is about the calls as WHOLES rather than about moments inside one,
   so it is also the only one with no timeline lane. Its clickable parts move between calls
   instead of within one: a call card selects that call, a transcript line seeks inside it. */

import { toStringList } from '@/lib/aiShapes';
import { duration as clock } from '@/lib/format';
import { useI18n } from '@/lib/useI18n';
import {
  asArray, numOrNull,
  type Segment, type SummaryBody, type SummaryCallCard, type SummaryParticipant, type SummaryResult,
} from './logic';
import { TightList, TimeBadge } from './parts';
import { seekProps, type SeekTarget } from './seek';
import { langName, speakerName, word, type T } from './strings';

/** What the transcripts section needs of a call — satisfied by the panel's own `Call` plus
    the labels resolved for the current language. */
export interface SummaryCallSource {
  filename: string;
  language: string;
  duration: number | null;
  segments: Segment[];
  transcript: string;
  speakerLabels: Record<string, string>;
}

export interface SummaryProps {
  data: SummaryResult;
  calls: SummaryCallSource[];
  /** Which call is on screen, so its card reads as selected. */
  active: number;
  onSeek: (target: SeekTarget) => void;
}

export function Summary({ data, calls, active, onSeek }: SummaryProps) {
  const { t } = useI18n();
  // `POST /summaries` nests the model's answer under `summary`; a stored row read back can be
  // the answer itself. Both shapes are in the database.
  const s: SummaryBody = (data.summary && typeof data.summary === 'object' ? data.summary : data) as SummaryBody;
  const participants = asArray<SummaryParticipant>(s.participants);
  const cards = asArray<SummaryCallCard>(s.calls);

  return (
    <div className="wb-res">
      <div className="wb-res-head">
        <h3>{t('wb.sum.title')}</h3>
        {s.language ? <span className="muted">{langName(t, s.language)}</span> : null}
      </div>

      <p className="wb-sum-short">{s.short_summary || ''}</p>

      <div className="row">
        <div>
          <b style={{ color: 'var(--mist)' }}>{t('res.keypoints')}</b>
          <TightList items={toStringList(s.key_points)} />
        </div>
        <div>
          <b style={{ color: 'var(--mist)' }}>{t('res.actions')}</b>
          <TightList items={toStringList(s.action_items)} />
        </div>
      </div>

      {participants.length > 0 && (
        <>
          <h4>{t('wb.sum.participants')}</h4>
          <div>
            {participants.map((p, i) => {
              const appears = asArray<number>(p.appears_in);
              return (
                <span className="wb-part" key={i}>
                  <b>{p.label}</b>
                  {p.role ? <span className="muted">{word(t, 'wb.role.', p.role)}</span> : null}
                  {appears.length > 0 ? (
                    <span className="muted">
                      · {t('wb.sum.appears', { list: appears.map(n => (numOrNull(n) || 0) + 1).join(', ') })}
                    </span>
                  ) : null}
                </span>
              );
            })}
          </div>
        </>
      )}

      {cards.length > 0 && (
        <>
          <h4>{t('wb.sum.calls')}</h4>
          <div className="wb-calls">
            {cards.map((c, i) => {
              const idx = numOrNull(c.index) != null ? (numOrNull(c.index) as number) : i;
              const call = calls[idx];
              const seek = calls.length > 1
                ? seekProps(onSeek, { call: idx }, t('wb.seek'))
                : {};
              return (
                <div className={`wb-call${idx === active ? ' active' : ''}`} key={i} {...seek}>
                  <div className="wb-call-title">{c.title || t('wb.call', { n: idx + 1 })}</div>
                  <div className="wb-call-file">
                    {c.filename || call?.filename || ''}
                    {call && call.duration != null ? ` · ${clock(call.duration)}` : ''}
                  </div>
                  {c.summary ? <p>{c.summary}</p> : null}
                  {c.outcome ? <p><b style={{ color: 'var(--mist)' }}>{t('wb.sum.outcome')}:</b> {c.outcome}</p> : null}
                </div>
              );
            })}
          </div>
        </>
      )}

      {calls.length > 0 && (
        <>
          <h4>{t('wb.sum.transcripts')}</h4>
          {calls.map((c, i) => (
            <div key={i}>
              <div className="wb-tx-head">
                <b>{t('wb.call', { n: i + 1 })}</b>
                {c.filename ? <span className="muted">{c.filename}</span> : null}
                {c.language ? <span className="muted">· {langName(t, c.language)}</span> : null}
                {c.duration != null ? <span className="muted">· {clock(c.duration)}</span> : null}
              </div>
              <Transcript
                t={t} segments={c.segments} transcript={c.transcript}
                labels={c.speakerLabels} callIndex={i} onSeek={onSeek}
              />
            </div>
          ))}
        </>
      )}
    </div>
  );
}

/** One call's transcript: a paragraph per turn, each of them a seek control.

    A source with no segments at all falls back to the raw text in a `<pre>` — there is
    nothing to make clickable, and showing the transcript beats showing nothing. */
export function Transcript({
  t, segments, transcript, labels, callIndex, onSeek,
}: {
  t: T;
  segments: Segment[];
  transcript: string;
  labels: Record<string, string>;
  callIndex: number | null;
  onSeek: (target: SeekTarget) => void;
}) {
  if (!segments.length) return <pre className="tx">{transcript || t('res.empty')}</pre>;

  return (
    <div className="wb-tx">
      {segments.map((s, i) => (
        <p key={i} {...seekProps(onSeek, { start: s.start, seg: s.i != null ? s.i : i, call: callIndex }, t('wb.seek'))}>
          <span className="wb-spkchip">{speakerName(t, s.speaker, labels)}</span>
          <TimeBadge start={s.start} end={s.end} />
          <span>{s.text}</span>
        </p>
      ))}
    </div>
  );
}
