'use client';
import { useCallback, useEffect, useRef, useState } from 'react';
import Header from '@/components/Header';
import { apiGetOrNull, readSession } from '@/lib/session';
import { useI18n } from '@/lib/useI18n';
import styles from './home/home.module.css';
import { AudioUploadPanel, type ClipResult } from './home/AudioUploadPanel';
import { ConvertPanel } from './home/ConvertPanel';
import { QuotaBanner } from './home/QuotaBanner';
import { SentimentCard } from './home/SentimentCard';
import { TtsPanel } from './home/TtsPanel';
import type { LimitsSnapshot } from './home/quota';

/* The public app — `index.html` → `/`.
   ===================================
   The only page an anonymous visitor uses, and the entry point the CommuniQ brand site links
   to. Four tabs: text to speech, transcription, the sentiment read and the audio converter.

   THE ONE INVARIANT OF THIS FILE, and of everything under `app/home/`: it sends the REGISTERED
   USER's token and nothing else. The legacy `pubAuth()` attaches `Authorization: Bearer
   <cq_user_token>` when there is one and no header at all otherwise — never `X-Admin-Token`,
   never a tenant Bearer — because a registered account has its own daily allowance and its own
   history to spend, while an operator's and a workspace's surfaces are elsewhere and their
   requests have always run here as a guest. Reaching for `authHeaders()` (or omitting `scope`,
   which falls back to it) would send an operator's superadmin token to `/tts`, `/transcribe`,
   `/sentiment` and `/limits` and silently promote them on the PUBLIC surface. In this stack
   that means `scope: 'user'` on the four routes that spend an allowance, and `scope: 'public'`
   on the catalogues and on the voice preview, which the legacy page sends unsigned too.
   docs/MIGRATION.md lists it first under "Deliberate decisions the port must preserve".

   WHICH TABS EXIST is the other half of the same decision. Signed out, the public surface is
   text-to-speech and the converter: the two things that cost an ffmpeg process and a few
   ElevenLabs characters. Transcription and the sentiment read are what a workspace pays for, so
   their tabs are NOT RENDERED rather than hidden — a panel left in the document is one class
   away from visible and still reachable by anything that moves focus into it. A registered
   account counts as signed in for exactly this reason: it has a daily allowance of its own for
   the same two features, so removing the tabs would hide a door it already has the key to. */

type TabName = 'tts' | 'stt' | 'sentiment' | 'convert';

