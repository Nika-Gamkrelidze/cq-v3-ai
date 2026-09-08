'use client';
import { useRef, useState, type DragEvent } from 'react';

/* The file drop target, shared by all three upload panels on this page.
   =====================================================================
   One component rather than three copies of the same six listeners — which is what the legacy
   page has, once per panel, because it had no way to share them.

   Two details are carried over rather than tidied:

     * The input is CLEARED after every pick (`e.target.value = ''`), which the legacy page does
       in the converter only. Without it, choosing the same file twice in a row fires no
       `change` at all, so re-adding a file you just removed from the converter's list silently
       does nothing. It applies to all three panels here because the File now lives in the
       caller's state rather than in the input, so clearing costs nothing — and it fixes the
       same dead click after a recording has replaced the picked file.
     * `dragover` AND `dragenter` both `preventDefault()`. Only `dragover` is strictly required
       to make an element a drop target, but without `dragenter` the browser paints the
       "not allowed" cursor for the first frame — which reads as a refusal.

   The accept list is the legacy one verbatim: `audio/*,video/*` covers what browsers label
   correctly, and the extension list catches the containers they routinely hand over with an
   empty `type` (a `.m4a` from an iPhone, an `.amr` from a PBX). */

const ACCEPT = 'audio/*,video/*,.m4a,.aac,.flac,.opus,.wma,.amr,.aiff,.oga,.3gp';

export interface DropzoneProps {
  id: string;
  /** The idle line — `t('drop.title')` or the converter's own. */
  title: string;
  sub: string;
  /** The chosen file's name, shown in place of `title`. Single-file panels only; the
      converter renders its queue as a table underneath instead. */
  fileName?: string;
  multiple?: boolean;
  onPick: (files: File[]) => void;
}

export function Dropzone({ id, title, sub, fileName, multiple, onPick }: DropzoneProps) {
  const [drag, setDrag] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const over = (e: DragEvent) => { e.preventDefault(); setDrag(true); };
  const leave = (e: DragEvent) => { e.preventDefault(); setDrag(false); };
  const drop = (e: DragEvent) => {
    e.preventDefault();
    setDrag(false);
    const list = e.dataTransfer?.files;
    if (!list || !list.length) return;
    onPick(multiple ? Array.from(list) : [list[0]]);
  };

  return (
    <div
      className={drag ? 'drop drag' : 'drop'}
      onDragOver={over}
      onDragEnter={over}
      onDragLeave={leave}
      onDrop={drop}
    >
      <input
        ref={inputRef}
        id={id}
        type="file"
        multiple={multiple}
        accept={ACCEPT}
        onChange={e => {
          const picked = Array.from(e.target.files || []);
          e.target.value = '';           // so picking the same file again still fires
          if (picked.length) onPick(picked);
        }}
      />
      <div className="drop-title">
        {fileName ? <span className="drop-file">{fileName}</span> : title}
      </div>
      <div className="drop-sub">{sub}</div>
    </div>
  );
}
