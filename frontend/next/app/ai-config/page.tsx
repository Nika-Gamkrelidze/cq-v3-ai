'use client';
import { useCallback, useEffect, useMemo, useState } from 'react';
import Header from '@/components/Header';
import { Select } from '@/components/ui/Select';
import { toast } from '@/components/ui/Toast';
import { apiGet, apiMessage, apiSend, readSession } from '@/lib/session';
import { useI18n } from '@/lib/useI18n';
import type { AiAssignment, AiAssignments, AiConnection, Capability } from '../console/api';
import {
  CAPABILITIES, assignmentOptions, assignmentsPayload, defaultOf, effectiveLabel,
  groupConnections, picksFromAssignments, sourceKey,
} from '../console/logic';

/* Which AI each workspace runs on — per capability.
   ===============================================
   Almost every workspace should be on the default and this page should be almost entirely
   "Default" rows: an assignment is a commercial exception (a customer who asked for a different
   model, or a pilot on a second provider), not a knob to turn. The page is shaped to make that
   obvious: the list leads with what each workspace is REALLY running on, per capability, and
   the editor's three pickers open on "Default (<name>)".

   The chain each picker sits in is `default <- assigned <- the workspace's own key`, and the
   last rung is not editable here: a workspace that brought its own key manages it from its
   portal, and this page only SAYS so (the "in effect" line). The one exception is the Text AI
   compatibility card at the bottom, which is the old per-workspace override — kept because it
   is the only place a gateway endpoint can be set for a workspace, and that is deliberately an
   operator's decision and never the customer's.

   The stored key is never sent back by the API, only `has_key`. So the field cannot be
   pre-filled, "save with an empty box" cannot mean "clear it", and removing a key is its own
   deliberate action. */

interface Tenant {
  id: string;
  name: string;
  slug: string | null;
  is_active: boolean;
}

/** The compatibility route's shape — `GET/PUT /admin/ai-config/{tenant_id}`, over the Text AI
    override. */
interface Config {
  enabled: boolean;
  provider: string;
  model: string | null;
  base_url: string | null;
  has_key: boolean;
  notes: string | null;
  updated_at: string | null;
  updated_by: string | null;
}

const BLANK: Config = {
  enabled: false, provider: 'anthropic', model: null, base_url: null,
  has_key: false, notes: null, updated_at: null, updated_by: null,
};

type Translate = (k: string, v?: Record<string, string | number>) => string;

const ADMIN = { scope: 'admin' } as const;
const when = (iso?: string | null) => (iso ? new Date(iso).toLocaleString() : '—');

/** One shared "nothing loaded" object. The editor re-reads its pickers whenever the
    assignments prop changes identity, so a fresh `{}` per render would wipe an unsaved pick on
    any re-render of the page. */
const NO_ASSIGNMENTS: AiAssignments = {};

