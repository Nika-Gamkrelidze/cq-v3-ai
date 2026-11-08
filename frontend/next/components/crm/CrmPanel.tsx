'use client';
/* "CRM form" in Analyse a call — fill one of the workspace's CRM forms from this recording.
   =========================================================================================
   On demand only, like every other check here: a click fills the chosen form (the primary
   one unless the person picks another), the result is stored as a DRAFT on the recording,
   and a reviewer corrects and approves it below. Opening a recording from History shows the
   latest filled form per form, so a draft someone started yesterday is still there.

   Its own section rather than a fifth check inside "All at once": filling a form is a per-
   form decision with its own review step, and a one-click run that silently filled the primary
   form would hand the reviewer a form they did not ask for. */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { confirmDialog } from '@/components/ui/Modal';
import { Select } from '@/components/ui/Select';
import { toast } from '@/components/ui/Toast';
import type { SeekTarget } from '@/components/Workbench/seek';
import type { CrmResultRow, FormRow } from '@/lib/crmForm';
import {
  ApiError, apiGet, apiMessage, apiSend, apiUpload, downloadAuthed,
} from '@/lib/session';
import { useI18n } from '@/lib/useI18n';
import type { CrmApi } from './api';
import { ResultView } from './ResultView';

export function useCrmApi(scope: 'tenant' | 'admin', onUnauthorized?: () => void): CrmApi {
  return useMemo(() => {
    const guard = async <T,>(p: Promise<T>): Promise<T> => {
      try { return await p; } catch (e) {
        if (e instanceof ApiError && e.status === 401) onUnauthorized?.();
        throw e;
      }
    };
    return {
      get: <T,>(path: string) => guard(apiGet<T>(path, { scope })),
      send: <T,>(method: 'POST' | 'PUT' | 'PATCH' | 'DELETE', path: string, body?: unknown) =>
        guard(apiSend<T>(method, path, body, { scope })),
      upload: <T,>(path: string, fd: FormData) => guard(apiUpload<T>(path, fd, { scope })),
      download: (path: string, name: string) => guard(downloadAuthed(path, name, { scope })),
    };
  }, [scope, onUnauthorized]);
}

