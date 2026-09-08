'use client';
import { useCallback, useEffect, useRef, useState } from 'react';
import { confirmDialog, showModal } from '@/components/ui/Modal';
import { Select } from '@/components/ui/Select';
import { Tip } from '@/components/ui/Tip';
import { toast } from '@/components/ui/Toast';
import { dateTime } from '@/lib/format';
import { apiGet } from '@/lib/session';
import { useI18n } from '@/lib/useI18n';
import { SessionExpired, adminGet, adminSend, errText } from './api';
import type {
  AiCatalog, AiConnection, AiLastTest, AiProviderEntry, Capability, HealthAi,
} from './api';
import {
  CAPABILITIES, MODEL_OTHER, connectionPayload, defaultOf, formFromConnection, groupConnections,
  modelFromPick, modelOptions, modelPickFor, settingsFields, testBadge,
} from './logic';
import { CheckRow, Msg, type Note } from './parts';

/* AI PROVIDERS — the registry of named connections the deployment's AI runs on.

   Three capabilities, resolved independently: text (analysis, fact-check, scoring, the bot),
   speech-to-text and text-to-speech. Each has AT MOST ONE default connection, which is what a
   workspace runs on unless an operator assigned it another (the AI setup page) or it brought its
   own key (its portal). When a capability has no default at all it is still running on the
   LEGACY deployment key — the one Integrations used to hold — and the table says so with a
   pseudo-row rather than an empty state, because "no connections" and "nothing is configured"
   are different situations and only the first is true.

   A key is written here and never read back: the table shows `has_key` and a masked hint, the
   form's key box is empty on every open, and clearing one is its own deliberate action
   (`logic.ts`, `connectionPayload`). The Test button is how a key gets verified — the OpenAI
   and Gemini adapters ship untested until an operator adds a key and presses it. */

type Translate = (key: string, vars?: Record<string, string | number>) => string;

const REG = '/admin/ai/connections';

/** The legacy layer's own settings, for the pseudo-row: which model and whether a key is set,
    from the same `/admin/settings` the console booted on. */
interface LegacySettings {
  llm_model?: string;
  stt_model?: string;
  tts_model?: string;
  anthropic_api_key_set?: boolean;
  anthropic_api_key_hint?: string;
  elevenlabs_api_key_set?: boolean;
  elevenlabs_api_key_hint?: string;
}

/** The provider the legacy layer is hardwired to, per capability (`services/ai_resolve.py`). */
const LEGACY_PROVIDER: Record<Capability, string> = { llm: 'anthropic', stt: 'elevenlabs', tts: 'elevenlabs' };

/** `ai.provider.<id>` when the dictionary has it, else the catalogue's label, else the id.
    `translate` hands back the key itself for a miss, which is the test. */
function providerLabel(t: Translate, id: string, entry?: AiProviderEntry): string {
  const key = `ai.provider.${id}`;
  const s = t(key);
  return s === key ? (entry?.label || id) : s;
}

