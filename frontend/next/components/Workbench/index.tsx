'use client';
/* The call workbench — "Analyse a call".
   =====================================
   One uploaded recording (or one pasted transcript), four analysers over it, and a timeline
   that shows where every finding lands. The workspace portal and the account page both mount
   this same panel; which analysers appear and which credential they run under is the page's
   decision, not this component's.

   What the port must not lose, in the order it bites:

     * UPLOADS GO THROUGH XMLHttpRequest, NOT `fetch`. The request has two long phases — the
       bytes going up, then minutes of model work coming back as SSE — and both have to be
       visible. `fetch` cannot report upload progress and `EventSource` cannot carry a file.
       `lib/xhrStream.ts` owns that transport, including the part that decides ONCE, off the
       Content-Type at `readyState === 2`, whether the answer is a stream or a plain JSON
       refusal.
     * `onUnauthorized` EXISTS BECAUSE OF THAT. An XHR cannot go through a page's shared fetch
       wrapper, so the panel reports its own 401s back to the page that mounted it — and once
       the callback exists, every 401 in here goes through it, not only the upload's.
     * SCORE COLOURS COME FROM THE WORKSPACE'S BANDS (`GET /scoring/bands`, 50/80 until they
       arrive). One source for the scorecard number, its bar and the timeline lane, because a
       dimension that is red on the card and olive on the timeline is a bug report waiting to
       happen.
     * THE WEIGHTED TOTAL IS THE SERVER'S. See `Scorecard.tsx`.
     * PARTIALLY_SUPPORTED IS NOT NOT_IN_KB. See `Factcheck.tsx`.
     * THE MISSING VOICE HALF IS EXPLAINED SPECIFICALLY. See `noVoiceKey` in `logic.ts`.
     * ONE TIMELINE PER CALL, MOUNTED ON FIRST VIEW AND KEPT. A summary's calls are switched
       between constantly and each mount re-fetches nothing but re-decodes the audio, so the
       inactive ones are hidden and PAUSED rather than thrown away. */

import {
  forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState,
} from 'react';
import { Timeline, type TimelineHandle } from '@/components/Timeline';
import { toast } from '@/components/ui/Toast';
import { Tip } from '@/components/ui/Tip';
import { bandsFrom, DEFAULT_BANDS, scoreBand, type ScoreBands } from '@/lib/aiShapes';
import { duration as clock } from '@/lib/format';
import { API, apiBlob, apiGet, apiMessage, apiSend, scopedHeaders } from '@/lib/session';
import type { SseData } from '@/lib/sse';
import { useI18n } from '@/lib/useI18n';
import { xhrStream } from '@/lib/xhrStream';
import { isUnauthorized } from './api';
import { Factcheck } from './Factcheck';
import {
  asArray, callFromRow, featureOrder, firstResultTab, laneDrawable, lanesFor, LANE_KINDS,
  marksOf, numOrNull, overTotalSize, queueFiles, rolesWithSpeakers, sortLanes,
  type Call, type Feature, type Lane, type RecordingRow, type ScoreResult, type SemanticResult,
  type Span, type SummaryResult,
} from './logic';
import { Scorecard } from './Scorecard';
import { Sentiment } from './Sentiment';
import { DoneCard, Progress, SourceCard } from './Source';
import { langName, speakerLabels, turnsLabel, type T } from './strings';
import { Summary, type SummaryCallSource } from './Summary';
import type { SeekTarget } from './seek';
import styles from './Workbench.module.css';

export type { Feature } from './logic';

export interface WorkbenchHandle {
  /** Load a stored recording and show its results. */
  open(recordingId: string): void;
  /** Load a stored multi-call summary. */
  openSummary(id: string): void;
  /** Back to the empty source card. */
  reset(): void;
  setTab(feature: Feature): void;
}

/** The tenant's sentiment guidance, read and written by the PAGE — the route differs between
    the workspace portal and the operator console, and the panel does not need to know which
    one it is inside. `null` hides the guidance panel entirely. */
export interface SentimentConfigIO {
  get(): Promise<{ guidance: string; readonly?: boolean }>;
  put(value: { guidance: string }): Promise<unknown>;
}

export interface WorkbenchProps {
  features: Feature[];
  /** Which credential `lib/session.ts` attaches to every request the panel makes. */
  scope: 'user' | 'tenant';
  /** Where "Edit the rubric" goes, or null to leave the note without a link. */
  rubricHref?: string | null;
  canEditScores?: boolean;
  sentimentConfig?: SentimentConfigIO | null;
  onUnauthorized?: () => void;
}

interface SourceState {
  kind: 'recording' | 'summary';
  calls: Call[];
  active: number;
  /** A note about the SOURCE as a whole (a summary whose audio is gone). Per-call notes live
      on the call. Both are dictionary keys — see `Call.noteKey`. */
  noteKey: string;
}