export function CrmPanel({ callId, scope, onSeek, callIndex, formsHref, epoch, active = true, onUnauthorized }: {
  callId: string | null;
  scope: 'tenant';
  onSeek: (target: SeekTarget) => void;
  callIndex: number;
  formsHref?: string | null;
  /** Changes when an operator picks another workspace: the form list is that workspace's. */
  epoch?: string;
  /** The section is on screen. The list is re-read each time it opens, so a form created or
      renamed in the CRM forms tab is offered here without reloading the page. */
  active?: boolean;
  onUnauthorized?: () => void;
}) {
  const { t } = useI18n();
  const api = useCrmApi(scope, onUnauthorized);
  const [forms, setForms] = useState<FormRow[] | null>(null);
  const [formKey, setFormKey] = useState('');
  /** `null` is NOT "none" — it is "this recording's filled forms could not be read". */
  const [results, setResults] = useState<CrmResultRow[] | null>(null);
  const [reload, setReload] = useState(0);
  const [loading, setLoading] = useState(false);
  const [running, setRunning] = useState(false);
  const [err, setErr] = useState('');
  /** The recording on screen NOW — read when a run finishes, not the one it started on. */
  const current = useRef(callId);
  current.current = callId;
  /** A review below has corrections typed into it that nobody has sent yet. A ref, not state:
      the only reader is a click handler, and it must see the value of the moment. */
  const dirty = useRef(false);
  const onDirty = useCallback((d: boolean) => { dirty.current = d; }, []);

  useEffect(() => {
    if (!active) return;
    let live = true;
    api.get<FormRow[]>('/crm/forms')
      .then(list => {
        if (!live) return;
        setForms(list);
        setFormKey(k => (list.some(f => f.form_key === k) ? k : (list.find(f => f.is_primary) || list[0])?.form_key || ''));
      })
      .catch(e => { if (live) { setForms([]); setErr(apiMessage(e, t)); } });
    return () => { live = false; };
  }, [api, t, epoch, active]);

  /** The filled forms this recording already has, or `null` when the read failed. Returning an
      empty list for a failure would say "no form filled yet" over a server that is merely down,
      under a button that charges an analysis unit to fill it again — and the draft a colleague
      approved yesterday would stay hidden on the older row. Same rule as `apiGetOrNull`. */
  const loadResults = useCallback(async (id: string): Promise<CrmResultRow[] | null> => {
    try {
      return await api.get<CrmResultRow[]>(`/recordings/${encodeURIComponent(id)}/crm`);
    } catch {
      return null;
    }
  }, [api]);

  useEffect(() => {
    setResults(null);
    setErr('');
    dirty.current = false;
    if (!callId) return;
    let live = true;
    setLoading(true);
    void loadResults(callId).then(rows => {
      if (!live) return;
      if (rows) setResults(rows);
      setLoading(false);
    });
    return () => { live = false; };
  }, [callId, loadResults, reload]);

  /** Everything below is about to be replaced by another row; ask before the typing goes. */
  const mayLeaveReview = async () =>
    !dirty.current || confirmDialog(t('crm.discard.confirm'), { ok: t('crm.discard') });

  const pickForm = async (next: string) => {
    if (next === formKey || !(await mayLeaveReview())) return;
    setFormKey(next);
  };

  const run = async () => {
    if (!callId || !formKey) return;
    if (!(await mayLeaveReview())) return;
    setRunning(true);
    setErr('');
    const forCall = callId;
    try {
      const row = await api.send<CrmResultRow>('POST', `/recordings/${encodeURIComponent(forCall)}/crm`, { form_key: formKey });
      // Dropped if the person has opened another recording meanwhile.
      if (forCall === current.current) setResults(prev => [row, ...(prev || []).filter(r => r.form_key !== row.form_key)]);
      toast(t('crm.run.done'), 'ok');
    } catch (e) {
      const m = apiMessage(e, t);
      setErr(m);
      toast(m, 'err');
    } finally {
      setRunning(false);
    }
  };

  const unread = !!callId && results === null && !loading;
  const shown = results?.find(r => r.form_key === formKey) || null;
  const replace = (row: CrmResultRow) => setResults(prev => (prev || []).map(r => (r.id === row.id ? row : r)));

  return (
    <div>
      <p className="hint wb-note">
        <span>{t('crm.run.note')}</span>
        {formsHref ? <> <a href={formsHref}>{t('crm.run.edit')}</a></> : null}
      </p>
      <div className="actions wb-run-row" style={{ alignItems: 'flex-end', gap: 10, flexWrap: 'wrap' }}>
        {forms && forms.length > 1 ? (
          <div style={{ minWidth: 220 }}>
            <label>{t('crm.run.form')}</label>
            <Select
              value={formKey} onChange={v => void pickForm(v)}
              options={forms.map(f => ({ value: f.form_key, label: `${f.name}${f.is_primary ? ' ★' : ''}` }))}
            />
          </div>
        ) : null}
        {/* Nothing is filled while the recording's own forms are unread: the label would read
            "Fill the CRM form" over a recording that already has one, and the click costs an
            analysis unit and buries the approved row under a fresh draft. */}
        <button type="button" className="primary wb-run" disabled={!callId || !formKey || running || unread} onClick={() => void run()}>
          {running ? <><span className="spinner" />{t('crm.run.running')}</> : shown ? t('crm.run.again') : t('crm.run.go')}
        </button>
      </div>
      {!callId ? <p className="hint">{t('wb.needsource')}</p> : null}
      {unread ? (
        <div className="msg err">
          {t('err.unavailable')}{' '}
          <button type="button" className="ghost" onClick={() => setReload(n => n + 1)}>{t('btn.retry')}</button>
        </div>
      ) : null}
      <div className="msg err wb-err" aria-live="polite">{err}</div>
      <div className="wb-result">
        {shown ? (
          <ResultView
            key={shown.id} result={shown.result} row={shown} api={api} editable
            onSaved={replace} onDirty={onDirty} onSeek={onSeek} callIndex={callIndex}
          />
        ) : null}
      </div>
    </div>
  );
}
