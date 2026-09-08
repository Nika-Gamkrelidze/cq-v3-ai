'use client';
/* TRANSCRIPTION — what this workspace tells the speech-to-text model.
   ==================================================================
   The tab is the middle link of an inheritance chain: code defaults ← the deployment default
   (the operator's console) ← THIS WORKSPACE ← one recording (the upload panel). It reads and
   writes `/transcription/config`, whose GET answers the EFFECTIVE settings plus `is_default`
   (this workspace has saved nothing of its own) and `inherited` (the layer underneath) — the
   same shape `/chat/config` and `/scoring/config` already use, and the same shape the bot and
   rubric tabs already render, so the three settings surfaces read alike.

   Two states, and the difference between them is the whole feature:

     INHERITING — the controls show the inherited values and are not editable. Saying "these
       are the settings" while quietly having none of its own is how a workspace ends up
       surprised by a change to the deployment default it never saw; the note and the pill say
       where the values come from, and `tr.override` is what takes ownership of them.

     OVERRIDDEN — the controls are this workspace's own and editable, and the inherited layer
       is shown beside them so "back to inherited" is a visible destination rather than a leap.

   Taking the override seeds the form from the inherited values and saves all four fields: the
   card shows exactly what this workspace's transcriptions will use, which is the property a
   partial per-field override would cost. Per-FIELD inheritance lives one level down, on a
   single recording. */

import { useCallback, useEffect, useRef, useState } from 'react';
import { confirmDialog } from '@/components/ui/Modal';
import { Select, type Option } from '@/components/ui/Select';
import { toast } from '@/components/ui/Toast';
import { failMessage, STALE, useWs } from './ctx';
import {
  fromDraft, KEYTERMS_MAX, parseKeyterms, readConfig, toDraft, validate,
  type Config, type ConfigReply, type Draft, type Settings,
} from './transcription';
import styles from './workspace.module.css';

/** The three the product speaks, offered by name. Any other code the server resolves to is
    still shown (and kept) — the deployment default is allowed to name a fourth language. */
const NAMED = ['en', 'ka', 'ru'];

/** Who may change these settings. The page's own owner predicate AND the server's answer for
    this exact reply (`can_edit`), because they can disagree: a registered user with no
    workspace is handed the system layer to READ, and only the server knows that. Whichever
    says no, wins — the form must never offer an edit the PUT will refuse. */
function readOnly(ws: { canConfigure: boolean }, cfg: Config | null): boolean {
  return !ws.canConfigure || cfg?.canEdit === false;
}

