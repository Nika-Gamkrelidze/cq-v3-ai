'use client';
/* The source card: what is being analysed, and the line it collapses to once it is loaded.
   ========================================================================================
   Two ways in, and they are not the same feature: an audio recording gets a player, a
   timeline and voice tone, where a pasted transcript gets none of those and says so before
   the person commits to typing into it.

   The card COLLAPSES after a successful load rather than staying open. What it holds after
   that is one line — the filename, the length, the language — because the panel below is the
   point and a 200px upload target above it is not. */

import type { ReactNode } from 'react';
import { useState } from 'react';
import { bytes } from '@/lib/format';
import type { T } from './strings';

/* Everything Scribe accepts. The bare `audio/*,video/*` pair is not enough on its own: macOS
   reports .m4a and .opus with no MIME type at all, and the picker then greys them out. */
export const ACCEPT = 'audio/*,video/*,.m4a,.aac,.flac,.opus,.wma,.amr,.aiff,.oga,.3gp';

export interface SourceCardProps {
  t: T;
  mode: 'audio' | 'text';
  onMode: (mode: 'audio' | 'text') => void;
  files: File[];
  onAdd: (files: FileList | File[] | null) => void;
  onRemove: (index: number) => void;
  paste: string;
  onPaste: (value: string) => void;
  /** The Summarise tab is open: several related calls, not one recording. */
  multi: boolean;
  /** An upload is in flight — the Cancel button is live and Go is not. */
  busy: boolean;
  /** A non-streamed POST is in flight (the pasted-transcript path). */
  posting: boolean;
  onGo: () => void;
  onCancel: () => void;
  error: { text: string; isError: boolean };
  progress: ReactNode;
  /** The collapsed "Transcription" section — what the recording will be transcribed WITH.

      A slot rather than a component of its own so it lives inside the audio block and
      disappears with it: a pasted transcript never goes near speech-to-text, and a panel
      offering to pick its audio format would be offering a setting with no effect. */
  transcription?: ReactNode;
}

export function SourceCard(p: SourceCardProps) {
  const { t } = p;
  const [dragging, setDragging] = useState(false);

  const goLabel = p.mode === 'text'
    ? t('wb.upload.text')
    : (p.files.length > 1 || p.multi) ? t('wb.upload.sum') : t('wb.upload');

  return (
    <section className="card wb-src">
      <div className="wb-src-head">
        <h3>{t('wb.src.title')}</h3>
        <div className="subtabs wb-modes" role="tablist">
          {(['audio', 'text'] as const).map(m => (
            <button
              key={m} type="button" role="tab"
              className={`subtab${p.mode === m ? ' active' : ''}`}
              aria-selected={p.mode === m}
              onClick={() => p.onMode(m)}
            >
              {t(m === 'audio' ? 'wb.src.audio' : 'wb.src.paste')}
            </button>
          ))}
        </div>
      </div>

      <div className={p.mode === 'audio' ? 'wb-mode-audio' : 'wb-mode-audio hidden'}>
        <div
          className={`drop wb-drop${dragging ? ' drag' : ''}`}
          onDragOver={e => { e.preventDefault(); setDragging(true); }}
          onDragEnter={e => { e.preventDefault(); setDragging(true); }}
          onDragLeave={e => { e.preventDefault(); setDragging(false); }}
          onDrop={e => { e.preventDefault(); setDragging(false); p.onAdd(e.dataTransfer?.files ?? null); }}
        >
          <input
            type="file" accept={ACCEPT} multiple aria-label={t('f.audiofile')}
            // Cleared after every pick so choosing the SAME file twice still fires `change`.
            onChange={e => { p.onAdd(e.target.files); e.target.value = ''; }}
          />
          <div className="drop-title">
            {p.files.length === 1
              ? <span className="drop-file">{p.files[0].name}</span>
              : p.files.length > 1
                ? <span className="drop-file">{t('wb.files.n', { n: p.files.length })}</span>
                : t('drop.title')}
          </div>
          <div className="drop-sub wb-drop-sub">{t(p.multi ? 'wb.drop.sub_multi' : 'wb.drop.sub')}</div>
        </div>

        {p.files.length > 1 && (
          <ul className="wb-files">
            {p.files.map((f, i) => (
              <li className="wb-file" key={`${f.name}-${i}`}>
                <span className="wb-file-num">{i + 1}</span>
                <span className="wb-file-name">{f.name}</span>
                <span className="wb-file-size">{bytes(f.size)}</span>
                <button
                  type="button" className="act" title={t('wb.file.remove')}
                  aria-label={t('wb.file.remove')} onClick={() => p.onRemove(i)}
                >✕</button>
              </li>
            ))}
          </ul>
        )}

        {p.transcription}
      </div>

      <div className={p.mode === 'text' ? 'wb-mode-text' : 'wb-mode-text hidden'}>
        <textarea
          className="wb-paste" value={p.paste} placeholder={t('wb.paste.ph')}
          aria-label={t('wb.src.paste')} onChange={e => p.onPaste(e.target.value)}
        />
        <div className="hint">{t('wb.paste.hint')}</div>
      </div>

      <div className="actions">
        <button type="button" className="primary wb-go" disabled={p.busy || p.posting} onClick={p.onGo}>
          {p.posting ? <><span className="spinner" />{t('wb.running')}</> : goLabel}
        </button>
        {p.busy && (
          <button type="button" className="ghost wb-cancel" onClick={p.onCancel}>{t('btn.cancel')}</button>
        )}
      </div>

      {p.progress}

      <div className={`msg wb-src-err${p.error.isError ? ' err' : ''}`} aria-live="polite">{p.error.text}</div>
    </section>
  );
}

/** The collapsed line: what is loaded, and the way back to the picker. */
export function DoneCard({
  t, summary, note, onChange, progress,
}: {
  t: T;
  /** The line itself — a name in bold, then the muted metadata. */
  summary: ReactNode;
  note: string;
  onChange: () => void;
  progress: ReactNode;
}) {
  return (
    <section className="card wb-done">
      <div className="wb-done-row">
        <span className="wb-done-icon" aria-hidden="true">🎧</span>
        <span className="wb-done-text">{summary}</span>
        <button type="button" className="ghost wb-change" onClick={onChange}>{t('wb.change')}</button>
      </div>
      {note ? <div className="msg wb-done-note">{note}</div> : null}
      {progress}
    </section>
  );
}

/** Upload, then the server's own stages. Indeterminate when there is no figure to show —
    a bar frozen at a number nobody is updating reads as a hang. */
export function Progress({ label, value }: { label: string; value: number | null }) {
  return (
    <div className="imp-prog wb-prog">
      <div className="imp-prog-head">
        <span className="wb-prog-label" aria-live="polite">{label}</span>
        <span className="imp-prog-pct" aria-hidden="true">{value == null ? '' : `${value}%`}</span>
      </div>
      <div
        className={`sc-bar${value == null ? ' indet' : ''}`}
        role="progressbar" aria-valuemin={0} aria-valuemax={100}
        aria-valuenow={value == null ? undefined : value}
      >
        <span style={{ width: value == null ? undefined : `${value}%` }} />
      </div>
    </div>
  );
}
