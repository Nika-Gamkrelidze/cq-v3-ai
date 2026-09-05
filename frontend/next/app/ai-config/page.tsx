'use client';
import { useCallback, useEffect, useMemo, useState } from 'react';
import Header from '@/components/Header';
import { apiGet, apiSend, readSession } from '@/lib/session';
import { useI18n } from '@/lib/useI18n';

/* Which AI each workspace runs on.
   ===============================
   Almost every workspace should be on the default and this page should be almost entirely
   "Default" rows — an override is a commercial exception (a customer who asked for a
   different model, or who brings their own provider key so the spend lands on their account),
   not a knob to turn. The page is shaped to make that obvious: the list leads with what a
   workspace runs on, and the editor opens switched off.

   The stored key is never sent back by the API, only `has_key`. So the field cannot be
   pre-filled, "save with an empty box" cannot mean "clear it", and removing a key is its own
   deliberate action. */

interface Tenant {
  id: string;
  name: string;
  slug: string | null;
  is_active: boolean;
}

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

const when = (iso?: string | null) => (iso ? new Date(iso).toLocaleString() : '—');

export default function AiConfigPage() {
  const { t } = useI18n();
  const [ready, setReady] = useState(false);
  const [isOperator, setIsOperator] = useState(false);
  const [tenants, setTenants] = useState<Tenant[] | null>(null);
  const [configs, setConfigs] = useState<Record<string, Config>>({});
  const [defaultModel, setDefaultModel] = useState('');
  const [q, setQ] = useState('');
  const [openId, setOpenId] = useState<string | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    setIsOperator(readSession().role === 'superadmin');
    setReady(true);
  }, []);

  const load = useCallback(async () => {
    setError('');
    try {
      const [list, settings] = await Promise.all([
        apiGet<Tenant[]>('/admin/tenants'),
        // Only for the "default is X" line. A failure here must not hide the whole page.
        apiGet<{ llm_model?: string }>('/admin/settings').catch(() => ({ llm_model: '' })),
      ]);
      setTenants(list);
      setDefaultModel(settings.llm_model || '');
      // One request per workspace, in parallel. Deliberately not a new bulk endpoint: the
      // number of tenants is small, and a workspace whose config fails to load shows as
      // default rather than taking the list down with it.
      const pairs = await Promise.all(list.map(async (tn) => {
        try { return [tn.id, await apiGet<Config>(`/admin/ai-config/${tn.id}`)] as const; }
        catch { return [tn.id, BLANK] as const; }
      }));
      setConfigs(Object.fromEntries(pairs));
    } catch (e) {
      setError(e instanceof Error ? e.message : t('aicfg.loadfail'));
    }
  }, [t]);

  useEffect(() => { if (ready && isOperator) void load(); }, [ready, isOperator, load]);

  // A deep link from the usage console: /ai-config#<client_id> opens that workspace.
  useEffect(() => {
    if (!tenants) return;
    const id = window.location.hash.slice(1);
    if (id && tenants.some(tn => tn.id === id)) setOpenId(id);
  }, [tenants]);

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
          <p className="hint">
            {defaultModel
              ? t('aicfg.default.is', { model: defaultModel })
              : t('aicfg.default.unset')}
          </p>
        </div>

        {error ? <div className="msg err">{error}</div> : null}

        {open ? (
          <Editor
            tenant={open}
            config={configs[open.id] || BLANK}
            defaultModel={defaultModel}
            onDone={(next) => {
              setConfigs(c => ({ ...c, [open.id]: next }));
            }}
            onBack={() => {
              setOpenId(null);
              if (window.location.hash) {
                history.replaceState(null, '', window.location.pathname);
              }
            }}
            t={t}
          />
        ) : (
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
                      <th>{t('aicfg.th.runson')}</th>
                      <th>{t('aicfg.th.billing')}</th>
                      <th>{t('aicfg.th.updated')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {shown.map(tn => {
                      const c = configs[tn.id] || BLANK;
                      const custom = c.enabled;
                      // A staged row (fields set, switch off) is called out rather than shown
                      // as plain "Default": the operator who staged it needs to find it again.
                      const runs = custom
                        ? (c.model || defaultModel || '—')
                        : (c.model || c.base_url || c.has_key)
                          ? t('aicfg.runs.staged')
                          : t('aicfg.runs.default');
                      return (
                        <tr key={tn.id} onClick={() => setOpenId(tn.id)} style={{ cursor: 'pointer' }}>
                          <td>
                            <b>{tn.name}</b>
                            {tn.slug ? <span className="hint"> · {tn.slug}</span> : null}
                            {!tn.is_active ? <span className="hint"> · inactive</span> : null}
                          </td>
                          <td>{custom ? <b>{runs}</b> : <span className="hint">{runs}</span>}</td>
                          <td>
                            {custom && c.has_key
                              ? <span className="warn-flag">{t('aicfg.bill.them')}</span>
                              : <span className="hint">{t('aicfg.bill.us')}</span>}
                          </td>
                          <td className="hint">{when(c.updated_at)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
      </main>
    </>
  );
}

function Editor({
  tenant, config, defaultModel, onDone, onBack, t,
}: {
  tenant: Tenant;
  config: Config;
  defaultModel: string;
  onDone: (next: Config) => void;
  onBack: () => void;
  t: (k: string, v?: Record<string, string | number>) => string;
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

  // Switching workspace reuses this component, so every field has to follow.
  useEffect(() => {
    setEnabled(config.enabled);
    setModel(config.model || '');
    setProvider(config.provider || 'anthropic');
    setBaseUrl(config.base_url || '');
    setNotes(config.notes || '');
    setNewKey('');
    setClearKey(false);
    setMsg('');
    setErr('');
  }, [tenant.id, config]);

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
      });
      onDone(next);
      setNewKey('');
      setClearKey(false);
      setMsg(t('aicfg.saved'));
    } catch (e) {
      setErr(e instanceof Error ? e.message : t('aicfg.savefail'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <div className="card">
        <div className="inline" style={{ justifyContent: 'space-between', flexWrap: 'wrap', gap: 10 }}>
          <div>
            <h3 style={{ margin: 0 }}>{tenant.name}</h3>
            <p className="hint" style={{ margin: '4px 0 0' }}>
              {config.updated_at
                ? t('aicfg.changed', { when: when(config.updated_at), who: config.updated_by || '—' })
                : t('aicfg.never')}
            </p>
          </div>
          <div className="inline" style={{ gap: 8 }}>
            <a className="ghost" href={`/usage#${tenant.id}`}>{t('aicfg.usage')}</a>
            <button className="ghost" onClick={onBack}>← {t('aicfg.back')}</button>
          </div>
        </div>
      </div>

      <div className="card">
        <label className="inline" style={{ gap: 10, cursor: 'pointer', margin: 0 }}>
          <input type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)} />
          <b>{t('aicfg.enabled')}</b>
        </label>
        <p className="hint" style={{ marginTop: 6 }}>
          {enabled ? t('aicfg.enabled.on') : t('aicfg.enabled.off')}
        </p>

        <div className="field">
          <label htmlFor="model">{t('aicfg.model')}</label>
          <input
            id="model"
            value={model}
            onChange={e => setModel(e.target.value)}
            placeholder={defaultModel}
            spellCheck={false}
          />
          <p className="hint">{t('aicfg.model.hint')}</p>
        </div>

        <div className="field">
          <label htmlFor="provider">{t('aicfg.provider')}</label>
          <input id="provider" value={provider} onChange={e => setProvider(e.target.value)} spellCheck={false} />
        </div>

        <div className="field">
          <label htmlFor="baseurl">{t('aicfg.baseurl')}</label>
          <input
            id="baseurl"
            value={baseUrl}
            onChange={e => setBaseUrl(e.target.value)}
            placeholder="https://api.anthropic.com"
            spellCheck={false}
          />
          <p className="hint">{t('aicfg.baseurl.hint')}</p>
        </div>

        <div className="field">
          <label htmlFor="apikey">{t('aicfg.key')}</label>
          <input
            id="apikey"
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
            <div className="msg warn" style={{ marginTop: 8 }}>
              {t('aicfg.key.removing')}{' '}
              <button className="ghost" type="button" onClick={() => setClearKey(false)}>
                {t('aicfg.key.keep')}
              </button>
            </div>
          ) : null}
        </div>

        <div className="field">
          <label htmlFor="notes">{t('aicfg.notes')}</label>
          <textarea id="notes" rows={3} value={notes} onChange={e => setNotes(e.target.value)} />
          <p className="hint">{t('aicfg.notes.hint')}</p>
        </div>

        {err ? <div className="msg err">{err}</div> : null}
        {msg ? <div className="msg ok">{msg}</div> : null}

        <div className="actions">
          <button onClick={save} disabled={saving}>
            {saving ? t('aicfg.saving') : t('aicfg.save')}
          </button>
        </div>
      </div>
    </>
  );
}
