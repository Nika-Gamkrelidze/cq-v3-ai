'use client';
import { useCallback, useEffect, useRef, useState } from 'react';
import { confirmDialog, showModal } from '@/components/ui/Modal';
import { Select } from '@/components/ui/Select';
import { toast } from '@/components/ui/Toast';
import { copyText } from '@/lib/clipboard';
import { dateTime } from '@/lib/format';
import { useI18n } from '@/lib/useI18n';
import { SessionExpired, adminGet, adminSend, errText } from './api';
import type { Grant, Integration, RevealedKey, Secret, Tenant } from './api';
import { keyState } from './logic';
import { Msg, type Note } from './parts';

/* CHAT CONNECTIONS — the credentials a chat service authenticates with, and the workspaces each
   may act for.

   A connection is NOT a tenant's `api_key`: one service fronts several workspaces, so the
   workspace is named per request (`X-CQ-Tenant`) and checked against the grants managed here —
   a service can only ever act for a workspace an operator granted it. Keys are stored HASHED,
   so the clear text exists in exactly one place, the reveal dialog, once; the table never shows
   more than a `key_id`. If an operator loses a key the answer is rotate, not recover. */

type Translate = (key: string, vars?: Record<string, string | number>) => string;

const CRED_URL = '/admin/integrations';

/* The verdicts the diagnosis can return. Each one owns a sentence naming its fix, so an
   unrecognised literal (an api newer than this console) falls back to generic copy rather
   than rendering a raw enum at the operator. */
const CRED_VERDICTS = new Set([
  'ok', 'ok_structurally', 'bad_key_shape', 'missing_tenant_selector', 'unknown_key_id',
  'secret_revoked', 'secret_expired', 'integration_inactive', 'tenant_not_found',
  'tenant_inactive', 'no_grant', 'grant_inactive', 'secret_mismatch',
]);

const CRED_SCOPES: readonly (readonly [string, string])[] = [
  ['chat:turn', 'cred.scope.turn'],
  ['chat:suggest', 'cred.scope.suggest'],
  ['chat:answer', 'cred.scope.answer'],
  ['chat:sync', 'cred.scope.sync'],
];

/** How long a rotated-out key keeps verifying. Long enough for a chat service to redeploy. */
const CRED_OVERLAP_DAYS = 7;

const credName = (x: Integration) => x.name || x.id;
const activeGrants = (x: Integration) => (x.grants || []).filter(g => g.is_active);
const wsLabel = (g: Grant) => g.name || g.slug || g.client_id;

