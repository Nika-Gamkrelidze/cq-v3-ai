'use client';
import { useCallback, useEffect, useRef, useState } from 'react';
import { confirmDialog, showModal } from '@/components/ui/Modal';
import { Tip } from '@/components/ui/Tip';
import { toast } from '@/components/ui/Toast';
import { copyText } from '@/lib/clipboard';
import { dateTime } from '@/lib/format';
import { useI18n } from '@/lib/useI18n';
import { SessionExpired, adminGet, adminSend, errText } from './api';
import type { AppUser } from './api';
import { OVERRIDE_KEYS, parseOverrides } from './logic';
import styles from './console.module.css';
import { CheckRow, Msg, type Note } from './parts';

/* Registered accounts — the self-service tier, and the people on it.

   Two cards, in the order an operator needs them: the TIER first (what a new account gets by
   default), then the accounts themselves. THERE IS NO MAIL PROVIDER, so the reset button in the
   table is the only password-recovery path a registered user has, and the operator reads the new
   password out loud. That is why it is shown once, in a dialog that says so.

   The table carries the four facts a support call starts from: is the account on, when was it
   made, when did they last get in, and what have they spent today. */

type Translate = (key: string, vars?: Record<string, string | number>) => string;

const REG_FEATURES = [
  ['analyze', 'feat.analyze'],
  ['tts', 'feat.tts'],
  ['convert', 'pb.feat.convert'],
  ['score', 'pb.feat.score'],
  ['semantic', 'pb.feat.semantic'],
  ['summarise', 'pb.feat.summarise'],
] as const;

interface RegisteredTier {
  enabled?: boolean;
  max_analyses_per_day?: number;
  max_audio_mb?: number;
  max_tts_per_day?: number;
  max_conversions_per_day?: number;
  features?: Record<string, boolean>;
}

