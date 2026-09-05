'use client';
import { useCallback, useEffect, useState } from 'react';
import Header from '@/components/Header';
import { apiGet, readSession } from '@/lib/session';
import { useI18n } from '@/lib/useI18n';

/* AI usage, for operators.
   =======================
   The first screen built on the new stack, and chosen for that on purpose: it is brand new,
   so there is no working page to regress, and it exercises everything the migration needs to
   prove — the shared header, the role-derived nav, i18n, session-aware fetches against the
   FastAPI backend, and a clean URL with no .html in it.

   It shows TOKENS, not money. A price per million depends on the model and on the contract,
   and a console that multiplies by a hardcoded rate is a console that quotes the wrong number
   confidently. Operators apply their own rate to these figures. */

interface Totals {
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_creation_tokens: number;
  total_tokens: number;
  byo_tokens: number;
  calls: number;
  failed: number;
  last_used?: string | null;
}

interface TenantRow extends Totals {
  client_id: string | null;
  name: string;
  slug: string | null;
}

interface Breakdown {
  window: string;
  total: Totals;
  by_user: (Totals & { actor: string })[];
  by_feature: (Totals & { feature: string })[];
  by_model: (Totals & { model: string })[];
  by_job: (Totals & { job_id: string | null; filename: string | null })[];
}

const WINDOWS = ['24h', '7d', '30d', '90d'] as const;

const fmt = (n: number) => (n ?? 0).toLocaleString();
const when = (iso?: string | null) => (iso ? new Date(iso).toLocaleString() : '—');