export default function AiProvidersTab({ onVoiceChanged }: {
  /** A speech connection changed — the Voices tab's list may now come from another account. */
  onVoiceChanged: () => void;
}) {
  const { t } = useI18n();
  const [catalog, setCatalog] = useState<AiCatalog | null>(null);
  const [conns, setConns] = useState<AiConnection[] | null>(null);
  const [legacy, setLegacy] = useState<LegacySettings>({});
  const [secrets, setSecrets] = useState('');
  const [note, setNote] = useState<Note | null>(null);
  const [showInactive, setShowInactive] = useState(false);
  const [testing, setTesting] = useState<Record<string, boolean>>({});

  /* One error path for every call on this tab. The server's own `detail` first — it names the
     field a save was refused on — and a bare 404 explained as "not deployed yet" rather than as
     a generic failure, since this console can be newer than the api it talks to. */
  const fail = useCallback((e: unknown, fallback = 'toast.error'): string => {
    const text = errText(e, t, fallback, { 404: 'adm.ai.unavailable' });
    setNote({ kind: 'err', text });
    toast(text, 'err');
    return text;
  }, [t]);

  const load = useCallback(async () => {
    setNote(null);
    // The four reads are independent and each degrades on its own: a catalogue that fails to
    // load disables "New connection" and says so, but the table still renders; `/health` and
    // `/admin/settings` only feed the banner and the legacy row. Only an expired session stops
    // everything, and `api.ts` is already redirecting when it does.
    let listError: unknown = null;
    let cat: AiCatalog | null;
    let list: AiConnection[] | null;
    let health: HealthAi | null;
    let settings: LegacySettings;
    try {
      [cat, list, health, settings] = await Promise.all([
        adminGet<AiCatalog>('/admin/ai/providers').catch((e: unknown) => {
          if (e instanceof SessionExpired) throw e;
          return null;
        }),
        adminGet<AiConnection[]>(REG).catch((e: unknown) => {
          if (e instanceof SessionExpired) throw e;
          listError = e;
          return null;
        }),
        // Public route, no credential: `/health` is the same probe a curl from anywhere gets.
        apiGet<HealthAi>('/health', { scope: 'public' }).catch(() => null),
        adminGet<LegacySettings>('/admin/settings').catch(() => ({} as LegacySettings)),
      ]);
    } catch {
      return;                                   // SessionExpired — already leaving
    }
    setCatalog(cat);
    setConns(Array.isArray(list) ? list : []);
    if (listError) fail(listError, 'ai.loadfail');
    else if (!cat) setNote({ kind: 'err', text: t('ai.catalog.loadfail') });
    setSecrets(typeof health?.secrets === 'string' ? health.secrets : '');
    setLegacy(settings || {});
  }, [fail, t]);

  // Once, on mount. `load` changes identity with the language and the table re-translates by
  // re-rendering; a language switch must not re-fetch.
  const loadRef = useRef(load);
  useEffect(() => { loadRef.current = load; });
  useEffect(() => { void loadRef.current(); }, []);

  const providersFor = (cap: Capability): Record<string, AiProviderEntry> => catalog?.[cap] || {};

  const openForm = (cap: Capability, existing: AiConnection | null) => {
    const providers = providersFor(cap);
    void showModal(close => (
      <ConnBody
        cap={cap}
        providers={providers}
        existing={existing}
        t={t}
        close={close}
        onSaved={() => {
          void load();
          if (cap !== 'llm') onVoiceChanged();
        }}
      />
    ), { maxWidth: '600px' });
  };

  const test = async (c: AiConnection) => {
    setTesting(m => ({ ...m, [c.id]: true }));
    try {
      const r = await adminSend<AiLastTest>('POST', `${REG}/${encodeURIComponent(c.id)}/test`);
      const last: AiLastTest = { ok: !!r?.ok, at: r?.at || new Date().toISOString(), detail: r?.detail || null };
      setConns(list => (list || []).map(x => (x.id === c.id ? { ...x, last_test: last } : x)));
      setNote(null);
      toast(`${t('adm.ai.tested')}: ${t(last.ok ? 'ai.test.ok' : 'ai.test.fail')}`, last.ok ? 'ok' : 'err');
    } catch (e) {
      if (e instanceof SessionExpired) return;
      // A non-2xx is still a test result — the row shows it as a failure with the server's own
      // words, the same thing the server stores as `last_test` when it answers that way.
      const text = fail(e);
      setConns(list => (list || []).map(x => (x.id === c.id
        ? { ...x, last_test: { ok: false, at: new Date().toISOString(), detail: text } }
        : x)));
    } finally {
      setTesting(m => ({ ...m, [c.id]: false }));
    }
  };

  const makeDefault = async (c: AiConnection) => {
    const message = t('ai.setdefault.confirm', { cap: t(`ai.cap.${c.capability}`) });
    if (!(await confirmDialog(message, { ok: t('ai.setdefault'), danger: false }))) return;
    try {
      await adminSend('POST', `${REG}/${encodeURIComponent(c.id)}/default`);
      setNote(null);
      toast(t('adm.ai.defaulted', { name: c.name }), 'ok');
      await load();
      if (c.capability !== 'llm') onVoiceChanged();
    } catch (e) {
      if (e instanceof SessionExpired) return;
      fail(e);
    }
  };

  const deactivate = async (c: AiConnection) => {
    if (!(await confirmDialog(t('ai.deactivate.confirm'), { ok: t('ai.deactivate'), danger: true }))) return;
    try {
      await adminSend('DELETE', `${REG}/${encodeURIComponent(c.id)}`);
      setNote(null);
      toast(t('ai.deactivated'), 'ok');
      await load();
      if (c.capability !== 'llm' && c.is_default) onVoiceChanged();
    } catch (e) {
      if (e instanceof SessionExpired) return;
      fail(e);
    }
  };

  /* The mirror of Deactivate: `PUT {is_active: true}` on the same row. A reactivated
     connection is NOT made the default again — that is a separate, confirmed click. */
  const reactivate = async (c: AiConnection) => {
    try {
      await adminSend('PUT', `${REG}/${encodeURIComponent(c.id)}`, { is_active: true });
      setNote(null);
      toast(t('adm.ai.reactivated'), 'ok');
      await load();
    } catch (e) {
      if (e instanceof SessionExpired) return;
      fail(e);
    }
  };

  const groups = groupConnections(conns || []);

  return (
    <>
      <div className="card">
        <div className="row" style={{ justifyContent: 'space-between', alignItems: 'flex-start' }}>
          <div style={{ flex: 1 }}>
            <h3 style={{ margin: 0 }}>{t('ai.heading')}</h3>
            <p className="hint">{t('ai.lead')}</p>
          </div>
          <div className="inline" style={{ flex: 0 }}>
            <button className="ghost" type="button" onClick={() => void load()}>{t('btn.refresh')}</button>
          </div>
        </div>
        {/* Keys stored in the clear are a deployment problem, not a connection's — so the
            warning sits once, above every table, and stays until SECRETS_KEY is set. */}
        {secrets === 'plaintext' ? <div className="msg err">{t('ai.secrets.plaintext')}</div> : null}
        <Msg note={note} />
      </div>

      {CAPABILITIES.map(cap => {
        const group = groups[cap];
        const def = defaultOf(group);
        const active = group.filter(c => c.is_active);
        const inactive = group.filter(c => !c.is_active);
        const rows = showInactive ? group : active;
        const providers = providersFor(cap);
        const legacyModel = legacy[`${cap}_model`] || '';
        const legacyKeySet = cap === 'llm' ? legacy.anthropic_api_key_set : legacy.elevenlabs_api_key_set;
        const legacyKeyHint = cap === 'llm' ? legacy.anthropic_api_key_hint : legacy.elevenlabs_api_key_hint;
        return (
          <div className="card" key={cap}>
            <div className="row" style={{ justifyContent: 'space-between', alignItems: 'flex-start' }}>
              <div style={{ flex: 1 }}>
                <h3 style={{ margin: 0 }}>{t(`ai.cap.${cap}`)}</h3>
                <p className="hint">
                  {t(`ai.cap.${cap}.desc`)}
                  {cap === 'tts' ? <> · {t('adm.ai.voices.refresh')}</> : null}
                </p>
              </div>
              <div className="inline" style={{ flex: 0 }}>
                <button
                  className="primary"
                  type="button"
                  disabled={!Object.keys(providers).length}
                  onClick={() => openForm(cap, null)}
                >
                  {t('ai.new')}
                </button>
              </div>
            </div>

            {conns === null ? <div className="empty"><span className="spinner" /></div> : (
              <div style={{ marginTop: 12 }}>
                {!def ? (
                  <p className="hint" style={{ marginBottom: 8 }}>
                    <span className="pill pending">{t('ai.default')}</span> {t('ai.nodefault')}
                  </p>
                ) : null}
                {!active.length ? <div className="empty">{t('ai.empty')}</div> : null}
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>{t('th.name')}</th>
                        <th>{t('ai.provider')}</th>
                        <th>{t('ai.model')}</th>
                        <th>{t('ai.key')}</th>
                        <th>{t('ai.test.detail')}</th>
                        <th />
                      </tr>
                    </thead>
                    <tbody>
                      {/* The legacy layer, shown as the default it still is. Not a connection:
                          it has no Test, no Edit and no Deactivate, and it disappears from the
                          table the moment a real default exists. */}
                      {!def ? (
                        <tr style={{ opacity: 0.8 }}>
                          <td>
                            <b>{t('ai.legacy')}</b>{' '}
                            <span className="pill on">{t('ai.default')}</span>
                            <div className="hint">{t('ai.legacy.hint')}</div>
                          </td>
                          <td>{providerLabel(t, LEGACY_PROVIDER[cap], providers[LEGACY_PROVIDER[cap]])}</td>
                          <td>{legacyModel ? <code>{legacyModel}</code> : <span className="hint">{t('adm.ai.model.default')}</span>}</td>
                          <td>
                            {legacyKeySet
                              ? t('ai.key.set', { hint: legacyKeyHint || '…' })
                              : <span className="pill off">{t('ai.key.unset')}</span>}
                          </td>
                          <td />
                          <td />
                        </tr>
                      ) : null}
                      {rows.map(c => (
                        <ConnRow
                          key={c.id}
                          c={c}
                          entry={providers[c.provider]}
                          busy={!!testing[c.id]}
                          t={t}
                          onTest={() => test(c)}
                          onDefault={() => makeDefault(c)}
                          onEdit={() => openForm(cap, c)}
                          onDeactivate={() => deactivate(c)}
                          onReactivate={() => reactivate(c)}
                        />
                      ))}
                    </tbody>
                  </table>
                </div>
                {inactive.length ? (
                  <CheckRow checked={showInactive} onChange={setShowInactive} style={{ marginTop: 10 }}>
                    <span className="hint" style={{ marginTop: 0 }}>{t('adm.ai.showinactive', { n: inactive.length })}</span>
                  </CheckRow>
                ) : null}
              </div>
            )}
          </div>
        );
      })}
    </>
  );
}