export function TranscriptionTab({ on, gen }: { on: boolean; gen: number }) {
  const ws = useWs();
  const { t } = ws;

  const [cfg, setCfg] = useState<Config | null>(null);
  /** Non-null means an override is being edited — either one the workspace already has, or one
      just taken. Null means the controls are showing an inherited (or read-only) value. */
  const [draft, setDraft] = useState<Draft | null>(null);
  const [msg, setMsg] = useState<{ text: string; kind: '' | 'ok' | 'err' }>({ text: '', kind: '' });
  const [busy, setBusy] = useState<'' | 'save' | 'reset'>('');

  /** The generation the settings on screen were loaded in — an operator's workspace switch
      bumps it. See `load`. */
  const shownGen = useRef(-1);

  /* Fails CLOSED, exactly as the rubric editor does: a load that failed — or that finished for
     a workspace the operator has since left — leaves NO settings on screen, because Save sends
     what is on screen to whichever workspace is selected when it is clicked. */
  const load = useCallback(async (g: number): Promise<'ok' | 'fail' | 'stale'> => {
    setMsg({ text: '', kind: '' });
    // A workspace switch empties the card BEFORE the new load lands, so there is no window in
    // which workspace A's key terms are on screen under workspace B's name with a live Save
    // button under them. A plain Refresh (same generation) leaves the card alone rather than
    // flashing it empty and back.
    if (shownGen.current !== g) { setCfg(null); setDraft(null); shownGen.current = -1; }
    const d = await ws.json<ConfigReply>('/transcription/config');
    if (d === STALE) return 'stale';
    if (d === null) {
      setCfg(null);
      setDraft(null);
      setMsg({ text: t('tr.loadfail'), kind: 'err' });
      return 'fail';
    }
    const c = readConfig(d);
    setCfg(c);
    shownGen.current = g;
    // Editable straight away only when there is already an override to edit. A workspace that
    // is inheriting has to ask for one first.
    setDraft(c.isDefault || readOnly(ws, c) ? null : toDraft(c.effective));
    return 'ok';
  }, [ws, t]);

  useEffect(() => { if (on && ws.ready) void load(gen); }, [on, gen, ws.ready, load]);

  const startOverride = () => {
    if (!cfg) return;
    setMsg({ text: '', kind: '' });
    setDraft(toDraft(cfg.effective));
  };

  const patch = (part: Partial<Draft>) => setDraft(prev => (prev ? { ...prev, ...part } : prev));

  const save = async () => {
    if (!cfg || !draft) return;
    const next: Settings = fromDraft(draft);
    // The backend validates too, and its message names the field. This is the same rule said
    // in the visitor's own language before the round trip, so a bad term in a list of two
    // hundred is named rather than reported as a 400.
    const problem = validate(next);
    if (problem) { setMsg({ text: t(problem.key, problem.vars), kind: 'err' }); return; }
    setBusy('save');
    const r = await ws.send('PUT', '/transcription/config', next);
    setBusy('');
    if (!r.ok) {
      const text = failMessage(r, t);
      setMsg({ text, kind: 'err' });
      toast(text, 'err');
      return;
    }
    // Re-read rather than trust the echo: the save also decides `is_default` and what the
    // inherited layer now is, and those two are what the card is about.
    if (await load(gen) !== 'ok') return;
    setMsg({ text: t('tr.saved'), kind: 'ok' });
    toast(t('tr.saved'), 'ok');
  };

  const reset = async () => {
    if (!cfg) return;
    // An override that was taken but never saved is not on the server: dropping it is undoing
    // a click, and confirming a click is noise.
    if (cfg.isDefault) { setDraft(null); setMsg({ text: '', kind: '' }); return; }
    // A saved one may carry a curated key-term list, which is real work — unlike the score
    // colours next door, which are two numbers anyone can retype.
    if (!(await confirmDialog(t('tn.tr.reset.confirm'), { ok: t('tr.reset') }))) return;
    setBusy('reset');
    const r = await ws.send('DELETE', '/transcription/config');
    setBusy('');
    if (!r.ok) {
      const text = failMessage(r, t);
      setMsg({ text, kind: 'err' });
      toast(text, 'err');
      return;
    }
    if (await load(gen) !== 'ok') return;
    setMsg({ text: t('tr.saved'), kind: 'ok' });
    toast(t('tr.saved'), 'ok');
  };

  if (!ws.ready) return <div className="empty">{t('con.tenant.pick')}</div>;

  const readonly = readOnly(ws, cfg);

  /* What the controls show: the draft while one is being edited, otherwise the effective
     settings, read-only. */
  const view: Draft | null = draft ?? (cfg ? toDraft(cfg.effective) : null);
  const editing = !!draft && !readonly;
  const inheriting = !!cfg && cfg.isDefault && !draft;
  /* The inherited layer is worth showing only when the controls are NOT already it. */
  const showInherited = !!cfg && (!!draft || !cfg.isDefault);

  const langLabel = (code: string | null) => (
    !code ? t('tr.language.auto') : NAMED.includes(code) ? t('lang.' + code) : code
  );

  const langOptions: Option[] = [
    { value: '', label: t('tr.language.auto') },
    ...NAMED.map(l => ({ value: l, label: t('lang.' + l) })),
  ];
  // A code the deployment default names that is not one of the three: keep it selectable so a
  // disabled control still tells the truth, and so saving does not silently change it.
  const cur = view?.language || '';
  if (cur && !langOptions.some(o => o.value === cur)) langOptions.push({ value: cur, label: cur });

  const termCount = view ? parseKeyterms(view.keyterms).length : 0;

  return (
    <>
      <div className="card">
        <div className="row" style={{ justifyContent: 'space-between', alignItems: 'flex-start' }}>
          <div style={{ flex: 1 }}>
            <h3 style={{ margin: 0 }}>{t('tr.heading')}</h3>
          </div>
          <div className="inline" style={{ flex: 0, gap: 8 }}>
            {inheriting ? <span className="pill notinkb">{t('tr.inherited')}</span> : null}
            <button type="button" className="ghost" onClick={() => void load(gen)}>{t('btn.refresh')}</button>
          </div>
        </div>
        <p className="hint">{t('tr.lead')}</p>
        {readonly ? <p className="hint" style={{ color: 'var(--pending)' }}>{t('tn.tr.readonly')}</p> : null}
        {/* The values below are the deployment's, not this workspace's — and a change to the
            default moves them until somebody takes the override. */}
        {inheriting ? <div className="msg">{t('tr.inherited.system')}</div> : null}
        {cfg ? null : <div className="msg err" aria-live="polite">{msg.text}</div>}
      </div>

      {cfg && view ? (
        <div className="card">
          <div className={editing ? undefined : styles.reading}>
            <div className="field">
              <label htmlFor="tr_lang">{t('tr.language')}</label>
              <Select
                id="tr_lang" value={view.language} disabled={!editing}
                onChange={v => patch({ language: v })}
                options={langOptions} ariaLabel={t('tr.language')}
              />
              <div className="hint">{t('tr.language.hint')}</div>
            </div>

            {/* Not `.field`: that block styles a label as a heading above its control, and this
                label sits BESIDE its checkbox — `.field > label`'s display:block would win over
                `.inline` and drop the gap between the box and its words. */}
            <div style={{ marginTop: 18 }}>
              <label className="inline" style={{ gap: 8, margin: 0 }}>
                <input
                  id="tr_diarize" type="checkbox" style={{ width: 'auto' }}
                  checked={view.diarize} disabled={!editing}
                  onChange={e => patch({ diarize: e.target.checked })}
                />
                <span>{t('tr.diarize')}</span>
              </label>
              {/* Visible, not behind an ⓘ: switching this off silently removes the per-speaker
                  analysis and the timeline lanes, and the cost has to be readable before the
                  click, not after it. */}
              <div className="hint">{t('tr.diarize.hint')}</div>
            </div>

            <div className="field">
              <label htmlFor="tr_keyterms">{t('tr.keyterms')}</label>
              <textarea
                id="tr_keyterms" value={view.keyterms} disabled={!editing}
                placeholder={t('tr.keyterms.ph')} style={{ minHeight: 96 }}
                onChange={e => patch({ keyterms: e.target.value })}
              />
              <div className="hint">{t('tr.keyterms.hint')}</div>
              {/* The count IS the over-the-limit message — read as "1002 of 1000 terms" it says
                  what is wrong without a sentence of its own. Hidden only when there is nothing
                  to count and nothing to type into. */}
              {editing || termCount ? (
                <div className="hint" style={termCount > KEYTERMS_MAX ? { color: 'var(--coral)' } : undefined}>
                  {t('tr.keyterms.count', { n: termCount, max: KEYTERMS_MAX })}
                </div>
              ) : null}
              {/* +20% per transcription. Stated where the terms are typed, not in a tip. */}
              {termCount ? <div className="hint">{t('tr.keyterms.cost')}</div> : null}
            </div>

            <div className="field">
              <label htmlFor="tr_format">{t('tr.format')}</label>
              <Select
                id="tr_format" value={view.audio_format} disabled={!editing}
                onChange={v => patch({ audio_format: v as Draft['audio_format'] })}
                options={cfg.formats.map(f => ({ value: f, label: t('tr.format.' + f) }))}
                ariaLabel={t('tr.format')}
              />
              {/* Each option trades fidelity against upload size, and which way that trade goes
                  is the point of the setting — so the chosen one explains itself here rather
                  than in a table nobody opens. */}
              <div className="hint">{t('tr.format.' + view.audio_format + '.desc')}</div>
              <div className="hint">{t('tr.format.hint')}</div>
            </div>
          </div>

          {showInherited ? (
            <>
              <hr className="sep" />
              {/* What "back to inherited" restores — the layer underneath, spelled out, so the
                  button next to it is a visible destination and not a leap. */}
              <h4 style={{ margin: '0 0 4px' }}>{t('tr.inherited')}</h4>
              <div className="row">
                <div>
                  <label>{t('tr.language')}</label>
                  <div className="muted">{langLabel(cfg.inherited.language_code)}</div>
                </div>
                <div>
                  <label>{t('tr.diarize')}</label>
                  <div className="muted" style={{ color: cfg.inherited.diarize ? 'var(--ok)' : 'var(--muted)' }}>
                    {cfg.inherited.diarize ? '✓' : '✗'}
                  </div>
                </div>
                <div>
                  <label>{t('tr.keyterms')}</label>
                  <div className="muted">
                    {t('tr.keyterms.count', { n: cfg.inherited.keyterms.length, max: KEYTERMS_MAX })}
                  </div>
                </div>
                <div>
                  <label>{t('tr.format')}</label>
                  <div className="muted">{t('tr.format.' + cfg.inherited.audio_format)}</div>
                </div>
              </div>
            </>
          ) : null}

          {/* A member gets no buttons at all rather than ones that would only ever 403 — the
              server's `may_configure_workspace` is the policy, this stops the UI promising
              more. */}
          {readonly ? null : (
            <div className="actions" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
              {draft
                ? (
                  <button type="button" className="ghost" disabled={!!busy} onClick={() => void reset()}>
                    {busy === 'reset' ? <span className="spinner" /> : t('tr.reset')}
                  </button>
                )
                : <span />}
              {draft
                ? (
                  <button type="button" className="primary" disabled={!!busy} onClick={() => void save()}>
                    {busy === 'save' ? <span className="spinner" /> : t('btn.save')}
                  </button>
                )
                : (
                  <button type="button" className="primary" onClick={startOverride}>
                    {t('tr.override')}
                  </button>
                )}
            </div>
          )}
          <div className={`msg${msg.kind ? ' ' + msg.kind : ''}`} aria-live="polite">{msg.text}</div>
        </div>
      ) : null}
    </>
  );
}
