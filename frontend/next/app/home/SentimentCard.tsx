'use client';
import { percent } from '@/lib/format';
import { useI18n } from '@/lib/useI18n';
import { isConflict, polarityClass, type Sentiment, type SentimentHalf } from '@/lib/aiShapes';

/* The public sentiment read — a port of `brand.js`'s `sentimentHTML`.
   ==================================================================
   Deliberately NOT `components/Workbench/Sentiment.tsx`: that one renders the SEMANTIC result
   (per-speaker cards, per-turn rows, timeline seeking) that `POST /v1/semantic` produces for a
   signed-in workspace. This page's `POST /sentiment` answers with the two-half shape —
   `{overall, agreement, text, prosody}` — and the workbench component cannot render it.

   The two halves are never averaged, and that is the whole point of the layout: when the words
   and the voice disagree, the disagreement IS the finding, and a single blended number would
   hide exactly the recording a reviewer should listen to first. `agreement === 'conflict'` says
   so out loud underneath. */

function Meter({ labelKey, value, t }: { labelKey: string; value: number | null | undefined; t: (k: string) => string }) {
  const n = percent(value);
  if (n === null) return null;
  return (
    <div style={{ marginTop: 8 }}>
      <div className="sc-meta">{t(labelKey)} · {n}%</div>
      <div className="sc-bar"><span style={{ width: `${n}%` }} /></div>
    </div>
  );
}

function Half({ titleKey, part, t }: { titleKey: string; part: SentimentHalf | null | undefined; t: (k: string) => string }) {
  return (
    <div style={{ flex: 1 }}>
      <b style={{ color: 'var(--mist)' }}>{t(titleKey)}</b>
      {part ? (
        <>
          <div style={{ marginTop: 6 }}>
            <span className={`pill ${polarityClass(part.polarity)}`}>{part.label ?? ''}</span>
          </div>
          {/* Only prosody carries meters — the text judge has no arousal or valence to report,
              so these simply render nothing on that half rather than as two empty bars. */}
          <Meter labelKey="sn.arousal" value={part.arousal} t={t} />
          <Meter labelKey="sn.valence" value={part.valence} t={t} />
        </>
      ) : (
        // A missing half is stated, not left blank: "no voice read for this recording" and
        // "the voice sounded neutral" are different answers.
        <div className="muted" style={{ marginTop: 6 }}>{t('sn.unavailable')}</div>
      )}
    </div>
  );
}

export function SentimentCard({ sn }: { sn: Sentiment }) {
  const { t } = useI18n();
  return (
    <div className="card">
      <div className="inline" style={{ justifyContent: 'space-between', alignItems: 'baseline' }}>
        <h3 style={{ margin: 0 }}>{t('sn.title')}</h3>
        <span className={`pill ${polarityClass(sn.overall)}`}>{sn.overall || '—'}</span>
      </div>
      <div className="row" style={{ marginTop: 10 }}>
        <Half titleKey="sn.text" part={sn.text} t={t} />
        <Half titleKey="sn.voice" part={sn.prosody} t={t} />
      </div>
      {isConflict(sn) ? <div className="msg err" style={{ marginTop: 12 }}>{t('sn.conflict')}</div> : null}
    </div>
  );
}
