'use client';
/* Import CRM forms from a JSON export or an Excel workbook — preview first, then apply.
   ===================================================================================
   Step 1 uploads the file to `POST /crm/import/preview`, which saves nothing: it returns each
   form in the file with its problems (by path) and, for a key this workspace already has, what
   would change. Step 2 sends the previewed definitions back — `replace` writes a new version
   of the existing form (its history keeps the old one), `new` adds a form beside it.

   In the console the dialog is a loader instead (`target="draft"`): the chosen definition goes
   into the default-form editor unsaved, because the default is everybody's form and deserves
   a look before it is saved. */

import { useState } from 'react';
import { Select } from '@/components/ui/Select';
import { apiMessage } from '@/lib/session';
import type { CrmDefinition, FormSummary, Problem } from '@/lib/crmForm';
import { useI18n } from '@/lib/useI18n';
import type { CrmApi } from './api';
import styles from './crm.module.css';

interface Preview {
  key: string | null;
  name: string;
  definition: CrmDefinition;
  errors: Problem[];
  warnings: Problem[];
  exists: boolean;
  summary: FormSummary;
  diff: { new_form: boolean; settings: string[]; added: string[]; removed: string[];
    changed: { key: string; options_added: number; options_removed: number; options_changed: number }[] };
}

export function ImportDialog({ api, target, close }: {
  api: CrmApi;
  target: 'workspace' | 'draft';
  close: (v?: unknown) => void;
}) {
  const { t } = useI18n();
  const [forms, setForms] = useState<Preview[] | null>(null);
  const [modes, setModes] = useState<Record<number, 'replace' | 'new' | 'skip'>>({});
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  const pick = async (file: File | undefined) => {
    if (!file) return;
    setErr('');
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append('file', file);
      const r = await api.upload<{ forms: Preview[] }>('/crm/import/preview', fd);
      setForms(r.forms);
      setModes(Object.fromEntries(r.forms.map((f, i) => [i, f.errors.length ? 'skip' : f.exists ? 'replace' : 'new'])));
    } catch (e) {
      setErr(apiMessage(e, t));
    } finally {
      setBusy(false);
    }
  };

  const apply = async () => {
    if (!forms) return;
    if (target === 'draft') {
      const i = forms.findIndex((_, j) => modes[j] !== 'skip');
      if (i >= 0) close(forms[i].definition);
      return;
    }
    const items = forms.map((f, i) => ({ f, mode: modes[i] })).filter(x => x.mode !== 'skip')
      .map(({ f, mode }) => ({ definition: f.definition, mode, key: f.key }));
    if (!items.length) return;
    setBusy(true);
    setErr('');
    try {
      const r = await api.send<{ saved: unknown[] }>('POST', '/crm/import', { items });
      close(r.saved);
    } catch (e) {
      setErr(apiMessage(e, t));
    } finally {
      setBusy(false);
    }
  };

  const chosen = forms ? forms.filter((_, i) => modes[i] !== 'skip').length : 0;

  return (
    <div>
      <h3 style={{ marginTop: 0 }}>{t('crm.import.title')}</h3>
      <p className="hint">{t('crm.import.hint')}</p>
      <input type="file" accept=".json,.xlsx,application/json,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        disabled={busy} onChange={e => void pick(e.target.files?.[0])} />
      {busy && !forms ? <p className="hint"><span className="spinner" /> {t('crm.import.reading')}</p> : null}

      {forms?.map((f, i) => (
        <div key={i} className="card" style={{ marginTop: 12 }}>
          <div className="inline" style={{ justifyContent: 'space-between' }}>
            <b>{f.name || t('crm.field.unnamed')}</b>
            <span className="hint">{t('crm.summary', { fields: f.summary.fields, options: f.summary.options })}</span>
          </div>
          {f.key ? <div className="hint">{t('crm.key')}: <code>{f.key}</code></div> : null}
          {f.errors.length ? (
            <div className={styles.problems}>
              <b>{t('crm.problems.errors', { n: f.errors.length })}</b>
              <ul>{f.errors.slice(0, 15).map((p, j) => <li key={j} className={styles.err}>{p.path ? `${p.path}: ` : ''}{p.message}</li>)}</ul>
            </div>
          ) : null}
          {f.warnings.length ? (
            <div className={`${styles.problems} ${styles.warnOnly}`}>
              <b>{t('crm.problems.warnings', { n: f.warnings.length })}</b>
              <ul>{f.warnings.slice(0, 10).map((p, j) => <li key={j} className={styles.warn}>{p.path ? `${p.path}: ` : ''}{p.message}</li>)}</ul>
            </div>
          ) : null}
          {f.exists && !f.errors.length ? (
            <ul className={styles.diff}>
              {f.diff.settings.length ? <li>{t('crm.diff.settings', { list: f.diff.settings.join(', ') })}</li> : null}
              {f.diff.added.length ? <li>{t('crm.diff.added', { list: f.diff.added.join(', ') })}</li> : null}
              {f.diff.removed.length ? <li className={styles.warn}>{t('crm.diff.removed', { list: f.diff.removed.join(', ') })}</li> : null}
              {f.diff.changed.map(c => (
                <li key={c.key}>{t('crm.diff.changed', { key: c.key, add: c.options_added, rm: c.options_removed, ch: c.options_changed })}</li>
              ))}
              {!f.diff.settings.length && !f.diff.added.length && !f.diff.removed.length && !f.diff.changed.length
                ? <li>{t('crm.diff.same')}</li> : null}
            </ul>
          ) : null}
          {!f.errors.length ? (
            <div className="row" style={{ marginTop: 8 }}>
              <div>
                <Select
                  value={modes[i] || 'skip'}
                  onChange={v => setModes(m => ({ ...m, [i]: v as 'replace' | 'new' | 'skip' }))}
                  options={target === 'draft'
                    ? [{ value: 'new', label: t('crm.import.load') }, { value: 'skip', label: t('crm.import.skip') }]
                    : [
                      ...(f.exists ? [{ value: 'replace', label: t('crm.import.replace') }] : []),
                      { value: 'new', label: t('crm.import.new') },
                      { value: 'skip', label: t('crm.import.skip') },
                    ]}
                />
              </div>
            </div>
          ) : null}
        </div>
      ))}

      {err ? <div className="msg err">{err}</div> : null}
      <div className="actions" style={{ marginTop: 14 }}>
        <button type="button" className="ghost" onClick={() => close()}>{t('btn.cancel')}</button>
        <button type="button" className="primary" disabled={!chosen || busy} onClick={() => void apply()}>
          {busy && forms ? <span className="spinner" /> : null}
          {target === 'draft' ? t('crm.import.load') : t('crm.import.apply', { n: chosen })}
        </button>
      </div>
    </div>
  );
}