export const Workbench = forwardRef<WorkbenchHandle, WorkbenchProps>(function Workbench(props, ref) {
  const { t } = useI18n();
  const { scope, onUnauthorized } = props;
  const featureKey = props.features.join('|');
  const order = useMemo(() => featureOrder(props.features), [featureKey]);   // eslint-disable-line react-hooks/exhaustive-deps

  const [mode, setMode] = useState<'audio' | 'text'>('audio');
  const [files, setFiles] = useState<File[]>([]);
  const [paste, setPaste] = useState('');
  const [tab, setTabState] = useState<Feature>(order[0] || 'score');
  const [source, setSource] = useState<SourceState | null>(null);
  const [summary, setSummary] = useState<SummaryResult | null>(null);
  const [bands, setBands] = useState<ScoreBands>(DEFAULT_BANDS);
  /* Which calls have ever been on screen. A summary's other calls stay unmounted until they
     are looked at — mounting all ten would decode ten recordings at once. */
  const [visited, setVisited] = useState<number[]>([0]);
  /** The segment the playhead is inside, for the sentiment turn list. */
  const [nowSegment, setNowSegment] = useState<number | null>(null);

  const [busy, setBusy] = useState(false);          // an XHR upload is in flight
  const [posting, setPosting] = useState(false);    // the pasted-transcript POST is in flight
  const [running, setRunning] = useState<Partial<Record<Feature, boolean>>>({});
  const [progress, setProgress] = useState<{ label: string; value: number | null } | null>(null);
  const [srcErr, setSrcErr] = useState<{ text: string; isError: boolean }>({ text: '', isError: true });
  const [paneErr, setPaneErr] = useState<Partial<Record<Feature, string>>>({});

  const [wantWords, setWantWords] = useState(true);
  const [voice, setVoice] = useState<{ touched: boolean; on: boolean }>({ touched: false, on: false });

  const handles = useRef(new Map<number, TimelineHandle | null>());
  const panes = useRef<Partial<Record<Feature, HTMLDivElement | null>>>({});
  const tlHostRef = useRef<HTMLDivElement | null>(null);
  const abortRef = useRef<(() => void) | null>(null);
  /* Bumped by every reset and every adoption. An async load that finishes after the panel has
     moved on compares it and drops its result rather than resurrecting a source the person
     already replaced. */
  const token = useRef(0);
  const sourceRef = useRef<SourceState | null>(null);
  const pendingSeek = useRef<{ index: number; target: SeekTarget } | null>(null);

  useEffect(() => { sourceRef.current = source; }, [source]);

  const activeIndex = source ? source.active : 0;
  const activeCall = source ? source.calls[activeIndex] || null : null;

  /* ------------------------------------------------------------------ plumbing */

  const patchCall = useCallback((index: number, fn: (call: Call) => Call) => {
    setSource(prev => (prev ? { ...prev, calls: prev.calls.map((c, i) => (i === index ? fn(c) : c)) } : prev));
  }, []);

  /** The stored bytes for a call, as a call that HAS them.

      A failure is not an error state: the recording may simply have aged out of retention.
      The call keeps its transcript and its results, loses its player, and says which of the
      two happened. */
  const withAudio = useCallback(async (call: Call): Promise<Call> => {
    if (call.blob || !call.hasAudio || !call.audioUrl) return call;
    try {
      return { ...call, blob: await apiBlob(call.audioUrl, { scope }) };
    } catch (e) {
      if (isUnauthorized(e)) onUnauthorized?.();
      return { ...call, hasAudio: false, noteKey: call.noteKey || 'wb.audiofail' };
    }
  }, [scope, onUnauthorized]);

  const clearSource = useCallback(() => {
    token.current += 1;
    setSource(null);
    setSummary(null);
    setVisited([0]);
    setNowSegment(null);
    pendingSeek.current = null;
    /* The handle map is NOT cleared here, and that is deliberate. Each entry is written by the
       timeline's own ref callback — set on mount, nulled on unmount — and clearing it by hand
       loses the handle of a component React DECIDED TO REUSE (same key, new source in the same
       commit), whose ref never fires again. A stale entry is unreachable instead: every read
       is by an index of the source currently on screen. */
    return token.current;
  }, []);

  const reset = useCallback(() => {
    abortRef.current?.();
    const tk = clearSource();
    setFiles([]);
    setSrcErr({ text: '', isError: true });
    setPaneErr({});
    setProgress(null);
    return tk;
  }, [clearSource]);

  // The panel owns an XHR that outlives a client-side navigation unless it is cancelled here.
  useEffect(() => () => { abortRef.current?.(); }, []);
  useEffect(() => {
    const onHide = () => abortRef.current?.();
    window.addEventListener('pagehide', onHide);
    return () => window.removeEventListener('pagehide', onHide);
  }, []);

  /* The workspace's own colour thresholds, once per panel. Failing is silent and harmless:
     the built-in 50/80 is what an unconfigured workspace gets anyway. */
  useEffect(() => {
    let dropped = false;
    (async () => {
      try {
        const d = await apiGet<unknown>('/scoring/bands', { scope });
        if (!dropped) setBands(bandsFrom(d));
      } catch { /* built-in thresholds stand */ }
    })();
    return () => { dropped = true; };
  }, [scope]);

  const labels = useMemo(() => (activeCall ? speakerLabels(t, activeCall) : {}), [activeCall, t]);

  /* ------------------------------------------------------------------ seeking */

  const seekNow = useCallback((target: SeekTarget, index?: number) => {
    const s = sourceRef.current;
    const at = index ?? s?.active ?? 0;
    const call = s ? s.calls[at] : null;
    const handle = handles.current.get(at);
    if (!call || !handle) return;
    let start = numOrNull(target.start);
    // A finding the model placed by segment rather than by time still has a time, as long as
    // the transcript is timestamped.
    if (start == null && target.seg != null) start = numOrNull(call.segments[target.seg]?.start);
    try {
      if (start != null) handle.seek(start);
      // The segment highlight is the whole interaction in TEXT MODE, where there is no clock
      // to seek to — a pasted transcript's findings are pointed at, not played.
      if (target.seg != null) handle.highlightSegment(target.seg);
    } catch (e) {
      console.error('workbench: seek', e);
    }
    if (start != null || target.seg != null) {
      tlHostRef.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }
  }, []);

  const showCall = useCallback(async (index: number) => {
    const s = sourceRef.current;
    if (!s || !s.calls[index]) return;
    setSource(prev => (prev ? { ...prev, active: index } : prev));
    setVisited(prev => (prev.includes(index) ? prev : [...prev, index]));
    setNowSegment(null);
    const call = s.calls[index];
    if (call.blob || !call.hasAudio) return;
    const tk = token.current;
    const loaded = await withAudio(call);
    if (token.current !== tk) return;
    setSource(prev => {
      if (!prev || prev.calls[index] !== call) return prev;    // it was replaced meanwhile
      const calls = [...prev.calls];
      calls[index] = loaded;
      return { ...prev, calls };
    });
  }, [withAudio]);

  const seek = useCallback((target: SeekTarget) => {
    const s = sourceRef.current;
    if (target.call != null && s && target.call !== s.active) {
      // Seek AFTER the switch has been committed and the new timeline has mounted — its
      // handle does not exist until React has painted it.
      pendingSeek.current = { index: target.call, target };
      void showCall(target.call);
      return;
    }
    seekNow(target);
  }, [seekNow, showCall]);

  useEffect(() => {
    const p = pendingSeek.current;
    if (!p || !source || source.active !== p.index) return;
    pendingSeek.current = null;
    seekNow(p.target, p.index);
  }, [source, seekNow]);

  const setTab = useCallback((k: Feature) => {
    if (order.includes(k)) setTabState(k);
  }, [order]);

  /** A click on a SPAN goes the other way: from the timeline to the finding that drew it. */
  const onSpanClick = useCallback((lane: Lane, span: Span) => {
    const kind = String(lane?.id || '').split(':')[0] as Feature;
    if (!order.includes(kind)) return;
    setTabState(kind);
    const seg = asArray<number>(span?.segments)[0];
    if (seg == null) return;
    /* One frame later: the pane this card lives in is `display:none` until the tab change is
       painted, and `scrollIntoView` on a hidden element does nothing. Reaching into the DOM
       here rather than threading a "flash this one" prop through all four renderers — the
       card is identified by the `data-seg` its seek control already carries. */
    requestAnimationFrame(() => {
      const el = panes.current[kind]?.querySelector<HTMLElement>(`[data-seg="${seg}"]`);
      if (!el) return;
      el.scrollIntoView({ block: 'center', behavior: 'smooth' });
      el.classList.add('wb-flash');
      window.setTimeout(() => el.classList.remove('wb-flash'), 1600);
    });
  }, [order]);

  /* ------------------------------------------------------------------ adopting a source */

  const openTab = useCallback((call: Call | null, hasSummary: boolean) => {
    const first = firstResultTab(order, call, hasSummary);
    if (first) setTabState(first);
  }, [order]);

  const adoptRecording = useCallback((row: RecordingRow, file: File | null, text?: string) => {
    clearSource();
    setFiles([]);
    const call = callFromRow(row, { blob: file, source: file ? 'audio' : 'text', text });
    setSource({ kind: 'recording', calls: [call], active: 0, noteKey: '' });
    openTab(call, false);
  }, [clearSource, openTab]);

  const adoptSummary = useCallback((sum: SummaryResult, sent: File[]) => {
    clearSource();
    setFiles([]);
    // The queue is CONSUMED by a successful upload: the files live on as the calls' own bytes,
    // so a later "Run again" re-sends those rather than treating the old queue as a new source.
    const calls = asArray<RecordingRow>(sum.calls)
      .map((row, i) => callFromRow(row, { blob: sent[i] || null, source: 'audio' }));
    setSource({ kind: 'summary', calls, active: 0, noteKey: '' });
    setSummary(sum);
    setTabState('summarise');
  }, [clearSource]);

  /* ------------------------------------------------------------------ uploads */

  const showError = useCallback((where: 'src' | Feature, text: string, isError = true) => {
    if (where === 'src') setSrcErr({ text, isError });
    else setPaneErr(prev => ({ ...prev, [where]: text }));
  }, []);

  /** POST a multipart body and read the server's SSE progress out of the same request. */
  const stream = useCallback(async <T,>(
    path: string,
    body: FormData,
    where: 'src' | Feature,
    o: { onStage: (d: SseData) => void; onDone: (data: T) => void; onSettle?: () => void },
  ) => {
    showError(where, '');
    const call = xhrStream<T>({
      url: `${API}${path}`,
      body,
      headers: scopedHeaders(scope),
      terminal: ['done'],
      onUploadProgress: p => setProgress({
        label: t('sc.import.stage.upload'),
        value: p == null ? null : Math.max(0, Math.min(100, Math.round(p))),
      }),
      onUploadEnd: () => setProgress({ label: t('sc.import.stage.queued'), value: null }),
      onEvent: (name, d) => { if (name === 'stage') o.onStage(d); },
    });
    abortRef.current = call.abort;
    setBusy(true);
    try {
      const { data } = await call.result;
      o.onDone(data);
    } catch (e) {
      // A cancel is the person's own decision: a quiet line, never a toast.
      if (e instanceof DOMException && e.name === 'AbortError') {
        showError(where, t('wb.cancelled'), false);
      } else {
        const m = apiMessage(e, t) || t('wb.fail');
        showError(where, m);
        toast(m, 'err');
        if (isUnauthorized(e)) onUnauthorized?.();
      }
    } finally {
      abortRef.current = null;
      setBusy(false);
      setProgress(null);
      o.onSettle?.();
    }
  }, [scope, t, showError, onUnauthorized]);

  const uploadSummary = useCallback(async (batch: File[], where: 'src' | Feature, keep = false) => {
    if (overTotalSize(batch)) {
      const m = t('wb.toobig.total', { max: '300 MB' });
      setRunning(prev => ({ ...prev, summarise: false }));
      showError(where, m);
      toast(m, 'err');
      return;
    }
    const fd = new FormData();
    for (const f of batch) fd.append('files', f);
    await stream<SummaryResult>('/summaries?stream=1', fd, where, {
      onStage: d => {
        if (d.stage === 'summarising') { setProgress({ label: t('wb.stage.summarising'), value: null }); return; }
        // 0-based (Python's enumerate). The count is decoration; the filename is the news.
        const i = numOrNull(d.index);
        setProgress({
          label: t('wb.stage.transcribing_n', {
            name: typeof d.filename === 'string' ? d.filename : '',
            i: i == null ? '' : i + 1,
            n: numOrNull(d.count) ?? batch.length,
          }),
          value: null,
        });
      },
      onDone: sum => {
        // `keep` is the re-run: the calls and their other results stay, only the summary is
        // replaced. Adopting would throw away three analysers' work to refresh one.
        if (keep) setSummary(sum);
        else adoptSummary(sum, batch);
        toast(t('wb.sum.done'), 'ok');
      },
      onSettle: keep ? () => setRunning(prev => ({ ...prev, summarise: false })) : undefined,
    });
  }, [stream, t, showError, adoptSummary]);

  const uploadRecording = useCallback(async (file: File) => {
    const fd = new FormData();
    fd.append('file', file);
    await stream<RecordingRow>('/recordings?stream=1', fd, 'src', {
      onStage: () => setProgress({ label: t('wb.stage.transcribing'), value: null }),
      onDone: rec => { adoptRecording(rec, file); toast(t('stt.done'), 'ok'); },
    });
  }, [stream, t, adoptRecording]);

  const submitText = useCallback(async () => {
    const text = paste.trim();
    if (!text) { showError('src', t('wb.needtext')); return; }
    setPosting(true);
    try {
      const rec = await apiSend<RecordingRow>('POST', '/recordings/text', { text }, { scope });
      adoptRecording(rec, null, text);
      toast(t('stt.done'), 'ok');
    } catch (e) {
      const m = apiMessage(e, t);
      showError('src', m);
      toast(m, 'err');
      if (isUnauthorized(e)) onUnauthorized?.();
    } finally {
      setPosting(false);
    }
  }, [paste, scope, t, showError, adoptRecording, onUnauthorized]);

  const go = useCallback(() => {
    showError('src', '');
    if (busy || posting) return;
    if (mode === 'text') { void submitText(); return; }
    if (!files.length) { showError('src', t('wb.needsource')); return; }
    if (files.length > 1 || tab === 'summarise') { void uploadSummary(files.slice(), 'src'); return; }
    void uploadRecording(files[0]);
  }, [busy, posting, mode, files, tab, t, showError, submitText, uploadSummary, uploadRecording]);

  /* ------------------------------------------------------------------ analysers */

  const run = useCallback(async (kind: Feature) => {
    if (kind === 'summarise') return;
    showError(kind, '');
    const index = activeIndex;
    const call = activeCall;
    if (!call) { showError(kind, t('wb.needsource')); return; }

    let body: unknown;
    if (kind === 'semantic') {
      const audio = call.source === 'audio' && (!!call.blob || call.hasAudio);
      const modes: string[] = [];
      if (wantWords) modes.push('text');
      if (audio && (voice.touched ? voice.on : true)) modes.push('voice');
      if (!modes.length) { showError(kind, t('wb.sem.pickone')); return; }
      body = { modes };
    }

    setRunning(prev => ({ ...prev, [kind]: true }));
    try {
      const data = await apiSend<Record<string, unknown>>(
        'POST', `/recordings/${encodeURIComponent(call.id)}/${kind}`, body, { scope },
      );
      patchCall(index, c => ({
        ...c,
        results: { ...c.results, [kind]: data },
        // A sentiment run is the only thing that can tell an agent from a customer; the roles
        // it finds relabel the chips, the timeline and the transcript.
        roles: kind === 'semantic' ? rolesWithSpeakers(c.roles, data as SemanticResult) : c.roles,
      }));
      toast(t(kind === 'score' ? 'pg.done' : kind === 'factcheck' ? 'wb.fc.done' : 'wb.sem.done'), 'ok');
    } catch (e) {
      const m = apiMessage(e, t);
      showError(kind, m);
      toast(m, 'err');
      if (isUnauthorized(e)) onUnauthorized?.();
    } finally {
      setRunning(prev => ({ ...prev, [kind]: false }));
    }
  }, [activeCall, activeIndex, wantWords, voice, scope, t, showError, patchCall, onUnauthorized]);

  const runSummarise = useCallback(async () => {
    showError('summarise', '');
    if (busy) return;
    // A queued file wins: the person picked new calls and pressed the button under them.
    if (files.length) {
      if (mode !== 'audio') setMode('audio');
      await uploadSummary(files.slice(), 'summarise');
      return;
    }
    const s = sourceRef.current;
    const calls = s ? s.calls : [];
    if (!calls.length) { showError('summarise', t('wb.needsource')); return; }

    setRunning(prev => ({ ...prev, summarise: true }));
    const tk = token.current;
    try {
      const loaded: Call[] = [];
      const batch: File[] = [];
      for (const c of calls) {
        const withIt = await withAudio(c);
        if (!withIt.blob) throw new Error(t('wb.sum.needaudio'));
        loaded.push(withIt);
        batch.push(withIt.blob instanceof File
          ? withIt.blob
          : new File([withIt.blob], withIt.filename || 'call.bin', { type: withIt.blob.type || '' }));
      }
      if (token.current !== tk) { setRunning(prev => ({ ...prev, summarise: false })); return; }
      // Keep the bytes that were just fetched, so a second re-run does not download them again.
      setSource(prev => (prev && prev.calls.length === loaded.length ? { ...prev, calls: loaded } : prev));
      await uploadSummary(batch, 'summarise', true);
    } catch (e) {
      setRunning(prev => ({ ...prev, summarise: false }));
      showError('summarise', e instanceof Error ? e.message : t('wb.fail'));
    }
  }, [busy, files, mode, t, showError, uploadSummary, withAudio]);

  /* ------------------------------------------------------------------ the handle */

  const openRecording = useCallback(async (recordingId: string) => {
    const tk = reset();
    try {
      const row = await apiGet<RecordingRow>(`/recordings/${encodeURIComponent(recordingId)}`, { scope });
      if (token.current !== tk) return;
      let call = callFromRow(row);
      if (call.hasAudio) call = await withAudio(call);
      if (token.current !== tk) return;
      // Audio that was never stored, or has aged out of retention: the transcript and the
      // results are all that is left, and saying so beats a player that will not play.
      if (call.source === 'audio' && !call.blob) {
        call = { ...call, hasAudio: false, noteKey: call.noteKey || 'wb.noaudio' };
      }
      setSource({ kind: 'recording', calls: [call], active: 0, noteKey: '' });
      openTab(call, false);
    } catch (e) {
      if (token.current !== tk) return;
      reset();
      const m = apiMessage(e, t);
      showError('src', m);
      toast(m, 'err');
      if (isUnauthorized(e)) onUnauthorized?.();
    }
  }, [reset, scope, t, withAudio, openTab, showError, onUnauthorized]);

  const openSummaryById = useCallback(async (summaryId: string) => {
    const tk = reset();
    try {
      const row = await apiGet<SummaryResult>(`/summaries/${encodeURIComponent(summaryId)}`, { scope });
      if (token.current !== tk) return;
      const calls = asArray<RecordingRow>(row.calls).map(c => callFromRow(c, { source: 'audio' }));
      const first = calls.length ? await withAudio(calls[0]) : null;
      if (token.current !== tk) return;
      if (first) calls[0] = first;
      const gone = !!first && first.source === 'audio' && !first.blob && !first.noteKey;
      setSource({ kind: 'summary', calls, active: 0, noteKey: gone ? 'wb.noaudio' : '' });
      setSummary(row);
      setTabState('summarise');
    } catch (e) {
      if (token.current !== tk) return;
      reset();
      const m = apiMessage(e, t);
      showError('src', m);
      toast(m, 'err');
      if (isUnauthorized(e)) onUnauthorized?.();
    }
  }, [reset, scope, t, withAudio, showError, onUnauthorized]);

  useImperativeHandle(ref, () => ({
    open: (recordingId: string) => { void openRecording(recordingId); },
    openSummary: (id: string) => { void openSummaryById(id); },
    reset: () => { reset(); },
    setTab,
  }), [openRecording, openSummaryById, reset, setTab]);

  /* ------------------------------------------------------------------ derived UI state */

  const audioAvailable = !!(activeCall && activeCall.source === 'audio' && (activeCall.blob || activeCall.hasAudio));
  const voiceChecked = audioAvailable && (voice.touched ? voice.on : true);

  const runState = (k: Feature) => {
    const has = k === 'summarise' ? !!summary : !!(activeCall && activeCall.results[k]);
    const can = k === 'summarise'
      ? (files.length > 0 || !!(activeCall && (activeCall.blob || activeCall.hasAudio)))
      : !!activeCall;
    return {
      spinning: !!running[k],
      label: running[k] ? t('wb.running') : t(has ? 'wb.rerun' : `wb.run.${k}`),
      disabled: !can || busy || !!running[k],
    };
  };

  const summaryCalls: SummaryCallSource[] = useMemo(
    () => (source ? source.calls.map(c => ({
      filename: c.filename,
      language: c.language,
      duration: c.duration,
      segments: c.segments,
      transcript: c.transcript,
      speakerLabels: speakerLabels(t, c),
    })) : []),
    [source, t],
  );

  const progressNode = progress ? <Progress label={progress.label} value={progress.value} /> : null;
  const sourceNoteKey = source ? (source.kind === 'summary' ? source.noteKey : (activeCall?.noteKey || '')) : '';

  /* ------------------------------------------------------------------ render */

  return (
    <div className={`wb ${styles.wb}`}>
      {source ? (
        <DoneCard
          t={t}
          summary={<DoneLine t={t} source={source} />}
          note={sourceNoteKey ? t(sourceNoteKey) : ''}
          onChange={() => reset()}
          progress={progressNode}
        />
      ) : (
        <SourceCard
          t={t}
          mode={mode}
          onMode={m => { setMode(m); showError('src', ''); }}
          files={files}
          onAdd={list => {
            const incoming = list ? Array.from(list as ArrayLike<File>) : [];
            const { files: next, notices } = queueFiles(files, incoming, tab === 'summarise');
            setFiles(next);
            for (const n of notices) toast(t(n.key, n.vars), n.kind);
            showError('src', '');
            if (mode !== 'audio') setMode('audio');
          }}
          onRemove={i => setFiles(prev => prev.filter((_, n) => n !== i))}
          paste={paste}
          onPaste={setPaste}
          multi={tab === 'summarise'}
          busy={busy}
          posting={posting}
          onGo={go}
          onCancel={() => abortRef.current?.()}
          error={srcErr}
          progress={progressNode}
        />
      )}

      {source && source.calls.length > 1 && (
        <div className="subtabs wb-switcher" role="tablist">
          {source.calls.map((c, i) => (
            <button
              key={c.id || i} type="button" role="tab"
              className={`subtab${i === activeIndex ? ' active' : ''}`}
              aria-selected={i === activeIndex}
              onClick={() => void showCall(i)}
            >
              {c.filename ? `${i + 1}. ${c.filename}` : t('wb.call', { n: i + 1 })}
            </button>
          ))}
        </div>
      )}

      <div className="wb-timelines" ref={tlHostRef}>
        {source && source.calls.map((call, i) => (visited.includes(i) ? (
          <CallTimeline
            key={call.id || i}
            call={call}
            index={i}
            isActive={i === activeIndex}
            bands={bands}
            onHandle={(index, handle) => { handles.current.set(index, handle); }}
            onSpanClick={onSpanClick}
            onSegment={(index, seg) => { if (index === (sourceRef.current?.active ?? 0)) setNowSegment(seg); }}
          />
        ) : null))}
      </div>

      <section className="card wb-an">
        <div className="subtabs wb-tabs" role="tablist">
          {order.map(k => (
            <button
              key={k} type="button" role="tab"
              className={`subtab${tab === k ? ' active' : ''}`}
              aria-selected={tab === k}
              onClick={() => setTab(k)}
            >
              {t(`wb.tab.${k}`)}
            </button>
          ))}
        </div>

        {order.map(k => {
          const state = runState(k);
          return (
            <div
              key={k} className={`wb-pane${tab === k ? ' active' : ''}`} data-an={k} role="tabpanel"
              ref={el => { panes.current[k] = el; }}
            >
              {k === 'factcheck' && <p className="hint wb-note">{t('wb.fc.note')}</p>}
              {k === 'score' && (
                <p className="hint wb-note">
                  <span>{t('wb.sc.note')}</span>
                  {props.rubricHref ? <> <a href={props.rubricHref}>{t('wb.sc.edit')}</a></> : null}
                </p>
              )}
              {k === 'summarise' && <p className="hint wb-note">{t('wb.sum.note')}</p>}
              {k === 'semantic' && (
                <>
                  <p className="hint wb-note">{t('wb.sem.note')}</p>
                  <div className="wb-opts">
                    <label>
                      <input type="checkbox" checked={wantWords} onChange={e => setWantWords(e.target.checked)} />
                      <span>{t('wb.sem.words')}</span>
                      <Tip text={t('wb.sem.words.tip')} />
                    </label>
                    <label className={`wb-voice-lab${audioAvailable ? '' : ' off'}`}>
                      <input
                        type="checkbox" checked={voiceChecked} disabled={!audioAvailable}
                        onChange={e => setVoice({ touched: true, on: e.target.checked })}
                      />
                      <span>{t('wb.sem.voice')}</span>
                      <Tip text={t('wb.sem.voice.tip')} />
                    </label>
                    {!audioAvailable && activeCall
                      ? <span className="hint wb-voice-hint">{t('wb.sem.voice.off')}</span>
                      : null}
                  </div>
                  {props.sentimentConfig ? <GuidancePanel t={t} io={props.sentimentConfig} /> : null}
                </>
              )}

              <div className="actions wb-run-row">
                <button
                  type="button" className="primary wb-run" disabled={state.disabled}
                  onClick={() => { if (k === 'summarise') void runSummarise(); else void run(k); }}
                >
                  {state.spinning ? <><span className="spinner" />{state.label}</> : state.label}
                </button>
              </div>

              <div className="msg err wb-err" aria-live="polite">{paneErr[k] || ''}</div>

              <div className="wb-result">
                {k === 'factcheck' && activeCall?.results.factcheck ? (
                  <Factcheck
                    data={activeCall.results.factcheck} bands={bands}
                    speakerLabels={labels} callIndex={activeIndex} onSeek={seek}
                  />
                ) : null}
                {k === 'score' && activeCall?.results.score ? (
                  <Scorecard
                    data={activeCall.results.score} bands={bands} jobId={activeCall.id}
                    editable={props.canEditScores !== false} scope={scope}
                    callIndex={activeIndex} onSeek={seek}
                    onSaved={(saved: ScoreResult) => patchCall(activeIndex, c => ({
                      ...c, results: { ...c.results, score: saved },
                    }))}
                    onUnauthorized={onUnauthorized}
                  />
                ) : null}
                {k === 'semantic' && activeCall?.results.semantic ? (
                  <Sentiment
                    data={activeCall.results.semantic} bands={bands}
                    segments={activeCall.segments} speakerLabels={labels}
                    callIndex={activeIndex} onSeek={seek} now={nowSegment}
                  />
                ) : null}
                {k === 'summarise' && summary ? (
                  <Summary data={summary} calls={summaryCalls} active={activeIndex} onSeek={seek} />
                ) : null}
              </div>
            </div>
          );
        })}
      </section>
    </div>
  );
});

