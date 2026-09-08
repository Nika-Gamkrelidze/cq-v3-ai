'use client';
/* The gate — two doors in one card: sign in, or make an account.
   =============================================================
   Both are real <form>s so Enter submits from either field and password managers recognise
   the pairs.

   THIS IS THE APP'S ONLY SIGN-IN SURFACE. The nav's single signed-out entry points here, and
   `POST /auth/login` serves all three kinds of account, so this one form has to admit a
   registered user, a workspace user AND the operator. Two fixes made that true and both are
   easy to undo by "tidying up":

     * The sign-in field is `type="text"`, NOT `type="email"` — see the comment on the input.
     * A workspace or operator credential is COMPLETED here and redirected, not rejected. */

import { useEffect, useRef, useState, type FormEvent } from 'react';
import { ApiError, apiMessage, apiSend } from '@/lib/session';
import { toast } from '@/components/ui/Toast';
import { useI18n } from '@/lib/useI18n';
import { storeForeignToken, storeUserToken } from './token';
import type { GateMessage } from './types';

/** The account, as `/auth/me`, `/auth/login` and `/auth/register` all return it. */
export interface Me {
  id: string;
  email: string;
  display_name: string | null;
}

interface LoginResponse {
  scope: 'admin' | 'tenant' | 'user';
  token: string;
  user?: Me;
}

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export interface GateProps {
  /** The boot session check is in flight: freeze the form so a valid session never flashes an
      interactive sign-in at the person who already has one. */
  checking: boolean;
  message: GateMessage | null;
  setMessage: (m: GateMessage | null) => void;
  afterLogin: { admin: string; tenant: string };
  onEnter: (user: Me | null) => void;
}

