'use client';
import { useState, type ReactNode } from 'react';
import { Recorder } from '@/components/Recorder';
import { toast } from '@/components/ui/Toast';
import { apiMessage, apiUpload } from '@/lib/session';
import { useI18n } from '@/lib/useI18n';
import { Dropzone } from './Dropzone';
import type { Sentiment } from '@/lib/aiShapes';

/* Upload or record one clip, POST it, render the answer.
   =====================================================
   The shape both signed-in tabs share — transcription and the sentiment read. The legacy page
   writes it out twice because it had no way to share it; the two panels differ only in their
   heading, their route and what they do with the response, so those are the props.

   `scope: 'user'` on the upload, and only there. See the note at the top of `TtsPanel` for why
   this page must never reach for the tab's admin or tenant token: `/transcribe` and
   `/sentiment` both spend an allowance and both file a row against whoever asked, and the
   public surface has always run an operator's request as a guest.

   The microphone drops its clip into the same `File` the dropzone produces, so there is one
   upload path rather than two — exactly what the legacy version achieved by writing the
   recording back into the file input. */

/** `/transcribe` and `/sentiment` answer with the same envelope. */
export interface ClipResult {
  id?: string;
  language?: string | null;
  transcript?: string | null;
  sentiment?: Sentiment | null;
}

export interface AudioUploadPanelProps {
  /** DOM id prefix — the file input needs a stable one and the two panels coexist. */
  idPrefix: string;
  headingKey: string;
  /** The primary button, e.g. `btn.transcribe` or `sn.run`. */
  runKey: string;
  /** Toast on success. */
  doneKey: string;
  path: '/transcribe' | '/sentiment';
  children: (result: ClipResult) => ReactNode;
  /** A clip was processed: the allowance banner has to count down. */
  onSpent: () => void;
}

export function AudioUploadPanel({
  idPrefix, headingKey, runKey, doneKey, path, children, onSpent,
}: AudioUploadPanelProps) {
  const { t } = useI18n();
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [result, setResult] = useState<ClipResult | null>(null);

  const run = async () => {
    setErr('');
    setResult(null);
    if (!file) { setErr(t('stt.nofile')); return; }
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append('file', file);
      setResult(await apiUpload<ClipResult>(path, fd, { scope: 'user' }));
      toast(t(doneKey), 'ok');
    } catch (e) {
      const msg = apiMessage(e, t);
      setErr(msg);
      toast(msg, 'err');
    } finally {
      setBusy(false);
      onSpent();
    }
  };

  return (
    <>
      <div className="card">
        <h3>{t(headingKey)}</h3>
        <label htmlFor={`${idPrefix}File`}>{t('f.audiofile')}</label>
        <Dropzone
          id={`${idPrefix}File`}
          title={t('drop.title')}
          sub={t('drop.sub_stt')}
          fileName={file?.name}
          onPick={files => { setFile(files[0] || null); }}
        />
        <div className="rec-row">
          <span className="hint">{t('rec.or')}</span>
          <Recorder onReady={setFile} disabled={busy} />
        </div>
        <div className="actions">
          <button className="primary" type="button" disabled={busy} onClick={() => void run()}>
            {busy ? <><span className="spinner" />{t(runKey)}…</> : t(runKey)}
          </button>
        </div>
        <div className="err">{err}</div>
      </div>
      {result ? children(result) : null}
    </>
  );
}
