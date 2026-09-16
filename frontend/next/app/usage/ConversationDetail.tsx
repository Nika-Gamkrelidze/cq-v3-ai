'use client';
import type { CSSProperties, JSX } from 'react';
import { CallsTable, type CallCol } from './CallsTable';
import { BackButton, KV_GRID, Kv, LoadError, Loading, TotalsStats, useUsageGet } from './listKit';
import { fmt, when } from './logic';
import { Consumed, Stat } from './parts';
import type { ConversationDetail as Detail, T } from './types';

/* One chat conversation as it happened: every message in order, and under each one the AI
   calls it caused — the triage that sorted a customer question, the answer, a handoff summary,
   the copilot drafts an operator message prompted. Messages that cost nothing stay in the
   timeline, because "the bot answered three questions for free and the fourth cost 9k tokens"
   is only visible with the free ones beside it. */

/** The server caps the timeline at this many messages. */
const TURN_CAP = 500;

const TURN_COLS: CallCol[] = ['feature', 'model', 'in', 'out', 'cached', 'latency', 'status'];

/** Who spoke, told apart by the quote's rule as well as the badge, so a long exchange can be
    scanned without reading the badges. */
const RULE: Record<string, string> = {
  customer: 'var(--hairline)',
  operator: 'var(--mist)',
  bot: 'var(--beam)',
};

const quote = (role: string): CSSProperties => ({
  margin: '8px 0 0',
  padding: '8px 12px',
  borderLeft: `3px solid ${RULE[role] || 'var(--hairline)'}`,
  borderRadius: 6,
  background: 'color-mix(in oklab,var(--surface-2) 40%,transparent)',
  whiteSpace: 'pre-wrap',          // the customer's own line breaks
  overflowWrap: 'anywhere',        // a pasted URL must not widen the page on a phone
  fontSize: 13.5,
});

function roleLabel(t: T, role: string): string {
  const key = `ul.role.${role}`;
  const s = t(key);
  return s === key ? role : s;
}

export default function ConversationDetail({ conversationId, t, onBack, backLabel }: {
  conversationId: string;
  t: T;
  onBack: () => void;
  /** Which list Back returns to; the call log opens this panel too. */
  backLabel?: string;
}): JSX.Element {
  const { data, error, retry } = useUsageGet<Detail>(
    `/admin/usage/conversations/${encodeURIComponent(conversationId)}`,
  );
  // Same tree shape in every state, so the focused Back button survives the answer arriving.
  const back = <BackButton label={backLabel || t('ul.back.conversations')} onBack={onBack} />;
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

  const { conversation: conv, total, turns, unattached } = data;
  const questions = turns.filter(x => x.calls.length).length;
  return (
    <>
      <div className="card">
        {back}
        <div className="eyebrow">{t('ul.th.conversation')}</div>
        <h3 style={{ margin: '0 0 4px', overflowWrap: 'anywhere' }}>{conv.external_ref || t('ul.noref')}</h3>
        {conv.subject ? <p className="lead" style={{ overflowWrap: 'anywhere' }}>{conv.subject}</p> : null}
        <div style={KV_GRID}>
          <Kv label={t('usage.th.tenant')}>{conv.tenant_name || t('usage.unattributed')}</Kv>
          <Kv label={t('ul.th.channel')}>{conv.channel || '—'}</Kv>
          <Kv label={t('ul.kv.language')}>{conv.locale || '—'}</Kv>
          <Kv label={t('ul.kv.state')}>{conv.state || '—'}</Kv>
          <Kv label={t('ul.kv.started')}>{when(conv.created_at)}</Kv>
          <Kv label={t('ul.kv.last')}>{when(conv.last_message_at)}</Kv>
        </div>
        <TotalsStats
          total={total}
          t={t}
          extra={<>
            <Stat label={t('ul.th.turns')} value={fmt(turns.length)} />
            <Stat label={t('ul.th.questions')} value={fmt(questions)} hint={t('ul.th.questions.tip')} />
          </>}
        />
      </div>

      <div className="card">
        <h3>{t('ul.timeline.title')}</h3>
        {turns.length >= TURN_CAP ? <p className="hint" style={{ margin: '0 0 10px' }}>{t('ul.turns.capped', { n: fmt(TURN_CAP) })}</p> : null}
        {!turns.length ? <div className="empty">{t('ul.turns.none')}</div> : (
          <ol style={{ listStyle: 'none', margin: 0, padding: 0 }}>
            {turns.map((turn, i) => (
              <li key={turn.turn_id}
                style={{ padding: '14px 0', borderTop: i ? '1px solid color-mix(in oklab,var(--hairline) 70%,transparent)' : undefined }}>
                <div className="inline wrap" style={{ gap: '4px 10px' }}>
                  <span className="chip" style={{ margin: 0 }}>{roleLabel(t, turn.role)}</span>
                  <span className="muted" style={{ fontSize: 12, fontVariantNumeric: 'tabular-nums' }} title={turn.turn_ref || undefined}>
                    {when(turn.created_at)}
                  </span>
                  {turn.grounded ? (
                    <span className="pill ready" title={t('ul.turn.grounded.hint')}>{t('ul.turn.grounded')}</span>
                  ) : null}
                </div>
                {/* Plain text on purpose: React escapes it, and a customer's message is the
                    last thing on this page that should be able to render markup. */}
                <blockquote style={quote(turn.role)}>
                  {turn.content || <span className="hint">{t('ul.turn.notext')}</span>}
                </blockquote>
                {turn.calls.length ? (
                  <div style={{ marginTop: 10 }}>
                    <div className="muted" style={{ marginBottom: 6 }}>
                      <b><Consumed tokens={turn.total.total_tokens} audioSeconds={turn.total.audio_seconds} characters={turn.total.characters} t={t} /></b>
                      {' · '}{t('ul.calls.n', { n: fmt(turn.total.calls) })}
                      {turn.total.failed ? <> · <span className="warn-flag">{t('ul.failed.n', { n: fmt(turn.total.failed) })}</span></> : null}
                    </div>
                    <CallsTable rows={turn.calls} cols={TURN_COLS} t={t} compact />
                  </div>
                ) : (
                  <div className="hint">{t('ul.turn.nocalls')}</div>
                )}
              </li>
            ))}
          </ol>
        )}
      </div>

      {unattached.length ? (
        <div className="card">
          <h3 style={{ marginBottom: 4 }}>{t('ul.unattached.title')}</h3>
          <p className="hint" style={{ margin: '0 0 12px' }}>{t('ul.unattached.hint')}</p>
          <CallsTable rows={unattached} cols={['time', ...TURN_COLS]} t={t} compact />
        </div>
      ) : null}
    </>
  );
}