/* ------------------------------------------------------------------- one row */

function ConnRow({
  c, entry, busy, t, onTest, onDefault, onEdit, onDeactivate, onReactivate,
}: {
  c: AiConnection;
  entry?: AiProviderEntry;
  busy: boolean;
  t: Translate;
  onTest: () => void;
  onDefault: () => void;
  onEdit: () => void;
  onDeactivate: () => void;
  onReactivate: () => void;
}) {
  const off = !c.is_active;
  const isDefault = c.is_default && c.is_active;
  const last = c.last_test;
  const badge = testBadge(last);
  return (
    <tr style={off ? { opacity: 0.5 } : undefined}>
      <td>
        <b>{c.name}</b>{' '}
        {isDefault ? <span className="pill on">{t('ai.default')}</span> : null}
        {off ? <span className="pill off">{t('ai.inactive')}</span> : null}
        {c.base_url ? <div className="hint"><code>{c.base_url}</code></div> : null}
        {c.updated_at ? (
          <div className="hint">
            {t('adm.ai.updated', { when: dateTime(c.updated_at), who: c.updated_by || '—' })}
          </div>
        ) : null}
      </td>
      <td>{providerLabel(t, c.provider, entry)}</td>
      <td>{c.model ? <code>{c.model}</code> : <span className="hint">{t('adm.ai.model.default')}</span>}</td>
      <td>
        {c.has_key
          ? t('ai.key.set', { hint: c.key_hint || '…' })
          : <span className="pill off">{t('ai.key.unset')}</span>}
      </td>
      <td>
        <span className="inline" style={{ gap: 4 }}>
          <span className={`pill ${badge.cls}`}>{t(badge.key)}</span>
          {/* The detail rides in a ⓘ: a provider's error message can be a paragraph, and a
              paragraph in a table cell pushes every other column off the screen. An untested
              row explains instead what "untested" costs. */}
          <Tip text={last ? (last.detail || '') : t('ai.untested.hint')} />
        </span>
        {last?.at ? <div className="hint">{t('ai.test.at', { when: dateTime(last.at) })}</div> : null}
      </td>
      <td className="inline">
        {off ? (
          <button className="ghost" type="button" onClick={onReactivate}>{t('adm.ai.reactivate')}</button>
        ) : (
          <>
            <button className="ghost" type="button" onClick={onTest} disabled={busy}>
              {busy ? <><span className="spinner" /> {t('ai.testing')}</> : t('ai.test')}
            </button>
            {isDefault ? null : (
              <button className="ghost" type="button" onClick={onDefault}>{t('ai.setdefault')}</button>
            )}
            <button className="ghost" type="button" onClick={onEdit}>{t('ai.edit')}</button>
            <button className="danger" type="button" onClick={onDeactivate}>{t('ai.deactivate')}</button>
          </>
        )}
      </td>
    </tr>
  );
}

