'use client';
/* The CRM form editor — form settings, the field list, one field open at a time.
   ==============================================================================
   Controlled: the definition comes in as `value` and every edit goes out through `onChange`;
   saving is the page's business (a workspace form and the console's default form save to
   different routes). What the editor owns is LIVE VALIDATION: shortly after each change the
   draft is sent to `POST /crm/validate` — the same `normalize_definition` every save runs —
   and the problems are listed at the top with a link to the field each one is about, and the
   field marked in the list. The server is the only judge on purpose: a second copy of the
   rules in TypeScript would be a copy that disagrees with the one that refuses the save. */

import { useEffect, useRef, useState } from 'react';
import { Select } from '@/components/ui/Select';
import {
  blankField, CHOICE_TYPES, move, problemsByField, removeField,
  type CrmDefinition, type Problem,
} from '@/lib/crmForm';
import { useI18n } from '@/lib/useI18n';
import type { CrmApi } from './api';
import styles from './crm.module.css';
import { FieldEditor } from './FieldEditor';

const VALIDATE_MS = 700;

export function FormEditor({ value, onChange, api, readonly, saveProblems }: {
  value: CrmDefinition;
  onChange: (d: CrmDefinition) => void;
  api: CrmApi;
  readonly?: boolean;
  /** Problems from the last refused save, shown until the next validation replaces them. */
  saveProblems?: Problem[];
}) {
  const { t } = useI18n();
  const [sel, setSel] = useState(0);
  const [problems, setProblems] = useState<{ errors: Problem[]; warnings: Problem[] }>({ errors: [], warnings: [] });
  const seq = useRef(0);

  useEffect(() => {
    if (saveProblems?.length) setProblems(p => ({ ...p, errors: saveProblems }));
  }, [saveProblems]);

  useEffect(() => {
    if (readonly) return;
    const my = ++seq.current;
    const timer = setTimeout(() => {
      api.send<{ errors: Problem[]; warnings: Problem[] }>('POST', '/crm/validate', { definition: value })
        .then(r => { if (my === seq.current) setProblems({ errors: r.errors || [], warnings: r.warnings || [] }); })
        .catch(() => { /* validation is advice; the save is the judge */ });
    }, VALIDATE_MS);
    return () => clearTimeout(timer);
  }, [value, api, readonly]);

  const index = Math.min(sel, Math.max(0, value.fields.length - 1));
  const byField = problemsByField(problems.errors);
  const warnByField = problemsByField(problems.warnings);
  const set = (part: Partial<CrmDefinition>) => onChange({ ...value, ...part });

  const addField = () => {
    const f = blankField(value);
    onChange({ ...value, fields: [...value.fields, f] });
    setSel(value.fields.length);
  };

  const all = [...problems.errors.map(p => ({ ...p, kind: 'err' })), ...problems.warnings.map(p => ({ ...p, kind: 'warn' }))];
  const goto = (path: string) => {
    const m = /^fields\[(\d+)\]/.exec(path);
    if (m) setSel(Number(m[1]));
  };

  return (
    <div>
      {all.length && !readonly ? (
        <div className={`${styles.problems}${problems.errors.length ? '' : ` ${styles.warnOnly}`}`} role="status">
          <b>{problems.errors.length ? t('crm.problems.errors', { n: problems.errors.length }) : t('crm.problems.warnings', { n: problems.warnings.length })}</b>
          <ul>
            {all.slice(0, 12).map((p, i) => (
              <li key={i} className={p.kind === 'err' ? styles.err : styles.warn}>
                {/^fields\[\d+\]/.test(p.path)
                  ? <button type="button" onClick={() => goto(p.path)}>{fieldName(value, p.path)}</button>
                  : <span>{p.path || t('crm.form')}</span>}
                {': '}{p.message}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <div className="card">
        <h3 style={{ marginTop: 0 }}>{t('crm.settings')}</h3>
        <div className="row">
          <div style={{ flex: 2 }}>
            <label htmlFor="crm-name">{t('crm.name')}</label>
            <input id="crm-name" value={value.name} disabled={readonly} onChange={e => set({ name: e.target.value })} />
          </div>
          <div>
            <label>{t('crm.language')}</label>
            <Select value={value.language} disabled={readonly} onChange={v => set({ language: v as CrmDefinition['language'] })}
              options={(['ka', 'en', 'ru'] as const).map(l => ({ value: l, label: t(`crm.lang.${l}`) }))} />
          </div>
          <div>
            <label>{t('crm.outlang')}</label>
            <Select value={value.output_language} disabled={readonly}
              onChange={v => set({ output_language: v as CrmDefinition['output_language'] })}
              options={[{ value: 'call', label: t('crm.outlang.call') }, { value: 'form', label: t('crm.outlang.form') }]} />
          </div>
        </div>
        <label htmlFor="crm-desc">{t('crm.description')}</label>
        <input id="crm-desc" value={value.description} disabled={readonly} onChange={e => set({ description: e.target.value })} />
        <div className="row">
          <div>
            <label>{t('crm.labelfield')}</label>
            <Select value={value.label_field || ''} disabled={readonly} onChange={v => set({ label_field: v || null })}
              options={[{ value: '', label: t('crm.labelfield.auto') },
                ...value.fields.filter(f => f.type === 'choice').map(f => ({ value: f.key, label: f.label || f.key }))]} />
            <p className="hint">{t('crm.labelfield.hint')}</p>
          </div>
        </div>
        <label htmlFor="crm-instr">{t('crm.instructions')}</label>
        <textarea id="crm-instr" rows={3} value={value.instructions} disabled={readonly}
          placeholder={t('crm.instructions.ph')} onChange={e => set({ instructions: e.target.value })} />
      </div>

      <div className={styles.layout} style={{ marginTop: 16 }}>
        <div className={styles.list} role="list" aria-label={t('crm.fields')}>
          {value.fields.map((f, i) => (
            <div key={`${f.key}-${i}`} role="listitem" className="inline">
              <button
                type="button" onClick={() => setSel(i)} aria-current={i === index}
                className={`${styles.item}${i === index ? ` ${styles.active}` : ''}${byField.has(i) ? ` ${styles.bad}` : ''}`}
              >
                <span className={styles.num}>{i + 1}</span>
                <span className={styles.itemMain}>
                  <span className={styles.itemLabel}>{f.label || <i>{t('crm.field.unnamed')}</i>}</span>
                  <span className={styles.itemMeta}>
                    {t(`crm.type.${f.type}`)}
                    {CHOICE_TYPES.includes(f.type) ? ` · ${f.options.length}` : ''}
                    {f.when ? ' · ⑂' : ''}
                    {warnByField.has(i) && !byField.has(i) ? ' · ⚠' : ''}
                  </span>
                </span>
                {f.required ? <span className={`${styles.badge} ${styles.req}`}>*</span> : null}
                {f.source !== 'ai' ? <span className={`${styles.badge} ${styles[f.source]}`}>{t(`crm.source.${f.source}.short`)}</span> : null}
              </button>
              {!readonly ? (
                <span style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                  <button type="button" className={`act ${styles.mini}`} disabled={i === 0} title={t('crm.up')} aria-label={t('crm.up')}
                    onClick={() => { onChange({ ...value, fields: move(value.fields, i, -1) }); setSel(i - 1); }}>↑</button>
                  <button type="button" className={`act ${styles.mini}`} disabled={i === value.fields.length - 1} title={t('crm.down')} aria-label={t('crm.down')}
                    onClick={() => { onChange({ ...value, fields: move(value.fields, i, 1) }); setSel(i + 1); }}>↓</button>
                </span>
              ) : null}
            </div>
          ))}
          {!readonly ? <button type="button" className="ghost" onClick={addField}>{t('crm.field.add')}</button> : null}
        </div>

        <div>
          {value.fields.length ? (
            <FieldEditor
              key={index} defn={value} index={index} onDefn={onChange} readonly={readonly}
              problems={[...(byField.get(index) || []), ...(warnByField.get(index) || [])]}
              onRemove={() => { onChange(removeField(value, value.fields[index].key)); setSel(Math.max(0, index - 1)); }}
            />
          ) : <div className="empty">{t('crm.fields.none')}</div>}
        </div>
      </div>
    </div>
  );
}

function fieldName(defn: CrmDefinition, path: string): string {
  const m = /^fields\[(\d+)\]/.exec(path);
  const f = m ? defn.fields[Number(m[1])] : null;
  return f ? (f.label || f.key) : path;
}
