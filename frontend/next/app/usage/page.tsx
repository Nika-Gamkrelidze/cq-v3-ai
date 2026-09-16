'use client';
import { useCallback, useEffect, useState } from 'react';
import Header from '@/components/Header';
import { readSession } from '@/lib/session';
import { useI18n } from '@/lib/useI18n';
import CallsTab from './CallsTab';
import ConversationsTab from './ConversationsTab';
import FilterBar from './Filters';
import OverviewTab from './OverviewTab';
import RecordingsTab from './RecordingsTab';
import { EMPTY_FILTERS, type Filters, type TabProps } from './types';

/* AI usage, for operators.
   =======================
   Where every AI call the platform makes is accounted for: text models in tokens,
   speech-to-text in tokens or in audio length (ElevenLabs Scribe reports no tokens),
   text-to-speech in characters. Four views over one filter bar — the totals, each recording,
   each chat conversation, and every call — so a figure on the overview can be followed down
   to the calls that make it up without re-choosing the period or the workspace.

   It shows USAGE, not money. A price per million depends on the model and on the contract,
   and a console that multiplies by a hardcoded rate is a console that quotes the wrong number
   confidently. Operators apply their own rate to these figures. */

const TABS = [
  { key: 'overview', label: 'usage.tab.overview' },
  { key: 'recordings', label: 'usage.tab.recordings' },
  { key: 'bot', label: 'usage.tab.bot' },
  { key: 'calls', label: 'usage.tab.calls' },
] as const;
type TabKey = typeof TABS[number]['key'];

const TAB_STORE = 'cq_usage_tab';
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

const isTab = (v: unknown): v is TabKey => TABS.some(x => x.key === v);

export default function UsagePage() {
  const { t } = useI18n();
  const [ready, setReady] = useState(false);
  const [isOperator, setIsOperator] = useState(false);
  const [tab, setTab] = useState<TabKey>('overview');
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);

  // The session, the remembered tab and the hash are only readable in the browser: this page
  // is prerendered at build time. All three are settled in ONE effect, before `ready`, so the
  // first request a tab makes already carries the deep-linked workspace instead of fetching
  // the unfiltered page and then throwing it away.
  useEffect(() => {
    setIsOperator(readSession().role === 'superadmin');
    try {
      const stored = localStorage.getItem(TAB_STORE);
      if (isTab(stored)) setTab(stored);
    } catch { /* storage blocked: start on the overview */ }

    // The AI-setup console links here as /usage#<client_id>. Honoured once, on the overview —
    // the view that answers "what did this workspace spend" — and then removed from the URL,
    // so a reload after clearing the filter does not quietly put it back.
    const hash = window.location.hash.slice(1);
    if (UUID.test(hash)) {
      setFilters(f => ({ ...f, client_id: hash.toLowerCase() }));
      setTab('overview');
      history.replaceState(null, '', window.location.pathname + window.location.search);
    }
    setReady(true);
  }, []);

  const choose = (key: TabKey) => {
    setTab(key);
    try { localStorage.setItem(TAB_STORE, key); } catch { /* private mode */ }
  };

  const onFilter = useCallback((patch: Partial<Filters>) => setFilters(f => ({ ...f, ...patch })), []);

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

  const props: TabProps = { filters, onFilter, t };

  return (
    <>
      <Header tag="Console" />
      <main className="console">
        <div style={{ marginBottom: 18 }}>
          <h1 style={{ margin: 0, fontSize: 'clamp(24px,4vw,32px)' }}>{t('usage.title')}</h1>
          <p className="lead">{t('usage.lead')}</p>
        </div>

        <div className="tabs" role="tablist" aria-label={t('usage.tabs')}>
          {TABS.map(x => (
            <button
              key={x.key}
              type="button"
              role="tab"
              id={`usage-tab-${x.key}`}
              aria-selected={tab === x.key}
              aria-controls="usage-panel"
              className={`tab${tab === x.key ? ' active' : ''}`}
              onClick={() => choose(x.key)}
            >
              {t(x.label)}
            </button>
          ))}
        </div>

        <FilterBar {...props} />

        {/* Only the active tab is mounted. Every tab refetches when the shared filters change,
            and a hidden one doing so would multiply the heaviest queries for a view nobody is
            reading; what must survive a tab switch — the filters — lives up here. */}
        <section id="usage-panel" role="tabpanel" aria-labelledby={`usage-tab-${tab}`}>
          {tab === 'overview' ? <OverviewTab {...props} /> : null}
          {tab === 'recordings' ? <RecordingsTab {...props} /> : null}
          {tab === 'bot' ? <ConversationsTab {...props} /> : null}
          {tab === 'calls' ? <CallsTab {...props} /> : null}
        </section>
      </main>
    </>
  );
}
