'use client';

import { Fragment, useCallback, useEffect, useRef, useState } from 'react';
import type { JSX } from 'react';
import Header from '@/components/Header';
import Select from '@/components/ui/Select';
import type { Option } from '@/components/ui/Select';
import { createEditor, type Editor, type EditorState, type LayerView } from '@/components/audio/engine';
import { apiGet, apiGetOrNull, apiMessage, apiUpload, downloadAuthed } from '@/lib/session';
import { useI18n } from '@/lib/useI18n';
import { archivePath, refusal, type ConvertSummary } from './convert';
import styles from './editor.module.css';

/* The audio editor — the port of `editor.html` + `audio-editor.js` + `audio-edit-core.js`.
   =====================================================================================
   The engine is `@/components/audio/*` and has no React in it; this file is the markup, the
   session-aware calls and the keyboard. That split is not tidiness. The legacy module wrote
   `host.innerHTML` and registered window and document listeners with no teardown, which in
   React deletes nodes the reconciler owns and — under `reactStrictMode`'s double-mount —
   doubles every leaked listener on the second pass. Everything below is JSX; the only two DOM
   nodes handed to the engine are the canvas it paints and the one-line clock it writes sixty
   times a second, and both are rendered here with no React children to fight over.

   THE PRINCIPAL. This is a PUBLIC surface — anonymous visitors edit and convert within the
   daily allowance — so every call it makes is `scope: 'user'`: the registered-user Bearer when
   there is one, and nothing at all otherwise. Never the shared precedence, which would send an
   operator's `X-Admin-Token` or a tenant Bearer to `/limits` and `/convert` and file a
   stranger's conversion under a workspace. That is the same rule `index.html`'s `pubAuth()`
   encodes and docs/MIGRATION.md pins as a decision to preserve. */

const MAX_BYTES = 120 * 1024 * 1024;

/* A generous ceiling rather than none: everything here holds the decoded audio in memory as
   float32 (a 30-minute stereo 48k call is ~700 MB decoded), and the honest failure is a
   sentence up front, not a tab the browser kills half way through a cut. */
const MAX_MB = Math.round(MAX_BYTES / 1048576);

const ACCEPT = 'audio/*,video/*,.m4a,.aac,.flac,.opus,.wma,.oga,.wav,.mp3,.ogg,.webm';

const IDLE: EditorState = {
  loaded: false, layers: [], active: 0, activeName: '', channels: 0, duration: 0, cursor: 0,
  hasSelection: false, from: 0, to: 0, canUndo: false, canRedo: false, playing: false,
};

type Msg = { text: string; kind: '' | 'ok' | 'err' };
const NO_MSG: Msg = { text: '', kind: '' };

/** What the allowance banner has to say: nothing, "anonymous access is off", or a count. */
type Quota = { kind: 'none' } | { kind: 'off' } | { kind: 'left'; n: number };

interface LimitsSnapshot {
  anonymous?: boolean;
  enabled?: boolean;
  remaining?: { conversions?: number | null };
}

interface FormatCatalogue {
  formats?: { id: string; label?: string }[];
  default?: string;
}

