'use client';
/* account.html -> /account — the REGISTERED-USER page (design-v2.md §13.5).
   =======================================================================
   Two pages behind one address, and the first half is the important one:

   THIS IS THE APP'S ONLY SIGN-IN DOOR. `POST /auth/login` serves all three kinds of account,
   so the gate below signs in a registered user, a workspace user and the operator, and ROUTES
   by the scope the server answers with. The nav's only signed-out entry points here. If the
   routing stops working, operators cannot get in at all — see `Gate.tsx`, where the two fixes
   that made that true are written down beside the code they belong to.

   Signed in, it is everything a personal account can do: the shared workbench (score /
   semantic / summarise — NO fact-check, that needs a knowledge base and a registered account
   has none), text to speech, the audio converter, its own history, its own scoring rubric and
   its profile.

   The session token lives in sessionStorage under `cq_user_token` — per tab, on purpose,
   exactly like the workspace portal: a signed-in console on a shared machine must not follow
   into every new tab. It is sent as `Authorization: Bearer <token>`, the same header the
   tenant portal uses, and the server decides which of the two kinds it is. Every request from
   this page runs at scope `'user'`, which is what attaches it. */

import { useCallback, useEffect, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent } from 'react';
import Header from '@/components/Header';
import { Workbench, type Feature, type WorkbenchHandle } from '@/components/Workbench';
import { toast } from '@/components/ui/Toast';
import { ApiError, apiGet } from '@/lib/session';
import { useI18n } from '@/lib/useI18n';
import { ConvertPanel } from './ConvertPanel';
import { Gate, type Me } from './Gate';
import { HistoryPanel } from './HistoryPanel';
import { ProfilePanel } from './ProfilePanel';
import { RubricPanel } from './RubricPanel';
import { TtsPanel } from './TtsPanel';
import { dropUserToken, readUserToken } from './token';
import type { GateMessage, Limits } from './types';

type TabName = 'analyse' | 'tts' | 'convert' | 'history' | 'rubric' | 'profile';

const TABS: { name: TabName; icon: string; key: string }[] = [
  { name: 'analyse', icon: '🎧', key: 'tab.analyze' },
  { name: 'tts', icon: '🗣', key: 'tab.tts' },
  { name: 'convert', icon: '🎚', key: 'tab.convert' },
  { name: 'history', icon: '🗂', key: 'tab.history' },
  { name: 'rubric', icon: '🎯', key: 'tab.scoring' },
  { name: 'profile', icon: '👤', key: 'ac.tab.profile' },
];

/* Where a non-user credential is sent after a successful sign-in.
   ONE place, deliberately: MIGRATION.md lists "admin.html's address is hardcoded in three
   places that must move with it" as a migration hazard, and this is one of the three. They
   are the PORTED routes, not the legacy filenames — the .html pages survive only until phase
   3 deletes them, and a redirect into a file that is about to disappear is a dead end for the
   one credential that has no other way in. */
const AFTER_LOGIN = { admin: '/console', tenant: '/workspace' } as const;

