'use client';
/* Profile — who this account is, and what is left of today. */

import { useEffect, useState, type FormEvent } from 'react';
import { toast } from '@/components/ui/Toast';
import { ApiError, apiMessage, apiSend } from '@/lib/session';
import { useI18n } from '@/lib/useI18n';
import styles from './account.module.css';
import type { Me } from './Gate';
import type { Limits } from './types';

/** The features a tier can switch off, and the label each one wears elsewhere on the page —
    so the pill row reads as "these are the tabs you have", not as a list of internal names. */
const FEATURES: [string, string][] = [
  ['analyze', 'tab.analyze'],
  ['tts', 'tab.tts'],
  ['convert', 'tab.convert'],
  ['score', 'wb.tab.score'],
  ['semantic', 'wb.tab.semantic'],
  ['summarise', 'wb.tab.summarise'],
];

export interface ProfilePanelProps {
  active: boolean;
  me: Me | null;
  limits: Limits | null;
  onMe: (me: Me) => void;
  onUnauthorized: () => void;
  reloadLimits: () => void;
}

export function ProfilePanel({ active, me, limits, onMe, onUnauthorized, reloadLimits }: ProfilePanelProps) {
  const { t } = useI18n();

  const [name, setName] = useState(me?.display_name || '');
  const [pfMsg, setPfMsg] = useState<{ text: string; kind: '' | 'ok' | 'err' }>({ text: '', kind: '' });
  const [savingName, setSavingName] = useState(false);

  const [cur, setCur] = useState('');
  const [next, setNext] = useState('');
  const [again, setAgain] = useState('');
  const [pwMsg, setPwMsg] = useState<{ text: string; kind: '' | 'ok' | 'err' }>({ text: '', kind: '' });
  const [savingPw, setSavingPw] = useState(false);

  // The field follows the account when it changes underneath the form — a rename that landed
  // through some other path, or the boot check finishing after this panel mounted.
  useEffect(() => { setName(me?.display_name || ''); }, [me]);

  // Opening the tab re-reads the allowance, exactly as `showTab('profile')` does: this is the
  // one screen whose whole point is the numbers being current.
  useEffect(() => { if (active) reloadLimits(); }, [active, reloadLimits]);

  async function saveName() {
    setPfMsg({ text: '', kind: '' });
    setSavingName(true);
    try {
      const d = await apiSend<{ user?: Me }>('PUT', '/auth/me', { display_name: name.trim() }, { scope: 'user' });
      if (d.user) onMe(d.user);
      setPfMsg({ text: t('ac.pf.saved'), kind: 'ok' });
      toast(t('ac.pf.saved'), 'ok');
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) onUnauthorized();
      setPfMsg({ text: apiMessage(e, t), kind: 'err' });
    } finally {
      setSavingName(false);
    }
  }

  async function changePassword(e: FormEvent) {
    e.preventDefault();
    setPwMsg({ text: '', kind: '' });
    if (!cur) { setPwMsg({ text: t('ac.pf.pw.needcur'), kind: 'err' }); return; }
    if (next.length < 8) { setPwMsg({ text: t('ac.reg.shortpw'), kind: 'err' }); return; }
    if (next !== again) { setPwMsg({ text: t('ac.pf.pw.mismatch'), kind: 'err' }); return; }
    setSavingPw(true);
    try {
      await apiSend('PUT', '/auth/me', { current_password: cur, new_password: next }, { scope: 'user' });
      setCur(''); setNext(''); setAgain('');
      setPwMsg({ text: t('ac.pf.pw.done'), kind: 'ok' });
      toast(t('ac.pf.pw.done'), 'ok');
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) onUnauthorized();
      // 403 is the CURRENT password, not the session: the server needs it even though the
      // caller already holds a valid token, so a token left in a shared browser is not enough
      // to lock the real owner out.
      const text = err instanceof ApiError && err.status === 403 ? t('ac.pf.pw.badcur') : apiMessage(err, t);
      setPwMsg({ text, kind: 'err' });
    } finally {
      setSavingPw(false);
    }
  }

  const feat = (n: string) => !limits || !limits.features || limits.features[n] !== false;

  /* One allowance: what has been spent, out of what. Same visual language as the rubric's
     weight bars — a number, its ceiling, and how much of it is gone. A cap of 0 means NO cap,
     which is a full bar's worth of nothing rather than an empty allowance. */
  const meter = (labelKey: string, used: number | undefined, max: number | undefined) => {
    const u = Number(used) || 0;
    const m = Number(max) || 0;
    const right = m > 0 ? t('ac.pf.of', { used: u, max: m }) : `${u} · ${t('ac.pf.nolimit')}`;
    const w = m > 0 ? Math.min(100, Math.round((u / m) * 100)) : 0;
    const cls = m > 0 && u >= m ? ' bad' : m > 0 && u / m >= 0.8 ? ' mid' : ' good';
    return (
      <div className={styles.useRow} key={labelKey}>
        <div className={styles.useHead}><b>{t(labelKey)}</b><span>{right}</span></div>
        <div className={`sc-bar${cls}`}><span style={{ width: `${w}%` }} /></div>
      </div>
    );
  };

  const used = limits?.used || {};

  return (
    <>
      <div className="card">
        <h3>{t('ac.pf.heading')}</h3>
        <label htmlFor="pfEmail">{t('ac.f.email')}</label>
        {/* The address is the identity the server looks accounts up by, and there is no
            verification email to prove a new one — so it is shown, not edited. */}
        <input type="email" id="pfEmail" disabled value={me?.email || ''} readOnly />
        <label htmlFor="pfName">{t('ac.f.name')}</label>
        <input
          type="text" id="pfName" autoComplete="name" value={name}
          onChange={e => setName(e.target.value)}
        />
        <div className="actions">
          <button type="button" className="primary" disabled={savingName} onClick={() => void saveName()}>
            {savingName ? <span className="spinner" /> : t('btn.save')}
          </button>
        </div>
        <div className={`msg${pfMsg.kind ? ` ${pfMsg.kind}` : ''}`} aria-live="polite">{pfMsg.text}</div>
      </div>

      <div className="card">
        <h3>{t('ac.pf.pw')}</h3>
        <form onSubmit={changePassword} autoComplete="on">
          <label htmlFor="pwCur">{t('ac.f.curpw')}</label>
          <input type="password" id="pwCur" autoComplete="current-password" value={cur} onChange={e => setCur(e.target.value)} />
          <label htmlFor="pwNew">{t('ac.f.newpw')}</label>
          <input type="password" id="pwNew" autoComplete="new-password" value={next} onChange={e => setNext(e.target.value)} />
          <label htmlFor="pwNew2">{t('ac.f.pw2')}</label>
          <input type="password" id="pwNew2" autoComplete="new-password" value={again} onChange={e => setAgain(e.target.value)} />
          <div className="actions">
            <button type="submit" className="primary" disabled={savingPw}>
              {savingPw ? <span className="spinner" /> : t('ac.pf.pw.change')}
            </button>
          </div>
        </form>
        <div className={`msg${pwMsg.kind ? ` ${pwMsg.kind}` : ''}`} aria-live="polite">{pwMsg.text}</div>
      </div>

      <div className="card">
        <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
          <h3 style={{ margin: 0 }}>{t('ac.pf.usage')}</h3>
          <button type="button" className="ghost" onClick={reloadLimits}>{t('btn.refresh')}</button>
        </div>
        <div style={{ marginTop: 8 }}>
          {!limits ? (
            <div className="empty">{t('err.unavailable')}</div>
          ) : (
            <>
              {meter('quota.analyses', used.analyses, limits.max_analyses_per_day)}
              {meter('quota.clips', used.tts, limits.max_tts_per_day)}
              {meter('quota.conversions', used.conversions, limits.max_conversions_per_day)}
              {Number(limits.max_audio_mb) > 0 ? (
                <div className={styles.useRow}>
                  <div className={styles.useHead}>
                    <b>{t('ac.pf.maxupload')}</b><span>{Number(limits.max_audio_mb)} MB</span>
                  </div>
                </div>
              ) : null}
            </>
          )}
        </div>
        {limits ? (
          <div style={{ marginTop: 14 }}>
            <div className="hint" style={{ marginBottom: 6 }}>{t('ac.pf.features')}</div>
            {FEATURES.map(([k, key]) => (
              <span key={k}><span className={`pill ${feat(k) ? 'on' : 'off'}`}>{t(key)}</span>{' '}</span>
            ))}
          </div>
        ) : null}
      </div>
    </>
  );
}