/* ---------------------------------------------------------- new / edit connection */

/** The connection form, for both create and edit.

    The model is a picker over the catalogue's known ids with an "Other…" row that reveals a
    text box: "known, not exhaustive" is the catalogue's own contract, and an operator must be
    able to name a model this build has never heard of. The base URL appears only for a
    provider whose entry allows one, and the key box is always empty — see the note at the top
    of the file. */
function ConnBody({
  cap, providers, existing, t, close, onSaved,
}: {
  cap: Capability;
  providers: Record<string, AiProviderEntry>;
  existing: AiConnection | null;
  t: Translate;
  close: (v?: unknown) => void;
  onSaved: () => void;
}) {
  const ids = Object.keys(providers);
  const firstProvider = existing?.provider || ids[0] || '';
  const initial = formFromConnection(cap, existing, providers[firstProvider], firstProvider);
  const initialPick = modelPickFor(providers[firstProvider]?.known_models || [], initial.model);

  const [name, setName] = useState(initial.name);
  const [provider, setProvider] = useState(initial.provider);
  const [pick, setPick] = useState(initialPick.pick);
  const [modelText, setModelText] = useState(initialPick.text);
  const [baseUrl, setBaseUrl] = useState(initial.baseUrl);
  const [apiKey, setApiKey] = useState('');
  const [clearKey, setClearKey] = useState(false);
  const [settings, setSettings] = useState<Record<string, string>>(initial.settings);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const entry = providers[provider];
  const known = entry?.known_models || [];
  const fields = settingsFields(cap, entry);

  /* A provider switch resets the model to the new provider's default: model ids do not carry
     across vendors, and leaving "claude-…" selected under OpenAI would save a connection that
     fails on its first call. The typed settings (a voice id) are kept — they may well apply. */
  const changeProvider = (id: string) => {
    if (id === provider) return;
    setProvider(id);
    setPick('');
    setModelText('');
  };

  const submit = async () => {
    const nm = name.trim();
    if (!nm) { setError(t('adm.ai.name.required')); return; }
    if (!provider) { setError(t('adm.ai.provider.required')); return; }
    setBusy(true);
    setError('');
    const body = connectionPayload(cap, {
      name: nm, provider, model: modelFromPick(pick, modelText), baseUrl, apiKey, clearKey, settings,
    }, entry);
    try {
      if (existing) await adminSend('PUT', `${REG}/${encodeURIComponent(existing.id)}`, body);
      else await adminSend('POST', REG, body);
      close(true);
      toast(t('ai.saved'), 'ok');
      onSaved();
    } catch (e) {
      if (e instanceof SessionExpired) return;
      const text = errText(e, t, 'toast.error', { 404: 'adm.ai.unavailable' });
      setError(text);
      toast(text, 'err');
    } finally {
      setBusy(false);
    }
  };

  const fieldLabel = (f: string) => (f === 'voice_id' ? t('ai.voice') : f);

  return (
    <>
      <h3 style={{ marginTop: 0 }}>
        {existing ? t('ai.edit') : t('ai.new')} · {t(`ai.cap.${cap}`)}
      </h3>

      <label htmlFor="aiName">{t('ai.name')}</label>
      <input
        id="aiName"
        maxLength={120}
        placeholder={t('ai.name.ph')}
        value={name}
        data-autofocus
        onChange={e => setName(e.target.value)}
      />

      <label htmlFor="aiProvider">{t('ai.provider')}</label>
      <Select
        id="aiProvider"
        value={provider}
        onChange={changeProvider}
        options={ids.map(id => ({ value: id, label: providerLabel(t, id, providers[id]) }))}
        ariaLabel={t('ai.provider')}
      />

      <label htmlFor="aiModel">{t('ai.model')}</label>
      <Select
        id="aiModel"
        value={pick}
        onChange={setPick}
        options={modelOptions(known, { none: t('adm.ai.model.default'), other: t('adm.ai.model.other') })}
        ariaLabel={t('ai.model')}
      />
      {pick === MODEL_OTHER ? (
        <>
          <label htmlFor="aiModelText">{t('adm.ai.model.custom')}</label>
          <input
            id="aiModelText"
            value={modelText}
            spellCheck={false}
            autoComplete="off"
            onChange={e => setModelText(e.target.value)}
          />
        </>
      ) : null}
      <div className="hint">{t('ai.model.hint')}</div>

      {entry?.allows_base_url ? (
        <>
          <label htmlFor="aiBaseUrl">{t('ai.baseurl')}</label>
          <input
            id="aiBaseUrl"
            value={baseUrl}
            spellCheck={false}
            autoComplete="off"
            placeholder="https://"
            onChange={e => setBaseUrl(e.target.value)}
          />
          <div className="hint">{t('ai.baseurl.hint')}</div>
        </>
      ) : null}

      <label htmlFor="aiKey">{t('ai.key')}</label>
      <input
        id="aiKey"
        type="password"
        autoComplete="off"
        value={apiKey}
        onChange={e => { setApiKey(e.target.value); if (e.target.value) setClearKey(false); }}
      />
      <div className="hint">
        {existing?.has_key
          ? `${t('ai.key.set', { hint: existing.key_hint || '…' })} — ${t('ai.key.replace')}`
          : t('ai.key.hint')}
      </div>
      {existing?.has_key && !clearKey ? (
        <button
          className="ghost"
          type="button"
          style={{ marginTop: 8 }}
          onClick={() => { setClearKey(true); setApiKey(''); }}
        >
          {t('adm.ai.key.clear')}
        </button>
      ) : null}
      {clearKey ? (
        <div className="msg err">
          {t('adm.ai.key.clearing')}{' '}
          <button className="ghost" type="button" onClick={() => setClearKey(false)}>
            {t('adm.ai.key.keep')}
          </button>
        </div>
      ) : null}

      {fields.map(f => (
        <div key={f}>
          <label htmlFor={`aiSet-${f}`}>{fieldLabel(f)}</label>
          <input
            id={`aiSet-${f}`}
            value={settings[f] || ''}
            spellCheck={false}
            autoComplete="off"
            onChange={e => setSettings(s => ({ ...s, [f]: e.target.value }))}
          />
        </div>
      ))}

      {error ? <div className="msg err">{error}</div> : null}
      <div className="actions">
        <button className="ghost" type="button" onClick={() => close(false)}>{t('btn.cancel')}</button>
        <button className="primary" type="button" onClick={submit} disabled={busy}>
          {busy ? <span className="spinner" /> : t('ai.save')}
        </button>
      </div>
    </>
  );
}
