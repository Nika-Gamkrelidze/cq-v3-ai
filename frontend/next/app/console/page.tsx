'use client';
import { useEffect, useState } from 'react';
import Header from '@/components/Header';
import { readSession } from '@/lib/session';
import { useI18n } from '@/lib/useI18n';
import { SIGN_IN, SessionExpired, adminGet } from './api';
import AiProvidersTab from './AiProvidersTab';
import AnonymousTab from './AnonymousTab';
import BotControlTab from './BotControlTab';
import DefaultBotTab from './DefaultBotTab';
import DefaultRubricTab from './DefaultRubricTab';
import EmbeddingsTab from './EmbeddingsTab';
import HealthTab from './HealthTab';
import IntegrationsTab from './IntegrationsTab';
import StorageTab from './StorageTab';
import TenantsTab from './TenantsTab';
import UsersTab from './UsersTab';
import VoicesTab from './VoicesTab';

/* The operator console — `admin.html` → `/console`.
   ================================================
   Eleven tabs over one credential. Everything here is `/admin/*` and therefore superadmin-only:
   there is no tenant mode on this page and no act-as-tenant header on any of its requests (see
   `api.ts`). Login itself happens on the unified sign-in page; this console is token-gated only.

   WHICH TABS ARE MOUNTED, and why it is not uniform:

     * Eight of them are mounted from the moment the console opens and hidden with `.panel`,
       exactly as the legacy page's ten `<section>`s were. That is what `loadAll()` did — every
       panel's data fetched at boot, in parallel — and it is why switching tabs here is instant
       and why a half-typed form survives a trip to another tab and back.
     * BOT CONTROL, DEFAULT BOT and AI PROVIDERS are mounted only while their tab is open, so
       they fetch on every activation. The legacy comment on the first two says why they were
       kept out of `loadAll()`: those routes ship with a feature of their own, and "a console
       that fails to open because one tab's endpoint is not deployed yet would take the kill
       switch down with it". The registry is the same kind of thing — it arrives with the
       provider feature and a console one deploy ahead of its backend must still open. Unmounting
       on the way out is the same thing the legacy `loadDefBot()` did on the way in — it
       overwrote the form from the server — so nothing is lost that was not already; the
       registry's forms live in modals, which close with the tab anyway.

   `?tab=<key>` opens the console on a tab. The AI setup page links to `?tab=ai` so an operator
   who needs a connection that does not exist yet lands on the table that creates one, not on
   Tenants. It is read once at boot and not written back — the tab strip is not a router.

   Re-translation needs no help here. The legacy page listened for `cq:lang` and re-ran six
   render functions by hand, because its tables were built as HTML strings that `applyI18n`
   could not reach; every string on this page goes through `t` during render, so a language
   change re-renders all ten panels for free. */

type TabKey =
  | 'tenants' | 'health' | 'users' | 'embeddings' | 'anon' | 'storage'
  | 'defrubric' | 'defbot' | 'ai' | 'integrations' | 'voices' | 'bot';

/* Tab strip order, from the legacy DOM. It is not alphabetical and not grouped by subject: it
   is roughly how often an operator reaches for each one. Health sits second because on a bad
   day it is the first thing an operator checks. */
const TABS: { key: TabKey; label: string }[] = [
  { key: 'tenants', label: 'adm.tenants' },
  { key: 'health', label: 'adm.health' },
  { key: 'users', label: 'pb.users' },
  { key: 'embeddings', label: 'adm.embeddings' },
  { key: 'anon', label: 'adm.anon' },
  { key: 'storage', label: 'pb.storage' },
  { key: 'defrubric', label: 'pb.defrubric' },
  { key: 'defbot', label: 'pb.defbot' },
  { key: 'ai', label: 'ai.tab' },
  { key: 'integrations', label: 'adm.integrations' },
  { key: 'voices', label: 'adm.voices' },
  { key: 'bot', label: 'adm.bot' },
];

type Phase = 'boot' | 'ready' | 'down';

