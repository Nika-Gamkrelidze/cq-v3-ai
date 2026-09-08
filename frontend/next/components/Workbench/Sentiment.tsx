'use client';
/* Sentiment: how the conversation was conducted — the words, and with audio the voice too.
   =======================================================================================
   The two halves are never averaged. When they disagree — polite words in a tense voice —
   that disagreement IS the finding, and a single blended number would hide exactly the call a
   reviewer should listen to first.

   Which is also why the missing half is explained rather than left blank: see `noVoiceText`. */

import { useEffect, useRef } from 'react';
import { percent } from '@/lib/format';
import { useI18n } from '@/lib/useI18n';
import {
  asArray, levelOf, numOrNull, pct, toneLevel, voiceVerdictLevel, worstLevel,
  type Segment, type SemanticResult, type SemanticSegment, type SemanticSpeaker,
} from './logic';
import { FixedBar, ScoreBar, TimeBadge } from './parts';
import { seekProps, type SeekTarget } from './seek';
import { langName, noVoiceText, speakerName, word, type T } from './strings';
import { type ScoreBands } from '@/lib/aiShapes';

export interface SentimentProps {
  data: SemanticResult;
  bands: ScoreBands;
  /** The call's own transcript, so a turn the model returned without its text can still be
      shown: the sentiment payload cites segments by index. */
  segments: Segment[];
  speakerLabels: Record<string, string>;
  callIndex: number | null;
  onSeek: (target: SeekTarget) => void;
  /** The segment the playhead is inside, from the timeline. The turn list follows it. */
  now?: number | null;
}

