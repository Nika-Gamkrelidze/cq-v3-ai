'use client';
/* The options of a choice field — a tree, with what the AI needs to recognise each one.
   ======================================================================================
   A row per option in TREE order (each parent followed by its branch), editable in place;
   the details — key, parent, how it sounds in a call, signal words, values it implies, its
   text template — open under the row. Option lists run to dozens (request types) or hundreds
   (clinic directories), so there is a filter past 25 options and a "paste a list" that turns
   one label per line into options, nesting by indentation.

   Key edits go through `renameOption`, which carries the new key into every condition and
   implied value that named the old one. */

import { useState } from 'react';
import { showModal } from '@/components/ui/Modal';
import { Select } from '@/components/ui/Select';
import {
  blankOption, KEY_RE, keywordLines, keywordList, move, optionsFromLines, removeOption,
  renameOption, visibleOptions,
  type CrmDefinition, type CrmField, type CrmOption,
} from '@/lib/crmForm';
import { useI18n } from '@/lib/useI18n';
import styles from './crm.module.css';
import { ValueInput } from './inputs';

const FILTER_FROM = 25;

export function OptionsEditor({ defn, field, onDefn, readonly }: {
  defn: CrmDefinition;
  field: CrmField;
  onDefn: (d: CrmDefinition) => void;
  readonly?: boolean;
}) {
  const { t } = useI18n();
  const [open, setOpen] = useState<string | null>(null);
  const [filter, setFilter] = useState('');

  const setOptions = (options: CrmOption[]) =>
    onDefn({ ...defn, fields: defn.fields.map(f => (f.key === field.key ? { ...f, options } : f)) });
  const patch = (key: string, part: Partial<CrmOption>) =>
    setOptions(field.options.map(o => (o.key === key ? { ...o, ...part } : o)));

  const add = (parent: string | null) => {
    const o = blankOption(field, '', parent);
    setOptions([...field.options, o]);
    setOpen(o.key);
  };

  const paste = async () => {
    const text = await showModal(close => <PasteBody close={close} />, { maxWidth: '560px' });
    if (typeof text === 'string' && text.trim()) setOptions(optionsFromLines(field, text));
  };

  // Fields whose text template is taken from this field's chosen option.
  const templated = defn.fields.some(f => f.type === 'template' && f.template_from === field.key);
  // The open row is shown whatever the filter says — see `visibleOptions`: the option just
  // added is blank, so a filter that is doing its job is exactly what hides it.
  const rows = visibleOptions(field.options, filter, open);

  return (
    <div>
      {field.options.length > FILTER_FROM ? (
        <input placeholder={t('crm.opt.filter')} value={filter} onChange={e => setFilter(e.target.value)} style={{ marginBottom: 6 }} />
      ) : null}
      {!field.options.length ? <p className="hint">{t('crm.opt.none')}</p> : null}
      {rows.map(({ option: o, depth, index }) => (
        <div key={o.key}>
          <div className={styles.optRow} style={{ paddingLeft: depth * 18 }}>
            {depth ? <span className={styles.optTree}>└</span> : null}
            <input
              value={o.label} placeholder={t('crm.opt.label.ph')} disabled={readonly}
              aria-label={t('crm.opt.label')}
              onChange={e => patch(o.key, { label: e.target.value })}
            />
            <span className={styles.optKey} title={o.key}>{o.key}</span>
            {o.keywords.length ? <span className={styles.badge} title={o.keywords.join(', ')}>{o.keywords.length} ⚑</span> : null}
            <button type="button" className={`act ${styles.mini}`} title={t('crm.opt.details')} aria-label={t('crm.opt.details')}
              aria-expanded={open === o.key} onClick={() => setOpen(open === o.key ? null : o.key)}>
              {open === o.key ? '▾' : '▸'}
            </button>
            {!readonly ? (
              <>
                <button type="button" className={`act ${styles.mini}`} title={t('crm.opt.child')} aria-label={t('crm.opt.child')} onClick={() => add(o.key)}>＋</button>
                <button type="button" className={`act ${styles.mini}`} title={t('crm.up')} aria-label={t('crm.up')} disabled={index === 0}
                  onClick={() => setOptions(move(field.options, index, -1))}>↑</button>
                <button type="button" className={`act ${styles.mini}`} title={t('crm.down')} aria-label={t('crm.down')} disabled={index === field.options.length - 1}
                  onClick={() => setOptions(move(field.options, index, 1))}>↓</button>
                <button type="button" className={`act danger ${styles.mini}`} title={t('crm.remove')} aria-label={t('crm.remove')}
                  onClick={() => setOptions(removeOption(field.options, o.key))}>🗑</button>
              </>
            ) : null}
          </div>
          {open === o.key ? (
            <OptionDetail
              defn={defn} field={field} option={o} readonly={readonly} templated={templated}
              onPatch={part => patch(o.key, part)}
              onRename={to => { onDefn(renameOption(defn, field.key, o.key, to)); setOpen(to); }}
            />
          ) : null}
        </div>
      ))}
      {!readonly ? (
        <div className="inline wrap" style={{ marginTop: 8 }}>
          <button type="button" className="ghost" onClick={() => add(null)}>{t('crm.opt.add')}</button>
          <button type="button" className="ghost" onClick={() => void paste()}>{t('crm.opt.paste')}</button>
        </div>
      ) : null}
    </div>
  );
}

