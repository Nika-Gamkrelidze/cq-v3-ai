'use client';
import { useCallback, useEffect, useRef, useState } from 'react';
import { duration as clock } from '@/lib/format';
import { useI18n } from '@/lib/useI18n';

/* The microphone button, ported from `brand.js`'s `attachRecorder`.
   ================================================================
   Records a clip and hands it over as a `File`, so the page's ordinary upload path — the same
   one a dropped file takes — runs unchanged. The legacy version reached into the page's
   `<input type=file>` and wrote the File into it with a `DataTransfer`, because the page's
   analyze button read the input directly; a React page holds the File in state instead, so
   `onReady` is the whole interface and the hidden input is gone.

   It renders a FRAGMENT — the button and its status line, nothing around them. The legacy
   markup puts both inside the page's own `.rec-row` next to an "or" hint, and that row belongs
   to the page, not here.

   getUserMedia needs a SECURE CONTEXT (https, or http://localhost). Production is currently
   plain HTTP over an IP address, so the unsupported branch is not theoretical: it is what an
   operator sees today, and it has to say why rather than being a button that does nothing. */

type Phase = 'idle' | 'recording' | 'done' | 'denied' | 'unsupported';

/* First supported wins. Opus in WebM is what Chrome and Firefox produce; Safari only offers
   MP4/AAC. Scribe accepts all of them, so the point of the list is only to hand MediaRecorder
   a container it will not reject. */
const MIME_CANDIDATES = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg'];

export function Recorder({ onReady, disabled }: { onReady: (file: File) => void; disabled?: boolean }) {
  const { t } = useI18n();

  const [supported, setSupported] = useState(true);
  const [recording, setRecording] = useState(false);
  const [phase, setPhase] = useState<Phase>('idle');
  const [seconds, setSeconds] = useState(0);
  const [length, setLength] = useState(0);

  const recRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const secondsRef = useRef(0);
  const mountedRef = useRef(true);
  // The page may hand a fresh closure on every render; the recorder must call the current one
  // when the clip lands, not the one that existed when recording started. Kept in step from an
  // effect rather than during render — the clip only ever arrives from an event, which is
  // after the commit either way.
  const onReadyRef = useRef(onReady);
  useEffect(() => { onReadyRef.current = onReady; }, [onReady]);

  /* A live microphone with no way to switch it off is the failure mode here: the browser's
     recording indicator stays lit, and on a laptop the OS one does too. Every exit path goes
     through this. */
  const releaseMic = useCallback(() => {
    const s = streamRef.current;
    streamRef.current = null;
    if (s) s.getTracks().forEach((track) => track.stop());
  }, []);

  const stopTimer = useCallback(() => {
    if (timerRef.current !== null) clearInterval(timerRef.current);
    timerRef.current = null;
  }, []);

  // Capability is read after mount: the page is prerendered at BUILD time, where neither
  // `isSecureContext` nor `MediaRecorder` exists, and deciding during render would make the
  // prerendered markup disagree with the browser on hydrate.
  useEffect(() => {
    const ok = !!window.isSecureContext
      && !!navigator.mediaDevices
      && typeof navigator.mediaDevices.getUserMedia === 'function'
      && typeof MediaRecorder !== 'undefined';
    setSupported(ok);
    if (!ok) setPhase('unsupported');
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      stopTimer();
      const r = recRef.current;
      recRef.current = null;
      // `stop()` fires `onstop`, which releases the mic — but only if the recorder was still
      // running and only on the next tick, so the release is repeated here unconditionally.
      if (r && r.state !== 'inactive') { try { r.stop(); } catch { /* already torn down */ } }
      releaseMic();
    };
  }, [releaseMic, stopTimer]);

  const start = useCallback(async () => {
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      // Denied, dismissed, or no input device. All three are the same to the visitor: the
      // browser will not give us a microphone, and the button must say so rather than sit
      // there doing nothing.
      if (mountedRef.current) setPhase('denied');
      return;
    }
    // The permission prompt is modal and slow; the page can have been left in the meantime.
    if (!mountedRef.current) { stream.getTracks().forEach((track) => track.stop()); return; }

    streamRef.current = stream;
    chunksRef.current = [];
    secondsRef.current = 0;
    setSeconds(0);

    const mime = MIME_CANDIDATES.find((m) => MediaRecorder.isTypeSupported(m)) || '';
    const rec = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
    recRef.current = rec;

    rec.ondataavailable = (e) => { if (e.data && e.data.size) chunksRef.current.push(e.data); };
    rec.onstop = () => {
      stopTimer();
      releaseMic();
      const type = (rec.mimeType || mime || 'audio/webm').split(';')[0];
      const ext = type.includes('mp4') ? 'm4a' : type.includes('ogg') ? 'ogg' : 'webm';
      const file = new File(chunksRef.current, `recording.${ext}`, { type });
      chunksRef.current = [];
      recRef.current = null;
      if (!mountedRef.current) return;
      setRecording(false);
      setLength(secondsRef.current);
      setPhase('done');
      onReadyRef.current(file);
    };

    rec.start();
    setRecording(true);
    setPhase('recording');
    timerRef.current = setInterval(() => {
      secondsRef.current += 1;
      setSeconds(secondsRef.current);
    }, 1000);
  }, [releaseMic, stopTimer]);

  const stop = useCallback(() => {
    const r = recRef.current;
    if (r && r.state !== 'inactive') r.stop();
  }, []);

  const status: { text: string; cls: string } = (() => {
    switch (phase) {
      case 'unsupported': return { text: t('rec.unsupported'), cls: '' };
      case 'denied': return { text: t('rec.denied'), cls: ' err' };
      case 'recording': return { text: `${t('rec.recording')} ${clock(seconds)}`, cls: ' rec-live' };
      case 'done': return { text: `${t('rec.ready')} (${clock(length)})`, cls: ' ok' };
      default: return { text: '', cls: '' };
    }
  })();

  return (
    <>
      <button
        type="button"
        className={supported ? 'ghost' : 'ghost rec-off'}
        // A recording in progress stays stoppable even while the page is busy — disabling the
        // only stop button is how a stream gets orphaned.
        disabled={!supported || (!!disabled && !recording)}
        onClick={() => { if (recording) stop(); else void start(); }}
      >
        {recording
          ? (<><span className="rec-dot" />{t('rec.stop')}</>)
          : `● ${t('rec.record')}`}
      </button>
      <span className={`rec-status hint${status.cls}`}>{status.text}</span>
    </>
  );
}