export default function AiConfigPage() {
  const { t } = useI18n();
  const [ready, setReady] = useState(false);
  const [isOperator, setIsOperator] = useState(false);
  const [tenants, setTenants] = useState<Tenant[] | null>(null);
  const [connections, setConnections] = useState<AiConnection[]>([]);
  const [assignments, setAssignments] = useState<Record<string, AiAssignments>>({});
  const [q, setQ] = useState('');
  const [openId, setOpenId] = useState<string | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    setIsOperator(readSession().role === 'superadmin');
    setReady(true);
  }, []);

  const loadAssignments = useCallback(async (id: string): Promise<AiAssignments> => {
    // A workspace whose assignments fail to load shows as unknown ("—") rather than taking the
    // list down with it; the editor's Save re-fetches and surfaces the real error.
    try { return await apiGet<AiAssignments>(`/admin/ai/assignments/${id}`, ADMIN); }
    catch { return {}; }
  }, []);

  const load = useCallback(async () => {
    setError('');
    try {
      const [list, conns] = await Promise.all([
        apiGet<Tenant[]>('/admin/tenants', ADMIN),
        // Only for the pickers and the "default is X" lines. A registry that is not deployed
        // yet must not hide the workspace list.
        apiGet<AiConnection[]>('/admin/ai/connections', ADMIN).catch(() => [] as AiConnection[]),
      ]);
      setTenants(list);
      setConnections(Array.isArray(conns) ? conns : []);
      // One request per workspace, in parallel. Deliberately not a new bulk endpoint: the
      // number of tenants is small, and each row degrades on its own.
      const pairs = await Promise.all(list.map(async tn => [tn.id, await loadAssignments(tn.id)] as const));
      setAssignments(Object.fromEntries(pairs));
    } catch (e) {
      setError(apiMessage(e, t) || t('aicfg.loadfail'));
    }
  }, [t, loadAssignments]);

  useEffect(() => { if (ready && isOperator) void load(); }, [ready, isOperator, load]);

  // A deep link from the usage console: /ai-config#<client_id> opens that workspace.
  useEffect(() => {
    if (!tenants) return;
    const id = window.location.hash.slice(1);
    if (id && tenants.some(tn => tn.id === id)) setOpenId(id);
  }, [tenants]);

  const groups = useMemo(() => groupConnections(connections), [connections]);

  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle || !tenants) return tenants || [];
    return tenants.filter(tn =>
      tn.name.toLowerCase().includes(needle) || (tn.slug || '').toLowerCase().includes(needle));
  }, [tenants, q]);

  if (!ready) return <><Header tag="Console" /><main /></>;

  if (!isOperator) {
    return (
      <>
        <Header tag="Console" />
        <main className="narrow">
          <div className="card">
            <p className="lead">{t('aicfg.adminonly')}</p>
            <div className="actions"><a className="ghost" href="/tenant.html">{t('nav.signin')}</a></div>
          </div>
        </main>
      </>
    );
  }

  const open = openId ? tenants?.find(tn => tn.id === openId) : null;

  return (
    <>
      <Header tag="Console" />
      <main className="console">
        <div style={{ marginBottom: 18 }}>
          <h1 style={{ margin: 0, fontSize: 'clamp(24px,4vw,32px)' }}>{t('aicfg.title')}</h1>
          <p className="lead">{t('aicfg.lead')}</p>
        </div>

        {error ? <div className="msg err">{error}</div> : null}

        {open ? (
          <Editor
            tenant={open}
            groups={groups}
            assignments={assignments[open.id] || NO_ASSIGNMENTS}
            onAssigned={next => setAssignments(a => ({ ...a, [open.id]: next }))}
            reload={() => loadAssignments(open.id)}
            onBack={() => {
              setOpenId(null);
              if (window.location.hash) {
                history.replaceState(null, '', window.location.pathname);
              }
            }}
            t={t}
          />
        ) : (
          <>
            {/* The defaults, once, above the list: every "Default" cell below means one of
                these, and an operator deciding whether a workspace needs an assignment needs
                to know what "Default" is without opening the console. */}
            <div className="card">
              <div className="inline" style={{ justifyContent: 'space-between', flexWrap: 'wrap', gap: 10 }}>
                <h3 style={{ margin: 0 }}>{t('aicfg.defaults')}</h3>
                <a className="ghost" href="/console?tab=ai">{t('aicfg.registry')}</a>
              </div>
              {CAPABILITIES.map(cap => {
                const def = defaultOf(groups[cap]);
                return (
                  <div className="kv" key={cap}>
                    <b>{t(`ai.cap.${cap}`)}</b>{' '}
                    {def
                      ? <>{def.name} <span className="hint">· {def.provider}{def.model ? ` · ${def.model}` : ''}</span></>
                      : <span className="hint">{t('ai.legacy')}</span>}
                  </div>
                );
              })}
            </div>

            <div className="card">
              <div className="inline" style={{ gap: 10, marginBottom: 12 }}>
                <input
                  type="search"
                  value={q}
                  onChange={e => setQ(e.target.value)}
                  placeholder={t('aicfg.search')}
                  aria-label={t('aicfg.search')}
                  style={{ maxWidth: 320 }}
                />
              </div>
              {tenants === null && !error ? (
                <div className="empty"><span className="spinner" /></div>
              ) : !tenants?.length ? (
                <div className="empty">{t('aicfg.none')}</div>
              ) : !shown.length ? (
                <div className="empty">{t('aicfg.nomatch')}</div>
              ) : (
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>{t('aicfg.th.tenant')}</th>
                        {CAPABILITIES.map(cap => <th key={cap}>{t(`ai.cap.${cap}`)}</th>)}
                      </tr>
                    </thead>
                    <tbody>
                      {shown.map(tn => {
                        const a = assignments[tn.id] || NO_ASSIGNMENTS;
                        return (
                          <tr key={tn.id} onClick={() => setOpenId(tn.id)} style={{ cursor: 'pointer' }}>
                            <td>
                              <b>{tn.name}</b>
                              {tn.slug ? <span className="hint"> · {tn.slug}</span> : null}
                              {!tn.is_active ? <span className="hint"> · {t('aicfg.inactive')}</span> : null}
                            </td>
                            {CAPABILITIES.map(cap => (
                              <td key={cap}><EffectiveCell a={a[cap]} t={t} /></td>
                            ))}
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </>
        )}
      </main>
    </>
  );
}

/** One capability's "runs on" for the list: the connection (or provider) in bold, the model and
    the source underneath, and a pill when the workspace's own key is what answers. A default
    that is merely inherited is greyed — the eye should land on the exceptions. */
function EffectiveCell({ a, t }: { a: AiAssignment | undefined; t: Translate }) {
  const eff = a?.effective;
  const label = effectiveLabel(eff);
  if (!eff || !label) return <span className="hint">—</span>;
  const inherited = eff.source === 'default' || eff.source === 'legacy';
  return (
    <>
      {inherited ? <span className="hint">{label.head}</span> : <b>{label.head}</b>}
      {eff.source === 'byo' ? <> <span className="pill on">{t('aicfg.byo')}</span></> : null}
      <div className="hint">
        {label.model ? <>{label.model} · </> : null}{t(sourceKey(eff.source))}
      </div>
    </>
  );
}

/* --------------------------------------------------------------------- the editor */

function Editor({
  tenant, groups, assignments, onAssigned, reload, onBack, t,
}: {
  tenant: Tenant;
  groups: Record<Capability, AiConnection[]>;
  assignments: AiAssignments;
  onAssigned: (next: AiAssignments) => void;
  reload: () => Promise<AiAssignments>;
  onBack: () => void;
  t: Translate;
}) {
  const [config, setConfig] = useState<Config | null>(null);

  // The compatibility card's data, fetched only for the open workspace — it is the one call
  // on this page that is not about assignments.
  useEffect(() => {
    let live = true;
    setConfig(null);
    apiGet<Config>(`/admin/ai-config/${tenant.id}`, ADMIN)
      .then(c => { if (live) setConfig(c); })
      .catch(() => { if (live) setConfig(BLANK); });
    return () => { live = false; };
  }, [tenant.id]);

  return (
    <>
      <div className="card">
        <div className="inline" style={{ justifyContent: 'space-between', flexWrap: 'wrap', gap: 10 }}>
          <div>
            <h3 style={{ margin: 0 }}>{tenant.name}</h3>
            {tenant.slug ? <p className="hint" style={{ margin: '4px 0 0' }}>{tenant.slug}</p> : null}
          </div>
          <div className="inline" style={{ gap: 8 }}>
            <a className="ghost" href={`/usage#${tenant.id}`}>{t('aicfg.usage')}</a>
            <a className="ghost" href="/console?tab=ai">{t('aicfg.registry')}</a>
            <button className="ghost" type="button" onClick={onBack}>← {t('aicfg.back')}</button>
          </div>
        </div>
      </div>

      <Assignments
        key={tenant.id}
        tenant={tenant}
        groups={groups}
        assignments={assignments}
        onAssigned={onAssigned}
        reload={reload}
        t={t}
      />

      {config ? (
        <OverrideCard key={tenant.id} tenant={tenant} config={config} onDone={setConfig} t={t} />
      ) : (
        <div className="card"><div className="empty"><span className="spinner" /></div></div>
      )}
    </>
  );
}

/** The three pickers. Saved together, as one PUT with every capability named (`logic.ts`,
    `assignmentsPayload`): a form that shows all three means all three. */
function Assignments({
  tenant, groups, assignments, onAssigned, reload, t,
}: {
  tenant: Tenant;
  groups: Record<Capability, AiConnection[]>;
  assignments: AiAssignments;
  onAssigned: (next: AiAssignments) => void;
  reload: () => Promise<AiAssignments>;
  t: Translate;
}) {
  const [picks, setPicks] = useState<Record<Capability, string>>(() => picksFromAssignments(assignments));
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState('');

  // A save re-fetches, and the pickers follow what the server now says — not what was sent.
  useEffect(() => { setPicks(picksFromAssignments(assignments)); }, [assignments]);

  const dirty = CAPABILITIES.some(cap => picks[cap] !== (assignments[cap]?.connection_id || ''));

  const labels = useMemo(() => ({
    default: (name: string | null) => (name ? t('ai.assign.default', { name }) : t('ai.assign.default.none')),
    connection: (c: AiConnection) => `${c.name} — ${c.provider}${c.model ? ` · ${c.model}` : ''}`,
  }), [t]);

  const save = async () => {
    setSaving(true);
    setErr('');
    try {
      await apiSend('PUT', `/admin/ai/assignments/${tenant.id}`, assignmentsPayload(picks), ADMIN);
      onAssigned(await reload());
      toast(t('ai.assign.saved'), 'ok');
    } catch (e) {
      setErr(apiMessage(e, t) || t('aicfg.savefail'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="card">
      <h3 style={{ margin: 0 }}>{t('aicfg.assign.heading')}</h3>
      <p className="hint">{t('ai.assign.hint')}</p>

      {CAPABILITIES.map(cap => {
        const a = assignments[cap];
        const eff = a?.effective;
        const label = effectiveLabel(eff);
        const byo = eff?.source === 'byo';
        const id = `assign-${cap}`;
        return (
          <div className="field" key={cap}>
            <label htmlFor={id}>{t(`ai.cap.${cap}`)}</label>
            <Select
              id={id}
              value={picks[cap]}
              onChange={v => setPicks(p => ({ ...p, [cap]: v }))}
              options={assignmentOptions(groups[cap], a?.connection_id || null, labels)}
              ariaLabel={t('ai.assign')}
              style={{ maxWidth: 480 }}
            />
            {/* What the resolver would answer right now, which is not always what the picker
                shows: a workspace's own key beats the assignment, and the line says so. */}
            <p className="hint">
              <b>{t('ai.effective')}:</b>{' '}
              {label
                ? <>{t(sourceKey(eff?.source))} — {label.head}{label.model ? ` · ${label.model}` : ''}</>
                : '—'}
              {byo ? <> <span className="pill on">{t('aicfg.byo')}</span> {t('ai.byo.readonly')}</> : null}
            </p>
          </div>
        );
      })}

      {err ? <div className="msg err">{err}</div> : null}
      <div className="actions">
        <button className="primary" type="button" onClick={save} disabled={saving || !dirty}>
          {saving ? t('aicfg.saving') : t('aicfg.save')}
        </button>
      </div>
    </div>
  );
}

/* ------------------------------------------------- the Text AI override (compat) */

/** The old per-workspace override, over `/admin/ai-config/{id}` — the workspace's own Text AI
    key as its owner set it, plus the two things only an operator may touch: the endpoint and
    the note. Kept as a card rather than folded into the pickers above because it is a different
    rung of the chain: the pickers choose between OUR connections, this is THEIRS. */
function OverrideCard({
  tenant, config, onDone, t,
}: {
  tenant: Tenant;
  config: Config;
  onDone: (next: Config) => void;
  t: Translate;
}) {
  const [enabled, setEnabled] = useState(config.enabled);
  const [model, setModel] = useState(config.model || '');
  const [provider, setProvider] = useState(config.provider || 'anthropic');
  const [baseUrl, setBaseUrl] = useState(config.base_url || '');
  const [notes, setNotes] = useState(config.notes || '');
  const [newKey, setNewKey] = useState('');
  const [clearKey, setClearKey] = useState(false);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState('');
  const [err, setErr] = useState('');

  // A save hands back the stored row, and every field follows it.
  useEffect(() => {
    setEnabled(config.enabled);
    setModel(config.model || '');
    setProvider(config.provider || 'anthropic');
    setBaseUrl(config.base_url || '');
    setNotes(config.notes || '');
    setNewKey('');
    setClearKey(false);
  }, [config]);

  const save = async () => {
    setSaving(true);
    setMsg('');
    setErr('');
    try {
      const next = await apiSend<Config>('PUT', `/admin/ai-config/${tenant.id}`, {
        enabled,
        provider: provider.trim() || 'anthropic',
        model: model.trim() || null,
        base_url: baseUrl.trim() || null,
        // Absent unless a new one was typed — the box is empty on every load, so sending it
        // unconditionally would wipe the stored key on an unrelated edit.
        ...(newKey.trim() ? { api_key: newKey.trim() } : {}),
        clear_key: clearKey,
        notes: notes.trim() || null,
      }, ADMIN);
      onDone(next);
      setMsg(t('aicfg.saved'));
    } catch (e) {
      setErr(apiMessage(e, t) || t('aicfg.savefail'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="card">
      <h3 style={{ margin: 0 }}>{t('aicfg.override.heading')}</h3>
      <p className="hint">{t('aicfg.override.lead')}</p>
      <p className="hint">
        {config.updated_at
          ? t('aicfg.changed', { when: when(config.updated_at), who: config.updated_by || '—' })
          : t('aicfg.never')}
      </p>

      <label className="inline" style={{ gap: 10, cursor: 'pointer', margin: '12px 0 0' }}>
        <input type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)} />
        <b>{t('aicfg.enabled')}</b>
      </label>
      <p className="hint" style={{ marginTop: 6 }}>
        {enabled ? t('aicfg.enabled.on') : t('aicfg.enabled.off')}
      </p>

      <div className="field">
        <label htmlFor="ovModel">{t('aicfg.model')}</label>
        <input id="ovModel" value={model} onChange={e => setModel(e.target.value)} spellCheck={false} />
        <p className="hint">{t('aicfg.model.hint')}</p>
      </div>

      <div className="field">
        <label htmlFor="ovProvider">{t('aicfg.provider')}</label>
        <input id="ovProvider" value={provider} onChange={e => setProvider(e.target.value)} spellCheck={false} />
      </div>

      <div className="field">
        <label htmlFor="ovBaseUrl">{t('aicfg.baseurl')}</label>
        <input
          id="ovBaseUrl"
          value={baseUrl}
          onChange={e => setBaseUrl(e.target.value)}
          placeholder="https://"
          spellCheck={false}
        />
        <p className="hint">{t('aicfg.baseurl.hint')}</p>
      </div>

      <div className="field">
        <label htmlFor="ovKey">{t('aicfg.key')}</label>
        <input
          id="ovKey"
          type="password"
          value={newKey}
          onChange={e => { setNewKey(e.target.value); if (e.target.value) setClearKey(false); }}
          placeholder={config.has_key ? t('aicfg.key.ph') : ''}
          autoComplete="off"
          spellCheck={false}
        />
        <p className="hint">{config.has_key ? t('aicfg.key.set') : t('aicfg.key.none')}</p>
        {config.has_key && !clearKey ? (
          <button className="ghost" type="button" onClick={() => { setClearKey(true); setNewKey(''); }}>
            {t('aicfg.key.remove')}
          </button>
        ) : null}
        {clearKey ? (
          <div className="msg err" style={{ marginTop: 8 }}>
            {t('aicfg.key.removing')}{' '}
            <button className="ghost" type="button" onClick={() => setClearKey(false)}>
              {t('aicfg.key.keep')}
            </button>
          </div>
        ) : null}
      </div>

      <div className="field">
        <label htmlFor="ovNotes">{t('aicfg.notes')}</label>
        <textarea id="ovNotes" rows={3} value={notes} onChange={e => setNotes(e.target.value)} />
        <p className="hint">{t('aicfg.notes.hint')}</p>
      </div>

      {err ? <div className="msg err">{err}</div> : null}
      {msg ? <div className="msg ok">{msg}</div> : null}

      <div className="actions">
        <button className="primary" type="button" onClick={save} disabled={saving}>
          {saving ? t('aicfg.saving') : t('aicfg.save')}
        </button>
      </div>
    </div>
  );
}
