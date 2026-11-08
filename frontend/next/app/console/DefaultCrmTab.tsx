'use client';
import { useEffect, useMemo, useState } from 'react';
import { problemsOf, type CrmApi } from '@/components/crm/api';
import { FormEditor } from '@/components/crm/FormEditor';
import { ImportDialog } from '@/components/crm/ImportDialog';
import { Select } from '@/components/ui/Select';
import { confirmDialog, showModal } from '@/components/ui/Modal';
import { toast } from '@/components/ui/Toast';
import type { CrmDefinition, Problem } from '@/lib/crmForm';
import { dateTime } from '@/lib/format';
import { apiUpload, downloadAuthed } from '@/lib/session';
import { useI18n } from '@/lib/useI18n';
import { SessionExpired, adminGet, adminSend, errText } from './api';
import { sourcePill } from './logic';
import { Msg, type Note } from './parts';

/* The default CRM form — what every workspace fills until it saves a form of its own.

   The workspace's own editor, one level up, over `/admin/crm/default-form`. The same SOURCE
   PILL as the default rubric, for the same reason: until an operator saves here, what is on
   screen is the built-in form, and editing it without noticing that nothing was ever stored
   is the easy mistake.

   An import or a preset only LOADS the editor; nothing is saved until Save. This form is the
   one every new workspace inherits, so it gets looked at before it ships. */

const CRM_SOURCE: Record<string, readonly [string, string]> = {
  stored: ['pb.src.stored', 'ready'],
  builtin: ['pb.src.builtin', 'notinkb'],
};

interface DefaultForm {
  definition: CrmDefinition;
  source: string;
  updated_at: string | null;
  updated_by: string | null;
}

interface Preset { id: string; title: string }

export default function DefaultCrmTab() {
  const { t } = useI18n();
  const [form, setForm] = useState<DefaultForm | null>(null);
  const [draft, setDraft] = useState<CrmDefinition | null>(null);
  const [presets, setPresets] = useState<Preset[]>([]);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<Note | null>(null);
  const [saveProblems, setSaveProblems] = useState<Problem[]>([]);

  /* `/crm/validate`, `/crm/presets` and `/crm/import/preview` admit a superadmin as well as a
     workspace, so the shared components run here unchanged at scope 'admin'. */
  const api = useMemo<CrmApi>(() => ({
    get: <T,>(path: string) => adminGet<T>(path),
    send: <T,>(method: 'POST' | 'PUT' | 'PATCH' | 'DELETE', path: string, body?: unknown) => adminSend<T>(method, path, body),
    upload: <T,>(path: string, fd: FormData) => apiUpload<T>(path, fd, { scope: 'admin' }),
    download: (path: string, name: string) => downloadAuthed(path, name, { scope: 'admin' }),
  }), []);

  const apply = (d: DefaultForm) => { setForm(d); setDraft(d.definition); };

  useEffect(() => {
    adminGet<DefaultForm>('/admin/crm/default-form').then(apply).catch(e => {
      if (e instanceof SessionExpired) return;
      setNote({ kind: 'err', text: t('toast.error') });
    });
    adminGet<Preset[]>('/crm/presets').then(setPresets).catch(() => setPresets([]));
    // Once, on mount — a language switch must not throw away an unsaved draft.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const dirty = !!form && !!draft && JSON.stringify(draft) !== JSON.stringify(form.definition);

  const save = async () => {
    if (!draft) return;
    setBusy(true);
    setNote(null);
    setSaveProblems([]);
    try {
      apply(await adminSend<DefaultForm>('PUT', '/admin/crm/default-form', { definition: draft }));
      toast(t('crm.def.saved'), 'ok');
    } catch (e) {
      if (e instanceof SessionExpired) return;
      setSaveProblems(problemsOf(e));
      setNote({ kind: 'err', text: errText(e, t) });
    } finally {
      setBusy(false);
    }
  };

  const reset = async () => {
    if (!(await confirmDialog(t('crm.def.reset.confirm')))) return;
    try {
      apply(await adminSend<DefaultForm>('POST', '/admin/crm/default-form/reset'));
      toast(t('crm.def.reset.done'), 'ok');
    } catch (e) {
      if (!(e instanceof SessionExpired)) setNote({ kind: 'err', text: errText(e, t) });
    }
  };

  const loadPreset = async (id: string) => {
    if (!id) return;
    if (dirty && !(await confirmDialog(t('crm.discard.confirm'), { ok: t('crm.discard') }))) return;
    try {
      const p = await adminGet<{ definition: CrmDefinition }>(`/crm/presets/${encodeURIComponent(id)}`);
      setDraft(p.definition);
      setNote({ kind: 'ok', text: t('crm.def.loaded') });
    } catch (e) {
      if (!(e instanceof SessionExpired)) setNote({ kind: 'err', text: errText(e, t) });
    }
  };

  const importFile = async () => {
    const defn = await showModal(close => <ImportDialog api={api} target="draft" close={close} />, { maxWidth: '680px' });
    if (defn && typeof defn === 'object') {
      setDraft(defn as CrmDefinition);
      setNote({ kind: 'ok', text: t('crm.def.loaded') });
    }
  };

  const exportSaved = (format: 'json' | 'xlsx') =>
    api.download(`/admin/crm/default-form/export?format=${format}`, `default-crm-form.${format}`)
      .catch(e => { if (!(e instanceof SessionExpired)) setNote({ kind: 'err', text: errText(e, t) }); });

  const pill = sourcePill(CRM_SOURCE, form?.source, 'builtin');

  return (
    <>
      <div className="card">
        <div className="inline wrap" style={{ justifyContent: 'space-between', alignItems: 'baseline', gap: 10 }}>
          <h3 style={{ margin: 0 }}>{t('crm.def.heading')}</h3>
          <span className="inline" style={{ gap: 8 }}>
            <span className={`pill ${form ? pill.cls : ''}`}>{form ? t(pill.key) : '—'}</span>
            <span className="hint">
              {form?.updated_at ? t('pb.defrubric.updated', { when: dateTime(form.updated_at), who: form.updated_by || '—' }) : ''}
            </span>
          </span>
        </div>
        <p className="hint">{t('crm.def.desc')}</p>
        <div className="inline wrap" style={{ marginTop: 10 }}>
          <button type="button" className="primary" disabled={busy || !dirty} onClick={() => void save()}>
            {busy ? <span className="spinner" /> : null}{t('btn.save')}
          </button>
          <button type="button" className="ghost" onClick={() => void importFile()}>{t('crm.import')}</button>
          <div style={{ minWidth: 220 }}>
            <Select
              value="" onChange={v => void loadPreset(v)} ariaLabel={t('crm.def.preset')}
              options={[{ value: '', label: t('crm.def.preset') }, ...presets.map(p => ({ value: p.id, label: p.title }))]}
            />
          </div>
          <button type="button" className="ghost" onClick={() => void exportSaved('json')}>{t('crm.export.json')}</button>
          <button type="button" className="ghost" onClick={() => void exportSaved('xlsx')}>{t('crm.export.xlsx')}</button>
          {form?.source === 'stored' ? (
            <button type="button" className="danger" onClick={() => void reset()}>{t('crm.def.reset')}</button>
          ) : null}
        </div>
        {dirty ? <p className="hint" style={{ color: 'var(--pending)' }}>{t('crm.unsaved')}</p> : null}
        <Msg note={note} />
      </div>
      {draft ? <FormEditor value={draft} onChange={setDraft} api={api} saveProblems={saveProblems} /> : null}
    </>
  );
}
