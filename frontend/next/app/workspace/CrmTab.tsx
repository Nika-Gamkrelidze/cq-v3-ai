'use client';
/* CRM FORMS — the forms this workspace's calls are filled into.
   =============================================================
   A workspace may keep as many forms as it likes (one per line of business, say); one is the
   PRIMARY form, the one "Analyse a call" opens with. Until the workspace saves anything it is
   on the shared default form, and the list says so — saving it, or creating another form,
   is what makes the forms its own.

   Everyone in the workspace can read and export the forms; changing them is the owner's
   (`may_configure_workspace`, which an operator acting on the workspace also has). The test
   run is part of editing and costs one analysis unit, so it is the owner's too. */

import { useCallback, useEffect, useState } from 'react';
import { downloadJson, problemsOf, type CrmApi } from '@/components/crm/api';
import { useCrmApi } from '@/components/crm/CrmPanel';
import styles from '@/components/crm/crm.module.css';
import { FormEditor } from '@/components/crm/FormEditor';
import { ImportDialog } from '@/components/crm/ImportDialog';
import { ResultView } from '@/components/crm/ResultView';
import { confirmDialog, showModal } from '@/components/ui/Modal';
import { toast } from '@/components/ui/Toast';
import type { CrmDefinition, CrmResult, FormRow, Problem } from '@/lib/crmForm';
import { dateTime } from '@/lib/format';
import { apiMessage } from '@/lib/session';
import { useI18n } from '@/lib/useI18n';
import { useWs, type T } from './ctx';

interface Preset { id: string; title: string; name: string; description: string; summary: { fields: number; options: number } }

