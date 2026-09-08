'use client';
/* Rubric import: upload a scoring-standard file (a scorecard spreadsheet, a policy DOCX…), the
   AI maps it to a dimensions DRAFT that lands in the editor — nothing is saved until the human
   presses Save, so the existing version/audit flow holds.

   WHY XMLHttpRequest and not fetch: this single request has two long phases — the upload, and
   then minutes of model work — and the user has to see both. `xhr.upload.onprogress` reports
   real uploaded bytes; `xhr.onprogress` exposes the response body as it grows, so the server's
   SSE frames can be read while they arrive. fetch() cannot report upload progress at all, and
   EventSource is GET-only so it cannot carry the file. One request, both halves. All of that
   lives in `lib/xhrStream.ts`, including the part that decides ONCE, off the Content-Type at
   readyState 2, whether the answer is a stream or a plain JSON refusal. */

import { useCallback, useEffect, useRef } from 'react';
import { toast } from '@/components/ui/Toast';
import { apiMessage, API, scopedHeaders } from '@/lib/session';
import type { SseData } from '@/lib/sse';
import { StreamError, xhrStream, type XhrStream } from '@/lib/xhrStream';
import { SCOPE, type T } from './ctx';

export interface Draft {
  dimensions?: { name?: string; weight?: number; description?: string; guidance?: string }[];
  rubric?: string;
}

export interface Stage { labelKey: string; pct: number | null }

const ACCEPT = '.pdf,.docx,.xlsx,.xlsm,.csv,.txt,.md';

export function RubricImport({ t, onLoaded, onMessage, onStage, funnel }: {
  t: T;
  onLoaded: (d: Draft) => void;
  onMessage: (m: { text: string; kind: '' | 'ok' | 'err' }) => void;
  /** The progress bar belongs BELOW the button row, not inside it, so the stage is reported to
      the page and the page renders `<ImportBar>` where the legacy `#scProg` lived. */
  onStage: (s: Stage | null) => void;
  funnel: (e: unknown) => void;
}) {
  const fileRef = useRef<HTMLInputElement>(null);
  const runRef = useRef<XhrStream<Draft> | null>(null);
  const setStage = onStage;

  // Leaving the page: drop the request so the server sees the disconnect on its next ping and
  // cancels the model call, instead of paying for a draft nobody will read.
  useEffect(() => {
    const drop = () => runRef.current?.abort();
    window.addEventListener('pagehide', drop);
    return () => { window.removeEventListener('pagehide', drop); drop(); };
  }, []);

  const run = useCallback(async (file: File) => {
    onMessage({ text: t('sc.import.loading'), kind: '' });
    const fd = new FormData();
    fd.append('file', file);

    const stream = xhrStream<Draft>({
      url: `${API}/scoring/import?stream=1`,
      body: fd,
      headers: scopedHeaders(SCOPE),
      // `draft` is the terminal state — the percentage never is. A real Georgian scorecard
      // finishes near 80% because the token estimate is deliberately conservative and the
      // server clamps at 99, so the arriving draft is what says "done", not the number.
      terminal: ['draft'],
      onUploadProgress: pct => setStage({ labelKey: 'sc.import.stage.upload', pct }),
      onUploadEnd: () => setStage({ labelKey: 'sc.import.stage.queued', pct: null }),
      onEvent: (name, d: SseData) => {
        if (name === 'stage') {
          setStage(d.stage === 'analyzing'
            ? { labelKey: 'sc.import.stage.analyzing', pct: 0 }
            : { labelKey: 'sc.import.stage.extracting', pct: null });
        } else if (name === 'progress') {
          setStage({ labelKey: 'sc.import.stage.analyzing', pct: Number(d.pct) || 0 });
        }
      },
    });
    runRef.current = stream;

    try {
      const { data } = await stream.result;
      onLoaded({
        dimensions: Array.isArray(data?.dimensions) ? data.dimensions : [],
        rubric: data?.rubric,
      });
      onMessage({ text: t('sc.import.loaded'), kind: 'ok' });
      toast(t('sc.import.loaded'), 'ok');
    } catch (e) {
      if (e instanceof DOMException && e.name === 'AbortError') {
        // A cancel is a quiet line, never a toast: somebody pressed the button.
        onMessage({ text: t('sc.import.cancelled'), kind: '' });
        return;
      }
      funnel(e);
      const netFail = e instanceof StreamError && e.code === 'network';
      onMessage({
        text: netFail ? t('sc.import.netfail') : (apiMessage(e, t) || t('sc.import.fail')),
        kind: 'err',
      });
      toast(t('sc.import.fail'), 'err');
    } finally {
      if (runRef.current === stream) runRef.current = null;
      setStage(null);
    }
  }, [t, onLoaded, onMessage, setStage, funnel]);

  return (
    <>
      <button type="button" className="ghost" onClick={() => fileRef.current?.click()}>{t('sc.import')}</button>
      <input
        type="file" ref={fileRef} accept={ACCEPT} className="hidden"
        onChange={e => {
          const file = e.target.files?.[0];
          e.target.value = '';                    // cleared up front: every exit path is covered
          if (!file) return;
          runRef.current?.abort();                // never two imports racing for one editor
          void run(file);
        }}
      />
    </>
  );
}

export function ImportBar({ t, stage }: { t: T; stage: Stage }) {
  /* Every number on this bar has a real denominator behind it. `pct === null` means the stage
     has none (extraction is one opaque pypdf/openpyxl call), so it draws the indeterminate
     stripe and shows NO figure rather than a number we made up. */
  const v = stage.pct == null ? null : Math.max(0, Math.min(100, Math.round(stage.pct)));
  return (
    <div className="imp-prog">
      <div className="imp-prog-head">
        <span aria-live="polite">{t(stage.labelKey)}</span>
        <span className="imp-prog-pct" aria-hidden="true">{v == null ? '' : `${v}%`}</span>
      </div>
      <div
        className={`sc-bar${v == null ? ' indet' : ''}`}
        role="progressbar" aria-valuemin={0} aria-valuemax={100}
        aria-valuenow={v ?? undefined}
      >
        <span style={{ width: v == null ? undefined : `${v}%` }} />
      </div>
    </div>
  );
}
