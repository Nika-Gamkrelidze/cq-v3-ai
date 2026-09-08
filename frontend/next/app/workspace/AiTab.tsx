'use client';
/* AI SUBSCRIPTION — this workspace's own provider key, per capability.
   ===================================================================
   The top link of the resolution chain (code defaults ← the deployment default ← the
   connection an operator assigned ← THIS WORKSPACE'S OWN KEY), and the only link a customer
   can touch. It reads `GET /ai/config`, which answers for each of the three capabilities what
   is IN EFFECT (whose layer answered, which provider and model, the operator's connection
   name when there is one — never a key, never a hint of ours) and the workspace's own
   OVERRIDE, if it has one. The same shape as the transcription tab next door: an inherited
   value, an override, a way back.

   Two states per capability, and the difference is the whole feature:

     ON COMMUNIQ'S — the "in effect" line says which of the operator's connections the
       workspace is running on, and `ai.byo.enable` reveals the form. Nothing is saved until
       Save; dropping the form is undoing a click.

     YOUR OWN KEY — the form shows the saved provider and model, the key only as "set" with
       its masked tail, and a new key can be pasted over it. Test probes what is SAVED, so it
       waits for Save while the form differs from it. `ai.byo.remove` deletes the override —
       key included — and the workspace falls back to the layer underneath.

   THERE IS NO ENDPOINT FIELD, and the card says why: a tenant-set base URL could keep every
   transcript it is handed. Only the operator can route a workspace through a gateway.

   Writes are for owners (and an operator acting on the workspace — the server's
   `may_configure_workspace`). A member sees the same card read-only: the saved provider and
   model in disabled controls, no key box, no buttons. */

import { useCallback, useEffect, useRef, useState } from 'react';
import { confirmDialog } from '@/components/ui/Modal';
import { Select } from '@/components/ui/Select';
import { toast } from '@/components/ui/Toast';
import {
  CAPABILITIES, canSave, isByo, isDirty, keyOnFile, modelFor, modelOptions, providerOptions,
  readAiConfig, toBody, toDraft, type AiConfig, type CapConfig, type Capability, type Draft,
} from './aiByo';
import { failMessage, STALE, useWs, type T } from './ctx';
import styles from './workspace.module.css';

type Msg = { text: string; kind: '' | 'ok' | 'err' };
const NO_MSG: Msg = { text: '', kind: '' };

/** Who may change these settings. The page's own owner predicate AND the server's answer for
    this exact reply (`can_edit`) when it gave one — whichever says no, wins. */
function readOnly(ws: { canConfigure: boolean }, cfg: AiConfig | null): boolean {
  return !ws.canConfigure || cfg?.canEdit === false;
}

/** A catalog id in the visitor's language when the dictionary has a word for it, the raw id
    when it does not — a provider added to the catalog before its label lands must show as
    `deepgram`, not as `ai.provider.deepgram`. */
function named(t: T, key: string, raw: string): string {
  const s = t(key);
  return s === key ? raw : s;
}