export function Sentiment({ data, bands, segments, speakerLabels, callIndex, onSeek, now }: SentimentProps) {
  const { t } = useI18n();
  const listRef = useRef<HTMLDivElement | null>(null);
  const modes = asArray<string>(data.modes);
  const speakers = asArray<SemanticSpeaker>(data.speakers);
  const rows = asArray<SemanticSegment>(data.segments);

  /* Follow the playhead INSIDE the list's own scroll box, never by scrolling the page: the
     reader is watching the timeline, and moving the document under them to reveal a turn is
     how the legacy version lost people's place. */
  useEffect(() => {
    const box = listRef.current;
    if (now == null || !box || box.scrollHeight <= box.clientHeight + 4) return;
    const el = box.querySelector<HTMLElement>(`.wb-seg[data-seg="${now}"]`);
    if (!el) return;
    const top = el.offsetTop - box.offsetTop;
    const bottom = top + el.offsetHeight;
    if (top < box.scrollTop + 4) box.scrollTop = Math.max(0, top - 4);
    else if (bottom > box.scrollTop + box.clientHeight - 4) box.scrollTop = bottom - box.clientHeight + 4;
  }, [now]);

  return (
    <div className="wb-res">
      <div className="wb-res-head">
        <h3>{t('wb.sem.title')}</h3>
        {data.language ? <span className="muted">{langName(t, data.language)}</span> : null}
      </div>

      {modes.includes('voice') && data.voice_available === false ? (
        <div className="msg" style={{ color: 'var(--pending)' }}>{noVoiceText(t, data)}</div>
      ) : null}

      <div className="wb-spk">
        {speakers.map((s, i) => (
          <SpeakerCard
            key={s.speaker || i} t={t} speaker={s} bands={bands}
            labels={speakerLabels} wantedVoice={modes.includes('voice')} result={data}
          />
        ))}
      </div>

      {data.summary ? (
        <>
          <h4>{t('wb.sem.summary')}</h4>
          <p style={{ margin: 0 }}>{data.summary}</p>
        </>
      ) : null}

      {rows.length > 0 && (
        <>
          <h4>{t('wb.sem.turns')}</h4>
          <div className="wb-segs" ref={listRef}>
            {rows.map((row, i) => (
              <TurnRow
                key={i} t={t} row={row} segments={segments} labels={speakerLabels}
                callIndex={callIndex} onSeek={onSeek}
                now={now != null && numOrNull(row.i) === now}
              />
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function SpeakerCard({
  t, speaker, bands, labels, wantedVoice, result,
}: {
  t: T;
  speaker: SemanticSpeaker;
  bands: ScoreBands;
  labels: Record<string, string>;
  wantedVoice: boolean;
  result: SemanticResult;
}) {
  const name = speakerName(t, speaker.speaker, labels);
  const roleWord = speaker.role && speaker.role !== 'unknown' ? word(t, 'wb.role.', speaker.role) : '';
  const text = speaker.text;
  const voice = speaker.voice;
  const politeness = numOrNull(text?.politeness);

  return (
    <div className="wb-spk-card">
      <div className="wb-spk-head">
        <span className="wb-spk-name">{name}</span>
        {/* Never "Agent · Agent": the role pill is a second fact only when the display name is
            not already the role. */}
        {roleWord && roleWord !== name ? <span className="pill">{roleWord}</span> : null}
      </div>

      {text ? (
        <div className="wb-sec">
          <div className="wb-sec-title">{t('wb.lane.words')}</div>
          <div className="wb-inline">
            <span className={`wb-tone ${toneLevel(text.overall)}`}>{word(t, 'wb.tone.', text.overall)}</span>
            <span className="sc-meta">{t('wb.sem.politeness')} {politeness == null ? '—' : politeness}/100</span>
          </div>
          <ScoreBar value={politeness} bands={bands} />
          {asArray<string>(text.flags).filter(Boolean).length > 0 && (
            <div className="wb-flags">
              {asArray<string>(text.flags).filter(Boolean).map((f, i) => <span className="chip" key={i}>{f}</span>)}
            </div>
          )}
          {text.rationale ? <div className="hint">{text.rationale}</div> : null}
        </div>
      ) : null}

      {voice ? (
        <div className="wb-sec">
          <div className="wb-sec-title">{t('wb.lane.voice')}</div>
          <div className="wb-inline">
            <span className={`wb-tone ${voiceVerdictLevel(voice.voice)}`}>{word(t, 'wb.voice.', voice.voice)}</span>
          </div>
          <div className="sc-meta" style={{ marginTop: 6 }}>{t('wb.sem.share_good')} · {pct((numOrNull(voice.share_good) || 0) * 100)}%</div>
          <FixedBar value={pct((numOrNull(voice.share_good) || 0) * 100)} tone="good" />
          <div className="sc-meta" style={{ marginTop: 6 }}>{t('wb.sem.share_bad')} · {pct((numOrNull(voice.share_bad) || 0) * 100)}%</div>
          <FixedBar value={pct((numOrNull(voice.share_bad) || 0) * 100)} tone="bad" />
        </div>
      ) : wantedVoice ? (
        <div className="wb-sec">
          <div className="wb-sec-title">{t('wb.lane.voice')}</div>
          <div className="hint">{noVoiceText(t, result)}</div>
        </div>
      ) : null}
    </div>
  );
}

function TurnRow({
  t, row, segments, labels, callIndex, onSeek, now,
}: {
  t: T;
  row: SemanticSegment;
  segments: Segment[];
  labels: Record<string, string>;
  callIndex: number | null;
  onSeek: (target: SeekTarget) => void;
  now: boolean;
}) {
  const i = numOrNull(row.i);
  const src = i != null ? segments[i] : undefined;
  const text = (src && src.text) || row.text || '';
  // The worse of the two reads decides the stripe: a turn that was polite but sounded
  // aggressive must not be filed as calm because the words half won.
  const level = worstLevel(
    row.text_tone ? row.text_level : 'none',
    row.voice_label ? row.voice_level : 'none',
  );
  const start = row.start != null ? row.start : (src ? src.start : null);
  const end = row.end != null ? row.end : (src ? src.end : null);
  const confidence = percent(row.voice_confidence);

  return (
    <div
      className={`wb-seg ${level}${now ? ' now' : ''}`}
      {...seekProps(onSeek, { start, seg: i, call: callIndex }, t('wb.seek'))}
    >
      <div className="wb-seg-head">
        <span className="wb-spkchip">{speakerName(t, row.speaker || (src && src.speaker), labels)}</span>
        <TimeBadge start={start} end={end} />
        {row.text_tone ? (
          <span className={`wb-tone ${levelOf(row.text_level)}`}>{word(t, 'wb.tone.', row.text_tone)}</span>
        ) : null}
        {row.voice_label ? (
          <span className={`wb-tone ${levelOf(row.voice_level)}`}>
            🎙 {word(t, 'wb.vl.', row.voice_label)}{confidence != null ? ` · ${confidence}%` : ''}
          </span>
        ) : null}
      </div>
      {text ? <div className="wb-seg-text">{text}</div> : null}
      {row.text_note ? <div className="hint wb-seg-note">{row.text_note}</div> : null}
    </div>
  );
}
