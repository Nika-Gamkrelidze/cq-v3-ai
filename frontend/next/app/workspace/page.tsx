'use client';
/* The workspace portal — `tenant.html` → `/workspace`.
   ===================================================
   THIS PAGE IS BOTH CONSOLES.

   The operator's KB console used to be a second implementation of everything below
   (`kb-admin.html`), and the two drifted: the same feature with two behaviours and two sets of
   bugs. They are now one page. A superadmin arrives with an admin token, picks a workspace
   from the header, and every call goes to the CUSTOMER'S OWN route with `X-Act-As-Tenant`
   beside the admin token — so what the operator sees is what the customer sees, by
   construction rather than by discipline.

   The picker grants nothing. It names which workspace an already-authorised operator is
   looking at; every route re-checks the superadmin token server-side, and a tenant's browser
   has no admin token to send in the first place. Calls to `/admin/...` (the workspace list)
   must NOT carry the scope header, or they would present as a tenant and be refused —
   `adminOnlyHeaders()`, exactly `tenant.html`'s `adminOnlyH()`.

   Only the shell lives here: boot, the gate, the picker, the tabs and the 401 funnel. Each tab
   is its own file. */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Header from '@/components/Header';
import { Select } from '@/components/ui/Select';
import { toast } from '@/components/ui/Toast';
import type { WorkbenchHandle } from '@/components/Workbench';
import {
  ApiError, apiGet, apiMessage, apiSend, getActingTenant, readSession, setActingTenant,
} from '@/lib/session';
import { useI18n } from '@/lib/useI18n';
import { AiTab } from './AiTab';
import { AnalyseTab } from './AnalyseTab';
import { BotTab } from './BotTab';
import { makeJson, makeSend, SCOPE, WsContext, type TabName, type Ws } from './ctx';
import { Gate } from './Gate';
import { HealthTab } from './HealthTab';
import { HistoryTab } from './HistoryTab';
import { KbTab } from './KbTab';
import { RubricTab } from './RubricTab';
import { SentimentTab } from './SentimentTab';
import { TranscriptionTab } from './TranscriptionTab';

interface TenantRow { id: string; name: string }
interface LoginReply {
  scope: 'admin' | 'tenant' | 'user';
  token: string;
  role?: string;
  client?: { id: string; name: string; slug: string | null };
}
interface MeReply {
  kind: string;
  role?: string;
  client?: { name: string; slug: string | null };
}

/* Allowlist for `?next=` after an admin sign-in. It is an allowlist and not a redirect because
   the value comes from the URL — anything else is an open redirect wearing a convenience's
   clothes. Both spellings of the console are listed on purpose: this is one of the three places
   MIGRATION.md flags as hardcoding `admin.html`, and they have to move together on the day that
   page becomes `/console`. */
const NEXT_OK = ['admin.html', 'kb-admin.html', '/console'];
/* The console has moved to `/console` in this same tree, so the address moves with it — that is
   the whole point of MIGRATION.md listing the three hardcodings together. The two .html
   spellings stay in the allowlist above only for a `?next=` produced by a page that has not been
   deleted yet. */
const CONSOLE_HREF = '/console';

const TABS: { key: TabName; icon: string; label: string }[] = [
  { key: 'kb', icon: '📚', label: 'tab.kb' },
  { key: 'analyze', icon: '🎧', label: 'tab.analyze' },
  { key: 'rubric', icon: '🎯', label: 'tab.scoring' },
  /* Beside the rubric because the rubric now DEPENDS on it: the courtesy dimension is scored
     from the tone analyser this tab configures, so the guidance written here moves a number on
     every scorecard. It also carries the only place in the product that says out loud whether
     the voice half is actually running. */
  { key: 'sentiment', icon: '💬', label: 'snt.tab' },
  /* Its own tab, beside the rubric and the bot, because it is the same KIND of thing: a
     per-workspace AI setting with an inherited default. It sits before them deliberately —
     transcription is upstream of everything else here, and a rubric scored off a misheard word
     is a compliance verdict made of a typo. `tr.heading` is the shared feature string the
     console and the upload panel also use, so the three surfaces are named alike. */
  { key: 'transcription', icon: '🎙', label: 'tr.heading' },
  /* Same KIND of thing again — a per-workspace AI setting with an inherited default — and the
     one underneath all the others: which provider key the text and voice models run on. It
     sits right after transcription so the three settings tabs read as a group. `ai.tab` is the
     shared vocabulary the console's registry uses, so the customer and the operator call the
     surface the same thing. */
  { key: 'ai', icon: '🔑', label: 'ai.tab' },
  { key: 'health', icon: '🩺', label: 'cur.tab' },
  { key: 'bot', icon: '🤖', label: 'tab.bot' },
  { key: 'history', icon: '🗂', label: 'tab.history' },
];
const TAB_KEYS = TABS.map(x => x.key);

