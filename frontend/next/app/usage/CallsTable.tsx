'use client';
import type { JSX, ReactNode } from 'react';
import { CLIP, LINK, NUM, TenantCell } from './listKit';
import { cached, fmt, providerModel, when } from './logic';
import { Consumed, SortTh, StatusPill, groupLabel } from './parts';
import type { CallRow, SortState, T } from './types';

/* One table of AI calls, three places: the call log (every column, server-sorted), a
   recording's analyser cards and a conversation's turns (a few columns, in time order). One
   column definition per field, so "input tokens" cannot be computed one way in the log and
   another way in a drill-down. */

export type CallCol =
  | 'time' | 'tenant' | 'analyser' | 'feature' | 'kind' | 'model' | 'in' | 'out' | 'cached'
  | 'total' | 'consumed' | 'latency' | 'status' | 'source';

/** A feature's readable name ("Claim verdicts"); a label added on the server after this
    dictionary was written shows as the raw label rather than as a key name. */
export function featureLabel(t: T, feature: string): string {
  const key = `ul.feature.${feature}`;
  const s = t(key);
  return s === key ? feature : s;
}

/** The server joins models as "provider/model"; shown the way every other table here shows
    them. Split on the FIRST slash — a provider name has none, a model id may. */
export function modelLabel(joined: string): string {
  const i = joined.indexOf('/');
  return i < 0 ? joined : providerModel(joined.slice(0, i), joined.slice(i + 1));
}

/** A token count that was never reported (ElevenLabs Scribe, voice tone) is a dash, not a
    zero: "0 input tokens" claims a measurement nobody took. */
const count = (n: number | null | undefined): string => (n === null || n === undefined ? '—' : fmt(n));

interface Ctx {
  t: T;
  onRecording?: (jobId: string, callId: string) => void;
  onConversation?: (conversationId: string, callId: string) => void;
  onTenant?: (clientId: string) => void;
  activeTenant: string;
}

interface ColDef {
  head: string;          // i18n key
  sort?: string;         // server sort key, when the column is sortable
  num: boolean;
  cell: (r: CallRow, c: Ctx) => ReactNode;
}