export default function EditorPage(): JSX.Element {
  const { t } = useI18n();

  const [st, setSt] = useState<EditorState>(IDLE);
  const [msg, setMsg] = useState<Msg>(NO_MSG);
  const [exp, setExp] = useState<Msg>(NO_MSG);
  const [over, setOver] = useState(false);
  const [insSecs, setInsSecs] = useState('1');
  const [quota, setQuota] = useState<Quota>({ kind: 'none' });
  const [formats, setFormats] = useState<Option[] | null>([]);
  const [fmt, setFmt] = useState('');
  const [converting, setConverting] = useState(false);

  const wrapRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const timeRef = useRef<HTMLDivElement | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);
  const edRef = useRef<Editor | null>(null);

  /* `t` behind a ref so a language switch does not rebuild the engine (which would throw away
     the open recording). The engine reads three labels — the clock line and the flattened
     layer's name — through this getter, and repaints when the language actually changes. */
  const tRef = useRef(t);
  useEffect(() => { tRef.current = t; edRef.current?.draw(); }, [t]);

  /* ---- the engine: created once, destroyed with the page ---- */
  useEffect(() => {
    const canvas = canvasRef.current, wrap = wrapRef.current;
    if (!canvas || !wrap) return;
    const ed = createEditor({
      canvas, wrap, timeEl: timeRef.current,
      t: k => tRef.current(k),
      onChange: setSt,
    });
    edRef.current = ed;
    setSt(ed.state());
    return () => { edRef.current = null; ed.destroy(); };
  }, []);

  /* ---- loading ---- */
  const loadFiles = useCallback(async (files: FileList | File[] | null) => {
    const list = [...(files || [])];
    if (!list.length) return;
    setMsg(NO_MSG);
    const failed: string[] = [];
    for (const f of list) {
      if (f.size > MAX_BYTES) {
        failed.push(`${f.name} — ${t('ed.err.big', { mb: MAX_MB })}`);
        continue;
      }
      setMsg({ text: t('ed.loading'), kind: '' });
      try {
        // Each file becomes its own layer; after the first, it lands at the cursor so a jingle
        // or a bed drops exactly where you were listening.
        await edRef.current?.add(f, true);
      } catch {
        // decodeAudioData refuses telephony codecs (gsm, alaw, amr, sln) — which is exactly
        // what the server-side converter is for, so say that instead of "failed".
        failed.push(`${f.name} — ${t('ed.err.decode')}`);
      }
    }
    if (failed.length) setMsg({ text: failed.join(' · '), kind: 'err' });
    else setMsg({ text: t('ed.added', { n: list.length }), kind: 'ok' });
  }, [t]);

  /* ---- keyboard ----
     Space plays, and the usual undo/redo keys work — but never while the caret is in a field,
     or typing "1.5" into the silence box would start playback. The legacy guard tested
     `tagName` against input/textarea/select, which was already half wrong on this page:
     `enhanceSelects()` replaced the format <select> with a custom control, and the element
     that actually holds focus is a <button>. `closest()` over the control's own wrapper (and
     its panel, which is portalled onto <body>) covers both spellings. Buttons stay OUT of the
     guard on purpose — space on a focused toolbar button toggling the transport is the legacy
     behaviour and the useful one. */
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target instanceof Element ? e.target : null;
      if (el?.closest('input, textarea, select, [contenteditable], .cq-select, .cq-select-panel')) return;
      const ed = edRef.current;
      if (!ed) return;
      const k = e.key.toLowerCase();
      if (e.key === ' ') { e.preventDefault(); ed.toggle(); }
      // [ and ] are the marks; i/o are the same thing in the editors most people have used.
      else if (k === '[' || k === 'i') { e.preventDefault(); ed.markIn(); }
      else if (k === ']' || k === 'o') { e.preventDefault(); ed.markOut(); }
      else if (k === 'escape') { ed.clearSelection(); }
      else if ((e.ctrlKey || e.metaKey) && k === 'z') {
        e.preventDefault();
        if (e.shiftKey) ed.redo(); else ed.undo();
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, []);

  /* ---- the allowance banner ----
     The same banner the public app shows, refreshed after a conversion so a visitor can see
     what a download cost them. Only `conversions` is quoted here: editing is local and free,
     so counting down anything else on this page would be inventing a limit the editor does not
     have. An absent key means "claim nothing", never zero — and a failed request leaves the
     banner exactly as it was, rather than claiming the allowance is spent.

     Sent AS THE SIGNED-IN USER, which the legacy page did not do. It fetched `/limits` with no
     credential at all, so a registered user was always told they were anonymous — and after
     the convert fix below sends their token, an anonymous-looking banner over an account-owned
     conversion would be a straight contradiction. */
  const loadQuota = useCallback(async () => {
    const d = await apiGetOrNull<LimitsSnapshot>('/limits', { scope: 'user' });
    if (!d) return;
    if (!d.anonymous) { setQuota({ kind: 'none' }); return; }
    if (!d.enabled) { setQuota({ kind: 'off' }); return; }
    const left = d.remaining?.conversions;
    if (left == null) { setQuota({ kind: 'none' }); return; }
    setQuota({ kind: 'left', n: left });
  }, []);

  /* Once on mount, and again after a conversion. NOT on a language change: the legacy page
     re-fetched there only because it had written the banner as `innerHTML` and had no other
     way to re-translate it. Here the banner is JSX over `t`, so switching language re-renders
     it from the numbers already in hand — a second `/limits` round trip would buy nothing. */
  useEffect(() => { void loadQuota(); }, [loadQuota]);

  /* ---- the format catalogue ----
     Anything that is not WAV goes to the SERVER's converter — the same endpoint, catalogue and
     quota the Convert tab uses. Two reasons, and neither is laziness: a second encoder in the
     browser would eventually disagree with the server about what a `.gsm` is, and the daily
     allowance for signed-out visitors is only enforceable where the server does the work.
     Editing itself stays free and local; it costs us nothing. */
  useEffect(() => {
    const ac = new AbortController();
    apiGet<FormatCatalogue>('/convert/formats', { scope: 'public', signal: ac.signal })
      .then(d => {
        const list = d.formats || [];
        setFormats(list.map(f => ({ value: f.id, label: f.label || f.id })));
        const fallback = d.default && list.some(f => f.id === d.default) ? d.default : list[0]?.id;
        setFmt(fallback || '');
      })
      .catch(e => {
        // An abort is this effect being cleaned up (or StrictMode's second pass), not an
        // outage — reporting it would blank a catalogue that is about to load.
        if (e instanceof DOMException && e.name === 'AbortError') return;
        setFormats(null);
      });
    return () => ac.abort();
  }, []);

  /* ---- export ---- */
  const saveWav = useCallback(() => {
    const w = edRef.current?.wav();
    if (!w) return;
    const url = URL.createObjectURL(w.blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${w.name}-edited.wav`;
    // In the document, because a detached <a> does not reliably fire a download everywhere.
    document.body.appendChild(a);
    a.click();
    a.remove();
    // Delayed: revoking straight after the click cancels the save in some browsers.
    setTimeout(() => URL.revokeObjectURL(url), 4000);
    setExp({ text: t('ed.saved'), kind: 'ok' });
  }, [t]);

  /* TWO CALLS, AS ONE PRINCIPAL — docs/MIGRATION.md defects 1 and 2, both fixed here.

     The legacy version POSTed the WAV and then did `await r.blob()`, saving the JSON summary
     the non-stream route actually returns under a `.zip` name; the archive lives behind
     `summary.download_path`. And neither call carried a credential, so a signed-in user's
     batch was filed under their IP — which also breaks the download, because `_owner()` keys
     the token on the principal and a second request as a different one gets an indistinguishable
     404. Both calls now go out at `scope: 'user'`, so they are the same principal by
     construction, and `downloadAuthed` is the helper that exists for exactly this. */
  const convertAndDownload = useCallback(async () => {
    setExp({ text: t('ed.converting'), kind: '' });
    setConverting(true);
    try {
      const w = edRef.current?.wav();
      if (!w) throw new Error(t('ed.err.convert'));
      const fd = new FormData();
      fd.append('files', new File([w.blob], `${w.name}.wav`, { type: 'audio/wav' }));
      fd.append('format', fmt);
      const summary = await apiUpload<ConvertSummary>('/convert', fd, { scope: 'user' });
      const path = archivePath(summary);
      // A batch that converted nothing is still a 200: the per-file reason is the answer.
      if (!path) throw new Error(refusal(summary) || t('ed.err.convert'));
      await downloadAuthed(path, `${w.name}-edited.zip`, { scope: 'user' });
      setExp({ text: t('ed.saved'), kind: 'ok' });
      void loadQuota();
    } catch (e) {
      setExp({ text: apiMessage(e, t), kind: 'err' });
    } finally {
      setConverting(false);
    }
  }, [fmt, loadQuota, t]);

  /* ---- derived button state, exactly as `render()` computed it ---- */
  const off = !st.loaded;
  const noSel = off || !st.hasSelection;

  const quotaClass = quota.kind === 'none'
    ? 'quota'
    : quota.kind === 'off'
      ? 'quota warn show'
      : `quota show${quota.n <= 0 ? ' warn' : ''}`;

  return (
    <>
      <Header tag="Voice AI" />
      <main className={styles.wide}>
        <div className={quotaClass}>
          {quota.kind === 'off' ? (
            <>
              {t('quota.disabled')} <a href="/account.html">{t('nav.signin')}</a>
            </>
          ) : quota.kind === 'left' ? (
            <>
              {t('quota.using')} <b>{quota.n}</b> {t('quota.conversions')} {t('quota.left')}{' '}
              <a href="/account.html">{t('nav.signin')}</a> {t('quota.more')}
            </>
          ) : null}
        </div>

        <div style={{ marginBottom: 18 }}>
          <span className="eyebrow">{t('ed.eyebrow')}</span>
          <h1 style={{ margin: 0, fontSize: 'clamp(24px,4vw,32px)' }}>{t('ed.title')}</h1>
          <p className="lead">{t('ed.lead')}</p>
        </div>

        {/* ---- source ---- */}
        <div className="card">
          <label>{t('ed.source')}</label>
          <div
            className={`${styles.drop}${over ? ` ${styles.over}` : ''}`}
            onDragEnter={e => { e.preventDefault(); setOver(true); }}
            onDragOver={e => { e.preventDefault(); setOver(true); }}
            onDragLeave={e => { e.preventDefault(); setOver(false); }}
            onDrop={e => { e.preventDefault(); setOver(false); void loadFiles(e.dataTransfer.files); }}
          >
            <div>{t('ed.drop')}</div>
            <div className="actions" style={{ justifyContent: 'center', marginTop: 10 }}>
              <button className="primary" onClick={() => fileRef.current?.click()}>
                {t('ed.choose')}
              </button>
            </div>
            <input
              ref={fileRef}
              type="file"
              accept={ACCEPT}
              className="hidden"
              multiple
              onChange={e => {
                void loadFiles(e.target.files);
                e.target.value = '';        // so re-picking the same file fires `change` again
              }}
            />
          </div>
          <div className={`msg${msg.kind ? ` ${msg.kind}` : ''}`}>{msg.text}</div>

          {/* One row per layer: which one edits land on, where it starts, how loud, and whether
              it is heard. */}
          <div className={styles.layers}>
            {st.layers.map((l, i) => (
              <LayerRow
                key={l.id}
                layer={l}
                t={t}
                onSelect={() => edRef.current?.selectLayer(i)}
                onOffset={v => edRef.current?.moveLayer(i, v)}
                onGain={db => edRef.current?.layerGainDb(i, db)}
                onMute={() => edRef.current?.toggleMute(i)}
                onSolo={() => edRef.current?.toggleSolo(i)}
                onRemove={() => edRef.current?.removeLayer(i)}
              />
            ))}
          </div>
          <p className="hint" style={{ marginTop: 10 }}>{t('ed.layers.hint')}</p>
        </div>

        {/* ---- waveform + transport ---- */}
        <div className="card">
          <div className={styles.wrap} ref={wrapRef}>
            <canvas className={styles.canvas} tabIndex={0} aria-label={t('ed.canvas.aria')} ref={canvasRef} />
            {st.loaded ? null : <div className={styles.empty}>{t('ed.empty')}</div>}
          </div>
          {/* Written by the engine with `textContent`, from inside the rAF loop. React renders
              it with no children of its own, so the two never contend. */}
          <div className={styles.time} ref={timeRef} />

          <div className={styles.bar}>
            <button className="ghost" disabled={off} onClick={() => edRef.current?.toggle()}>
              {st.playing ? t('ed.pause') : t('ed.play')}
            </button>
            <button className="ghost" disabled={off} onClick={() => edRef.current?.stop()}>{t('ed.stop')}</button>
            <button className="ghost" disabled={noSel} onClick={() => edRef.current?.playSelection()}>{t('ed.playsel')}</button>
            <span className={styles.sep} />
            <button className="ghost" disabled={off} onClick={() => edRef.current?.markIn()}>{t('ed.markin')}</button>
            <button className="ghost" disabled={off} onClick={() => edRef.current?.markOut()}>{t('ed.markout')}</button>
            <span className={styles.sep} />
            <button className="ghost" disabled={off} onClick={() => edRef.current?.zoomIn()}>＋</button>
            <button className="ghost" disabled={off} onClick={() => edRef.current?.zoomOut()}>－</button>
            <button className="ghost" disabled={off} onClick={() => edRef.current?.zoomFit()}>{t('ed.fit')}</button>
            <button className="ghost" disabled={noSel} onClick={() => edRef.current?.zoomSelection()}>{t('ed.zoomsel')}</button>
            <button className="ghost" disabled={off} onClick={() => edRef.current?.clearSelection()}>{t('ed.selectall')}</button>
            <button className="ghost" disabled={off || st.layers.length < 2} onClick={() => edRef.current?.flatten()}>
              {t('ed.flatten')}
            </button>
            <span className={styles.sep} />
            <button className="ghost" disabled={!st.canUndo} onClick={() => edRef.current?.undo()}>{t('ed.undo')}</button>
            <button className="ghost" disabled={!st.canRedo} onClick={() => edRef.current?.redo()}>{t('ed.redo')}</button>
          </div>
          <p className="hint" style={{ marginTop: 10 }}>{t('ed.selhint')}</p>
          <p className="hint">{t('ed.markhint')}</p>
        </div>

        {/* ---- operations ---- */}
        <div className={styles.grid}>
          <div className={styles.group}>
            <h4>{t('ed.g.edit')}</h4>
            <div className={styles.bar} style={{ margin: 0 }}>
              <button className="ghost" disabled={off} onClick={() => edRef.current?.cut()}>{t('ed.cut')}</button>
              <button className="ghost" disabled={off} onClick={() => edRef.current?.trim()}>{t('ed.trim')}</button>
              <button className="ghost" disabled={off} onClick={() => edRef.current?.silence()}>{t('ed.silence')}</button>
              <button className="ghost" disabled={off} onClick={() => edRef.current?.reverse()}>{t('ed.reverse')}</button>
            </div>
            <label htmlFor="insSecs" style={{ marginTop: 12 }}>{t('ed.insert')}</label>
            <div className="inline" style={{ gap: 8 }}>
              <input
                id="insSecs"
                type="number"
                min="0.1"
                max="60"
                step="0.1"
                className="w-num"
                value={insSecs}
                onChange={e => setInsSecs(e.target.value)}
              />
              <button
                className="ghost"
                disabled={off}
                onClick={() => edRef.current?.insertSilence(parseFloat(insSecs) || 0)}
              >
                {t('ed.insert.go')}
              </button>
            </div>
          </div>

          <div className={styles.group}>
            <h4>{t('ed.g.level')}</h4>
            <div className={styles.bar} style={{ margin: 0 }}>
              <button className="ghost" disabled={off} onClick={() => edRef.current?.gainDb(3)}>+3 dB</button>
              <button className="ghost" disabled={off} onClick={() => edRef.current?.gainDb(-3)}>−3 dB</button>
              <button className="ghost" disabled={off} onClick={() => edRef.current?.normalize()}>{t('ed.normalize')}</button>
            </div>
            <div className={styles.bar}>
              <button className="ghost" disabled={off} onClick={() => edRef.current?.fadeIn()}>{t('ed.fadein')}</button>
              <button className="ghost" disabled={off} onClick={() => edRef.current?.fadeOut()}>{t('ed.fadeout')}</button>
              <button className="ghost" disabled={off} onClick={() => edRef.current?.invert()}>{t('ed.invert')}</button>
            </div>
            <p className="hint" style={{ marginTop: 10 }}>{t('ed.level.hint')}</p>
          </div>

          <div className={styles.group}>
            <h4>{t('ed.g.channels')}</h4>
            <div className={styles.bar} style={{ margin: 0 }}>
              <button className="ghost" disabled={off || st.channels < 2} onClick={() => edRef.current?.toMono()}>
                {t('ed.mono')}
              </button>
              <button className="ghost" disabled={off || st.channels >= 2} onClick={() => edRef.current?.toStereo()}>
                {t('ed.stereo')}
              </button>
              <button className="ghost" disabled={off || st.channels < 2} onClick={() => edRef.current?.swapChannels()}>
                {t('ed.swap')}
              </button>
            </div>
            {/* Built from the file that is actually open: a mono recording has no "channel 2"
                to mute, and offering one would be a button that does nothing. */}
            <div className={styles.bar}>
              {Array.from({ length: st.loaded ? st.channels : 0 }, (_, c) => (
                <Fragment key={c}>
                  <button className="ghost" disabled={off} onClick={() => edRef.current?.extractChannel(c)}>
                    {t('ed.ch.keep', { n: c + 1 })}
                  </button>
                  <button className="ghost" disabled={off} onClick={() => edRef.current?.muteChannel(c)}>
                    {t('ed.ch.mute', { n: c + 1 })}
                  </button>
                </Fragment>
              ))}
            </div>
            <p className="hint" style={{ marginTop: 10 }}>{t('ed.channels.hint')}</p>
          </div>
        </div>

        {/* ---- export ---- */}
        <div className="card" style={{ marginTop: 16 }}>
          <h3>{t('ed.g.export')}</h3>
          <p className="hint">{t('ed.export.hint')}</p>
          <div className={styles.bar}>
            <button className="primary" disabled={off} onClick={saveWav}>{t('ed.dl.wav')}</button>
            <span className={styles.sep} />
            <label htmlFor="fmt" style={{ margin: 0 }}>{t('ed.format')}</label>
            <Select
              id="fmt"
              value={fmt}
              onChange={setFmt}
              options={formats ?? []}
              disabled={!formats}
              placeholder={formats ? undefined : t('ed.fmt.unavailable')}
              ariaLabel={t('ed.format')}
              style={{ minWidth: 190 }}
            />
            <button className="ghost" disabled={off || converting} onClick={() => void convertAndDownload()}>
              {t('ed.dl.convert')}
            </button>
          </div>
          <div className={`msg${exp.kind ? ` ${exp.kind}` : ''}`}>{exp.text}</div>
        </div>
      </main>
    </>
  );
}

/* One layer row.

   Its own component for one reason: the "starts at" box. The legacy page rebuilt the layer
   list only when its SHAPE changed and, when it did not, refreshed the offset input only
   `if (document.activeElement !== off)` — because rewriting the value under a caret fights the
   number the user is typing. The same rule here is `focused`, and the commit is on blur and
   Enter rather than on every keystroke: the DOM `change` event the legacy code listened for
   fires exactly there, and one undo entry per keystroke is not an edit history. */
function LayerRow({
  layer, t, onSelect, onOffset, onGain, onMute, onSolo, onRemove,
}: {
  layer: LayerView;
  t: (k: string, v?: Record<string, string | number>) => string;
  onSelect: () => void;
  onOffset: (seconds: number) => void;
  onGain: (db: number) => void;
  onMute: () => void;
  onSolo: () => void;
  onRemove: () => void;
}): JSX.Element {
  const [text, setText] = useState(layer.offset.toFixed(2));
  const focused = useRef(false);

  useEffect(() => {
    if (!focused.current) setText(layer.offset.toFixed(2));
  }, [layer.offset]);

  const commit = () => onOffset(parseFloat(text) || 0);

  return (
    <div
      className={`${styles.layer}${layer.active ? ` ${styles.on}` : ''}`}
      onClick={e => {
        const el = e.target as Element;
        if (el.closest('button') || el.tagName === 'INPUT') return;
        onSelect();
      }}
    >
      {/* Plain text, never markup: the name comes from a filename the user chose. */}
      <span className={styles.nm}>{layer.name}</span>
      <span className={styles.meta}>{layer.duration.toFixed(1)}s · {layer.channels}ch</span>
      <label className={styles.meta} style={{ margin: 0 }}>{t('ed.startsat')}</label>
      <input
        type="number"
        step="0.1"
        min="0"
        value={text}
        aria-label={t('ed.startsat')}
        onFocus={() => { focused.current = true; }}
        onChange={e => setText(e.target.value)}
        onBlur={() => { focused.current = false; commit(); }}
        onKeyDown={e => { if (e.key === 'Enter') commit(); }}
      />
      <button className="ghost" onClick={() => onGain(3)}>+3 dB</button>
      <button className="ghost" onClick={() => onGain(-3)}>−3 dB</button>
      <button className="ghost" onClick={onMute}>{layer.muted ? t('ed.unmute') : t('ed.mute')}</button>
      <button className="ghost" onClick={onSolo}>{layer.solo ? t('ed.unsolo') : t('ed.solo')}</button>
      <button className="act danger" onClick={onRemove} title={t('ed.removelayer')} aria-label={t('ed.removelayer')}>
        🗑
      </button>
    </div>
  );
}