/** One call's player, and the lanes drawn on it.

    A component per call rather than one timeline the panel re-points, because each call owns
    a decoded waveform and a playhead: switching between them by swapping props would throw
    both away every time, and a summary is read by moving between its calls. */
function CallTimeline({
  call, index, isActive, bands, onHandle, onSpanClick, onSegment,
}: {
  call: Call;
  index: number;
  isActive: boolean;
  bands: ScoreBands;
  onHandle: (index: number, handle: TimelineHandle | null) => void;
  onSpanClick: (lane: Lane, span: Span) => void;
  onSegment: (index: number, segment: number) => void;
}) {
  const { t } = useI18n();
  const handle = useRef<TimelineHandle | null>(null);

  const labels = useMemo(() => speakerLabels(t, call), [t, call]);
  const lanes = useMemo(() => {
    const scoreLevel = (v: number | null) => scoreBand(v, bands);
    const names = { factcheck: t('wb.lane.factcheck'), words: t('wb.lane.words'), voice: t('wb.lane.voice') };
    const out: Lane[] = [];
    for (const kind of LANE_KINDS) {
      const result = call.results[kind];
      if (result) out.push(...lanesFor(kind, result, { names, scoreLevel }));
    }
    return sortLanes(out);
  }, [call.results, bands, t]);

  const hasAudio = !!call.blob;

  /* Pushed through the handle rather than through the `lanes` PROP, which the Timeline
     documents as being for a declarative caller: a defined prop is re-applied whenever its
     identity changes and would overwrite what this just set.

     Both calls matter. `setLanes` draws the rows; `markSegments` marks the transcript, and it
     is given EVERY lane — including the ones `laneDrawable` kept off the axis, which have
     findings but no times to draw them at. */
  useEffect(() => {
    const h = handle.current;
    if (!h) return;
    try {
      h.setLanes(lanes.filter(l => laneDrawable(l, hasAudio)));
      for (const l of lanes) h.markSegments(l.id, marksOf(l.spans));
    } catch (e) {
      console.error('workbench: setLanes', e);
    }
  }, [lanes, hasAudio]);

  useEffect(() => {
    try { handle.current?.setSpeakerLabels(labels); } catch (e) { console.error('workbench: setSpeakerLabels', e); }
  }, [labels]);

  // Switching calls stops the one being left: two players talking at once is the bug a single
  // shared player was invented to prevent.
  useEffect(() => {
    if (isActive) return;
    try { handle.current?.pause(); } catch { /* nothing loaded */ }
  }, [isActive]);

  return (
    <div className="wb-tl" hidden={!isActive}>
      {call.noteKey ? <p className="hint wb-tl-note">{t(call.noteKey)}</p> : null}
      <Timeline
        ref={h => { handle.current = h; onHandle(index, h); }}
        src={call.blob}
        segments={call.segments}
        duration={call.duration}
        speakerLabels={labels}
        filename={call.filename || undefined}
        onSpanClick={onSpanClick}
        onSegment={i => onSegment(index, i)}
      />
    </div>
  );
}

