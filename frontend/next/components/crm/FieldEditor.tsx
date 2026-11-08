'use client';
/* One field of a CRM form: what it is, where its value comes from, when it applies.
   =================================================================================
   The type decides which extra controls appear — options for a choice, the template and its
   slots for a template field, a unit for money — and the SOURCE decides whether the AI is
   involved at all: `derived` and `external` fields are never asked of the model, `fixed`
   takes its default on every call. The hint under the source select says that in words,
   because a field marked "external" that comes back blank is otherwise a bug report. */

import { useEffect, useState } from 'react';
import { confirmDialog } from '@/components/ui/Modal';
import { Select } from '@/components/ui/Select';
import {
  CHOICE_TYPES, FIELD_TYPES, KEY_RE, SOURCES, slotNames, slugKey, renameField,
  type CrmDefinition, type CrmField, type FieldType, type Problem, type Source,
} from '@/lib/crmForm';
import { useI18n } from '@/lib/useI18n';
import styles from './crm.module.css';
import { ValueInput } from './inputs';
import { OptionsEditor } from './OptionsEditor';
import { WhenEditor } from './WhenEditor';

export function FieldEditor({ defn, index, onDefn, onRemove, readonly, problems }: {
  defn: CrmDefinition;
  index: number;
  onDefn: (d: CrmDefinition) => void;
  onRemove: () => void;
  readonly?: boolean;
  problems: Problem[];
}) {
  const { t } = useI18n();
  const field = defn.fields[index];
  const [key, setKey] = useState(field.key);
  useEffect(() => { setKey(field.key); }, [field.key]);

  const patch = (part: Partial<CrmField>) =>
    onDefn({ ...defn, fields: defn.fields.map((f, i) => (i === index ? { ...f, ...part } : f)) });
  const others = defn.fields.filter(f => f.key !== field.key);
  const keyOk = KEY_RE.test(key) && (key === field.key || !others.some(f => f.key === key));

  const commitKey = (next: string) => {
    if (next !== field.key && KEY_RE.test(next) && !others.some(f => f.key === next)) onDefn(renameField(defn, field.key, next));
    else setKey(field.key);
  };

  /* A field still carrying its generated placeholder key takes a key from its label when the
     label is first written — in Latin letters. A Georgian label keeps `field_3`: the server
     would refuse a Georgian key, and a transliteration is a key nobody chose. */
  const labelBlur = () => {
    if (!/^field_\d+(_\d+)?$/.test(field.key) || !field.label) return;
    const next = slugKey(field.label, field.key, others.map(f => f.key));
    if (next !== field.key) commitKey(next);
  };

  const setType = (type: FieldType) => {
    const part: Partial<CrmField> = { type };
    if (type === 'template') part.source = 'ai';
    if (!CHOICE_TYPES.includes(type)) part.filter_by = null;
    patch(part);
  };

  const remove = async () => {
    if (await confirmDialog(t('crm.field.remove.confirm', { name: field.label || field.key }))) onRemove();
  };

  const choiceFields = others.filter(f => f.type === 'choice');
  const tplSource = field.template_from ? defn.fields.find(f => f.key === field.template_from) : null;
  const slots = Array.from(new Set([
    ...slotNames(field.template),
    ...(tplSource ? tplSource.options.flatMap(o => slotNames(o.template)) : []),
  ]));
  const hint = (name: string) => field.slots.find(s => s.name === name)?.instruction || '';
  const setHint = (name: string, instruction: string) => {
    const rest = field.slots.filter(s => s.name !== name);
    patch({ slots: instruction ? [...rest, { name, instruction }] : rest });
  };

  return (
    <div className="card" style={{ marginTop: 0 }}>
      {problems.length ? (
        <div className={styles.problems}>
          <ul>{problems.map((p, i) => <li key={i}>{p.message}</li>)}</ul>
        </div>
      ) : null}
      <div className="row">
        <div style={{ flex: 2 }}>
          <label htmlFor="crm-f-label">{t('crm.field.label')}</label>
          <input id="crm-f-label" value={field.label} disabled={readonly} placeholder={t('crm.field.label.ph')}
            onChange={e => patch({ label: e.target.value })} onBlur={labelBlur} />
        </div>
        <div>
          <label htmlFor="crm-f-key">{t('crm.key')}</label>
          <input id="crm-f-key" value={key} disabled={readonly} className={keyOk ? '' : styles.err}
            onChange={e => setKey(e.target.value.trim())} onBlur={() => commitKey(key)} />
          {!keyOk ? <div className={`hint ${styles.err}`}>{t('crm.key.bad')}</div> : <div className="hint">{t('crm.key.hint')}</div>}
        </div>
      </div>
      <div className="row">
        <div>
          <label>{t('crm.field.type')}</label>
          <Select value={field.type} disabled={readonly} onChange={v => setType(v as FieldType)}
            options={FIELD_TYPES.map(x => ({ value: x, label: t(`crm.type.${x}`) }))} />
        </div>
        <div>
          <label>{t('crm.field.source')}</label>
          <Select value={field.source} disabled={readonly || field.type === 'template'}
            onChange={v => patch({ source: v as Source })}
            options={SOURCES.map(x => ({ value: x, label: t(`crm.source.${x}`) }))} />
        </div>
        <div style={{ flex: '0 1 150px', alignSelf: 'center' }}>
          <label className="inline" style={{ marginTop: 18 }}>
            <input type="checkbox" checked={field.required} disabled={readonly} onChange={e => patch({ required: e.target.checked })} />
            <span>{t('crm.field.required')}</span>
          </label>
        </div>
      </div>
      <p className="hint">{t(`crm.source.${field.source}.hint`)}</p>

      {field.source === 'ai' ? (
        <>
          <label htmlFor="crm-f-instr">{t('crm.field.instruction')}</label>
          <textarea id="crm-f-instr" rows={2} value={field.instruction} disabled={readonly}
            placeholder={t('crm.field.instruction.ph')} onChange={e => patch({ instruction: e.target.value })} />
        </>
      ) : null}

      {field.source === 'fixed' ? (
        <>
          <label>{t('crm.field.default')}</label>
          <ValueInput field={field} value={field.default} disabled={readonly} onChange={v => patch({ default: v })} />
        </>
      ) : null}

      {field.type === 'money' ? (
        <div className="row"><div>
          <label>{t('crm.field.unit')}</label>
          <input value={field.unit} disabled={readonly} placeholder="₾" onChange={e => patch({ unit: e.target.value })} />
        </div></div>
      ) : null}
      {field.type === 'text' || field.type === 'textarea' ? (
        <div className="row"><div className="w-num">
          <label>{t('crm.field.maxlen')}</label>
          <input inputMode="numeric" value={field.max_length ?? ''} disabled={readonly}
            onChange={e => patch({ max_length: e.target.value ? Number(e.target.value.replace(/\D/g, '')) || null : null })} />
        </div></div>
      ) : null}

      {CHOICE_TYPES.includes(field.type) ? (
        <div className={styles.section}>
          <h4>{t('crm.field.options')}</h4>
          <div className="row">
            <div>
              <label>{t('crm.field.filter')}</label>
              <Select value={field.filter_by || ''} disabled={readonly}
                onChange={v => patch({ filter_by: v || null })}
                options={[{ value: '', label: t('crm.none') }, ...choiceFields.map(f => ({ value: f.key, label: f.label || f.key }))]} />
              <p className="hint">{t('crm.field.filter.hint')}</p>
            </div>
          </div>
          <OptionsEditor defn={defn} field={field} onDefn={onDefn} readonly={readonly} />
        </div>
      ) : null}

      {field.type === 'template' ? (
        <div className={styles.section}>
          <h4>{t('crm.tpl.heading')}</h4>
          <label>{t('crm.tpl.from')}</label>
          <Select value={field.template_from || ''} disabled={readonly}
            onChange={v => patch({ template_from: v || null })}
            options={[{ value: '', label: t('crm.none') }, ...choiceFields.map(f => ({ value: f.key, label: f.label || f.key }))]} />
          <p className="hint">{t('crm.tpl.from.hint')}</p>
          <label>{field.template_from ? t('crm.tpl.fallback') : t('crm.tpl.text')}</label>
          <textarea rows={3} value={field.template} disabled={readonly} placeholder={t('crm.tpl.ph')}
            onChange={e => patch({ template: e.target.value })} />
          <p className="hint">{t('crm.tpl.hint')}</p>
          {slots.length ? (
            <>
              <label>{t('crm.tpl.slots')}</label>
              {slots.map(name => (
                <div className={styles.rule} key={name}>
                  <span className={styles.slot} style={{ flex: '0 0 auto', alignSelf: 'center' }}>{`{${name}}`}</span>
                  <input value={hint(name)} disabled={readonly} placeholder={t('crm.tpl.slot.ph')}
                    onChange={e => setHint(name, e.target.value)} />
                </div>
              ))}
            </>
          ) : null}
        </div>
      ) : null}

      <div className={styles.section}>
        <h4>{t('crm.when.heading')}</h4>
        <WhenEditor defn={defn} field={field} readonly={readonly} onChange={when => patch({ when })} />
      </div>

      {!readonly ? (
        <div className="actions" style={{ marginTop: 16 }}>
          <button type="button" className="danger" onClick={() => void remove()}>{t('crm.field.remove')}</button>
        </div>
      ) : null}
    </div>
  );
}
