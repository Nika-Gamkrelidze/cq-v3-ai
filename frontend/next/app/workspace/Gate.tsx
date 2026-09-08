'use client';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { useI18n } from '@/lib/useI18n';

/* The sign-in gate. A real <form> so Enter submits from EITHER field and password managers
   recognise the pair; submission is intercepted and never navigates.

   `notice` is the PAGE's message — "your session expired", or an outage with a retry button —
   and it stays visible until the visitor tries again, at which point their own result replaces
   it. `frozen` covers the boot check: a person with a valid session must never see an
   interactive sign-in form flash at them while `/auth/me` is still in flight. */
export function Gate({
  frozen, notice, onLogin,
}: {
  frozen: boolean;
  notice: ReactNode;
  /** Rejects with the message to show under the form. */
  onLogin: (username: string, password: string) => Promise<void>;
}) {
  const { t } = useI18n();
  const [u, setU] = useState('');
  const [pw, setPw] = useState('');
  const [showPw, setShowPw] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  const uRef = useRef<HTMLInputElement>(null);
  const pRef = useRef<HTMLInputElement>(null);

  useEffect(() => { if (!frozen) uRef.current?.focus(); }, [frozen]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr('');
    const user = u.trim();
    if (!user || !pw) {
      setErr(t('login.empty'));
      (!user ? uRef : pRef).current?.focus();
      return;
    }
    setBusy(true);
    try {
      await onLogin(user, pw);
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : t('toast.error'));
    } finally {
      setBusy(false);
    }
  };

  const disabled = frozen || busy;

  return (
    <form className="gate card" id="gate" autoComplete="on" onSubmit={submit}>
      <h3>{t('login.heading')}</h3>
      <label htmlFor="u">{t('f.username')}</label>
      <input
        type="text" id="u" ref={uRef} autoComplete="username" disabled={disabled}
        value={u} onChange={e => setU(e.target.value)}
      />
      <label htmlFor="p">{t('f.password')}</label>
      <div className="inline" style={{ gap: 6 }}>
        <input
          type={showPw ? 'text' : 'password'} id="p" ref={pRef} autoComplete="current-password"
          style={{ flex: 1 }} disabled={disabled}
          value={pw} onChange={e => setPw(e.target.value)}
        />
        <button
          type="button" className="icon-btn" title={t('login.showpw')}
          aria-pressed={showPw} onClick={() => setShowPw(v => !v)}
        >&#128065;</button>
      </div>
      <div className="actions">
        <button type="submit" className="primary" disabled={disabled}>
          {busy ? <><span className="spinner" />{t('btn.signin')}…</> : t('btn.signin')}
        </button>
      </div>
      {/* The visitor's own result wins over the page's standing notice. */}
      <div className={`msg${err ? ' err' : ''}`} aria-live="polite">{err || notice}</div>
    </form>
  );
}