export default function UsagePage() {
  const { t } = useI18n();
  const [ready, setReady] = useState(false);
  const [isOperator, setIsOperator] = useState(false);
  const [window_, setWindow] = useState<string>('30d');
  const [rows, setRows] = useState<TenantRow[] | null>(null);
  const [error, setError] = useState('');
  const [openId, setOpenId] = useState<string | null>(null);
  const [openName, setOpenName] = useState('');
  const [detail, setDetail] = useState<Breakdown | null>(null);

  // The session is only readable in the browser: this page is prerendered at build time.
  useEffect(() => {
    setIsOperator(readSession().role === 'superadmin');
    setReady(true);
  }, []);

  const loadTenants = useCallback(async () => {
    setError('');
    setRows(null);
    try {
      const d = await apiGet<{ tenants: TenantRow[] }>(`/admin/usage/tenants?window=${window_}`);
      setRows(d.tenants || []);
    } catch (e) {
      setError(e instanceof Error ? e.message : t('usage.loadfail'));
    }
  }, [window_, t]);

  useEffect(() => {
    if (ready && isOperator && !openId) void loadTenants();
  }, [ready, isOperator, openId, loadTenants]);

  const open = useCallback(async (row: TenantRow) => {
    if (!row.client_id) return;                 // a deleted workspace has nothing to drill into
    setOpenId(row.client_id);
    setOpenName(row.name);
    setDetail(null);
    setError('');
    try {
      setDetail(await apiGet<Breakdown>(`/admin/usage/tenants/${row.client_id}?window=${window_}`));
    } catch (e) {
      setError(e instanceof Error ? e.message : t('usage.loadfail'));
    }
  }, [window_, t]);

  // The AI-setup console links here as /usage#<client_id>. Honoured once, after the list has
  // loaded, so the link lands on the workspace it names instead of on the list.
  const [deepLink, setDeepLink] = useState<string | null>(null);
  useEffect(() => { setDeepLink(window.location.hash.slice(1) || null); }, []);
  useEffect(() => {
    if (!deepLink || !rows) return;
    const row = rows.find(r => r.client_id === deepLink);
    setDeepLink(null);
    history.replaceState(null, '', window.location.pathname);
    // A workspace with no usage in this window is simply not in `rows`; the list stays open
    // rather than showing an empty drill-down for a tenant that spent nothing.
    if (row) void open(row);
  }, [deepLink, rows, open]);

  if (!ready) return <><Header tag="Console" /><main /></>;

  if (!isOperator) {
    return (
      <>
        <Header tag="Console" />
        <main className="narrow">
          <div className="card">
            <p className="lead">{t('usage.adminonly')}</p>
            <div className="actions">
              <a className="ghost" href="/tenant.html">{t('nav.signin')}</a>
            </div>
          </div>
        </main>
      </>
    );
  }

  return (
    <>
      <Header tag="Console" />
      <main className="console">
        <div style={{ marginBottom: 18 }}>
          <h1 style={{ margin: 0, fontSize: 'clamp(24px,4vw,32px)' }}>{t('usage.title')}</h1>
          <p className="lead">{t('usage.lead')}</p>
        </div>

        <div className="card">
          <div className="inline" style={{ gap: 10, flexWrap: 'wrap' }}>
            <label htmlFor="win" style={{ margin: 0 }}>{t('usage.window')}</label>
            <select
              id="win"
              value={window_}
              onChange={e => { setWindow(e.target.value); setOpenId(null); setDetail(null); }}
              style={{ minWidth: 190 }}
            >
              {WINDOWS.map(w => <option key={w} value={w}>{t(`usage.window.${w}`)}</option>)}
            </select>
            {openId ? (
              <button className="ghost" onClick={() => { setOpenId(null); setDetail(null); }}>
                ← {t('usage.back')}
              </button>
            ) : null}
          </div>
        </div>

        {error ? <div className="msg err">{error}</div> : null}

        {!openId ? (
          <div className="card">
            <h3>{t('usage.tenants')}</h3>
            {rows === null && !error ? (
              <div className="empty"><span className="spinner" /></div>
            ) : rows && rows.length === 0 ? (
              <div className="empty">{t('usage.none')}</div>
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>{t('usage.th.tenant')}</th>
                      <th>{t('usage.th.total')}</th>
                      <th>{t('usage.th.in')}</th>
                      <th>{t('usage.th.out')}</th>
                      <th>{t('usage.th.cache')}</th>
                      <th>{t('usage.th.calls')}</th>
                      <th>{t('usage.th.failed')}</th>
                      <th>{t('usage.th.last')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(rows || []).map(r => (
                      <tr
                        key={r.client_id || 'none'}
                        onClick={() => open(r)}
                        style={{ cursor: r.client_id ? 'pointer' : 'default' }}
                      >
                        <td><b>{r.name}</b>{r.slug ? <span className="hint"> · {r.slug}</span> : null}</td>
                        <td><b>{fmt(r.total_tokens)}</b></td>
                        <td>{fmt(r.input_tokens)}</td>
                        <td>{fmt(r.output_tokens)}</td>
                        <td>{fmt(r.cache_read_tokens + r.cache_creation_tokens)}</td>
                        <td>{fmt(r.calls)}</td>
                        <td>{r.failed ? <span className="warn-flag">{fmt(r.failed)}</span> : '—'}</td>
                        <td className="hint">{when(r.last_used)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        ) : (
          <>
            <div className="card">
              <div className="inline" style={{ justifyContent: 'space-between', flexWrap: 'wrap', gap: 10 }}>
                <h3 style={{ margin: 0 }}>{openName}</h3>
                <a className="ghost" href={`/ai-config#${openId}`}>{t('usage.configure')}</a>
              </div>
              {!detail ? (
                <div className="empty"><span className="spinner" /></div>
              ) : (
                <>
                  <div className="stat-row">
                    <Stat label={t('usage.th.total')} value={fmt(detail.total.total_tokens)} />
                    <Stat label={t('usage.th.in')} value={fmt(detail.total.input_tokens)} />
                    <Stat label={t('usage.th.out')} value={fmt(detail.total.output_tokens)} />
                    <Stat
                      label={t('usage.th.cache')}
                      value={fmt(detail.total.cache_read_tokens + detail.total.cache_creation_tokens)}
                    />
                    <Stat label={t('usage.th.calls')} value={fmt(detail.total.calls)} />
                    <Stat label={t('usage.th.failed')} value={fmt(detail.total.failed)} />
                  </div>
                  {/* Only shown when there IS spend on the customer's own key: on the ordinary
                      workspace it would be a permanent zero explaining a distinction that does
                      not apply to them. */}
                  {detail.total.byo_tokens ? (
                    <p className="hint" style={{ marginTop: 10 }}>
                      <b>{t('usage.th.byo')}: {fmt(detail.total.byo_tokens)}</b> — {t('usage.byo.hint')}
                    </p>
                  ) : null}
                </>
              )}
            </div>

            {detail ? (
              <>
                <Section
                  title={t('usage.byuser')}
                  head={t('usage.th.user')}
                  empty={t('usage.empty.section')}
                  rows={detail.by_user.map(r => ({
                    key: r.actor,
                    label: r.actor === 'unattributed' ? t('usage.unattributed') : r.actor,
                    ...r,
                  }))}
                  t={t}
                />
                <Section
                  title={t('usage.byfeature')}
                  head={t('usage.th.feature')}
                  empty={t('usage.empty.section')}
                  rows={detail.by_feature.map(r => ({ key: r.feature, label: r.feature, ...r }))}
                  t={t}
                />
                <Section
                  title={t('usage.bymodel')}
                  head={t('usage.th.model')}
                  empty={t('usage.empty.section')}
                  rows={detail.by_model.map(r => ({ key: r.model, label: r.model, ...r }))}
                  t={t}
                />
                <Section
                  title={t('usage.byjob')}
                  head={t('usage.th.job')}
                  empty={t('usage.empty.section')}
                  rows={detail.by_job.map(r => ({
                    key: r.job_id || 'none',
                    // A recording that retention has purged still owes its tokens to the
                    // total, so it is listed by what survives rather than dropped.
                    label: r.filename || t('usage.purged'),
                    ...r,
                  }))}
                  t={t}
                />
              </>
            ) : null}
          </>
        )}
      </main>
    </>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="stat">
      <b>{value}</b>
      <span>{label}</span>
    </div>
  );
}

function Section({
  title, head, rows, empty, t,
}: {
  title: string;
  head: string;
  empty: string;
  rows: (Totals & { key: string; label: string })[];
  t: (k: string) => string;
}) {
  return (
    <div className="card">
      <h3>{title}</h3>
      {!rows.length ? (
        <div className="empty">{empty}</div>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>{head}</th>
                <th>{t('usage.th.total')}</th>
                <th>{t('usage.th.in')}</th>
                <th>{t('usage.th.out')}</th>
                <th>{t('usage.th.calls')}</th>
                <th>{t('usage.th.last')}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(r => (
                <tr key={r.key}>
                  <td>{r.label}</td>
                  <td><b>{fmt(r.total_tokens)}</b></td>
                  <td>{fmt(r.input_tokens)}</td>
                  <td>{fmt(r.output_tokens)}</td>
                  <td>{fmt(r.calls)}</td>
                  <td className="hint">{when(r.last_used)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