function OptionDetail({ defn, field, option, onPatch, onRename, readonly, templated }: {
  defn: CrmDefinition;
  field: CrmField;
  option: CrmOption;
  onPatch: (part: Partial<CrmOption>) => void;
  onRename: (key: string) => void;
  readonly?: boolean;
  templated: boolean;
}) {
  const { t } = useI18n();
  const [key, setKey] = useState(option.key);
  const keyOk = KEY_RE.test(key) && (key === option.key || !field.options.some(o => o.key === key));
  // A parent may be anything but the option itself and its own branch.
  const branch = new Set<string>([option.key]);
  let grew = true;
  while (grew) {
    grew = false;
    for (const o of field.options) if (o.parent && branch.has(o.parent) && !branch.has(o.key)) { branch.add(o.key); grew = true; }
  }
  const targets = defn.fields.filter(f => f.key !== field.key && f.type !== 'template');
  const implies = Object.entries(option.implies || {});

  return (
    <div className={styles.optDetail}>
      <div className="row">
        <div>
          <label>{t('crm.key')}</label>
          <input
            value={key} disabled={readonly} className={keyOk ? '' : styles.err}
            onChange={e => setKey(e.target.value.trim())}
            onBlur={() => { if (keyOk && key !== option.key) onRename(key); else setKey(option.key); }}
          />
          {!keyOk ? <div className={`hint ${styles.err}`}>{t('crm.key.bad')}</div> : null}
        </div>
        <div>
          <label>{t('crm.opt.parent')}</label>
          <Select
            value={option.parent || ''} disabled={readonly}
            onChange={v => onPatch({ parent: v || null })}
            options={[{ value: '', label: t('crm.opt.top') },
              ...field.options.filter(o => !branch.has(o.key)).map(o => ({ value: o.key, label: o.label || o.key }))]}
          />
        </div>
      </div>
      <label>{t('crm.opt.desc')}</label>
      <textarea rows={2} value={option.description} disabled={readonly} placeholder={t('crm.opt.desc.ph')}
        onChange={e => onPatch({ description: e.target.value })} />
      <label>{t('crm.opt.keywords')}</label>
      <textarea rows={2} value={option.keywords.join('\n')} disabled={readonly} placeholder={t('crm.opt.keywords.ph')}
        onChange={e => onPatch({ keywords: keywordLines(e.target.value) })}
        onBlur={e => onPatch({ keywords: keywordList(e.target.value) })} />
      <p className="hint">{t('crm.opt.keywords.hint')}</p>

      <label>{t('crm.opt.implies')}</label>
      {implies.map(([tk, tv], i) => {
        const target = defn.fields.find(f => f.key === tk);
        return (
          <div className={styles.rule} key={i}>
            <Select
              value={tk} disabled={readonly}
              onChange={v => {
                const next = Object.fromEntries(implies.map(([k, val], j) => (j === i ? [v, null] : [k, val])));
                onPatch({ implies: next });
              }}
              options={targets.map(f => ({ value: f.key, label: f.label || f.key }))}
            />
            {target ? (
              <ValueInput
                field={target} value={tv} disabled={readonly}
                onChange={v => onPatch({ implies: { ...option.implies, [tk]: v } })}
              />
            ) : <span />}
            {!readonly ? (
              <button type="button" className="act danger" aria-label={t('crm.remove')} title={t('crm.remove')}
                onClick={() => { const next = { ...option.implies }; delete next[tk]; onPatch({ implies: next }); }}>🗑</button>
            ) : null}
          </div>
        );
      })}
      {!readonly && targets.some(f => !(f.key in (option.implies || {}))) ? (
        <button type="button" className="ghost" onClick={() => {
          const free = targets.find(f => !(f.key in (option.implies || {})));
          if (free) onPatch({ implies: { ...option.implies, [free.key]: null } });
        }}>{t('crm.opt.implies.add')}</button>
      ) : null}
      <p className="hint">{t('crm.opt.implies.hint')}</p>

      {templated || option.template ? (
        <>
          <label>{t('crm.opt.template')}</label>
          <textarea rows={2} value={option.template} disabled={readonly} placeholder={t('crm.tpl.ph')}
            onChange={e => onPatch({ template: e.target.value })} />
          <p className="hint">{t('crm.opt.template.hint')}</p>
        </>
      ) : null}
    </div>
  );
}

function PasteBody({ close }: { close: (v?: unknown) => void }) {
  const { t } = useI18n();
  const [text, setText] = useState('');
  return (
    <div>
      <h3 style={{ marginTop: 0 }}>{t('crm.opt.paste')}</h3>
      <p className="hint">{t('crm.opt.paste.hint')}</p>
      <textarea rows={10} value={text} onChange={e => setText(e.target.value)} autoFocus />
      <div className="actions" style={{ marginTop: 12 }}>
        <button type="button" className="ghost" onClick={() => close()}>{t('btn.cancel')}</button>
        <button type="button" className="primary" disabled={!text.trim()} onClick={() => close(text)}>{t('crm.opt.paste.add')}</button>
      </div>
    </div>
  );
}