export default function ConsolePage() {
  const { t } = useI18n();
  const [phase, setPhase] = useState<Phase>('boot');
  const [tab, setTab] = useState<TabKey>('tenants');

  /* The Voices tab's reload counter. A speech connection saved, defaulted or deactivated in
     the AI providers tab may mean the voice list now comes from a different account, so that
     tab bumps this and Voices re-fetches. Owned here because the two tabs never see each
     other. */
  const [voicesEpoch, setVoicesEpoch] = useState(0);

  /* Boot is one probe, not a load: `GET /admin/settings` answers the only two questions that
     decide whether this page can exist at all — is the token still good, and is the API up. The
     panels below fetch their own data, so the response itself is thrown away.

       * no token at all -> the sign-in page, before anything renders;
       * 401             -> `api.ts` has already cleared it and started the same redirect;
       * anything else   -> a visible retry, not a permanently blank page. */
  useEffect(() => {
    if (!readSession().admin) { location.replace(SIGN_IN); return; }
    const want = new URLSearchParams(location.search).get('tab');
    if (want && TABS.some(x => x.key === want)) setTab(want as TabKey);
    let live = true;
    adminGet('/admin/settings')
      .then(() => { if (live) setPhase('ready'); })
      .catch(e => {
        if (e instanceof SessionExpired) return;    // already leaving
        if (live) setPhase('down');
      });
    return () => { live = false; };
  }, []);

  if (phase !== 'ready') {
    return (
      <>
        <Header tag="Console" />
        <main className="console">
          {phase === 'down' ? (
            <div className="card" style={{ textAlign: 'center' }}>
              <p>{t('err.unavailable')}</p>
              <button className="primary" type="button" onClick={() => location.reload()}>
                {t('btn.retry')}
              </button>
            </div>
          ) : null}
        </main>
      </>
    );
  }

  return (
    <>
      <Header tag="Console" />
      <main className="console">
        <div className="tabs" role="tablist">
          {TABS.map(x => (
            <button
              key={x.key}
              type="button"
              role="tab"
              aria-selected={tab === x.key}
              aria-controls={`panel-${x.key}`}
              className={`tab${tab === x.key ? ' active' : ''}`}
              onClick={() => setTab(x.key)}
            >
              {t(x.label)}
            </button>
          ))}
        </div>

        <Panel id="tenants" tab={tab}><TenantsTab /></Panel>
        {/* Mounted on activation, like the bot tabs, for a second reason on top of theirs: the
            health tab POLLS, and its timers must stop when nobody is looking at them. */}
        <Panel id="health" tab={tab}>{tab === 'health' ? <HealthTab /> : null}</Panel>
        <Panel id="users" tab={tab}><UsersTab /></Panel>
        <Panel id="embeddings" tab={tab}><EmbeddingsTab /></Panel>
        <Panel id="anon" tab={tab}><AnonymousTab /></Panel>
        <Panel id="storage" tab={tab}><StorageTab /></Panel>
        <Panel id="defrubric" tab={tab}><DefaultRubricTab /></Panel>
        {/* Mounted on activation — see the note at the top of the file. */}
        <Panel id="defbot" tab={tab}>{tab === 'defbot' ? <DefaultBotTab /> : null}</Panel>
        <Panel id="ai" tab={tab}>
          {tab === 'ai' ? <AiProvidersTab onVoiceChanged={() => setVoicesEpoch(n => n + 1)} /> : null}
        </Panel>
        <Panel id="integrations" tab={tab}>
          <IntegrationsTab onOpenAi={() => setTab('ai')} />
        </Panel>
        <Panel id="voices" tab={tab}>
          <VoicesTab epoch={voicesEpoch} />
        </Panel>
        <Panel id="bot" tab={tab}>{tab === 'bot' ? <BotControlTab /> : null}</Panel>
      </main>
    </>
  );
}

/* `.panel` / `.panel.active` is `display:none` / `display:block` plus the fade-in, from
   globals.css. Not React's `hidden`: the class is what carries the animation, and it is what
   the legacy sheet already styles. */
function Panel({ id, tab, children }: { id: TabKey; tab: TabKey; children: React.ReactNode }) {
  return (
    <section
      id={`panel-${id}`}
      role="tabpanel"
      className={`panel${tab === id ? ' active' : ''}`}
    >
      {children}
    </section>
  );
}