export default function ChatConnections() {
  const { t } = useI18n();
  const [creds, setCreds] = useState<Integration[]>([]);
  const [note, setNote] = useState<Note | null>(null);

  /* One error path for every call on this card. The server's own `detail` comes first because
     it names WHICH grant or name was refused; a bare 404 is explained as "not deployed on this
     server yet" rather than as a generic failure, since this console can be newer than the api
     it talks to. The message goes into both a `.msg` and a toast: the dialog that triggered it
     may already be closing. */
  const fail = useCallback((e: unknown, fallback = 'toast.error'): string => {
    const text = errText(e, t, fallback, { 404: 'cred.unavailable' });
    setNote({ kind: 'err', text });
    toast(text, 'err');
    return text;
  }, [t]);

  const load = useCallback(async () => {
    setNote(null);
    try {
      const d = await adminGet<{ integrations?: Integration[] }>(CRED_URL);
      setCreds(Array.isArray(d?.integrations) ? d.integrations : []);
    } catch (e) {
      if (e instanceof SessionExpired) return;
      setCreds([]);
      fail(e, 'cred.loadfail');
    }
  }, [fail]);

  // Once, on mount. `load` changes identity with the language (through `fail`), and this table
  // re-translates by re-rendering — the legacy page had to re-FETCH it to retranslate it.
  const loadRef = useRef(load);
  useEffect(() => { loadRef.current = load; });
  useEffect(() => { void loadRef.current(); }, []);

  /** Only ACTIVE workspaces are ever offered: an inactive one cannot be served whatever the
      grant says, and offering it would mint a grant that can never be used. */
  const activeTenants = async (): Promise<Tenant[]> => {
    try {
      const l = await adminGet<Tenant[]>('/admin/tenants');
      return (Array.isArray(l) ? l : []).filter(x => x.is_active);
    } catch (e) {
      if (e instanceof SessionExpired) throw e;
      return [];
    }
  };

  const reveal = (d: RevealedKey, name: string, overlapDays: number) => {
    void showModal(close => (
      <RevealBody data={d} name={name} overlapDays={overlapDays} t={t} close={close} />
    ), { maxWidth: '640px' });
  };

  const create = async () => {
    const tenants = await activeTenants().catch(() => null);
    if (tenants === null) return;                       // session gone; already redirecting
    void showModal(close => (
      <CreateBody
        tenants={tenants}
        t={t}
        close={close}
        onCreated={(d, name) => { reveal(d, name, 0); void load(); }}
      />
    ), { maxWidth: '560px' });
  };

  /* Rotation is NOT dangerous — the old key keeps working through the overlap — so the confirm
     is the plain kind, and the reveal that follows is the whole point of the click. */
  const rotate = async (x: Integration) => {
    const message = t('cred.rotate.confirm', { name: credName(x), days: CRED_OVERLAP_DAYS });
    if (!(await confirmDialog(message, { ok: t('btn.rotate'), danger: false }))) return;
    try {
      const d = await adminSend<RevealedKey>(
        'POST', `${CRED_URL}/${encodeURIComponent(x.id)}/rotate?overlap_days=${CRED_OVERLAP_DAYS}`);
      setNote(null);
      toast(t('cred.rotated'), 'ok');
      reveal(d, credName(x), d.overlap_days ?? CRED_OVERLAP_DAYS);
      await load();
    } catch (e) {
      if (e instanceof SessionExpired) return;
      fail(e);
    }
  };

  const deactivate = async (x: Integration) => {
    if (!(await confirmDialog(t('cred.deactivate.confirm', { name: credName(x) }),
      { ok: t('cred.deactivate'), danger: true }))) return;
    try {
      await adminSend('DELETE', `${CRED_URL}/${encodeURIComponent(x.id)}`);
      setNote(null);
      toast(t('cred.deactivated'), 'ok');
      await load();
    } catch (e) {
      if (e instanceof SessionExpired) return;
      fail(e);
    }
  };

  const addGrant = async (x: Integration) => {
    const granted = new Set(activeGrants(x).map(g => String(g.client_id)));
    const all = await activeTenants().catch(() => null);
    if (all === null) return;
    // Already-granted workspaces are filtered out: re-granting is a server error the operator
    // would otherwise have to read before understanding it changed nothing.
    const free = all.filter(w => !granted.has(String(w.id)));
    void showModal(close => (
      <GrantBody
        name={credName(x)}
        free={free}
        t={t}
        close={close}
        submit={async (tenant) => {
          try {
            await adminSend('POST', `${CRED_URL}/${encodeURIComponent(x.id)}/grants`, { tenant });
            setNote(null);
            toast(t('cred.grant.added'), 'ok');
            void load();
            return null;
          } catch (e) {
            if (e instanceof SessionExpired) return null;
            return errText(e, t, 'toast.error', { 404: 'cred.unavailable' });
          }
        }}
      />
    ));
  };

  const removeGrant = async (x: Integration, clientId: string) => {
    const g = activeGrants(x).find(y => String(y.client_id) === String(clientId));
    const message = t('cred.grant.remove.confirm', {
      ws: g ? wsLabel(g) : clientId,
      name: credName(x),
    });
    if (!(await confirmDialog(message, { ok: t('btn.remove'), danger: true }))) return;
    try {
      await adminSend('DELETE',
        `${CRED_URL}/${encodeURIComponent(x.id)}/grants/${encodeURIComponent(clientId)}`);
      setNote(null);
      toast(t('cred.grant.removed'), 'ok');
      await load();
    } catch (e) {
      if (e instanceof SessionExpired) return;
      fail(e);
    }
  };

  return (
    <>
    <div className="card">
      <div className="row" style={{ justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div style={{ flex: 1 }}>
          <h3 style={{ margin: 0 }}>{t('cred.heading')}</h3>
          <p className="hint">{t('cred.lead')}</p>
        </div>
        <div className="inline" style={{ flex: 0, gap: 8 }}>
          <button className="ghost" type="button" onClick={() => void load()}>{t('btn.refresh')}</button>
          <button className="primary" type="button" onClick={create}>{t('cred.new')}</button>
        </div>
      </div>

      <div style={{ marginTop: 12 }}>
        {!creds.length ? <div className="empty">{t('cred.empty')}</div> : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>{t('th.name')}</th>
                  <th>{t('th.status')}</th>
                  <th>{t('cred.scopes')}</th>
                  <th>{t('cred.workspaces')}</th>
                  <th>{t('cred.keys')}</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {creds.map(x => {
                  const off = !x.is_active;
                  const grants = activeGrants(x);
                  return (
                    <tr key={x.id} style={off ? { opacity: 0.5 } : undefined}>
                      <td>
                        <b>{credName(x)}</b>
                        <div className="hint"><code>{x.id}</code></div>
                      </td>
                      <td>
                        <span className={`pill ${off ? 'off' : 'on'}`}>
                          {t(off ? 'cred.state.off' : 'cred.state.on')}
                        </span>
                      </td>
                      <td>
                        {(x.scopes || []).length
                          ? (x.scopes || []).map(s => <span className="chip" key={s}>{s}</span>)
                          : '—'}
                      </td>
                      <td>
                        {grants.length ? grants.map(g => (
                          /* The × lives INSIDE the chip so it reads as "this workspace,
                             removable" rather than as a row action; the reset makes it a glyph,
                             not a second pill-shaped button. */
                          <span className="chip" key={String(g.client_id)} title={(g.scopes || []).join(', ')}>
                            {wsLabel(g)}
                            {off ? null : (
                              <button
                                type="button"
                                title={t('cred.grant.remove')}
                                aria-label={t('cred.grant.remove')}
                                onClick={() => removeGrant(x, String(g.client_id))}
                                style={{
                                  background: 'none', border: 0, padding: '0 0 0 6px', margin: 0,
                                  color: 'inherit', cursor: 'pointer', font: 'inherit', lineHeight: 1,
                                }}
                              >
                                ×
                              </button>
                            )}
                          </span>
                        )) : <span className="hint">{t('cred.grant.none')}</span>}
                        {off ? null : (
                          <button
                            className="ghost"
                            type="button"
                            title={t('cred.grant.add')}
                            aria-label={t('cred.grant.add')}
                            style={{ padding: '3px 10px', marginTop: 3 }}
                            onClick={() => addGrant(x)}
                          >
                            +
                          </button>
                        )}
                      </td>
                      <td>
                        {(x.secrets || []).length
                          ? (x.secrets || []).map(s => <KeyBlock key={s.key_id} secret={s} t={t} />)
                          : <span className="hint">{t('cred.keys.none')}</span>}
                      </td>
                      <td className="inline">
                        {off ? null : (
                          <>
                            <button className="ghost" type="button" onClick={() => rotate(x)}>
                              {t('btn.rotate')}
                            </button>
                            <button className="danger" type="button" onClick={() => deactivate(x)}>
                              {t('cred.deactivate')}
                            </button>
                          </>
                        )}
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
    <CredCheck t={t} />
    </>
  );
}

/* ------------------------------------------------------------- check a workspace */

/** What `/admin/integrations/diagnose` reports. Read defensively: this console can be newer
    or older than the api it talks to, and an unknown verdict or an extra step must degrade to
    generic copy rather than to a crash. */
interface DiagCheck {
  step?: string;
  ok: boolean | null;
  detail?: string | null;
}

interface Diagnosis {
  verdict?: string;
  checks?: DiagCheck[];
  granted_tenants?: { client_id?: string; slug?: string | null; name?: string | null }[];
}

const wsText = (w: { client_id?: string; slug?: string | null; name?: string | null }) =>
  [w.slug || w.client_id || '', w.name || ''].filter(Boolean).join(' — ');

/** THE ANSWER TO A 401 FROM A CHAT SERVICE.

    `/v1/chat/*` returns ONE opaque 401 for nine independent conditions, and that opacity is a
    security property toward the caller: a chat service must not be able to probe which
    workspaces exist or whether a key id is real. The operator holds the superadmin token and is
    a different principal entirely, so the same question is answered here in full — paste what
    the chat service holds, name the workspace it names, and the server says which condition
    failed.

    The key field is a password field and is CLEARED after a successful check: an operator will
    paste a live secret into it, and this panel never renders one back. */
function CredCheck({ t }: { t: Translate }) {
  const [key, setKey] = useState('');
  const [tenant, setTenant] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState<Diagnosis | null>(null);
  // The selector as it was WHEN CHECKED, so the "not granted" line keeps naming what produced
  // the verdict on screen while the operator retypes the field.
  const [asked, setAsked] = useState('');

  const run = async () => {
    const k = key.trim();
    const ws = tenant.trim();
    if (!k || !ws) { setError(t('cred.check.required')); return; }
    setBusy(true);
    setError('');
    try {
      const d = await adminSend<Diagnosis>('POST', `${CRED_URL}/diagnose`, { key: k, tenant: ws });
      setResult(d && typeof d === 'object' ? d : {});
      setAsked(ws);
      setKey('');
    } catch (e) {
      if (e instanceof SessionExpired) return;
      setResult(null);
      setError(errText(e, t, 'cred.check.failed', { 404: 'cred.unavailable' }));
    } finally {
      setBusy(false);
    }
  };

  const verdict = result?.verdict || '';
  const good = verdict === 'ok' || verdict === 'ok_structurally';
  const granted = (result?.granted_tenants || []).filter(w => w && (w.slug || w.client_id));
  const needle = asked.toLowerCase();
  const hit = granted.some(w =>
    String(w.client_id || '').toLowerCase() === needle || String(w.slug || '').toLowerCase() === needle);

  return (
    <div className="card">
      <h3 style={{ margin: 0 }}>{t('cred.check.heading')}</h3>
      <p className="hint">{t('cred.check.lead')}</p>

      <div className="row" style={{ gap: 12, alignItems: 'flex-end' }}>
        <div style={{ flex: '2 1 260px' }}>
          <label htmlFor="ccdKey">{t('cred.check.key')}</label>
          <input
            id="ccdKey"
            type="password"
            autoComplete="off"
            placeholder="cqi_…"
            value={key}
            onChange={e => setKey(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); void run(); } }}
          />
        </div>
        <div style={{ flex: '1 1 200px' }}>
          <label htmlFor="ccdWs">{t('cred.check.tenant')}</label>
          <input
            id="ccdWs"
            placeholder={t('cred.check.tenant.ph')}
            value={tenant}
            onChange={e => setTenant(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); void run(); } }}
          />
        </div>
        <div className="actions" style={{ flex: '0 0 auto', margin: 0 }}>
          <button className="primary" type="button" onClick={() => void run()} disabled={busy}>
            {t('cred.check.run')}
          </button>
        </div>
      </div>
      <p className="hint">{t('cred.check.privacy')}</p>

      {error ? <div className="msg err">{error}</div> : null}

      {result ? (
        <div style={{ marginTop: 10 }}>
          <div className={`msg ${good ? 'ok' : 'err'}`}>
            {t(CRED_VERDICTS.has(verdict) ? `cred.verdict.${verdict}` : 'cred.check.unknown')}
          </div>

          {(result.checks || []).length ? (
            <ul style={{ listStyle: 'none', margin: '10px 0 0', padding: 0 }}>
              {(result.checks || []).map((c, i) => (
                <li key={c.step || i} style={{ margin: '4px 0' }}>
                  {/* A dash, not a cross: a step the server never reached is not a step that
                      failed, and reading it as a failure sends the operator after the wrong
                      condition. */}
                  <span aria-hidden style={{ display: 'inline-block', width: 18 }}>
                    {c.ok === true ? '✓' : c.ok === false ? '✗' : '–'}
                  </span>
                  <code>{c.step || '?'}</code>
                  {c.detail ? <span className="hint"> — {c.detail}</span> : null}
                </li>
              ))}
            </ul>
          ) : null}

          {granted.length ? (
            <div style={{ marginTop: 10 }}>
              <div className="hint">{t('cred.check.granted')}</div>
              <div>{granted.map(w => <span className="chip" key={w.client_id || w.slug}>{wsText(w)}</span>)}</div>
              {/* The common case in an incident: the key is fine and the workspace simply is
                  not on its list. Saying so beats leaving the operator to compare uuids. */}
              {hit ? null : <p className="msg err">{t('cred.check.notgranted', { tenant: asked })}</p>}
            </div>
          ) : <p className="hint" style={{ marginTop: 10 }}>{t('cred.check.nogrants')}</p>}
        </div>
      ) : null}
    </div>
  );
}

/** One secret's public face: its `key_id`, its state, and the two stamps that say whether
    anything is still using it. */
function KeyBlock({ secret, t }: { secret: Secret; t: Translate }) {
  const state = keyState(secret);
  return (
    <div style={{ whiteSpace: 'nowrap' }}>
      <code>{secret.key_id}</code>{' '}
      {state === 'revoked' ? <span className="pill off">{t('cred.key.revoked')}</span> : null}
      {state === 'expired' ? <span className="pill off">{t('cred.key.expired')}</span> : null}
      {state === 'expires'
        ? <span className="pill pending">{t('cred.key.expires', { when: dateTime(secret.expires_at) })}</span>
        : null}
      <div className="hint">
        {t('cred.key.created')} {dateTime(secret.created_at)} · {t('cred.key.lastused')}{' '}
        {secret.last_used_at ? dateTime(secret.last_used_at) : t('pb.never')}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ new connection */

/** Scopes default to ALL FOUR: a connection that cannot do something fails at 3am in the chat
    service's logs, not here, so the safe default is "can" and narrowing is a deliberate choice. */
function CreateBody({
  tenants, t, close, onCreated,
}: {
  tenants: Tenant[];
  t: Translate;
  close: (v?: unknown) => void;
  onCreated: (d: RevealedKey, name: string) => void;
}) {
  const [name, setName] = useState('');
  const [scopes, setScopes] = useState<Record<string, boolean>>(
    () => Object.fromEntries(CRED_SCOPES.map(([s]) => [s, true])),
  );
  const [picked, setPicked] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const submit = async () => {
    const nm = name.trim();
    const chosen = CRED_SCOPES.map(([s]) => s).filter(s => scopes[s]);
    const grants = tenants.map(x => String(x.id)).filter(id => picked[id]);
    if (!nm) { setError(t('cred.name.required')); return; }
    if (!chosen.length) { setError(t('cred.scopes.required')); return; }
    setBusy(true);
    try {
      const d = await adminSend<RevealedKey>('POST', CRED_URL, { name: nm, scopes: chosen, grants });
      close(true);
      toast(t('cred.created'), 'ok');
      onCreated(d, nm);
    } catch (e) {
      if (e instanceof SessionExpired) return;
      const text = errText(e, t, 'toast.error', { 404: 'cred.unavailable' });
      setError(text);
      toast(text, 'err');
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <h3 style={{ marginTop: 0 }}>{t('cred.new')}</h3>
      <label htmlFor="ccName">{t('f.name')}</label>
      <input
        id="ccName"
        maxLength={120}
        placeholder={t('cred.name.ph')}
        value={name}
        data-autofocus
        onChange={e => setName(e.target.value)}
        onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); void submit(); } }}
      />
      <label>{t('cred.scopes')}</label>
      {CRED_SCOPES.map(([s, key]) => (
        <label className="inline" style={{ gap: 8, margin: '8px 0 0' }} key={s}>
          <input
            type="checkbox"
            checked={!!scopes[s]}
            onChange={e => setScopes(v => ({ ...v, [s]: e.target.checked }))}
          />
          <span><code>{s}</code> — {t(key)}</span>
        </label>
      ))}
      {/* A key short a scope fails with the SAME 401 as a bad key (chat.py refuses to say which),
          so the one place to prevent that diagnosis is here, before the key is minted. */}
      <p className="hint">{t('cred.scopes.hint')}</p>
      <label>{t('cred.workspaces')}</label>
      <div style={{ maxHeight: 220, overflow: 'auto' }}>
        {tenants.length ? tenants.map(x => (
          <label className="inline" style={{ gap: 8, margin: '8px 0 0' }} key={x.id}>
            <input
              type="checkbox"
              checked={!!picked[String(x.id)]}
              onChange={e => setPicked(v => ({ ...v, [String(x.id)]: e.target.checked }))}
            />
            <span>{x.name} <code>{x.slug}</code></span>
          </label>
        )) : <p className="hint">{t('cred.workspaces.none')}</p>}
      </div>
      <p className="hint">{t('cred.workspaces.hint')}</p>
      {error ? <div className="msg err">{error}</div> : null}
      <div className="actions">
        <button className="ghost" type="button" onClick={() => close(false)}>{t('btn.cancel')}</button>
        <button className="primary" type="button" onClick={submit} disabled={busy}>{t('cred.create')}</button>
      </div>
    </>
  );
}

/* --------------------------------------------------------------- the key, once */

/** THE ONLY PLACE A KEY IS EVER IN THE CLEAR.

    `warning` is the server's own sentence about that and is preferred; the built-in wording
    covers an api that sends none. The header snippet is here, next to the key, because the
    operator pastes both into the chat service's config in the same minute — a separate docs
    page would be one more tab to lose the key behind.

    THE COPY BUTTON AWAITS NOTHING BEFORE `copyText`. Production is plain HTTP, where
    `navigator.clipboard` does not exist and the synchronous textarea fallback inside `copyText`
    is the path that runs — and `document.execCommand('copy')` only works inside the click's own
    gesture, which any `await` before it would have spent. The `select()` first is deliberate
    too: a failed copy still leaves the key selected, so it can be copied by hand. */
function RevealBody({
  data, name, overlapDays, t, close,
}: {
  data: RevealedKey;
  name: string;
  overlapDays: number;
  t: Translate;
  close: (v?: unknown) => void;
}) {
  const key = data.api_key || '';
  const input = useRef<HTMLInputElement>(null);
  const mono = 'ui-monospace,SFMono-Regular,Menlo,monospace';
  const snippet = [
    `X-CQ-Key: ${key}`,
    `X-CQ-Tenant: <${t('cred.snippet.tenant')}>`,
    `X-CQ-Expect-Tenant: <${t('cred.snippet.same')}>`,
  ].join('\n');

  return (
    <>
      <h3 style={{ marginTop: 0 }}>{t('cred.reveal.title')}</h3>
      <p className="hint" style={{ marginTop: -6 }}>{name}</p>
      <div className="inline" style={{ gap: 8 }}>
        <input
          ref={input}
          readOnly
          value={key}
          style={{ fontFamily: mono, flex: 1 }}
          onFocus={e => e.currentTarget.select()}
        />
        <button
          className="ghost"
          type="button"
          style={{ flex: '0 0 auto' }}
          onClick={() => {
            input.current?.select();
            copyText(key).then(ok => toast(ok ? t('pb.copied') : t('cred.copyfail'), ok ? 'ok' : 'err'));
          }}
        >
          {t('pb.copy')}
        </button>
      </div>
      <p className="msg err" style={{ marginTop: 10 }}>{data.warning || t('cred.reveal.once')}</p>
      {overlapDays ? <p className="hint">{t('cred.reveal.overlap', { days: overlapDays })}</p> : null}
      <p className="hint">{t('cred.reveal.headers')}</p>
      <pre className="tx" style={{ margin: 0, fontFamily: mono }}>{snippet}</pre>
      <p className="hint">{t('cred.reveal.serverside')}</p>
      <div className="actions">
        <button className="primary" type="button" data-autofocus onClick={() => close(true)}>
          {t('pb.close')}
        </button>
      </div>
    </>
  );
}

/* -------------------------------------------------------------------- one grant */

function GrantBody({
  name, free, t, close, submit,
}: {
  name: string;
  free: Tenant[];
  t: Translate;
  close: (v?: unknown) => void;
  submit: (tenant: string) => Promise<string | null>;
}) {
  const [value, setValue] = useState(free.length ? String(free[0].id) : '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const go = async () => {
    setBusy(true);
    const message = await submit(value);
    setBusy(false);
    if (message) setError(message);
    else close(true);
  };

  return (
    <>
      <h3 style={{ marginTop: 0 }}>{t('cred.grant.title', { name })}</h3>
      {free.length ? (
        <>
          <label htmlFor="agWs">{t('cred.grant.pick')}</label>
          <Select
            id="agWs"
            value={value}
            onChange={setValue}
            options={free.map(w => ({ value: String(w.id), label: `${w.name} (${w.slug})` }))}
            ariaLabel={t('cred.grant.pick')}
          />
        </>
      ) : <p className="hint">{t('cred.grant.allgranted')}</p>}
      {error ? <div className="msg err">{error}</div> : null}
      <div className="actions">
        <button className="ghost" type="button" onClick={() => close(false)}>{t('btn.cancel')}</button>
        {free.length ? (
          <button className="primary" type="button" onClick={go} disabled={busy}>
            {t('cred.grant.submit')}
          </button>
        ) : null}
      </div>
    </>
  );
}