export function AiTab({ on, gen }: { on: boolean; gen: number }) {
  const ws = useWs();
  const { t } = ws;

  const [cfg, setCfg] = useState<AiConfig | null>(null);
  const [loadErr, setLoadErr] = useState('');

  /** The generation the settings on screen were loaded in — an operator's workspace switch
      bumps it. See `load`. */
  const shownGen = useRef(-1);

  /* Fails CLOSED, as the transcription and rubric tabs do: a load that failed — or that
     finished for a workspace the operator has since left — leaves NO settings on screen,
     because Save sends what is on screen to whichever workspace is selected when it is
     clicked, and a provider key saved into the wrong customer's account is the worst
     version of that. */
  const load = useCallback(async (g: number): Promise<'ok' | 'fail' | 'stale'> => {
    setLoadErr('');
    // A workspace switch empties the cards BEFORE the new load lands, so there is no window
    // in which workspace A's provider is on screen under workspace B's name with a live Save
    // under it. A plain Refresh (same generation) leaves them alone.
    if (shownGen.current !== g) { setCfg(null); shownGen.current = -1; }
    const d = await ws.json<Record<string, unknown>>('/ai/config');
    if (d === STALE) return 'stale';
    if (d === null) {
      setCfg(null);
      setLoadErr(t('ai.loadfail'));
      return 'fail';
    }
    setCfg(readAiConfig(d));
    shownGen.current = g;
    return 'ok';
  }, [ws, t]);

  useEffect(() => { if (on && ws.ready) void load(gen); }, [on, gen, ws.ready, load]);

  if (!ws.ready) return <div className="empty">{t('con.tenant.pick')}</div>;

  const readonly = readOnly(ws, cfg);

  return (
    <>
      <div className="card">
        <div className="row" style={{ justifyContent: 'space-between', alignItems: 'flex-start' }}>
          <div style={{ flex: 1 }}>
            <h3 style={{ margin: 0 }}>{t('ai.byo.heading')}</h3>
          </div>
          <div className="inline" style={{ flex: 0, gap: 8 }}>
            <button type="button" className="ghost" onClick={() => void load(gen)}>{t('btn.refresh')}</button>
          </div>
        </div>
        <p className="hint">{t('ai.byo.lead')}</p>
        {readonly ? <p className="hint" style={{ color: 'var(--pending)' }}>{t('ai.byo.readonly')}</p> : null}
        {cfg ? null : <div className="msg err" aria-live="polite">{loadErr}</div>}
      </div>

      {cfg
        ? CAPABILITIES.map(cap => (
          <CapCard
            key={cap}
            cap={cap}
            data={cfg.caps[cap]}
            readonly={readonly}
            reload={() => load(gen)}
          />
        ))
        : null}
    </>
  );
}

/* ---------------------------------------------------------------- one capability */