export function Gate({ checking, message, setMessage, afterLogin, onEnter }: GateProps) {
  const { t } = useI18n();
  const [mode, setMode] = useState<'signin' | 'register'>('signin');
  const [busy, setBusy] = useState<'' | 'signin' | 'register'>('');
  const [showPw, setShowPw] = useState(false);

  const [signinId, setSigninId] = useState('');
  const [signinPw, setSigninPw] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [name, setName] = useState('');

  const idRef = useRef<HTMLInputElement>(null);
  const pwRef = useRef<HTMLInputElement>(null);
  const emailRef = useRef<HTMLInputElement>(null);
  const regPwRef = useRef<HTMLInputElement>(null);

  // The signed-out landing state: the caret is already where typing starts. Not while the
  // boot check is running — the field is disabled, and focusing a disabled input does nothing
  // except move focus off the message that explains why it is disabled.
  useEffect(() => { if (!checking) idRef.current?.focus(); }, [checking]);

  const switchMode = (next: 'signin' | 'register') => {
    setMode(next);
    setMessage(null);
    // After the render that swapped the forms, or the focus lands on a node that is gone.
    setTimeout(() => (next === 'signin' ? idRef : emailRef).current?.focus(), 0);
  };

  /* `checking` freezes the FIELDS — the boot check may be about to sign this person in.
     A submit in flight only disables the buttons, exactly as the legacy form does: greying
     out the password box under someone's hands is not what a spinner on the button means. */
  const frozen = checking;
  const disabled = checking || busy !== '';

  async function submit(which: 'signin' | 'register', run: () => Promise<void>) {
    setMessage(null);
    setBusy(which);
    try { await run(); }
    catch (e) { setMessage({ text: apiMessage(e, t), error: true }); }
    finally { setBusy(''); }
  }

  function onSignin(e: FormEvent) {
    e.preventDefault();
    const id = signinId.trim();
    if (!id || !signinPw) {
      setMessage({ text: t('login.empty'), error: true });
      (!id ? idRef : pwRef).current?.focus();
      return;
    }
    void submit('signin', async () => {
      let d: LoginResponse;
      try {
        d = await apiSend<LoginResponse>('POST', '/auth/login',
          { username: id, password: signinPw }, { scope: 'public' });
      } catch (e) {
        // The server's own words for a bad credential are English and deliberately vague;
        // this page has a translated sentence for exactly that case.
        if (e instanceof ApiError && e.status === 401) throw new Error(t('login.failed'));
        throw e;
      }
      /* The one login endpoint serves three kinds of account. An operator or workspace
         credential is not the wrong credential, only a different destination — so complete
         the sign-in and go there. Telling someone who just typed a correct password to walk
         to another page and type it again is a dead end, and it was the ONLY way in for the
         operator, whose username this form used to reject outright. */
      if (d.scope === 'admin' || d.scope === 'tenant') {
        const admin = d.scope === 'admin';
        storeForeignToken(admin ? 'admin' : 'tenant', d.token);
        setMessage({ text: t(admin ? 'ac.gate.isadmin' : 'ac.gate.istenant'), error: false });
        toast(t('toast.welcome'), 'ok');
        // A full navigation, not a router push: the destination boots its own session from
        // the token just written, and it may still be a legacy page.
        window.location.href = admin ? afterLogin.admin : afterLogin.tenant;
        return;
      }
      storeUserToken(d.token);
      onEnter(d.user || null);
      toast(t('toast.welcome'), 'ok');
    });
  }

  function onRegister(e: FormEvent) {
    e.preventDefault();
    const addr = email.trim();
    // Checked here as well as on the server, so the two rules a person can get wrong are
    // reported in their own language instead of the API's English.
    if (!EMAIL_RE.test(addr)) {
      setMessage({ text: t('ac.reg.bademail'), error: true });
      emailRef.current?.focus();
      return;
    }
    if (password.length < 8) {
      setMessage({ text: t('ac.reg.shortpw'), error: true });
      regPwRef.current?.focus();
      return;
    }
    void submit('register', async () => {
      let d: LoginResponse;
      try {
        d = await apiSend<LoginResponse>('POST', '/auth/register',
          { email: addr, password, display_name: name.trim() }, { scope: 'public' });
      } catch (e) {
        // 403 here means the operator closed sign-ups; 409 means the address is taken. Both
        // arrive as English detail strings, so they are translated rather than echoed.
        if (e instanceof ApiError && e.status === 403) throw new Error(t('ac.reg.closed'));
        if (e instanceof ApiError && e.status === 409) throw new Error(t('ac.reg.dup'));
        throw e;
      }
      storeUserToken(d.token);
      onEnter(d.user || null);
      toast(t('ac.reg.done'), 'ok');
    });
  }

  const spin = (label: string, on: boolean) =>
    on ? <><span className="spinner" />{label}…</> : label;

  return (
    <div className="gate">
      <div className="card">
        <h3>{t('ac.gate.heading')}</h3>
        <div className="subtabs" role="tablist">
          {(['signin', 'register'] as const).map(g => (
            <div
              key={g}
              className={`subtab${mode === g ? ' active' : ''}`}
              role="tab"
              aria-selected={mode === g}
              tabIndex={0}
              onClick={() => switchMode(g)}
              onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); switchMode(g); } }}
            >
              {t(g === 'signin' ? 'ac.gate.signin' : 'ac.gate.register')}
            </div>
          ))}
        </div>

        <form onSubmit={onSignin} autoComplete="on" className={mode === 'signin' ? undefined : 'hidden'}>
          <label htmlFor="sEmail">{t('ac.f.signinid')}</label>
          {/* type=text, NOT type=email: this one box takes a registered user's email AND an
              operator's or workspace user's username, because /auth/login serves all three.
              type=email made the browser refuse a superadmin's username before the form ever
              submitted, which left the operator with no way in from this page. */}
          <input
            type="text" id="sEmail" autoComplete="username" ref={idRef}
            value={signinId} disabled={frozen}
            onChange={e => setSigninId(e.target.value)}
          />
          <label htmlFor="sPw">{t('f.password')}</label>
          <div className="inline" style={{ gap: 6 }}>
            <input
              type={showPw ? 'text' : 'password'} id="sPw" autoComplete="current-password"
              ref={pwRef} style={{ flex: 1 }} value={signinPw} disabled={frozen}
              onChange={e => setSigninPw(e.target.value)}
            />
            <button
              type="button" className="icon-btn" title={t('login.showpw')} aria-label={t('login.showpw')}
              aria-pressed={showPw} onClick={() => setShowPw(v => !v)}
            >
              &#128065;
            </button>
          </div>
          <div className="actions">
            <button type="submit" className="primary" disabled={disabled}>
              {spin(t('btn.signin'), busy === 'signin')}
            </button>
          </div>
        </form>

        <form onSubmit={onRegister} autoComplete="on" className={mode === 'register' ? undefined : 'hidden'}>
          <label htmlFor="rEmail">{t('ac.f.email')}</label>
          <input
            type="email" id="rEmail" autoComplete="username" ref={emailRef}
            value={email} disabled={frozen} onChange={e => setEmail(e.target.value)}
          />
          <label htmlFor="rPw">{t('f.password')}</label>
          {/* No verification email: there is no mail provider behind this app, so an address
              is taken on trust and the password is the whole credential. */}
          <input
            type="password" id="rPw" autoComplete="new-password" ref={regPwRef}
            value={password} disabled={frozen} onChange={e => setPassword(e.target.value)}
          />
          <div className="hint">{t('ac.reg.pwhint')}</div>
          <label htmlFor="rName">{t('ac.f.name')}</label>
          <input
            type="text" id="rName" autoComplete="name"
            value={name} disabled={frozen} onChange={e => setName(e.target.value)}
          />
          <div className="hint">{t('ac.f.name.hint')}</div>
          <div className="actions">
            <button type="submit" className="primary" disabled={disabled}>
              {spin(t('ac.gate.register'), busy === 'register')}
            </button>
          </div>
        </form>

        <div className={message?.error === false ? 'msg' : 'msg err'} aria-live="polite">
          {message ? message.text : ''}
          {message?.retry ? (
            <>
              {' '}
              <button type="button" className="ghost" onClick={() => window.location.reload()}>
                {t('btn.retry')}
              </button>
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}