/** The collapsed source line: what is loaded, and how much of it there is. */
function DoneLine({ t, source }: { t: T; source: SourceState }) {
  if (source.kind === 'summary') {
    const total = source.calls.reduce((a, c) => a + (numOrNull(c.duration) || 0), 0);
    const meta = [total ? clock(total) : '', langName(t, source.calls[0]?.language)].filter(Boolean).join(' · ');
    return (
      <>
        <b>{t('wb.src.calls', { n: source.calls.length })}</b>{' '}
        <span className="wb-meta">{meta}</span>
      </>
    );
  }
  const call = source.calls[0];
  if (!call) return null;
  const meta = [
    call.duration != null ? clock(call.duration) : '',
    langName(t, call.language),
    turnsLabel(t, call),
  ].filter(Boolean).join(' · ');
  return (
    <>
      <b>{call.source === 'text' ? t('wb.src.text') : (call.filename || t('wb.call', { n: 1 }))}</b>{' '}
      <span className="wb-meta">{meta}</span>
    </>
  );
}

/** The workspace's sentiment guidance, edited in place.

    Loaded on FIRST OPEN rather than on mount: most runs never touch it, and the route behind
    it is the page's, not the panel's. */
function GuidancePanel({ t, io }: { t: T; io: SentimentConfigIO }) {
  const [loaded, setLoaded] = useState(false);
  const [text, setText] = useState('');
  const [readonly, setReadonly] = useState(false);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState('');

  const load = useCallback(async () => {
    try {
      const d = (await io.get()) || { guidance: '' };
      setText(d.guidance || '');
      setReadonly(typeof io.put !== 'function' || d.readonly === true);
      setLoaded(true);
    } catch (e) {
      setMsg(e instanceof Error ? e.message : t('err.unavailable'));
    }
  }, [io, t]);

  const save = useCallback(async () => {
    setMsg('');
    setSaving(true);
    try {
      await io.put({ guidance: text });
      toast(t('sn.saved'), 'ok');
    } catch (e) {
      setMsg(e instanceof Error ? e.message : t('err.unavailable'));
    } finally {
      setSaving(false);
    }
  }, [io, text, t]);

  return (
    <details
      className="wb-guide"
      onToggle={e => { if ((e.currentTarget as HTMLDetailsElement).open && !loaded) void load(); }}
    >
      <summary>{t('wb.sem.guidance')}</summary>
      <div className="wb-guide-body">
        {readonly ? <p className="hint wb-guide-ro" style={{ color: 'var(--pending)' }}>{t('sn.readonly')}</p> : null}
        <label>{t('sn.guidance')}</label>
        <textarea
          className="wb-guide-text" style={{ minHeight: 70 }} value={text} disabled={readonly}
          placeholder={t('sn.guidance.ph')} onChange={e => setText(e.target.value)}
        />
        {!readonly && (
          <div className="actions">
            <button type="button" className="ghost wb-guide-save" disabled={saving} onClick={() => void save()}>
              {saving ? <span className="spinner" /> : t('sn.save')}
            </button>
          </div>
        )}
        <div className={`msg wb-guide-msg${msg ? ' err' : ''}`} aria-live="polite">{msg}</div>
      </div>
    </details>
  );
}
