'use client';
import { useState } from 'react';
import type { JSX } from 'react';
import RecordingDetail from './RecordingDetail';
import { modelLabel } from './CallsTable';
import {
  CLIP, HeadTh, ListBody, NUM, SearchBox, TenantCell, firstDir, revealTop, rowOpen, useDebounced, usePanel, useUsageGet,
} from './listKit';
import { duration, fmt, nextSort, usageQuery, when } from './logic';
import { Consumed, Pager, SortTh, groupLabel } from './parts';
import { RECORDING_GROUPS } from './types';
import type { GroupCell, Page, RecordingRow, SortState, T, TabProps } from './types';

/* Recordings, one row per analysed call, with a column per analyser — so "which recordings
   are expensive, and which step made them so" is one sorted column away. The server sorts and
   pages; a row opens that recording's calls in place of the list. */

const LIMIT = 50;
const ASC_FIRST: ReadonlySet<string> = new Set(['filename', 'tenant']);

/** One analyser's spend on one recording. Tokens when there are any; otherwise what the
    provider did bill — ElevenLabs Scribe transcribes with no tokens at all, and a column of
    zeros would hide exactly the recordings that were long. */
function GroupTd({ group, cell, t }: { group: string; cell: GroupCell | undefined; t: T }): JSX.Element {
  if (!cell || !cell.calls) return <td style={NUM}>—</td>;
  const title = [
    groupLabel(t, group),
    t('ul.calls.n', { n: fmt(cell.calls) }),
    cell.failed ? t('ul.failed.n', { n: fmt(cell.failed) }) : '',
    cell.models.length ? t('ul.models', { list: cell.models.map(modelLabel).join(', ') }) : '',
  ].filter(Boolean).join(' · ');
  return (
    <td style={NUM} title={title}>
      {cell.total_tokens
        ? fmt(cell.total_tokens)
        : <Consumed tokens={null} audioSeconds={cell.audio_seconds} characters={cell.characters} t={t} />}
      {cell.failed ? (
        <span className="warn-flag" style={{ marginLeft: 4 }}>
          <span aria-hidden="true">!</span>
          <span className="cq-sr">{t('ul.failed.n', { n: fmt(cell.failed) })}</span>
        </span>
      ) : null}
    </td>
  );
}

export default function RecordingsTab({ filters, onFilter, t }: TabProps): JSX.Element {
  const [qInput, setQInput] = useState('');
  const q = useDebounced(qInput.trim(), 300);
  const [sort, setSort] = useState<SortState>({ key: 'last_used', dir: 'desc' });

  // The page belongs to one (filters, search, sort): change any of them and the list starts
  // again at the top, without a second request for the old offset first.
  const base = usageQuery(filters, { q, sort: sort.key, dir: sort.dir });
  const [pageAt, setPageAt] = useState({ base, offset: 0 });
  // A new (filters, search, sort) forgets the old page at once. Only comparing would bring
  // page 3 back the moment the operator cleared a search and returned to the same base.
  if (pageAt.base !== base) setPageAt({ base, offset: 0 });
  const offset = pageAt.base === base ? pageAt.offset : 0;

  const path = `/admin/usage/recordings${usageQuery(filters, { q, sort: sort.key, dir: sort.dir, limit: LIMIT, offset })}`;
  const list = useUsageGet<Page<RecordingRow>>(path, true);
  const { panel, open, back, topRef } = usePanel<string>();

  const onSort = (key: string) => setSort(cur => nextSort(cur, key, firstDir(key, ASC_FIRST)));
  const onPage = (o: number) => { setPageAt({ base, offset: o }); revealTop(topRef.current); };
  const rows = list.data?.rows ?? [];

  return (
    <div ref={topRef}>
      {panel ? (
        <RecordingDetail jobId={panel} t={t} onBack={back} />
      ) : (
        <div className="card">
          <div style={{ marginBottom: 12 }}>
            <SearchBox value={qInput} onChange={setQInput} label={t('ul.search.recordings')} />
          </div>
          <ListBody
            t={t}
            error={list.error}
            loading={list.loading}
            hasData={!!list.data}
            isEmpty={!rows.length}
            emptyText={q ? t('ul.empty.search', { q }) : t('ul.empty.recordings')}
            onRetry={list.retry}
          >
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <HeadTh label={t('ul.th.recording')} k="filename" alt={{ label: t('ul.th.date'), k: 'created_at' }}
                      sort={sort} onSort={onSort} num={false} />
                    <SortTh label={t('usage.th.tenant')} k="tenant" sort={sort} onSort={onSort} num={false} />
                    <SortTh label={t('ul.th.duration')} k="duration_s" sort={sort} onSort={onSort} />
                    {RECORDING_GROUPS.map(g => (
                      <SortTh key={g} label={groupLabel(t, g)} k={`group:${g}`} sort={sort} onSort={onSort} />
                    ))}
                    <SortTh label={t('usage.th.total')} k="total_tokens" sort={sort} onSort={onSort} />
                    <SortTh label={t('usage.th.calls')} k="calls" sort={sort} onSort={onSort} />
                    <SortTh label={t('usage.th.last')} k="last_used" sort={sort} onSort={onSort} num={false} />
                  </tr>
                </thead>
                <tbody>
                  {rows.map(r => (
                    <tr key={r.job_id} {...rowOpen(r.job_id, () => open(r.job_id, r.job_id))}>
                      <td>
                        <div style={CLIP} title={r.filename || undefined}>
                          {r.filename || <span className="muted">{t('usage.purged')}</span>}
                        </div>
                        <div className="hint" style={{ whiteSpace: 'nowrap' }}>{when(r.created_at)}</div>
                      </td>
                      <td>
                        <TenantCell id={r.client_id} name={r.tenant_name} active={filters.client_id}
                          onFilter={id => onFilter({ client_id: id })} t={t} />
                      </td>
                      <td style={NUM}>{duration(r.duration_s)}</td>
                      {RECORDING_GROUPS.map(g => <GroupTd key={g} group={g} cell={r.groups[g]} t={t} />)}
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
                  ))}
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