const COLS: Record<CallCol, ColDef> = {
  time: {
    head: 'ul.th.time', sort: 'created_at', num: false,
    cell: r => <span style={{ whiteSpace: 'nowrap' }}>{when(r.created_at)}</span>,
  },
  tenant: {
    head: 'usage.th.tenant', sort: 'tenant', num: false,
    cell: (r, c) => c.onTenant
      ? <TenantCell id={r.client_id} name={r.tenant_name} active={c.activeTenant} onFilter={c.onTenant} t={c.t} />
      : (r.tenant_name || c.t('usage.unattributed')),
  },
  analyser: {
    head: 'ul.th.analyser', sort: 'feature', num: false,
    cell: (r, c) => (
      <>
        <div style={{ whiteSpace: 'nowrap' }}>{groupLabel(c.t, r.group)}</div>
        <div className="hint" title={r.feature} style={{ whiteSpace: 'nowrap' }}>{featureLabel(c.t, r.feature)}</div>
      </>
    ),
  },
  feature: {
    head: 'usage.th.feature', num: false,
    cell: (r, c) => <span title={r.feature} style={{ whiteSpace: 'nowrap' }}>{featureLabel(c.t, r.feature)}</span>,
  },
  kind: {
    head: 'ul.th.kind', num: false,
    cell: (r, c) => <span style={{ whiteSpace: 'nowrap' }}>{c.t(`usage.cap.${r.capability || 'llm'}`)}</span>,
  },
  model: {
    head: 'ul.th.model', sort: 'model', num: false,
    cell: r => <span style={{ whiteSpace: 'nowrap' }}>{providerModel(r.provider, r.model)}</span>,
  },
  in: { head: 'usage.th.in', sort: 'input_tokens', num: true, cell: r => count(r.input_tokens) },
  out: { head: 'usage.th.out', sort: 'output_tokens', num: true, cell: r => count(r.output_tokens) },
  cached: {
    head: 'usage.th.cache', num: true,
    cell: r => (r.cache_read_tokens === null && r.cache_creation_tokens === null
      ? '—'
      : fmt(cached({ cache_read_tokens: r.cache_read_tokens ?? 0, cache_creation_tokens: r.cache_creation_tokens ?? 0 }))),
  },
  total: { head: 'usage.th.total', sort: 'total_tokens', num: true, cell: r => fmt(r.total_tokens) },
  consumed: {
    head: 'ul.th.consumed', sort: 'consumed', num: true,
    cell: (r, c) => <Consumed tokens={null} audioSeconds={r.audio_seconds} characters={r.characters} t={c.t} />,
  },
  latency: { head: 'ul.th.latency', sort: 'latency_ms', num: true, cell: r => count(r.latency_ms) },
  status: {
    head: 'ul.th.status', num: false,
    cell: (r, c) => (
      <span style={{ whiteSpace: 'nowrap' }}>
        <StatusPill ok={r.ok} t={c.t} />
        {r.byo ? <span className="hint" title={c.t('ul.byo.hint')} style={{ marginLeft: 6 }}>{c.t('ul.byo')}</span> : null}
      </span>
    ),
  },
  source: {
    head: 'ul.th.source', num: false,
    cell: (r, c) => {
      if (r.job_id) {
        const name = r.filename || c.t('usage.purged');
        return c.onRecording ? (
          <button type="button" data-return={r.id} style={{ ...LINK, ...CLIP, display: 'block' }} title={name}
            onClick={() => c.onRecording?.(r.job_id as string, r.id)}>
            {name}
          </button>
        ) : <div style={CLIP} title={name}>{name}</div>;
      }
      if (r.conversation_id) {
        const ref = r.conversation_ref || c.t('ul.noref');
        return (
          <>
            {c.onConversation ? (
              <button type="button" data-return={r.id} style={{ ...LINK, ...CLIP, display: 'block' }} title={ref}
                onClick={() => c.onConversation?.(r.conversation_id as string, r.id)}>
                {ref}
              </button>
            ) : <div style={CLIP} title={ref}>{ref}</div>}
            {r.turn_preview ? (
              <div className="hint" style={CLIP} title={r.turn_preview}>{r.turn_preview}</div>
            ) : null}
          </>
        );
      }
      if (r.summary_id) return c.t('ul.source.summary');
      return '—';
    },
  },
};

export function CallsTable({
  rows, cols, t, sort, onSort, onRecording, onConversation, onTenant, activeTenant = '', compact = false,
}: {
  rows: CallRow[];
  cols: CallCol[];
  t: T;
  /** Server-side sort. Omit both for a table in time order (the drill-downs). */
  sort?: SortState;
  onSort?: (key: string) => void;
  onRecording?: (jobId: string, callId: string) => void;
  onConversation?: (conversationId: string, callId: string) => void;
  onTenant?: (clientId: string) => void;
  activeTenant?: string;
  /** Smaller type for the tables nested under a drill-down's headings. */
  compact?: boolean;
}): JSX.Element {
  const ctx: Ctx = { t, onRecording, onConversation, onTenant, activeTenant };
  return (
    <div className="table-wrap">
      <table style={compact ? { fontSize: 12.5 } : undefined}>
        <thead>
          <tr>
            {cols.map(k => {
              const d = COLS[k];
              if (sort && onSort && d.sort) {
                return <SortTh key={k} label={t(d.head)} k={d.sort} sort={sort} onSort={onSort} num={d.num} />;
              }
              return <th key={k} style={{ textAlign: d.num ? 'right' : 'left', whiteSpace: 'nowrap' }}>{t(d.head)}</th>;
            })}
          </tr>
        </thead>
        <tbody>
          {rows.map(r => (
            <tr key={r.id}>
              {cols.map(k => {
                const d = COLS[k];
                return <td key={k} style={d.num ? NUM : undefined}>{d.cell(r, ctx)}</td>;
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