export default function PublicPage() {
  const { t } = useI18n();

  /* Session, health and the allowance are all browser-only: this page is prerendered at BUILD
     time, where there is no sessionStorage and no API. `ready` is what everything below waits
     on, so nothing decides "signed out" from the prerender's answer. */
  const [ready, setReady] = useState(false);
  const [signedIn, setSignedIn] = useState(false);
  const [health, setHealth] = useState<'wait' | 'ok' | 'bad'>('wait');
  const [limits, setLimits] = useState<LimitsSnapshot | null>(null);
  const [tab, setTab] = useState<TabName>('tts');
  const [convertOn, setConvertOn] = useState(true);
  const booted = useRef(false);

  useEffect(() => {
    const s = readSession();
    setSignedIn(!!(s.admin || s.tenant || s.user));
    setReady(true);
  }, []);

  /** `GET /limits` — at `scope: 'user'`, like every other allowance-spending call here.
      A failure LEAVES THE PREVIOUS BANNER: `apiGetOrNull` answers `null` for "the request
      failed", and replacing a real count with nothing (or worse, with zeroes) would tell a
      visitor something about their allowance that we do not currently know. */
  const loadLimits = useCallback(async () => {
    const d = await apiGetOrNull<LimitsSnapshot>('/limits', { scope: 'user' });
    if (d) setLimits(d);
  }, []);

  useEffect(() => {
    if (!ready || booted.current) return;
    booted.current = true;
    void loadLimits();
    void (async () => {
      const d = await apiGetOrNull<{ status?: string }>('/health', { scope: 'public' });
      setHealth(d && d.status === 'ok' ? 'ok' : 'bad');
    })();
  }, [ready, loadLimits]);

  /* The converter takes its own tab away when the server has no ffmpeg (or predates the
     feature). If the visitor was looking at it, move them to the first tab that is left —
     which is always TTS, the one tab that is unconditionally here. */
  const dropConvert = useCallback(() => {
    setConvertOn(false);
    setTab(cur => (cur === 'convert' ? 'tts' : cur));
  }, []);

  const tabs: { name: TabName; icon: string; key: string }[] = [
    { name: 'tts', icon: '🗣', key: 'tab.tts' },
    ...(signedIn ? ([
      { name: 'stt' as const, icon: '🎧', key: 'tab.stt' },
      { name: 'sentiment' as const, icon: '🎭', key: 'tab.sentiment' },
    ]) : []),
    ...(convertOn ? [{ name: 'convert' as const, icon: '🎚', key: 'tab.convert' }] : []),
  ];

  const panel = (name: TabName) => (name === tab ? 'panel active' : 'panel');

  return (
    <>
      <Header tag="Voice AI" />
      <main className="narrow">
        <QuotaBanner limits={limits} signedIn={signedIn} />

        <div style={{ marginBottom: 22 }}>
          <span className="eyebrow">{t('hero.eyebrow')}</span>
          {/* The API status dot. `Header` exposes only `tag`, where the legacy page passed an
              `extra` slot for this — and growing the shared header is not this page's call — so
              the dot sits on the first line of the page instead. */}
          <span className={`dot ${health} ${styles.status}`} title="API status" />
          <h1 style={{ margin: 0, fontSize: 'clamp(24px,4vw,32px)' }}>{t('hero.title')}</h1>
        </div>

        <div className="tabs">
          {tabs.map(x => (
            <div
              key={x.name}
              className={x.name === tab ? 'tab active' : 'tab'}
              onClick={() => setTab(x.name)}
              role="button"
              tabIndex={0}
              onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setTab(x.name); } }}
            >
              <span>{x.icon}</span> <span>{t(x.key)}</span>
            </div>
          ))}
        </div>

        {/* Every panel that EXISTS stays mounted and is shown or hidden by `.panel.active`,
            exactly as the legacy page does it. That is what keeps the single audio player alive
            across a tab switch — unmounting the TTS panel would tear the player down mid-clip —
            and it is why the two signed-in panels are excluded from the tree entirely rather
            than merely left inactive. */}
        <section className={panel('tts')}>
          <TtsPanel ready={ready} signedIn={signedIn} onSpent={() => void loadLimits()} />
        </section>

        {signedIn ? (
          <>
            <section className={panel('stt')}>
              <AudioUploadPanel
                idPrefix="audio"
                headingKey="stt.heading"
                runKey="btn.transcribe"
                doneKey="stt.done"
                path="/transcribe"
                onSpent={() => void loadLimits()}
              >
                {d => <TranscriptCard result={d} />}
              </AudioUploadPanel>
            </section>

            {/* Sentiment is its own tab, deliberately separate from transcription: upload or
                record audio and get only a sentiment read-out — what was said and how it
                sounded — with no transcript clutter. */}
            <section className={panel('sentiment')}>
              <AudioUploadPanel
                idPrefix="sn"
                headingKey="sn.heading"
                runKey="sn.run"
                doneKey="sn.done"
                path="/sentiment"
                onSpent={() => void loadLimits()}
              >
                {d => (d.sentiment && (d.sentiment.text || d.sentiment.prosody)
                  ? <SentimentCard sn={d.sentiment} />
                  : <div className="card"><p className="muted">{t('sn.none')}</p></div>)}
              </AudioUploadPanel>
            </section>
          </>
        ) : null}

        {convertOn ? (
          <section className={panel('convert')}>
            <ConvertPanel limits={limits} onSpent={() => void loadLimits()} onUnavailable={dropConvert} />
          </section>
        ) : null}
      </main>
    </>
  );
}

/** The transcript card: what was said, and which language the recogniser heard. */
function TranscriptCard({ result }: { result: ClipResult }) {
  const { t } = useI18n();
  return (
    <div className="card">
      <div className="row">
        <div>
          <h3>{t('res.transcript')}</h3>
          <div className="kv"><b>{t('res.language')}</b><span>{result.language || '—'}</span></div>
        </div>
      </div>
      <pre className="transcript">{result.transcript || t('res.empty')}</pre>
    </div>
  );
}