export function CrmTab({ on, gen }: { on: boolean; gen: number }) {
  const ws = useWs();
  const { t } = ws;
  const api = useCrmApi('tenant', ws.unauthorized);
  const [forms, setForms] = useState<FormRow[] | null>(null);
  const [loadErr, setLoadErr] = useState('');
  const [editing, setEditing] = useState<FormRow | null>(null);

  const load = useCallback(async () => {
    setLoadErr('');
    const at = ws.gen();
    try {
      const list = await api.get<FormRow[]>('/crm/forms');
      if (at === ws.gen()) setForms(list);
    } catch (e) {
      if (at === ws.gen()) { setForms(null); setLoadErr(apiMessage(e, t)); }
    }
  }, [api, ws, t]);

  // A workspace switch drops whatever was open: Save sends what is on screen to the workspace
  // selected at click time, and one customer's form must never be saved into another's.
  useEffect(() => { setEditing(null); setForms(null); }, [gen]);
  useEffect(() => { if (on && ws.ready) void load(); }, [on, gen, ws.ready, load]);

  if (!ws.ready) return <div className="empty">{t('con.tenant.pick')}</div>;

  if (editing) {
    return (
      <EditView
        api={api} t={t} row={editing} canEdit={ws.canConfigure}
        onClose={() => { setEditing(null); void load(); }}
        onSaved={row => setEditing(row)}
      />
    );
  }

  const open = async (row: FormRow) => {
    try {
      setEditing(await api.get<FormRow>(`/crm/forms/${encodeURIComponent(row.form_key)}`));
    } catch (e) { toast(apiMessage(e, t), 'err'); }
  };

  const create = async () => {
    const made = await showModal(close => <NewFormBody api={api} forms={forms || []} close={close} />, { maxWidth: '680px' });
    if (made && typeof made === 'object') { setEditing(made as FormRow); toast(t('crm.created'), 'ok'); }
  };

  const importFile = async () => {
    const saved = await showModal(close => <ImportDialog api={api} target="workspace" close={close} />, { maxWidth: '680px' });
    if (Array.isArray(saved)) { toast(t('crm.import.done', { n: saved.length }), 'ok'); void load(); }
  };

  const primary = async (row: FormRow) => {
    try {
      await api.send('POST', `/crm/forms/${encodeURIComponent(row.form_key)}/primary`);
      void load();
    } catch (e) { toast(apiMessage(e, t), 'err'); }
  };

  const remove = async (row: FormRow) => {
    if (!(await confirmDialog(t('crm.delete.confirm', { name: row.name })))) return;
    try {
      await api.send('DELETE', `/crm/forms/${encodeURIComponent(row.form_key)}`);
      toast(t('crm.deleted'), 'ok');
      void load();
    } catch (e) { toast(apiMessage(e, t), 'err'); }
  };

  const exportForm = (row: FormRow, format: 'json' | 'xlsx') =>
    api.download(`/crm/forms/${encodeURIComponent(row.form_key)}/export?format=${format}`, `${row.form_key}.${format}`)
      .catch(e => toast(apiMessage(e, t), 'err'));

  const onDefault = !!forms && forms.length === 1 && forms[0].is_default;

  return (
    <>
      <div className="card">
        <div className="inline wrap" style={{ justifyContent: 'space-between', alignItems: 'baseline' }}>
          <h3 style={{ margin: 0 }}>{t('crm.heading')}</h3>
          <div className="inline wrap">
            {ws.canConfigure ? <button type="button" className="primary" onClick={() => void create()}>{t('crm.new')}</button> : null}
            {ws.canConfigure ? <button type="button" className="ghost" onClick={() => void importFile()}>{t('crm.import')}</button> : null}
            <button type="button" className="ghost" disabled={!forms?.length}
              onClick={() => api.download('/crm/export', 'crm-forms.json').catch(e => toast(apiMessage(e, t), 'err'))}>
              {t('crm.exportall')}
            </button>
          </div>
        </div>
        <p className="hint">{t('crm.intro')}</p>
        {!ws.canConfigure ? <p className="hint" style={{ color: 'var(--pending)' }}>{t('crm.readonly')}</p> : null}
        {onDefault ? <div className="msg">{t('crm.ondefault')}</div> : null}
        {loadErr ? <div className="msg err">{loadErr}</div> : null}
      </div>

      {forms === null && !loadErr ? <div className="empty"><span className="spinner" /></div> : null}
      {forms?.length ? (
        <div className="card table-wrap">
          <table className={styles.formsTable}>
            <thead>
              <tr>
                <th>{t('crm.name')}</th><th>{t('crm.col.fields')}</th><th>{t('crm.col.version')}</th>
                <th>{t('crm.col.updated')}</th><th />
              </tr>
            </thead>
            <tbody>
              {forms.map(f => (
                <tr key={f.form_key}>
                  <td>
                    <b>{f.name}</b>{' '}
                    {f.is_primary ? <span className="pill on" title={t('crm.primary.tip')}>★ {t('crm.primary')}</span> : null}{' '}
                    {f.is_default ? <span className="pill pending">{t('crm.default')}</span> : null}
                    {f.description ? <div className="hint">{f.description}</div> : null}
                    <div className="hint"><code>{f.form_key}</code></div>
                  </td>
                  <td>{t('crm.summary', { fields: f.summary?.fields ?? 0, options: f.summary?.options ?? 0 })}</td>
                  <td>{f.version ? `v${f.version}` : '—'}</td>
                  <td>{f.updated_at ? dateTime(f.updated_at) : '—'}{f.updated_by ? <div className="hint">{f.updated_by}</div> : null}</td>
                  <td>
                    <div className="inline wrap" style={{ justifyContent: 'flex-end' }}>
                      <button type="button" className="ghost" onClick={() => void open(f)}>
                        {ws.canConfigure ? t('crm.edit') : t('crm.view')}
                      </button>
                      {ws.canConfigure && !f.is_primary && !f.is_default ? (
                        <button type="button" className="ghost" onClick={() => void primary(f)}>{t('crm.makeprimary')}</button>
                      ) : null}
                      <button type="button" className="act" title={t('crm.export.json')} aria-label={t('crm.export.json')}
                        onClick={() => void exportForm(f, 'json')}>⤓</button>
                      <button type="button" className="act" title={t('crm.export.xlsx')} aria-label={t('crm.export.xlsx')}
                        onClick={() => void exportForm(f, 'xlsx')}>▦</button>
                      {ws.canConfigure && !f.is_default ? (
                        <button type="button" className="act danger" title={t('crm.delete')} aria-label={t('crm.delete')}
                          onClick={() => void remove(f)}>🗑</button>
                      ) : null}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </>
  );
}

/* ------------------------------------------------------------------ editing one form */

function EditView({ api, t, row, canEdit, onClose, onSaved }: {
  api: CrmApi;
  t: T;
  row: FormRow;
  canEdit: boolean;
  onClose: () => void;
  onSaved: (row: FormRow) => void;
}) {
  const [draft, setDraft] = useState<CrmDefinition>(row.definition!);
  const [saved, setSaved] = useState<CrmDefinition>(row.definition!);
  const [busy, setBusy] = useState(false);
  const [saveProblems, setSaveProblems] = useState<Problem[]>([]);
  const dirty = JSON.stringify(draft) !== JSON.stringify(saved);

  const back = async () => {
    if (dirty && !(await confirmDialog(t('crm.discard.confirm'), { ok: t('crm.discard') }))) return;
    onClose();
  };

  const save = async () => {
    setBusy(true);
    setSaveProblems([]);
    try {
      const r = await api.send<{ form: FormRow; warnings: Problem[] }>(
        'PUT', `/crm/forms/${encodeURIComponent(row.form_key)}`, { definition: draft });
      setDraft(r.form.definition!);
      setSaved(r.form.definition!);
      onSaved(r.form);
      toast(t('crm.saved', { v: r.form.version }), 'ok');
    } catch (e) {
      setSaveProblems(problemsOf(e));
      toast(apiMessage(e, t), 'err');
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className="card">
        <div className="inline wrap" style={{ justifyContent: 'space-between' }}>
          <div>
            <button type="button" className="ghost" onClick={() => void back()}>← {t('crm.back')}</button>
            <span className="hint" style={{ marginLeft: 10 }}>
              {row.is_default ? t('crm.editing.default') : `${row.form_key} · v${row.version}`}
              {dirty ? ` · ${t('crm.unsaved')}` : ''}
            </span>
          </div>
          <div className="inline wrap">
            <button type="button" className="ghost" onClick={() => downloadJson(`${row.form_key}.json`,
              { format: 'cq.crm-form', schema_version: 1, key: row.form_key, form: draft })}>{t('crm.export.json')}</button>
            {canEdit ? (
              <button type="button" className="primary" disabled={busy || !dirty} onClick={() => void save()}>
                {busy ? <span className="spinner" /> : null}{t('btn.save')}
              </button>
            ) : null}
          </div>
        </div>
        {row.is_default && canEdit ? <div className="msg">{t('crm.editing.default.hint')}</div> : null}
      </div>
      <FormEditor value={draft} onChange={setDraft} api={api} readonly={!canEdit} saveProblems={saveProblems} />
      {canEdit ? <TestCard api={api} t={t} definition={draft} /> : null}
    </>
  );
}

function TestCard({ api, t, definition }: { api: CrmApi; t: T; definition: CrmDefinition }) {
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<CrmResult | null>(null);
  const [err, setErr] = useState('');

  const run = async () => {
    setBusy(true);
    setErr('');
    try {
      setResult(await api.send<CrmResult>('POST', '/crm/test', { definition, transcript: text }));
    } catch (e) {
      setErr(apiMessage(e, t));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="card" style={{ marginTop: 16 }}>
      <h3 style={{ marginTop: 0 }}>{t('crm.test.heading')}</h3>
      <p className="hint">{t('crm.test.hint')}</p>
      <textarea rows={6} value={text} placeholder={t('crm.test.ph')} onChange={e => setText(e.target.value)} />
      <div className="actions" style={{ marginTop: 10 }}>
        <button type="button" className="primary" disabled={busy || !text.trim()} onClick={() => void run()}>
          {busy ? <><span className="spinner" />{t('crm.run.running')}</> : t('crm.test.run')}
        </button>
      </div>
      {err ? <div className="msg err">{err}</div> : null}
      {result ? <div style={{ marginTop: 14 }}><ResultView result={result} /></div> : null}
    </div>
  );
}

/* ------------------------------------------------------------------ new form */

/* Rendered by the global modal host, which sits OUTSIDE the workspace context — so the
   dictionary comes from `useI18n`, never from `useWs()`, which would throw here. */
function NewFormBody({ api, forms, close }: { api: CrmApi; forms: FormRow[]; close: (v?: unknown) => void }) {
  const { t } = useI18n();
  const [presets, setPresets] = useState<Preset[]>([]);
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  useEffect(() => {
    api.get<Preset[]>('/crm/presets').then(setPresets).catch(() => setPresets([]));
  }, [api]);

  const make = async (body: Record<string, unknown>) => {
    setBusy(true);
    setErr('');
    try {
      const r = await api.send<{ form: FormRow }>('POST', '/crm/forms', { ...body, name: name.trim() || undefined });
      close(r.form);
    } catch (e) {
      setErr(apiMessage(e, t));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <h3 style={{ marginTop: 0 }}>{t('crm.new')}</h3>
      <label htmlFor="crm-new-name">{t('crm.name')}</label>
      <input id="crm-new-name" value={name} placeholder={t('crm.new.name.ph')} onChange={e => setName(e.target.value)} />
      {forms.length === 1 && forms[0].is_default ? <p className="hint">{t('crm.new.keepsdefault')}</p> : null}

      <h4>{t('crm.new.start')}</h4>
      <div className={styles.cards}>
        <button type="button" className={styles.pick} disabled={busy} onClick={() => void make({})}>
          <b>{t('crm.new.blank')}</b><span className="hint">{t('crm.new.blank.hint')}</span>
        </button>
        {presets.map(p => (
          <button key={p.id} type="button" className={styles.pick} disabled={busy} onClick={() => void make({ preset: p.id })}>
            <b>{p.title}</b>
            <span className="hint">{p.description}</span>
            <div className="hint">{t('crm.summary', { fields: p.summary.fields, options: p.summary.options })}</div>
          </button>
        ))}
        {forms.map(f => (
          <button key={f.form_key} type="button" className={styles.pick} disabled={busy} onClick={() => void make({ copy_from: f.form_key })}>
            <b>{t('crm.new.copy', { name: f.name })}</b>
            <span className="hint">{t('crm.summary', { fields: f.summary?.fields ?? 0, options: f.summary?.options ?? 0 })}</span>
          </button>
        ))}
      </div>
      {err ? <div className="msg err">{err}</div> : null}
      <div className="actions" style={{ marginTop: 14 }}>
        <button type="button" className="ghost" onClick={() => close()}>{t('btn.cancel')}</button>
      </div>
    </div>
  );
}