export default function UsersTab() {
  const { t } = useI18n();

  /* ---- the tier ---- */
  const [tier, setTier] = useState<RegisteredTier>({});
  const [signups, setSignups] = useState(false);
  const [analyses, setAnalyses] = useState('');
  const [mb, setMb] = useState('');
  const [tts, setTts] = useState('');
  const [conv, setConv] = useState('');
  const [features, setFeatures] = useState<Record<string, boolean>>({});
  const [tierBusy, setTierBusy] = useState(false);
  const [tierNote, setTierNote] = useState<Note | null>(null);

  /* ---- the accounts ---- */
  const [users, setUsers] = useState<AppUser[]>([]);
  const [q, setQ] = useState('');
  const [note, setNote] = useState<Note | null>(null);

  const applyTier = (d: RegisteredTier) => {
    setTier(d || {});
    setSignups(!!d.enabled);
    setAnalyses(String(d.max_analyses_per_day ?? 0));
    setMb(String(d.max_audio_mb ?? 0));
    setTts(String(d.max_tts_per_day ?? 0));
    setConv(String(d.max_conversions_per_day ?? 0));
    const f = d.features || {};
    setFeatures(Object.fromEntries(REG_FEATURES.map(([k]) => [k, !!f[k]])));
  };

  useEffect(() => {
    adminGet<RegisteredTier>('/admin/registered-limits').then(applyTier).catch(() => {});
  }, []);

  const saveTier = async () => {
    setTierNote(null);
    setTierBusy(true);
    try {
      const d = await adminSend<RegisteredTier>('PUT', '/admin/registered-limits', {
        enabled: signups,
        max_analyses_per_day: parseInt(analyses, 10) || 0,
        max_audio_mb: parseInt(mb, 10) || 0,
        max_tts_per_day: parseInt(tts, 10) || 0,
        max_conversions_per_day: parseInt(conv, 10) || 0,
        features: Object.fromEntries(REG_FEATURES.map(([k]) => [k, !!features[k]])),
      });
      // Kept, because the override dialog shows these numbers as its placeholders — "leave it
      // empty and the account gets THIS".
      setTier(d || tier);
      setTierNote({ kind: 'ok', text: t('toast.saved') });
      toast(t('toast.saved'), 'ok');
    } catch (e) {
      if (e instanceof SessionExpired) return;
      setTierNote({ kind: 'err', text: errText(e, t) });
    } finally {
      setTierBusy(false);
    }
  };

  const load = useCallback(async (needle: string) => {
    setNote(null);
    try {
      const d = await adminGet<{ users?: AppUser[] }>(
        `/admin/users?limit=200&q=${encodeURIComponent(needle)}`);
      setUsers(Array.isArray(d?.users) ? d.users : []);
    } catch (e) {
      if (e instanceof SessionExpired) return;
      setNote({ kind: 'err', text: t('toast.error') });
    }
  }, [t]);

  /* One request per PAUSE, not per keystroke — but the first load is immediate, because the
     panel opening is not a keystroke. `load` is read through a ref so that a language switch
     (which gives `t`, and therefore `load`, a new identity) does not re-run this effect and
     re-issue the search. */
  const loadRef = useRef(load);
  useEffect(() => { loadRef.current = load; });
  const first = useRef(true);
  const needle = q.trim();
  useEffect(() => {
    if (first.current) { first.current = false; void loadRef.current(needle); return; }
    const id = setTimeout(() => void loadRef.current(needle), 250);
    return () => clearTimeout(id);
  }, [needle]);

  /** PUT one account. Returns the server's row, or the message that explains why not. */
  const putUser = useCallback(async (
    id: string, body: unknown,
  ): Promise<{ user: AppUser } | { error: string }> => {
    setNote(null);
    try {
      const d = await adminSend<AppUser>('PUT', `/admin/users/${id}`, body);
      if (d && d.id) setUsers(prev => prev.map(u => (String(u.id) === String(id) ? d : u)));
      return { user: d };
    } catch (e) {
      if (e instanceof SessionExpired) throw e;
      const text = errText(e, t);
      setNote({ kind: 'err', text });
      toast(text, 'err');
      return { error: text };
    }
  }, [t]);

  const toggleActive = async (u: AppUser) => {
    const r = await putUser(u.id, { is_active: !u.is_active });
    if ('user' in r) toast(t('pb.user.saved'), 'ok');
  };

  const remove = async (u: AppUser) => {
    /* The confirm says what SURVIVES the delete, because the surprising half of this action is
       not what it removes — it is what it leaves behind: recordings, summaries and TTS clips
       stay until the retention purge reaches them. */
    if (!(await confirmDialog(t('pb.del.confirm', { email: u.email }), { ok: t('btn.delete') }))) return;
    try {
      await adminSend('DELETE', `/admin/users/${u.id}`);
      toast(t('pb.del.done'), 'ok');
      setUsers(prev => prev.filter(x => String(x.id) !== String(u.id)));
    } catch (e) {
      if (e instanceof SessionExpired) return;
      toast(t('toast.error'), 'err');
    }
  };

  const editLimits = (u: AppUser) => {
    void showModal(close => (
      <LimitsBody user={u} tier={tier} t={t} put={putUser} close={close} />
    ));
  };

  const resetPassword = async (u: AppUser) => {
    if (!(await confirmDialog(t('pb.pw.confirm', { email: u.email }), { ok: t('pb.act.resetpw') }))) return;
    let password = '';
    try {
      const d = await adminSend<{ password?: string }>('POST', `/admin/users/${u.id}/reset-password`);
      password = d.password || '';
      if (!password) throw new Error('no password');
    } catch (e) {
      if (e instanceof SessionExpired) return;
      toast(errText(e, t), 'err');
      return;
    }
    void showModal(close => <PasswordBody email={u.email} password={password} t={t} close={close} />);
  };

  return (
    <>
      <div className="card">
        <h3>{t('pb.reg.heading')}</h3>
        <p className="hint">{t('pb.reg.desc')}</p>
        {/* "Sign-ups open" is NOT a master switch over existing accounts, and the ⓘ says so:
            the obvious reading of a checkbox called "enabled" is the wrong one, and an operator
            reaching for it at 3am is trying to stop new sign-ups, not to lock out everyone who
            already has an account. */}
        <CheckRow checked={signups} onChange={setSignups}>
          <>
            <span>{t('pb.reg.signups')}</span>
            <span className={styles.tipFlush}><Tip text={t('pb.reg.signups.hint')} /></span>
          </>
        </CheckRow>
        <div className="row" style={{ marginTop: 12 }}>
          <div>
            <label htmlFor="r_analyses">{t('adm.maxanalyses')}</label>
            <input id="r_analyses" type="number" min={0} value={analyses} onChange={e => setAnalyses(e.target.value)} />
          </div>
          <div>
            <label htmlFor="r_mb">{t('adm.maxmb')}</label>
            <input id="r_mb" type="number" min={0} value={mb} onChange={e => setMb(e.target.value)} />
          </div>
          <div>
            <label htmlFor="r_tts">{t('adm.maxtts')}</label>
            <input id="r_tts" type="number" min={0} value={tts} onChange={e => setTts(e.target.value)} />
          </div>
          <div>
            <label htmlFor="r_conv">{t('pb.reg.maxconv')}</label>
            <input id="r_conv" type="number" min={0} value={conv} onChange={e => setConv(e.target.value)} />
          </div>
        </div>
        <h4>{t('adm.features')}</h4>
        <div className="inline" style={{ gap: 18, flexWrap: 'wrap', rowGap: 8 }}>
          {REG_FEATURES.map(([k, label]) => (
            <CheckRow
              key={k}
              checked={!!features[k]}
              onChange={v => setFeatures(f => ({ ...f, [k]: v }))}
              style={{ gap: 6 }}
            >
              <span>{t(label)}</span>
            </CheckRow>
          ))}
        </div>
        <div className="actions">
          <button className="primary" type="button" onClick={saveTier} disabled={tierBusy}>
            {t('btn.savelimits')}
          </button>
        </div>
        <Msg note={tierNote} />
      </div>

      <div className="card">
        <div
          className="inline"
          style={{ justifyContent: 'space-between', alignItems: 'center', gap: 10, flexWrap: 'wrap', rowGap: 8 }}
        >
          <h3 style={{ margin: 0 }}>{t('pb.users.heading')}</h3>
          <div className="inline" style={{ gap: 8 }}>
            <input
              value={q}
              onChange={e => setQ(e.target.value)}
              placeholder={t('pb.users.search')}
              aria-label={t('pb.users.search')}
              style={{ width: 220 }}
            />
            <button className="ghost" type="button" onClick={() => void load(needle)}>{t('btn.refresh')}</button>
          </div>
        </div>
        <p className="hint">{t('pb.users.legend')}</p>

        <div style={{ marginTop: 6 }}>
          {!users.length ? (
            <div className="empty">{needle ? t('pb.users.nomatch') : t('pb.users.none')}</div>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>{t('pb.th.email')}</th>
                    <th>{t('pb.th.name')}</th>
                    <th>{t('th.active')}</th>
                    <th>{t('pb.th.created')}</th>
                    <th>{t('pb.th.lastlogin')}</th>
                    <th>{t('pb.th.today')}</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {users.map(u => {
                    const used = u.used || {};
                    // A dot on the Limits button, so an account that already carries an
                    // override is visible without opening four dialogs to find it.
                    const overridden = Object.keys(u.limits || {}).length > 0;
                    return (
                      <tr key={u.id}>
                        <td>{u.email}</td>
                        <td>{u.display_name || '—'}</td>
                        <td><span className={`pill ${u.is_active ? 'on' : 'off'}`}>{u.is_active ? '●' : '○'}</span></td>
                        <td className="hint">{dateTime(u.created_at)}</td>
                        <td className="hint">{u.last_login_at ? dateTime(u.last_login_at) : t('pb.never')}</td>
                        <td className="hint">
                          {(used.analyses ?? 0)} · {(used.tts ?? 0)} · {(used.conversions ?? 0)}
                        </td>
                        <td className="inline">
                          <button className="ghost" type="button" onClick={() => toggleActive(u)}>
                            {u.is_active ? t('pb.act.deactivate') : t('pb.act.activate')}
                          </button>
                          <button className="ghost" type="button" onClick={() => editLimits(u)}>
                            {t('pb.act.limits')}{overridden ? ' ●' : ''}
                          </button>
                          <button className="ghost" type="button" onClick={() => resetPassword(u)}>
                            {t('pb.act.resetpw')}
                          </button>
                          <button className="danger" type="button" onClick={() => remove(u)}>
                            {t('btn.delete')}
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
        <Msg note={note} />
      </div>
    </>
  );
}

/* -------------------------------------------------------- per-account overrides */

/** The PUT REPLACES the blob, so this form is the whole truth for one account: a field left
    empty is not "unchanged", it is "use the tier's number" — and the placeholder shows which
    number that would be. */
function LimitsBody({
  user, tier, t, put, close,
}: {
  user: AppUser;
  tier: RegisteredTier;
  t: Translate;
  put: (id: string, body: unknown) => Promise<{ user: AppUser } | { error: string }>;
  close: (v?: unknown) => void;
}) {
  const [values, setValues] = useState<Record<string, string>>(
    () => Object.fromEntries(OVERRIDE_KEYS.map(([k]) => [k, String((user.limits || {})[k] ?? '')])),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const submit = async () => {
    const parsed = parseOverrides(values);
    if (!parsed.ok) { setError(t('toast.error')); return; }
    setBusy(true);
    try {
      const r = await put(user.id, { limits: parsed.limits });
      if ('user' in r) { toast(t('pb.lim.saved'), 'ok'); close(true); }
      else setError(r.error);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <h3 style={{ marginTop: 0 }}>{t('pb.lim.title')}</h3>
      <p className="hint" style={{ marginTop: -6 }}>{user.email}</p>
      <div className="row">
        {OVERRIDE_KEYS.map(([key, label]) => (
          <div key={key}>
            <label htmlFor={`ul-${key}`}>{t(label)}</label>
            <input
              id={`ul-${key}`}
              type="number"
              min={0}
              value={values[key]}
              placeholder={String((tier as Record<string, unknown>)[key] ?? '')}
              onChange={e => setValues(v => ({ ...v, [key]: e.target.value }))}
            />
          </div>
        ))}
      </div>
      <p className="hint">{t('pb.lim.note')}</p>
      {error ? <div className="msg err">{error}</div> : null}
      <div className="actions">
        <button className="ghost" type="button" onClick={() => close(false)}>{t('btn.cancel')}</button>
        <button className="primary" type="button" onClick={submit} disabled={busy}>{t('btn.save')}</button>
      </div>
    </>
  );
}

/* ------------------------------------------------------------- the new password */

/** Shown once and never stored in the clear — the dialog says so, because an operator who
    closes it without copying has to reset again, and they should learn that BEFORE they close
    it.

    THE COPY BUTTON DOES NOT AWAIT ANYTHING before calling `copyText`. Production is plain HTTP,
    where `navigator.clipboard` does not exist and the textarea fallback inside `copyText` is
    the path that actually runs — and `document.execCommand('copy')` only works inside the
    click's own gesture, which any prior `await` would have spent. See lib/clipboard.ts. */
function PasswordBody({
  email, password, t, close,
}: {
  email: string;
  password: string;
  t: Translate;
  close: (v?: unknown) => void;
}) {
  return (
    <>
      <h3 style={{ marginTop: 0 }}>{t('pb.pw.title')}</h3>
      <p className="hint" style={{ marginTop: -6 }}>{email}</p>
      <div className="inline" style={{ gap: 10, flexWrap: 'wrap' }}>
        <code style={{ fontSize: 16, userSelect: 'all' }}>{password}</code>
        <button
          className="ghost"
          type="button"
          onClick={() => {
            copyText(password).then(ok => toast(ok ? t('pb.copied') : t('pb.copyfail'), ok ? 'ok' : 'err'));
          }}
        >
          {t('pb.copy')}
        </button>
      </div>
      <p className="hint">{t('pb.pw.once')}</p>
      <div className="actions">
        <button className="primary" type="button" onClick={() => close(true)}>{t('pb.close')}</button>
      </div>
    </>
  );
}
