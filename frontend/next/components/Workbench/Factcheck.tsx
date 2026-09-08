'use client';
/* Fact-check: every checkable claim in the call, against this workspace's knowledge base.
   ======================================================================================
   The compliance surface. What a reviewer takes from it is "did the agent say something
   untrue", so the contradicted claims are lifted out and shown FIRST, above the full list
   they also appear in — a reviewer who reads only the top of the card still sees the
   misinformation. */

import { normalizeVerdict, verdictClass, verdictCounts, type ScoreBands } from '@/lib/aiShapes';
import { useI18n } from '@/lib/useI18n';
import {
  asArray, numOrNull, VERDICT_LABEL_KEY,
  type FactCheckClaim, type FactCheckResult,
} from './logic';
import { bandClass, TimeBadge } from './parts';
import { seekProps, type SeekTarget } from './seek';
import { speakerName, type T } from './strings';

export interface FactcheckProps {
  data: FactCheckResult;
  bands: ScoreBands;
  speakerLabels: Record<string, string>;
  /** Which call of a summary these findings belong to, so a click can switch to it first. */
  callIndex: number | null;
  onSeek: (target: SeekTarget) => void;
}

export function Factcheck({ data, bands, speakerLabels, callIndex, onSeek }: FactcheckProps) {
  const { t } = useI18n();
  const claims = asArray<FactCheckClaim>(data.claims);

  if (!claims.length) {
    return (
      <div className="wb-res">
        <h3>{t('fc.title')}</h3>
        <div className="empty">{t('fc.nochecked')}</div>
      </div>
    );
  }

  const accuracy = numOrNull(data.accuracy_score);
  /* Counted from the claims rather than read from the stored `counts`, per `lib/aiShapes`:
     the pills sit directly above the misinformation section, so on an older or hand-edited
     record whose two halves disagree, trusting the stored number would print "0 contradicted"
     over a populated list. The stored counts remain the fallback for a payload that carries
     the summary without the claims. */
  const counts = verdictCounts({
    accuracy_score: accuracy,
    counts: data.counts ?? null,
    claims: claims.map(c => ({ claim: String(c.claim ?? ''), verdict: normalizeVerdict(c.verdict) })),
  });
  const contradicted = claims.filter(c => normalizeVerdict(c.verdict) === 'CONTRADICTED');

  const card = (claim: FactCheckClaim, key: string) => (
    <ClaimCard
      key={key} t={t} claim={claim} speakerLabels={speakerLabels}
      callIndex={callIndex} onSeek={onSeek}
    />
  );

  return (
    <div className="wb-res">
      <div className="wb-res-head">
        <h3>{t('fc.title')}</h3>
        <div className="fc-accuracy">
          <div className={`num ${bandClass(accuracy, bands)}`}>{accuracy == null ? '—' : accuracy}</div>
          <span className="muted">{t('fc.accuracy')}</span>
        </div>
      </div>

      <div className="wb-pills">
        <span className="pill supported">{counts.supported} {t('fc.supported')}</span>
        <span className="pill partial">{counts.partially_supported} {t('wb.fc.partial')}</span>
        <span className="pill contradicted">{counts.contradicted} {t('fc.contradicted')}</span>
        <span className="pill notinkb">{counts.not_in_kb} {t('fc.notinkb')}</span>
      </div>

      {contradicted.length > 0 && (
        <>
          <h4 style={{ color: 'var(--coral)' }}>⚠ {t('fc.misinfo')}</h4>
          {contradicted.map((c, i) => card(c, `bad-${i}`))}
          <h4>{t('fc.allclaims')}</h4>
        </>
      )}
      {claims.map((c, i) => card(c, `all-${i}`))}
    </div>
  );
}

function ClaimCard({
  t, claim, speakerLabels, callIndex, onSeek,
}: {
  t: T;
  claim: FactCheckClaim;
  speakerLabels: Record<string, string>;
  callIndex: number | null;
  onSeek: (target: SeekTarget) => void;
}) {
  const verdict = normalizeVerdict(claim.verdict);
  const cls = verdictClass(claim.verdict);
  const evidence = claim.evidence && typeof claim.evidence === 'object' ? claim.evidence : null;
  const seg = asArray<number>(claim.segments)[0];
  const confidence = numOrNull(claim.confidence);
  const who = claim.speaker ? speakerName(t, claim.speaker, speakerLabels) : '';
  // Joined from the parts that exist rather than concatenated: the legacy string appended the
  // confidence with its own separator, so a claim with a confidence and no speaker or
  // category rendered as a leading " · 85%".
  const meta = [
    who,
    claim.category ? String(claim.category) : '',
    confidence != null ? `${Math.round(confidence * 100)}%` : '',
  ].filter(Boolean).join(' · ');

  return (
    <div
      className={`fc-claim v-${verdict} wb-seekcard`}
      {...seekProps(onSeek, { start: claim.start, seg: seg ?? null, call: callIndex }, t('wb.seek'))}
    >
      <div className="wb-claim-head">
        <span className={`pill ${cls}`}>{t(VERDICT_LABEL_KEY[cls])}</span>
        <TimeBadge start={claim.start} end={claim.end} />
        {meta ? <span className="hint">{meta}</span> : null}
      </div>
      <div className="wb-claim-text">{claim.claim}</div>
      {claim.rationale ? <div className="hint">{claim.rationale}</div> : null}
      {evidence ? (
        <div className="fc-ev">
          <div className="fc-ev-src">
            📄 {evidence.title || evidence.doc_type || 'KB'}
            {numOrNull(evidence.score) != null ? ` · ${evidence.score}` : ''}
          </div>
          {evidence.snippet || ''}
        </div>
      ) : null}
    </div>
  );
}
