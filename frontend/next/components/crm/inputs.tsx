'use client';
/* One input per field type — the control for a VALUE of a CRM field.
   ==================================================================
   Used wherever a person types a value a field will hold: reviewing a filled form, a fixed
   field's default, a value an option implies. One component so a date is a date picker and a
   choice is the option tree in all three places; the server coerces whatever arrives anyway
   (`crm_form.coerce`), so this is about not making people type `2026-11-05` by hand. */

import { Select } from '@/components/ui/Select';
import { optionPath, type FieldType } from '@/lib/crmForm';
import { useI18n } from '@/lib/useI18n';
import styles from './crm.module.css';

export interface ValueField {
  type: FieldType;
  options?: { key: string; label: string; parent: string | null }[] | null;
  unit?: string;
}

function str(v: unknown): string {
  if (v === null || v === undefined) return '';
  return String(v);
}

export function ValueInput({ field, value, onChange, disabled, id }: {
  field: ValueField;
  value: unknown;
  onChange: (v: unknown) => void;
  disabled?: boolean;
  id?: string;
}) {
  const { t } = useI18n();
  const opts = field.options || [];
  switch (field.type) {
    case 'choice':
      return (
        <Select
          id={id} value={str(value)} disabled={disabled}
          onChange={v => onChange(v || null)}
          options={[{ value: '', label: '—' }, ...opts.map(o => ({ value: o.key, label: optionPath(opts, o.key) }))]}
        />
      );
    case 'multichoice': {
      const picked = new Set(Array.isArray(value) ? value.map(String) : value ? [String(value)] : []);
      return (
        <div className={styles.checks} id={id}>
          {opts.map(o => (
            <label key={o.key}>
              <input
                type="checkbox" checked={picked.has(o.key)} disabled={disabled}
                onChange={e => {
                  const next = new Set(picked);
                  if (e.target.checked) next.add(o.key); else next.delete(o.key);
                  onChange(opts.map(x => x.key).filter(k => next.has(k)));
                }}
              />
              <span>{optionPath(opts, o.key)}</span>
            </label>
          ))}
        </div>
      );
    }
    case 'boolean':
      return (
        <Select
          id={id} disabled={disabled}
          value={value === true ? 'yes' : value === false ? 'no' : ''}
          onChange={v => onChange(v === 'yes' ? true : v === 'no' ? false : null)}
          options={[{ value: '', label: '—' }, { value: 'yes', label: t('crm.yes') }, { value: 'no', label: t('crm.no') }]}
        />
      );
    case 'date':
      return <input id={id} type="date" value={str(value)} disabled={disabled} onChange={e => onChange(e.target.value || null)} />;
    case 'datetime':
      return <input id={id} type="datetime-local" value={str(value)} disabled={disabled} onChange={e => onChange(e.target.value || null)} />;
    case 'time':
      return <input id={id} type="time" value={str(value)} disabled={disabled} onChange={e => onChange(e.target.value || null)} />;
    case 'textarea':
    case 'template':
      return <textarea id={id} rows={3} value={str(value)} disabled={disabled} onChange={e => onChange(e.target.value)} />;
    case 'number':
    case 'money':
    case 'percent':
      return (
        <input
          id={id} inputMode="decimal" value={str(value)} disabled={disabled}
          placeholder={field.type === 'percent' ? '%' : field.unit || ''}
          onChange={e => onChange(e.target.value)}
        />
      );
    default:
      return (
        <input
          id={id} type={field.type === 'email' ? 'email' : field.type === 'phone' ? 'tel' : 'text'}
          value={str(value)} disabled={disabled} onChange={e => onChange(e.target.value)}
        />
      );
  }
}
