'use client';
/* A filled CRM form — what the AI put in each field, why, and the reviewer's corrections.
   =====================================================================================
   Every field of the form is listed in form order with its status said out loud: filled (with
   the quote it came from — click it and the player jumps there), not mentioned in the call,
   an answer that did not fit the field (shown raw, so nothing the model said is hidden),
   "filled by your CRM" for external fields, and — collapsed by default — the fields whose
   condition did not hold. A REQUIRED field left empty is marked, because that is the line a
   reviewer must not miss.

   Review is a draft until approved. The reviewer's values are stored beside the model's, never
   over them (`crm_results.final`), so "what did the AI say" survives every correction — the
   same rule the scorecard's manual edits follow. */

import { useEffect, useMemo, useState } from 'react';
import { toast } from '@/components/ui/Toast';
import { seekProps, type SeekTarget } from '@/components/Workbench/seek';
import { copyText } from '@/lib/clipboard';
import {
  displayValue, draftEdits, finalValue, recordText, reviewValues,
  type CrmResult, type CrmResultRow, type ResultField,
} from '@/lib/crmForm';
import { dateTime } from '@/lib/format';
import { apiMessage } from '@/lib/session';
import { useI18n } from '@/lib/useI18n';
import { badFieldsOf, downloadJson, type CrmApi } from './api';
import styles from './crm.module.css';
import { ValueInput } from './inputs';