export default function WorkspacePage() {
  const { t } = useI18n();

  const [boot, setBoot] = useState<'checking' | 'gate' | 'app'>('checking');
  const [notice, setNotice] = useState<React.ReactNode>(null);
  /** Operator mode — an admin token and NO tenant session. A superadmin who deliberately signed
      into a workspace stays that workspace's user, exactly as before. */
  const [operator, setOperator] = useState(false);
  const [role, setRole] = useState('');              // tenant user role (owner|member)
  const [tenants, setTenants] = useState<TenantRow[] | null>(null);
  const [tenantHint, setTenantHint] = useState('');
  const [tid, setTid] = useState('');
  const [tab, setTab] = useState<TabName>('kb');

  /* Switching workspace starts fresh loads while the previous ones are still in flight.
     Whichever lands last wins the DOM, so without this an operator who switches quickly can end
     up reading workspace A's documents under workspace B's name. Every load carries the
     generation it was issued in; a reply from an older one is dropped. */
  const [genState, setGenState] = useState(0);
  const genRef = useRef(0);
  const gen = useCallback(() => genRef.current, []);

  const tidRef = useRef('');
  useEffect(() => { tidRef.current = tid; }, [tid]);

  const wbRef = useRef<WorkbenchHandle | null>(null);

  /** Mirrors the server's `may_configure_workspace`: an operator acting on a workspace has the
      same authority over its settings as the account's own owner. */
  const canConfigure = operator || role === 'owner';
  const ready = !operator || !!tid;

  /* ------------------------------------------------------------------ the 401 funnel */

  const sessionExpired = useCallback(() => {
    try { sessionStorage.removeItem('cq_tenant_token'); } catch { /* private mode */ }
    setBoot('gate');
    setNotice(t('session.expired'));
    document.title = 'CommuniQ — ' + t('login.heading');
  }, [t]);

  /** An expired token mid-session: one gate, one message, instead of every feature quietly
      failing in its own way. Every API caller funnels through here. */
  const unauthorized = useCallback(() => {
    // `else if`, deliberately: sessionExpired() clears the tenant token, which would make
    // "am I an operator" flip TRUE inside the SAME call and tear down a still-valid console
    // session too. Both conditions re-read the store on purpose, so the hazard the `else if`
    // guards is the real one and not an artefact of a snapshot taken above.
    if (readSession().tenant) sessionExpired();
    else if (isOperatorNow() && tidRef.current) {
      // An expired admin token must not leave every panel failing in its own way either.
      // Guarded on the selected workspace: a 401 from a call made before one was chosen means
      // that call forgot its scope header — throwing away a perfectly good console session over
      // it would turn one caller's bug into a mystery logout.
      try { sessionStorage.removeItem('cq_admin_token'); } catch { /* private mode */ }
      setActingTenant(null);
      location.replace('/workspace');
    }
  }, [sessionExpired]);

  const funnel = useCallback((e: unknown) => {
    if (e instanceof ApiError && e.status === 401) unauthorized();
  }, [unauthorized]);

  const json = useMemo(() => makeJson(funnel, gen), [funnel, gen]);
  const send = useMemo(() => makeSend(funnel, t), [funnel, t]);

  /* ------------------------------------------------------------------ entering */

  const enterAsOperator = useCallback(async () => {
    setOperator(true);
    setBoot('app');
    document.title = 'CommuniQ — ' + t('nav.kb');

    let list: unknown = null;
    try {
      list = await apiGet<unknown>('/admin/tenants', { scope: 'admin' });
    } catch {
      // null means the request failed. Saying "no workspaces yet" then would be a claim about
      // the customer list made from a request that never reached it.
      setTenants(null);
      setTenantHint(t('err.unavailable'));
      return;
    }
    const rows: TenantRow[] = Array.isArray(list)
      ? (list as TenantRow[])
      : (Array.isArray((list as { tenants?: TenantRow[] }).tenants) ? (list as { tenants: TenantRow[] }).tenants : []);
    if (!rows.length) { setTenants([]); setTenantHint(t('con.tenant.none')); return; }
    setTenants(rows);
    setTenantHint('');

    // Remember the workspace across reloads — an operator working one account should not
    // re-pick it after every refresh. ?tenant= wins, so a shared link opens where it says.
    const wanted = new URLSearchParams(location.search).get('tenant') || getActingTenant();
    const picked = wanted && rows.some(x => String(x.id) === wanted) ? wanted : String(rows[0].id);
    // BEFORE anything mounts that fetches: `setActingTenant` is what puts the workspace on
    // every request, and the workbench starts fetching the moment it appears.
    setActingTenant(picked);
    setTid(picked);
  }, [t]);

  const enter = useCallback((client: MeReply['client'] | LoginReply['client'], r?: string) => {
    setOperator(false);
    setRole(r || '');
    setBoot('app');
    // The workspace's name lives in the document title. The legacy header had a `who` slot
    // beside the brand tag for it; the shared <Header/> has none, and inventing one here would
    // mean editing a phase-1 component every other page also renders.
    if (client) document.title = 'CommuniQ — ' + client.name;
  }, []);

  /* ------------------------------------------------------------------ boot */

  useEffect(() => {
    const s = readSession();
    if (s.tenant) {
      // Freeze the gate while the session check is in flight, so a user with a valid session
      // never sees an interactive sign-in form flash at them.
      setNotice(t('login.checking'));
      apiGet<MeReply>('/auth/me', { scope: SCOPE })
        .then(me => {
          setNotice(null);
          if (me.kind === 'tenant') enter(me.client, me.role);
          else { try { sessionStorage.removeItem('cq_tenant_token'); } catch { /* */ } setBoot('gate'); }
        })
        .catch((e: unknown) => {
          setNotice(null);
          const status = e instanceof ApiError ? e.status : 0;
          if (status === 401 || status === 403) {
            try { sessionStorage.removeItem('cq_tenant_token'); } catch { /* */ }
            setBoot('gate');
            return;
          }
          // Network/API down: KEEP the token and offer a retry, rather than signing somebody
          // out over an outage.
          setBoot('gate');
          setNotice(
            <>
              {t('err.unavailable')}{' '}
              <button type="button" className="ghost" onClick={() => location.reload()}>{t('btn.retry')}</button>
            </>,
          );
        });
      return;
    }
    if (s.admin) {
      // A superadmin with a live console session gets the console itself — this page, in
      // operator mode, with a workspace picker — instead of a password prompt for an account
      // they do not have.
      apiGet<MeReply>('/auth/me', { scope: 'admin' })
        .then(me => {
          if (me && me.kind === 'superadmin') return enterAsOperator();
          try { sessionStorage.removeItem('cq_admin_token'); } catch { /* */ }
          setBoot('gate');
          return undefined;
        })
        .catch(() => { setBoot('gate'); });
      return;
    }
    setBoot('gate');
    // Runs once, on mount: this is the session check, not a reaction to anything on screen.
  }, []);   // eslint-disable-line react-hooks/exhaustive-deps

  /* ------------------------------------------------------------------ sign in */

  const login = useCallback(async (username: string, password: string) => {
    setNotice(null);
    let d: LoginReply;
    try {
      d = await apiSend<LoginReply>('POST', '/auth/login', { username, password }, { scope: 'public' });
    } catch (e) {
      throw new Error(e instanceof ApiError && e.status === 401 ? t('login.failed') : apiMessage(e, t));
    }
    if (d.scope === 'admin') {
      try { sessionStorage.setItem('cq_admin_token', d.token); } catch { /* private mode */ }
      const next = new URLSearchParams(location.search).get('next');
      location.href = next && NEXT_OK.includes(next) ? next : CONSOLE_HREF;
      return;
    }
    try { sessionStorage.setItem('cq_tenant_token', d.token); } catch { /* private mode */ }
    enter(d.client, d.role);
    toast(t('toast.welcome') + (d.client ? ', ' + d.client.name : ''), 'ok');
  }, [enter, t]);

  /* ------------------------------------------------------------------ tab plumbing */

  const showTab = useCallback((next: TabName) => { setTab(next); }, []);

  /* The workbench's "edit the rubric" is an <a href="/workspace#rubric"> inside a component
     this page does not own, so the hash is the contract. Handled BOTH ways: the capture-phase
     click below keeps it a tab switch rather than a navigation, and `hashchange` catches a link
     followed from anywhere else (or a bookmark). */
  useEffect(() => {
    const fromHash = () => {
      const h = location.hash.slice(1) as TabName;
      if (TAB_KEYS.includes(h)) setTab(h);
    };
    fromHash();
    window.addEventListener('hashchange', fromHash);
    return () => window.removeEventListener('hashchange', fromHash);
  }, []);

  const onAppClick = useCallback((e: React.MouseEvent) => {
    const a = (e.target as HTMLElement).closest?.('a[href$="#rubric"]');
    if (!a) return;
    e.preventDefault();
    setTab('rubric');
  }, []);

  const openRecording = useCallback((id: string) => {
    setTab('analyze');
    wbRef.current?.open(id);
  }, []);
  const openSummary = useCallback((id: string) => {
    setTab('analyze');
    wbRef.current?.openSummary(id);
  }, []);

  const pickWorkspace = useCallback((next: string) => {
    setActingTenant(next);
    genRef.current += 1;                 // abandon anything still in flight for the old one
    setGenState(genRef.current);
    setTid(next);
  }, []);

  const ws: Ws = useMemo(() => ({
    t, operator, tid, ready, canConfigure, gen, json, send, funnel, unauthorized,
    showTab, openRecording, openSummary,
  }), [t, operator, tid, ready, canConfigure, gen, json, send, funnel, unauthorized,
    showTab, openRecording, openSummary]);

  /* ------------------------------------------------------------------ render */

  if (boot !== 'app') {
    return (
      <>
        <Header key="out" tag="Tenant" />
        <main className="console">
          <Gate frozen={boot === 'checking'} notice={notice} onLogin={login} />
        </main>
      </>
    );
  }

  return (
    <>
      {/* Only the TAG differs by mode; the navigation itself is the shared, role-derived list,
          so a person sees the same options here as on every other page.

          The `key` is what replaces `CQ.refreshNav()`: signing in here does not reload the page,
          and the header reads the session once, on mount. Changing the key remounts it so the
          bar follows the session — without reaching into a component this page does not own. */}
      <Header key={operator ? 'operator' : 'tenant'} tag={operator ? 'Console' : 'Tenant'} />
      <main className="console" onClickCapture={onAppClick}>
        {/* Operator only: which workspace this page is showing. Hidden entirely for a customer,
            who has one workspace and no business seeing a list of the others. */}
        {operator && (
          <div className="card" style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
            <label htmlFor="tenantPick" style={{ margin: 0 }}>{t('con.tenant')}</label>
            <Select
              id="tenantPick"
              value={tid}
              onChange={pickWorkspace}
              options={(tenants || []).map(x => ({ value: String(x.id), label: x.name }))}
              ariaLabel={t('con.tenant')}
              style={{ flex: 1, minWidth: 220, maxWidth: 420 }}
            />
            {tenantHint ? <span className="hint">{tenantHint}</span> : null}
          </div>
        )}

        <div className="tabs">
          {TABS.map(x => (
            <div
              key={x.key}
              className={`tab${tab === x.key ? ' active' : ''}`}
              role="tab" tabIndex={0} aria-selected={tab === x.key}
              onClick={() => setTab(x.key)}
              onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setTab(x.key); } }}
            >
              <span>{x.icon}</span> <span>{t(x.label)}</span>
            </div>
          ))}
        </div>

        <WsContext.Provider value={ws}>
          {/* Every panel stays MOUNTED and is hidden with `.panel`, exactly as the legacy DOM
              did it. Each one loads when it becomes visible (and reloads when the workspace
              changes under it), so nothing fetches for a tab nobody has opened — and the
              workbench, which History opens rows into, survives a trip through other tabs. */}
          <section className={`panel${tab === 'kb' ? ' active' : ''}`}><KbTab on={tab === 'kb'} gen={genState} /></section>
          <section className={`panel${tab === 'analyze' ? ' active' : ''}`}><AnalyseTab wbRef={wbRef} /></section>
          <section className={`panel${tab === 'rubric' ? ' active' : ''}`}><RubricTab on={tab === 'rubric'} gen={genState} /></section>
          <section className={`panel${tab === 'sentiment' ? ' active' : ''}`}><SentimentTab on={tab === 'sentiment'} gen={genState} /></section>
          <section className={`panel${tab === 'transcription' ? ' active' : ''}`}><TranscriptionTab on={tab === 'transcription'} gen={genState} /></section>
          <section className={`panel${tab === 'ai' ? ' active' : ''}`}><AiTab on={tab === 'ai'} gen={genState} /></section>
          <section className={`panel${tab === 'health' ? ' active' : ''}`}><HealthTab on={tab === 'health'} gen={genState} /></section>
          <section className={`panel${tab === 'bot' ? ' active' : ''}`}><BotTab on={tab === 'bot'} gen={genState} /></section>
          <section className={`panel${tab === 'history' ? ' active' : ''}`}><HistoryTab on={tab === 'history'} gen={genState} /></section>
        </WsContext.Provider>
      </main>
    </>
  );
}

/** Operator mode, read LIVE from the store — see the note in the 401 funnel. */
function isOperatorNow(): boolean {
  const s = readSession();
  return !!s.admin && !s.tenant;
}
