'use client';
import { useState } from 'react';
import type { JSX } from 'react';
import ConversationDetail from './ConversationDetail';
import RecordingDetail from './RecordingDetail';
import { CallsTable, type CallCol } from './CallsTable';
import { ListBody, SearchBox, firstDir, revealTop, useDebounced, usePanel, useUsageGet } from './listKit';
import { nextSort, usageQuery } from './logic';
import { Pager } from './parts';
import type { CallRow, Page, SortState, TabProps } from './types';

/* Every AI call, one row each — the ledger the other tabs are sums of. Its Source column is
   the way into those sums: a call made for a recording opens that recording's analysers, a
   call made for a chat message opens the conversation at that message. */

const LIMIT = 50;
const ASC_FIRST: ReadonlySet<string> = new Set(['feature', 'provider', 'model', 'tenant']);

const COLS: CallCol[] = [
  'time', 'tenant', 'analyser', 'kind', 'model', 'in', 'out', 'cached', 'total', 'consumed', 'latency',
  'status', 'source',
];

type Panel = { kind: 'recording' | 'conversation'; id: string };

export default function CallsTab({ filters, onFilter, t }: TabProps): JSX.Element {
  const [qInput, setQInput] = useState('');
  const q = useDebounced(qInput.trim(), 300);
  const [sort, setSort] = useState<SortState>({ key: 'created_at', dir: 'desc' });

  // A page number belongs to one (filters, search, sort); changing any of them starts at the top.
  const base = usageQuery(filters, { q, sort: sort.key, dir: sort.dir });
  const [pageAt, setPageAt] = useState({ base, offset: 0 });
  // A new (filters, search, sort) forgets the old page at once. Only comparing would bring
  // page 3 back the moment the operator cleared a search and returned to the same base.
  if (pageAt.base !== base) setPageAt({ base, offset: 0 });
  const offset = pageAt.base === base ? pageAt.offset : 0;

  const path = `/admin/usage/calls${usageQuery(filters, { q, sort: sort.key, dir: sort.dir, limit: LIMIT, offset })}`;
  const list = useUsageGet<Page<CallRow>>(path, true);
  const { panel, open, back, topRef } = usePanel<Panel>();

  const onSort = (key: string) => setSort(cur => nextSort(cur, key, firstDir(key, ASC_FIRST)));
  const onPage = (o: number) => { setPageAt({ base, offset: o }); revealTop(topRef.current); };
  const rows = list.data?.rows ?? [];

  return (
    <div ref={topRef}>
      {panel?.kind === 'recording' ? (
        <RecordingDetail jobId={panel.id} t={t} onBack={back} backLabel={t('ul.back.calls')} />
      ) : panel?.kind === 'conversation' ? (
        <ConversationDetail conversationId={panel.id} t={t} onBack={back} backLabel={t('ul.back.calls')} />
      ) : (
        <div className="card">
          <div style={{ marginBottom: 12 }}>
            <SearchBox value={qInput} onChange={setQInput} label={t('ul.search.calls')} />
          </div>
          <ListBody
            t={t}
            error={list.error}
            loading={list.loading}
            hasData={!!list.data}
            isEmpty={!rows.length}
            emptyText={q ? t('ul.empty.search', { q }) : t('ul.empty.calls')}
            onRetry={list.retry}
          >
            <CallsTable
              rows={rows}
              cols={COLS}
              t={t}
              sort={sort}
              onSort={onSort}
              onTenant={id => onFilter({ client_id: id })}
              activeTenant={filters.client_id}
              // Focus returns to the Source link the panel was opened from (`data-return`).
              onRecording={(id, callId) => open({ kind: 'recording', id }, callId)}
              onConversation={(id, callId) => open({ kind: 'conversation', id }, callId)}
            />
            <Pager offset={offset} limit={LIMIT} total={list.data?.total ?? 0} onPage={onPage} t={t} />
          </ListBody>
        </div>
      )}
    </div>
  );
}