export function ResultView({ result, row, api, editable, onSaved, onDirty, onSeek, callIndex }: {
  result: CrmResult;
  /** The stored row — absent for the editor's test run, which stays unstored. */
  row?: CrmResultRow | null;
  api?: CrmApi;
  editable?: boolean;
  onSaved?: (row: CrmResultRow) => void;
  /** Corrections are typed but not sent yet. The row on screen is chosen one level up, and
      changing it unmounts this view with the draft in it, so the owner of that choice is the
      one that has to ask before it throws the work away. */
  onDirty?: (dirty: boolean) => void;
  onSeek?: (target: SeekTarget) => void;
  callIndex?: number;
}) {
  const { t } = useI18n();
  const yesNo: [string, string] = [t('crm.yes'), t('crm.no')];
  const [showAll, setShowAll] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Record<string, unknown>>({});
  const [bad, setBad] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);

  const view = useMemo(() => row || ({ final: null, result } as Pick<CrmResultRow, 'final' | 'result'>), [row, result]);
  const current = (f: ResultField) => (f.key in draft ? draft[f.key] : finalValue(view, f));
  const hidden = result.fields.filter(f => f.status === 'not_applicable').length;
  const fields = result.fields.filter(f => showAll || f.status !== 'not_applicable');
  const missing = result.fields.filter(f => f.required && f.status !== 'not_applicable'
    && f.status !== 'external' && isEmpty(current(f))).length;

  const dirty = editing && draftEdits(view, draft).length > 0;
  // The cleanup matters as much as the report: this view is unmounted by the very changes the
  // parent is guarding against, and a `true` left behind would warn about the next row's draft.
  useEffect(() => {
    onDirty?.(dirty);
    return () => onDirty?.(false);
  }, [dirty, onDirty]);

  const startEdit = () => { setDraft({}); setBad([]); setEditing(true); };

  const save = async (status: 'draft' | 'approved') => {
    if (!row || !api) return;
    setBusy(true);
    setBad([]);
    try {
      const values = reviewValues(row, draft);
      const saved = await api.send<CrmResultRow>('PATCH', `/crm/results/${encodeURIComponent(row.id)}`, { values, status });
      onSaved?.(saved);
      setEditing(false);
      setDraft({});
      toast(t(status === 'approved' ? 'crm.res.approved' : 'crm.res.saved'), 'ok');
    } catch (e) {
      setBad(badFieldsOf(e));
      toast(apiMessage(e, t), 'err');
    } finally {
      setBusy(false);
    }
  };

  // Called straight from the click: copyText must start inside the gesture (lib/clipboard.ts).
  const copy = async () => {
    const ok = await copyText(recordText(view, yesNo));
    toast(t(ok ? 'crm.res.copied' : 'crm.res.copyfail'), ok ? 'ok' : 'err');
  };

  const download = async () => {
    if (!row || !api) return;
    try {
      const rec = await api.get<unknown>(`/crm/results/${encodeURIComponent(row.id)}/record`);
      downloadJson(`${row.form_key}-${(row.job_id || row.id).slice(0, 8)}.json`, rec);
    } catch (e) {
      toast(apiMessage(e, t), 'err');
    }
  };

  return (
    <div>
      <div className={styles.head}>
        <div>
          <div className={styles.label}>{labelText(result, view) || t('crm.res.nolabel')}</div>
          <div className="hint">
            {result.form_name}{result.form_version ? ` · v${result.form_version}` : ''}
            {row ? ` · ${dateTime(row.created_at)}` : ''}
            {row?.reviewed_by ? ` · ${t('crm.res.reviewedby', { who: row.reviewed_by })}` : ''}
          </div>
        </div>
        <div className="inline wrap">
          {row ? <span className={`pill ${row.status === 'approved' ? 'done' : 'pending'}`}>{t(`crm.res.status.${row.status}`)}</span> : null}
          {dirty ? <span className="pill pending">{t('crm.unsaved')}</span> : null}
          {missing ? <span className="pill bad">{t('crm.res.missing', { n: missing })}</span> : null}
        </div>
      </div>

      <div className={styles.grid}>
        {fields.map(f => {
          const value = current(f);
          const edited = !!row?.final && f.key in row.final && !sameValue(row.final[f.key], f.value);
          const isMissing = f.required && f.status !== 'not_applicable' && f.status !== 'external' && isEmpty(value);
          return (
            <FieldRow
              key={f.key} f={f} value={value} edited={edited || f.key in draft} missing={isMissing}
              invalid={bad.includes(f.key)} editing={editing} yesNo={yesNo}
              onChange={v => setDraft(d => ({ ...d, [f.key]: v }))}
              onSeek={onSeek} callIndex={callIndex}
            />
          );
        })}
      </div>

      <div className="inline wrap" style={{ marginTop: 10, justifyContent: 'space-between' }}>
        {hidden ? (
          <button type="button" className="ghost" onClick={() => setShowAll(s => !s)}>
            {showAll ? t('crm.res.hidena') : t('crm.res.showna', { n: hidden })}
          </button>
        ) : <span />}
        <div className="inline wrap">
          <button type="button" className="ghost" onClick={() => void copy()}>{t('crm.res.copy')}</button>
          {row && api ? <button type="button" className="ghost" onClick={() => void download()}>{t('crm.res.json')}</button> : null}
          {row && api && editable && !editing ? (
            <button type="button" className="primary" onClick={startEdit}>{t('crm.res.review')}</button>
          ) : null}
          {editing ? (
            <>
              <button type="button" className="ghost" disabled={busy} onClick={() => { setEditing(false); setDraft({}); setBad([]); }}>{t('btn.cancel')}</button>
              <button type="button" className="ghost" disabled={busy} onClick={() => void save('draft')}>{t('crm.res.savedraft')}</button>
              <button type="button" className="primary" disabled={busy} onClick={() => void save('approved')}>
                {busy ? <span className="spinner" /> : null}{t('crm.res.approve')}
              </button>
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function FieldRow({ f, value, edited, missing, invalid, editing, yesNo, onChange, onSeek, callIndex }: {
  f: ResultField;
  value: unknown;
  edited: boolean;
  missing: boolean;
  invalid: boolean;
  editing: boolean;
  yesNo: [string, string];
  onChange: (v: unknown) => void;
  onSeek?: (target: SeekTarget) => void;
  callIndex?: number;
}) {
  const { t } = useI18n();
  const na = f.status === 'not_applicable';
  const shown = displayValue(f, value, yesNo);
  const canEdit = editing && !na && f.status !== 'external';
  const cls = `${missing || invalid ? styles.missing : ''} ${na ? styles.na : ''}`;
  return (
    <>
      <div className={`${styles.k} ${cls}`}>
        {f.label}{f.required ? ' *' : ''}
      </div>
      <div className={`${styles.v} ${cls}`}>
        {canEdit ? (
          <ValueInput field={f} value={value} onChange={onChange} />
        ) : shown ? (
          <span className={styles.value}>{shown}</span>
        ) : (
          <span className={styles.empty}>
            {na ? t('crm.res.na') : f.status === 'external' ? t('crm.res.external')
              : f.status === 'invalid' ? t('crm.res.invalid') : t('crm.res.notsaid')}
          </span>
        )}
        {edited ? <span className={styles.edited}>{t('crm.res.edited')}</span> : null}
        {!canEdit && (f.raw || f.confidence || f.evidence.length || f.source === 'derived' || missing) ? (
          <div className={styles.meta}>
            {missing ? <span className="err">{t('crm.res.required')}</span> : null}
            {f.raw ? <span title={t('crm.res.raw.tip')}>{t('crm.res.raw', { raw: f.raw })}</span> : null}
            {f.source === 'derived' ? <span>{t('crm.res.derived')}</span> : null}
            {f.confidence && f.status === 'filled' && f.source === 'ai' ? (
              <span className={styles.conf} title={t(`crm.res.conf.${f.confidence}`)}>
                {[0, 1, 2].map(i => <i key={i} className={i < ({ high: 3, medium: 2, low: 1 }[f.confidence!]) ? styles.on : ''} />)}
              </span>
            ) : null}
            {f.status !== 'not_applicable' ? f.evidence.map((e, i) => (
              onSeek ? (
                <span key={i} className={styles.quote}
                  {...seekProps(onSeek, { start: e.start, seg: e.segments[0] ?? null, call: callIndex ?? null }, t('crm.res.seek'))}>
                  “{e.quote}”
                </span>
              ) : <span key={i} className={styles.quote}>“{e.quote}”</span>
            )) : null}
          </div>
        ) : null}
      </div>
    </>
  );
}

function isEmpty(v: unknown): boolean {
  return v === null || v === undefined || v === '' || (Array.isArray(v) && !v.length);
}

function sameValue(a: unknown, b: unknown): boolean {
  return JSON.stringify(a ?? null) === JSON.stringify(b ?? null);
}

function labelText(result: CrmResult, view: Pick<CrmResultRow, 'final'>): string {
  const lf = result.label_field;
  const f = lf ? result.fields.find(x => x.key === lf) : null;
  if (!f) return result.label?.text || '';
  return displayValue(f, view.final && lf! in view.final ? view.final[lf!] : f.value);
}