export default function AccountPage() {
  const { t } = useI18n();

  const [signedIn, setSignedIn] = useState(false);
  const [me, setMe] = useState<Me | null>(null);
  const [checking, setChecking] = useState(false);
  const [gateMsg, setGateMsg] = useState<GateMessage | null>(null);

  const [limits, setLimits] = useState<Limits | null>(null);
  /* Whether /limits has been ASKED once — not whether it answered. The legacy page awaits the
     first call before mounting the workbench, and a failed call leaves LIMITS null, which
     `feat()` reads as "everything is on". Mounting before the question is asked would mount
     the wrong feature set and then remount. */
  const [limitsAsked, setLimitsAsked] = useState(false);

  const [tab, setTab] = useState<TabName>('analyse');
  /* A finished conversion is a new History row. Bumped rather than pushed, so the History
     panel decides whether it is on screen and worth re-reading. */
  const [conversionEpoch, setConversionEpoch] = useState(0);
  /* Bumped whenever the session changes. `Header` reads the session once, on mount — the
     legacy page called `CQ.refreshNav()` for the same reason — so remounting it is how the
     bar follows a sign-in that happened without a page load. */
  const [navEpoch, setNavEpoch] = useState(0);

  const wbRef = useRef<WorkbenchHandle>(null);
  const [pendingOpen, setPendingOpen] = useState<{ kind: 'rec' | 'sum'; id: string } | null>(null);
  const booted = useRef(false);

  /* ---------------- the session, and the one funnel for a 401 ---------------- */

  /* An expired token mid-session: one gate, one message, instead of every panel quietly
     failing in its own way. Every API caller on this page funnels its 401 through here.

     Only `cq_user_token` is dropped — never `signOut()`, which clears all three: a workspace
     or operator session sitting in the same tab is not this page's to end. */
  const sessionExpired = useCallback(() => {
    dropUserToken();
    setSignedIn(false);
    setMe(null);
    setLimits(null);
    setLimitsAsked(false);
    setNavEpoch(n => n + 1);
    setGateMsg({ text: t('session.expired'), error: true });
  }, [t]);

  // Nothing to expire if the token is already gone: the legacy guard is `if (r.status === 401
  // && TOKEN)`, and without it a 401 from a request made while signed out would blank a gate
  // that is already showing and overwrite whatever it was saying.
  const onUnauthorized = useCallback(() => {
    if (readUserToken()) sessionExpired();
  }, [sessionExpired]);

  /* ---------------- limits: the banner, the tabs, the meters ---------------- */

  const loadLimits = useCallback(async () => {
    try {
      const d = await apiGet<Limits>('/limits', { scope: 'user' });
      if (d && typeof d === 'object') setLimits(d);
    } catch (e) {
      /* A 401 goes through the funnel like every other request's — the legacy `loadLimits`
         runs through `apiFetch`, which does exactly this. Anything else is swallowed: /limits
         decorates the page rather than gating it, and a toast per failed refresh would bury
         the action that triggered it. */
      if (e instanceof ApiError && e.status === 401) onUnauthorized();
    } finally {
      setLimitsAsked(true);
    }
  }, [onUnauthorized]);

  const feat = useCallback(
    (name: string) => !limits || !limits.features || limits.features[name] !== false,
    [limits],
  );

  /* The workbench without fact-check: a registered account has no knowledge base, and the
     server answers 403 on that route for exactly that reason. The other three analysers are
     feature switches the operator can turn off per tier, so the tab list follows /limits. */
  const wbFeatures = useMemo(
    () => (['score', 'semantic', 'summarise'] as Feature[]).filter(f => feat(f)),
    [feat],
  );
  const wbMounted = limitsAsked && wbFeatures.length > 0;

  const visible = useMemo<Record<TabName, boolean>>(() => ({
    analyse: feat('analyze') && wbFeatures.length > 0,
    tts: feat('tts'),
    convert: feat('convert'),
    history: true,
    rubric: feat('score'),
    profile: true,
  }), [feat, wbFeatures.length]);

  const firstVisibleTab = useCallback(
    (): TabName => TABS.find(x => visible[x.name])?.name || 'profile',
    [visible],
  );

  /** Switch tabs, falling back when the asked-for one is switched off. Returns where it
      actually landed, so a caller that wanted the workbench knows whether it got there. */
  const showTab = useCallback((name: TabName): TabName => {
    const eff = visible[name] ? name : firstVisibleTab();
    setTab(eff);
    return eff;
  }, [visible, firstVisibleTab]);

  // An operator switching a feature off under a signed-in account: the active tab goes away
  // and the page must not be left showing nothing.
  useEffect(() => {
    if (!visible[tab]) setTab(firstVisibleTab());
  }, [visible, tab, firstVisibleTab]);

  /* ---------------- entering and leaving ---------------- */

  const whoLabel = me ? (me.display_name || me.email) : '';

  const enter = useCallback((user: Me | null) => {
    if (user) setMe(user);
    setSignedIn(true);
    setGateMsg(null);
    setNavEpoch(n => n + 1);
    void loadLimits();
  }, [loadLimits]);

  /* ---------------- boot ---------------- */

  useEffect(() => {
    if (booted.current) return;
    booted.current = true;
    if (!readUserToken()) return;

    /* Freeze the gate while the session check is in flight, so someone with a valid session
       never sees an interactive sign-in form flash at them. */
    setChecking(true);
    setGateMsg({ text: t('login.checking'), error: false });

    void (async () => {
      try {
        const d = await apiGet<{ kind?: string; user?: Me }>('/auth/me', { scope: 'user' });
        setChecking(false);
        // A workspace token in this tab is not this page's session: leave it alone and ask for
        // the account's own credentials instead of clearing someone else's.
        if (d.kind === 'user') { enter(d.user || null); return; }
        dropUserToken();
        setGateMsg(null);
      } catch (e) {
        setChecking(false);
        const status = e instanceof ApiError ? e.status : -1;
        // 403 is a DISABLED account, and its token is still good — keep it, sign in, and let
        // the banner explain. 401 is a token this page should forget.
        if (status === 401) { dropUserToken(); setGateMsg(null); return; }
        if (status === 403) { setGateMsg({ text: t('ac.disabled'), error: true }); return; }
        // An outage, not an invalid token — including `status === 0`, which is "nothing
        // answered at all". KEEP the token and offer a retry.
        setGateMsg({ text: t('err.unavailable'), error: true, retry: true });
      }
    })();
  }, [enter, t]);

  useEffect(() => {
    document.title = signedIn
      ? `CommuniQ — ${whoLabel || t('ac.tab.profile')}`
      : `CommuniQ — ${t('ac.gate.heading')}`;
  }, [signedIn, whoLabel, t]);

  /* ---------------- history -> workbench ---------------- */

  /* The panel is mounted but HIDDEN until the tab flips, and `open()` on a `display:none`
     timeline measures nothing. The legacy page gets this for free because `showTab` writes
     the class synchronously; here the request is parked and replayed from an effect, which
     React runs after the tab has been committed to the DOM. */
  useEffect(() => {
    if (!pendingOpen || tab !== 'analyse') return;
    const wb = wbRef.current;
    if (!wb) return;
    if (pendingOpen.kind === 'rec') wb.open(pendingOpen.id);
    else wb.openSummary(pendingOpen.id);
    setPendingOpen(null);
  }, [pendingOpen, tab]);

  const openInWorkbench = useCallback((kind: 'rec' | 'sum', id: string) => {
    if (!wbMounted) { toast(t('ac.hist.noanalyse'), 'err'); return; }
    if (showTab('analyse') === 'analyse') setPendingOpen({ kind, id });
  }, [wbMounted, showTab, t]);

  // The workbench's "edit the rubric" link is a same-page anchor here, not a navigation.
  const onWorkbenchClick = useCallback((e: ReactMouseEvent<HTMLDivElement>) => {
    const target = e.target as HTMLElement | null;
    if (target && target.closest && target.closest('a[href="#rubric"]')) {
      e.preventDefault();
      showTab('rubric');
    }
  }, [showTab]);

  /* ---------------- render ---------------- */

  const panel = (name: TabName) => `panel${visible[name] && tab === name ? ' active' : ''}`;

  return (
    <>
      <Header key={navEpoch} tag="Account" />
      <main className="console">
        {!signedIn ? (
          <Gate
            checking={checking}
            message={gateMsg}
            setMessage={setGateMsg}
            afterLogin={AFTER_LOGIN}
            onEnter={enter}
          />
        ) : (
          <div>
            {/* `active:false` is the operator switching this account off. Nothing is hidden —
                the tabs still explain what the account HAD — but the reason every button now
                fails is stated once, at the top, instead of arriving as a 403 per click. */}
            <div className={limits && limits.active === false ? 'quota warn show' : 'quota'}>
              {limits && limits.active === false ? t('ac.disabled') : ''}
            </div>

            <div className="tabs">
              {TABS.filter(x => visible[x.name]).map(x => (
                <div
                  key={x.name}
                  className={`tab${tab === x.name ? ' active' : ''}`}
                  role="button"
                  tabIndex={0}
                  onClick={() => showTab(x.name)}
                  onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); showTab(x.name); } }}
                >
                  <span>{x.icon}</span> <span>{t(x.key)}</span>
                </div>
              ))}
            </div>

            {/* ANALYSE — the shared workbench, minus fact-check. */}
            <section className={panel('analyse')}>
              <div onClick={onWorkbenchClick}>
                {wbMounted ? (
                  <Workbench
                    /* Keyed on the feature SET, so an operator flipping one off destroys and
                       rebuilds the panel exactly as `mountWorkbench()` does — and, because the
                       key is otherwise stable, a `/limits` refresh after every synthesis does
                       not throw away a recording someone is reading. */
                    key={wbFeatures.join('|')}
                    ref={wbRef}
                    features={wbFeatures}
                    scope="user"
                    rubricHref="#rubric"
                    canEditScores
                    sentimentConfig={null}   /* the tone guidance is the operator's, not this account's */
                    onUnauthorized={onUnauthorized}
                  />
                ) : null}
              </div>
            </section>

            {/* TEXT TO SPEECH — the public page's form, signed in: the clip is stored against
                the account so it comes back in History. */}
            <section className={panel('tts')}>
              <TtsPanel onUnauthorized={onUnauthorized} onSpend={loadLimits} />
            </section>

            {/* CONVERT — the public converter, signed in. */}
            <section className={panel('convert')}>
              <ConvertPanel
                onUnauthorized={onUnauthorized}
                onSpend={loadLimits}
                onConverted={() => setConversionEpoch(n => n + 1)}
              />
            </section>

            <section className={panel('history')}>
              <HistoryPanel
                active={tab === 'history'}
                conversionEpoch={conversionEpoch}
                onUnauthorized={onUnauthorized}
                onOpen={openInWorkbench}
              />
            </section>

            <section className={panel('rubric')}>
              <RubricPanel active={tab === 'rubric'} onUnauthorized={onUnauthorized} />
            </section>

            <section className={panel('profile')}>
              <ProfilePanel
                active={tab === 'profile'}
                me={me}
                limits={limits}
                onMe={setMe}
                onUnauthorized={onUnauthorized}
                reloadLimits={loadLimits}
              />
            </section>
          </div>
        )}
      </main>
    </>
  );
}
