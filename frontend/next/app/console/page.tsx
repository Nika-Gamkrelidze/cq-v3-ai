'use client';
import { useCallback, useEffect, useRef, useState } from 'react';
import Header from '@/components/Header';
import { readSession } from '@/lib/session';
import { useI18n } from '@/lib/useI18n';
import { SIGN_IN, SessionExpired, adminGet } from './api';
import type { PreviewCache } from './api';
import AnonymousTab from './AnonymousTab';
import BotControlTab from './BotControlTab';
import DefaultBotTab from './DefaultBotTab';
import DefaultRubricTab from './DefaultRubricTab';
import EmbeddingsTab from './EmbeddingsTab';
import IntegrationsTab from './IntegrationsTab';
import StorageTab from './StorageTab';
import TenantsTab from './TenantsTab';
import UsersTab from './UsersTab';
import VoicesTab from './VoicesTab';

/* The operator console — `admin.html` → `/console`.
   ================================================
   Ten tabs over one credential. Everything here is `/admin/*` and therefore superadmin-only:
   there is no tenant mode on this page and no act-as-tenant header on any of its requests (see
   `api.ts`). Login itself happens on the unified sign-in page; this console is token-gated only.

   WHICH TABS ARE MOUNTED, and why it is not uniform:

     * Eight of them are mounted from the moment the console opens and hidden with `.panel`,
       exactly as the legacy page's ten `<section>`s were. That is what `loadAll()` did — every
       panel's data fetched at boot, in parallel — and it is why switching tabs here is instant
       and why a half-typed form survives a trip to another tab and back.
     * BOT CONTROL and DEFAULT BOT are mounted only while their tab is open, so they fetch on
       every activation. Their comment in the legacy file says why they were kept out of
       `loadAll()`: those two routes ship with the chat feature, and "a console that fails to
       open because one tab's endpoint is not deployed yet would take the kill switch down with
       it". Unmounting on the way out is the same thing the legacy `loadDefBot()` did on the way
       in — it overwrote the form from the server — so nothing is lost that was not already.

   Re-translation needs no help here. The legacy page listened for `cq:lang` and re-ran six
   render functions by hand, because its tables were built as HTML strings that `applyI18n`
   could not reach; every string on this page goes through `t` during render, so a language
   change re-renders all ten panels for free. */

type TabKey =
  | 'tenants' | 'users' | 'embeddings' | 'anon' | 'storage'
  | 'defrubric' | 'defbot' | 'integrations' | 'voices' | 'bot';

/* Tab strip order, from the legacy DOM. It is not alphabetical and not grouped by subject: it
   is roughly how often an operator reaches for each one. */
const TABS: { key: TabKey; label: string }[] = [
  { key: 'tenants', label: 'adm.tenants' },
  { key: 'users', label: 'pb.users' },
  { key: 'embeddings', label: 'adm.embeddings' },
  { key: 'anon', label: 'adm.anon' },
  { key: 'storage', label: 'pb.storage' },
  { key: 'defrubric', label: 'pb.defrubric' },
  { key: 'defbot', label: 'pb.defbot' },
  { key: 'integrations', label: 'adm.integrations' },
  { key: 'voices', label: 'adm.voices' },
  { key: 'bot', label: 'adm.bot' },
];

type Phase = 'boot' | 'ready' | 'down';

export default function ConsolePage() {
  const { t } = useI18n();
  const [phase, setPhase] = useState<Phase>('boot');
  const [tab, setTab] = useState<TabKey>('tenants');

  /* The voice preview map, shared by two tabs and owned by neither.

     Integrations previews a voice id by its free `preview_url` rather than by spending an
     ElevenLabs TTS call, and the map it looks that up in comes from `/admin/voices` — the
     UNFILTERED list, because the public one is curated and a hidden voice would miss the map
     and fall through to a paid POST. Both tabs invalidate it: a new ElevenLabs key may be a
     different account entirely, and saving the allowlist changes what is in the list. A ref
     rather than state because nothing renders from it. */
  const previews = useRef<PreviewCache>({ fetched: false, map: {} });
  const [voicesEpoch, setVoicesEpoch] = useState(0);

  const dropPreviews = useCallback(() => { previews.current = { fetched: false, map: {} }; }, []);

  /* Boot is one probe, not a load: `GET /admin/settings` answers the only two questions that
     decide whether this page can exist at all — is the token still good, and is the API up. The
     panels below fetch their own data, so the response itself is thrown away.

       * no token at all -> the sign-in page, before anything renders;
       * 401             -> `api.ts` has already cleared it and started the same redirect;
       * anything else   -> a visible retry, not a permanently blank page. */
  useEffect(() => {
    if (!readSession().admin) { location.replace(SIGN_IN); return; }
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
        <Panel id="users" tab={tab}><UsersTab /></Panel>
        <Panel id="embeddings" tab={tab}><EmbeddingsTab /></Panel>
        <Panel id="anon" tab={tab}><AnonymousTab /></Panel>
        <Panel id="storage" tab={tab}><StorageTab /></Panel>
        <Panel id="defrubric" tab={tab}><DefaultRubricTab /></Panel>
        {/* Mounted on activation — see the note at the top of the file. */}
        <Panel id="defbot" tab={tab}>{tab === 'defbot' ? <DefaultBotTab /> : null}</Panel>
        <Panel id="integrations" tab={tab}>
          <IntegrationsTab
            previews={previews}
            onKeysChanged={() => { dropPreviews(); setVoicesEpoch(n => n + 1); }}
          />
        </Panel>
        <Panel id="voices" tab={tab}>
          <VoicesTab epoch={voicesEpoch} onSaved={dropPreviews} />
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
