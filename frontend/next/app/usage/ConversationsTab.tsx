'use client';
import { useState } from 'react';
import type { JSX } from 'react';
import ConversationDetail from './ConversationDetail';
import { featureLabel } from './CallsTable';
import {
  CLIP, HeadTh, ListBody, NUM, SearchBox, TenantCell, firstDir, revealTop, rowOpen, useDebounced, usePanel, useUsageGet,
} from './listKit';
import { fmt, nextSort, usageQuery, when } from './logic';
import { Pager, SortTh } from './parts';
import { CHAT_FEATURES } from './types';
import type { ConversationRow, FeatureCell, Page, SortState, T, TabProps } from './types';

/* Chat conversations, one row per conversation the bot or the copilot spent tokens on, with a
   column per chat feature — triage, the answer, the handoff summary, the copilot — so a
   conversation that cost a lot says which of the four did it. A row opens the conversation
   turn by turn. */

const LIMIT = 50;
const ASC_FIRST: ReadonlySet<string> = new Set(['tenant', 'channel']);

function FeatureTd({ feature, cell, t }: { feature: string; cell: FeatureCell | undefined; t: T }): JSX.Element {
  if (!cell || !cell.calls) return <td style={NUM}>—</td>;
  const title = [
    featureLabel(t, feature),
    t('ul.calls.n', { n: fmt(cell.calls) }),
    cell.failed ? t('ul.failed.n', { n: fmt(cell.failed) }) : '',
  ].filter(Boolean).join(' · ');
  return (
    <td style={NUM} title={title}>
      {fmt(cell.total_tokens)}
      {cell.failed ? (
        <span className="warn-flag" style={{ marginLeft: 4 }}>
          <span aria-hidden="true">!</span>
          <span className="cq-sr">{t('ul.failed.n', { n: fmt(cell.failed) })}</span>
        </span>
      ) : null}
    </td>
  );
}

export default function ConversationsTab({ filters, onFilter, t }: TabProps): JSX.Element {
  const [qInput, setQInput] = useState('');
  const q = useDebounced(qInput.trim(), 300);
  const [sort, setSort] = useState<SortState>({ key: 'last_used', dir: 'desc' });

  // A page number belongs to one (filters, search, sort); changing any of them starts at the top.
  const base = usageQuery(filters, { q, sort: sort.key, dir: sort.dir });
  const [pageAt, setPageAt] = useState({ base, offset: 0 });
  // A new (filters, search, sort) forgets the old page at once. Only comparing would bring
  // page 3 back the moment the operator cleared a search and returned to the same base.
  if (pageAt.base !== base) setPageAt({ base, offset: 0 });
  const offset = pageAt.base === base ? pageAt.offset : 0;

  const path = `/admin/usage/conversations${usageQuery(filters, { q, sort: sort.key, dir: sort.dir, limit: LIMIT, offset })}`;
  const list = useUsageGet<Page<ConversationRow>>(path, true);
  const { panel, open, back, topRef } = usePanel<string>();

  const onSort = (key: string) => setSort(cur => nextSort(cur, key, firstDir(key, ASC_FIRST)));
  const onPage = (o: number) => { setPageAt({ base, offset: o }); revealTop(topRef.current); };
  const rows = list.data?.rows ?? [];

  return (
    <div ref={topRef}>
      {panel ? (
        <ConversationDetail conversationId={panel} t={t} onBack={back} />
      ) : (
        <div className="card">
          <div style={{ marginBottom: 12 }}>
            <SearchBox value={qInput} onChange={setQInput} label={t('ul.search.conversations')} />
          </div>
          <ListBody
            t={t}
            error={list.error}
            loading={list.loading}
            hasData={!!list.data}
            isEmpty={!rows.length}
            emptyText={q ? t('ul.empty.search', { q }) : t('ul.empty.conversations')}
            onRetry={list.retry}
          >
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    {/* No sort by the external id itself (it is the chat site's opaque id);
                        the date under it is what a person sorts by. */}
                    <HeadTh label={t('ul.th.conversation')} alt={{ label: t('ul.th.started'), k: 'created_at' }}
                      sort={sort} onSort={onSort} num={false} />
                    <SortTh label={t('usage.th.tenant')} k="tenant" sort={sort} onSort={onSort} num={false} />
                    <SortTh label={t('ul.th.channel')} k="channel" sort={sort} onSort={onSort} num={false} />
                    <SortTh label={t('ul.th.turns')} k="turns" sort={sort} onSort={onSort} />
                    <HeadTh label={t('ul.th.questions')} k="questions" tip={t('ul.th.questions.tip')} sort={sort} onSort={onSort} />
                    {CHAT_FEATURES.map(f => (
                      <HeadTh key={f} label={featureLabel(t, f)} tip={t(`ul.feature.${f}.tip`)} sort={sort} onSort={onSort} />
                    ))}
                    <SortTh label={t('usage.th.total')} k="total_tokens" sort={sort} onSort={onSort} />
                    <SortTh label={t('usage.th.calls')} k="calls" sort={sort} onSort={onSort} />
                    <SortTh label={t('usage.th.last')} k="last_used" sort={sort} onSort={onSort} num={false} />
                  </tr>
                </thead>
                <tbody>
                  {rows.map(r => {
                    const ref = r.external_ref || t('ul.noref');
                    return (
                      <tr key={r.conversation_id} {...rowOpen(r.conversation_id, () => open(r.conversation_id, r.conversation_id))}>
                        <td>
                          <div style={CLIP} title={ref}>{r.external_ref || <span className="muted">{ref}</span>}</div>
                          <div className="hint" style={CLIP} title={r.subject || undefined}>
                            {r.subject ? `${r.subject} · ` : ''}{when(r.created_at)}
                          </div>
                        </td>
                        <td>
                          <TenantCell id={r.client_id} name={r.tenant_name} active={filters.client_id}
                            onFilter={id => onFilter({ client_id: id })} t={t} />
                        </td>
                        <td style={{ whiteSpace: 'nowrap' }}>{r.channel || '—'}</td>
                        <td style={NUM}>{fmt(r.turns)}</td>
                        <td style={NUM}>{fmt(r.questions)}</td>
                        {CHAT_FEATURES.map(f => <FeatureTd key={f} feature={f} cell={r.features[f]} t={t} />)}
                        <td style={NUM}><b>{fmt(r.total.total_tokens)}</b></td>
                        <td style={NUM}>
                          {fmt(r.total.calls)}
                          {r.total.failed ? (
                            <span className="warn-flag" style={{ marginLeft: 6 }} title={t('ul.failed.n', { n: fmt(r.total.failed) })}>
                              {fmt(r.total.failed)}
                            </span>
                          ) : null}
                        </td>
                        <td style={{ whiteSpace: 'nowrap' }}>{when(r.total.last_used)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <Pager offset={offset} limit={LIMIT} total={list.data?.total ?? 0} onPage={onPage} t={t} />
          </ListBody>
        </div>
      )}
    </div>
  );
}
