'use client';
/* "This field applies only when …" — the condition builder.
   =========================================================
   Writes `{match, rules}` exactly as the server stores it. The value control follows the
   field the rule is about: a choice field offers its option tree (and on a tree a rule on a
   parent covers every option under it — the hint says so, because that is what makes "when
   request is under Medical" one rule instead of forty), a yes/no field offers yes/no, anything
   else is typed. */

import { Select } from '@/components/ui/Select';
import {
  CHOICE_TYPES, OPS, optionPath, type CrmDefinition, type CrmField, type Op, type Rule, type When,
} from '@/lib/crmForm';
import { useI18n } from '@/lib/useI18n';
import styles from './crm.module.css';

export function WhenEditor({ defn, field, onChange, readonly }: {
  defn: CrmDefinition;
  field: CrmField;
  onChange: (when: When | null) => void;
  readonly?: boolean;
}) {
  const { t } = useI18n();
  const others = defn.fields.filter(f => f.key !== field.key);
  const when = field.when;
  const mode = !when || !when.rules.length ? 'always' : when.match;

  const setRules = (rules: Rule[], match = when?.match || 'all') =>
    onChange(rules.length ? { match, rules } : null);

  const addRule = () => {
    const first = others[0];
    if (!first) return;
    setRules([...(when?.rules || []), { field: first.key, op: 'filled', value: null }]);
  };

  return (
    <div>
      <div className="row">
        <div>
          <label>{t('crm.when.mode')}</label>
          <Select
            value={mode} disabled={readonly || !others.length}
            onChange={v => {
              if (v === 'always') onChange(null);
              else if (when?.rules.length) onChange({ ...when, match: v as 'all' | 'any' });
              else if (others[0]) onChange({ match: v as 'all' | 'any', rules: [{ field: others[0].key, op: 'filled', value: null }] });
            }}
            options={[
              { value: 'always', label: t('crm.when.always') },
              { value: 'all', label: t('crm.when.all') },
              { value: 'any', label: t('crm.when.any') },
            ]}
          />
        </div>
      </div>
      {(when?.rules || []).map((r, i) => (
        <RuleRow
          key={i} rule={r} fields={others} readonly={readonly}
          onChange={next => setRules(when!.rules.map((x, j) => (j === i ? next : x)))}
          onRemove={() => setRules(when!.rules.filter((_, j) => j !== i))}
        />
      ))}
      {mode !== 'always' && !readonly ? (
        <button type="button" className="ghost" onClick={addRule} style={{ marginTop: 6 }}>{t('crm.when.add')}</button>
      ) : null}
      <p className="hint">{t('crm.when.hint')}</p>
    </div>
  );
}

function RuleRow({ rule, fields, onChange, onRemove, readonly }: {
  rule: Rule;
  fields: CrmField[];
  onChange: (r: Rule) => void;
  onRemove: () => void;
  readonly?: boolean;
}) {
  const { t } = useI18n();
  const target = fields.find(f => f.key === rule.field);
  const isChoice = !!target && CHOICE_TYPES.includes(target.type);
  const needsList = rule.op === 'in' || rule.op === 'not_in';
  const needsValue = rule.op !== 'filled' && rule.op !== 'empty';

  const setOp = (op: Op) => {
    let value: Rule['value'] = null;
    if (op === 'in' || op === 'not_in') value = Array.isArray(rule.value) ? rule.value : rule.value ? [String(rule.value)] : [];
    else if (op === 'is' || op === 'is_not') value = Array.isArray(rule.value) ? rule.value[0] || '' : rule.value ?? '';
    onChange({ ...rule, op, value });
  };

  let control = null;
  if (needsValue && target) {
    const opts = target.options;
    if (isChoice && needsList) {
      const picked = new Set(Array.isArray(rule.value) ? rule.value.map(String) : []);
      control = (
        <div className={styles.checks}>
          {opts.map(o => (
            <label key={o.key}>
              <input
                type="checkbox" checked={picked.has(o.key)} disabled={readonly}
                onChange={e => {
                  const next = new Set(picked);
                  if (e.target.checked) next.add(o.key); else next.delete(o.key);
                  onChange({ ...rule, value: opts.map(x => x.key).filter(k => next.has(k)) });
                }}
              />
              <span>{optionPath(opts, o.key)}</span>
            </label>
          ))}
        </div>
      );
    } else if (isChoice) {
      control = (
        <Select
          value={String(rule.value ?? '')} disabled={readonly}
          onChange={v => onChange({ ...rule, value: v })}
          options={[{ value: '', label: '—' }, ...opts.map(o => ({ value: o.key, label: optionPath(opts, o.key) }))]}
        />
      );
    } else if (target.type === 'boolean') {
      control = (
        <Select
          value={String(rule.value ?? '')} disabled={readonly}
          onChange={v => onChange({ ...rule, value: v })}
          options={[{ value: 'yes', label: t('crm.yes') }, { value: 'no', label: t('crm.no') }]}
        />
      );
    } else {
      control = (
        <input
          value={Array.isArray(rule.value) ? rule.value.join(', ') : String(rule.value ?? '')}
          disabled={readonly} placeholder={needsList ? t('crm.when.list.ph') : ''}
          onChange={e => onChange({
            ...rule,
            value: needsList ? e.target.value.split(',').map(s => s.trim()).filter(Boolean) : e.target.value,
          })}
        />
      );
    }
  }

  return (
    <div className={styles.rule}>
      <Select
        value={rule.field} disabled={readonly}
        onChange={v => onChange({ field: v, op: 'filled', value: null })}
        options={fields.map(f => ({ value: f.key, label: f.label || f.key }))}
      />
      <Select
        value={rule.op} disabled={readonly}
        onChange={v => setOp(v as Op)}
        options={OPS.map(op => ({ value: op, label: t(`crm.op.${op}`) }))}
      />
      {control}
      {!readonly ? (
        <button type="button" className="act danger" aria-label={t('crm.remove')} title={t('crm.remove')} onClick={onRemove}>🗑</button>
      ) : null}
    </div>
  );
}