function CapCard({ cap, data, readonly, reload }: {
  cap: Capability;
  data: CapConfig;
  readonly: boolean;
  reload: () => Promise<'ok' | 'fail' | 'stale'>;
}) {
  const ws = useWs();
  const { t } = ws;

  /** Non-null means the form is open — over an override the workspace already has, or one
      just taken and not yet saved. Null means the card is showing what is in effect. */
  const seed = () => (data.override && !readonly ? toDraft(data) : null);
  const [draft, setDraft] = useState<Draft | null>(seed);
  const [busy, setBusy] = useState<'' | 'save' | 'remove' | 'test'>('');
  const [msg, setMsg] = useState<Msg>(NO_MSG);

  /* Re-seed from the server's answer whenever it changes — a reload after Save or Remove, a
     Refresh, a workspace switch. Editable straight away only when there is already an
     override to edit; a workspace on CommuniQ's has to ask for one first. The message is
     deliberately NOT cleared here: the reload after a save is what makes this run, and the
     "saved" line is set right after it. */
  useEffect(() => {
    setDraft(data.override && !readonly ? toDraft(data) : null);
  }, [data, readonly]);

  const patch = (part: Partial<Draft>) => setDraft(prev => (prev ? { ...prev, ...part } : prev));

  const fail = (text: string) => {
    setMsg({ text, kind: 'err' });
    toast(text, 'err');
  };

  const enable = () => {
    setMsg(NO_MSG);
    setDraft(toDraft(data));
  };

  // An override that was taken but never saved is not on the server: dropping it is undoing
  // a click, and confirming a click is noise.
  const cancel = () => {
    setMsg(NO_MSG);
    setDraft(null);
  };

  const save = async () => {
    if (!draft || !canSave(draft, data.override)) return;
    setMsg(NO_MSG);
    setBusy('save');
    const r = await ws.send('PUT', `/ai/config/${cap}`, toBody(draft));
    setBusy('');
    if (!r.ok) { fail(failMessage(r, t)); return; }
    // Re-read rather than trust the echo: the save also decides what is now in effect and
    // what the key's masked tail is, and those two are what the card is about.
    if (await reload() !== 'ok') return;
    setMsg({ text: t('ai.byo.saved'), kind: 'ok' });
    toast(t('ai.byo.saved'), 'ok');
  };

  const remove = async () => {
    if (!data.override) return;
    const capName = t('ai.cap.' + cap);
    if (!(await confirmDialog(t('ai.byo.remove.confirm', { cap: capName }), { ok: t('ai.byo.remove') }))) return;
    setMsg(NO_MSG);
    setBusy('remove');
    const r = await ws.send('DELETE', `/ai/config/${cap}`);
    setBusy('');
    if (!r.ok) { fail(failMessage(r, t)); return; }
    if (await reload() !== 'ok') return;
    setMsg({ text: t('ai.byo.removed'), kind: 'ok' });
    toast(t('ai.byo.removed'), 'ok');
  };

  /* Probes the SAVED override — the server decrypts the stored key and makes one small call
     with it. Nothing typed here is sent, which is why the button waits while the form differs
     from what is saved. */
  const test = async () => {
    setMsg(NO_MSG);
    setBusy('test');
    const r = await ws.send<{ ok?: unknown; detail?: unknown }>('POST', `/ai/config/${cap}/test`);
    setBusy('');
    if (!r.ok) { fail(failMessage(r, t)); return; }
    const ok = r.data?.ok === true;
    const detail = typeof r.data?.detail === 'string' ? r.data.detail.trim() : '';
    setMsg({
      text: (ok ? t('ai.test.ok') : t('ai.test.fail')) + (detail ? ' — ' + detail : ''),
      kind: ok ? 'ok' : 'err',
    });
  };

  /* What the controls show: the draft while one is open; otherwise the saved override, read-
     only, for a member who may look but not touch; nothing at all when there is neither. */
  const view: Draft | null = draft ?? (data.override ? toDraft(data) : null);
  const editing = !!draft && !readonly;
  const byo = isByo(data);

  const eff = data.effective;
  const effectiveLine = [
    eff.source ? named(t, 'ai.source.' + eff.source, eff.source) : '',
    eff.provider ? named(t, 'ai.provider.' + eff.provider, eff.provider) : '',
    eff.model || '',
  ].filter(Boolean).join(' · ') + (eff.connectionName ? ` (${eff.connectionName})` : '');

  const known = view ? (data.knownModels[view.provider] ?? []) : [];
  const hasKey = !!view && keyOnFile(view, data.override);
  const dirty = !!view && isDirty(view, data.override);

  return (
    <div className="card">
      <div className="row" style={{ justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div style={{ flex: 1 }}>
          <h3 style={{ margin: 0 }}>{t('ai.cap.' + cap)}</h3>
          <div className="hint">{t('ai.cap.' + cap + '.desc')}</div>
        </div>
        <div className="inline" style={{ flex: 0, gap: 8 }}>
          {/* Grey, not red, for "off": running on CommuniQ's account is the normal state of a
              workspace, not a fault. */}
          <span className={`pill ${byo ? 'on' : 'notinkb'}`}>{byo ? t('ai.byo.on') : t('ai.byo.off')}</span>
        </div>
      </div>

      {/* Whose layer answered, and on what. Never a key and never a hint of one: the
          connection underneath may be CommuniQ's, and its credential is not the customer's to
          see even masked. */}
      <div className="kv" style={{ marginTop: 12 }}>
        <b>{t('ai.effective')}</b>
        <span>{effectiveLine || '—'}</span>
      </div>

      {view ? (
        <div className={editing ? undefined : styles.reading}>
          <div className="field">
            <label htmlFor={`ai_${cap}_provider`}>{t('ai.provider')}</label>
            {/* The <Select> carries a real `disabled`: a member's control refuses to open
                rather than opening onto a change the PUT would refuse. */}
            <Select
              id={`ai_${cap}_provider`} value={view.provider} disabled={!editing}
              onChange={v => { setMsg(NO_MSG); patch({ provider: v, model: modelFor(data, v, view.model) }); }}
              options={providerOptions(data, view.provider).map(p => ({ value: p, label: named(t, 'ai.provider.' + p, p) }))}
              ariaLabel={t('ai.provider')}
            />
          </div>

          <div className="field">
            <label htmlFor={`ai_${cap}_model`}>{t('ai.model')}</label>
            {/* Known models are a dropdown, "known, not exhaustive"; a provider the server sent
                no list for gets a plain box, because a dropdown with nothing on it is a dead
                end and the id is the provider's to accept. */}
            {known.length
              ? (
                <Select
                  id={`ai_${cap}_model`} value={view.model} disabled={!editing}
                  onChange={v => { setMsg(NO_MSG); patch({ model: v }); }}
                  options={modelOptions(data, view.provider, view.model).map(m => ({ value: m, label: m }))}
                  ariaLabel={t('ai.model')}
                />
              )
              : (
                <input
                  id={`ai_${cap}_model`} value={view.model} disabled={!editing}
                  spellCheck={false} autoComplete="off"
                  onChange={e => { setMsg(NO_MSG); patch({ model: e.target.value }); }}
                />
              )}
            <div className="hint">{t('ai.model.hint')}</div>
          </div>

          <div className="field">
            <label htmlFor={`ai_${cap}_key`}>{t('ai.key')}</label>
            {/* Write-only, and only for someone who may write. The stored key never comes
                back — only whether one is set and its masked tail — so the box cannot be
                pre-filled, and an empty box on Save means "keep it", never "clear it". */}
            {editing ? (
              <input
                id={`ai_${cap}_key`} type="password" value={view.apiKey}
                autoComplete="new-password" spellCheck={false}
                onChange={e => { setMsg(NO_MSG); patch({ apiKey: e.target.value }); }}
              />
            ) : null}
            <div className="hint">
              {hasKey
                ? t('ai.key.set', { hint: data.override?.keyHint || '…' }) + (editing ? ' ' + t('ai.key.replace') : '')
                : t('ai.key.unset')}
            </div>
            {editing ? <div className="hint">{t('ai.key.hint')}</div> : null}
            {/* Said where an endpoint field would be, so its absence reads as a decision. */}
            <div className="hint">{t('ai.byo.nobase')}</div>
          </div>
        </div>
      ) : null}

      {/* A member gets no buttons at all rather than ones that would only ever 403 — the
          server's `may_configure_workspace` is the policy, this stops the UI promising more. */}
      {readonly ? null : (
        <div className="actions" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
          {draft
            ? (
              <div className="inline" style={{ gap: 8, flexWrap: 'wrap' }}>
                {data.override
                  ? (
                    <button type="button" className="ghost" disabled={!!busy} onClick={() => void remove()}>
                      {busy === 'remove' ? <span className="spinner" /> : t('ai.byo.remove')}
                    </button>
                  )
                  : (
                    <button type="button" className="ghost" disabled={!!busy} onClick={cancel}>
                      {t('btn.cancel')}
                    </button>
                  )}
                {data.override
                  ? (
                    <button type="button" className="ghost" disabled={!!busy || dirty} onClick={() => void test()}>
                      {busy === 'test' ? t('ai.testing') : t('ai.test')}
                    </button>
                  )
                  : null}
              </div>
            )
            : <span />}
          {draft
            ? (
              <button
                type="button" className="primary"
                disabled={!!busy || !canSave(draft, data.override)}
                onClick={() => void save()}
              >
                {busy === 'save' ? <span className="spinner" /> : t('btn.save')}
              </button>
            )
            : (
              <button type="button" className="primary" onClick={enable}>
                {t('ai.byo.enable')}
              </button>
            )}
        </div>
      )}
      <div className={`msg${msg.kind ? ' ' + msg.kind : ''}`} aria-live="polite">{msg.text}</div>
    </div>
  );
}
