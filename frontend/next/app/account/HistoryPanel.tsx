'use client';
/* History — four lists, one per thing this account can produce.
   ============================================================
   Every list reads through `listJson` below, and the reason it returns THREE values rather
   than an array is written up in `lib/session.ts::apiGetOrNull` and in MIGRATION.md's list of
   decisions the port must preserve: `null` means "the request failed", `[]` means "the account
   genuinely has none". Collapsing them tells someone their history is empty when the server is
   merely down. */

import { useCallback, useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from 'react';
import { AudioPlayer, type PlayerHandle } from '@/components/AudioPlayer';
import { toast } from '@/components/ui/Toast';
import { bytes as human, dateTime, durationOrEmpty } from '@/lib/format';
import { ApiError, apiBlob, apiGet, apiMessage, downloadAuthed } from '@/lib/session';
import { useI18n } from '@/lib/useI18n';
import styles from './account.module.css';
import { expiryLabel } from './expiry';
import type { ConversionRow, RecordingRow, SummaryRow, TtsRow } from './types';

export interface HistoryPanelProps {
  /** The tab is on screen. Lists are (re)read when it becomes so, exactly as `showTab` does. */
  active: boolean;
  /** Bumped by a finished conversion, so a batch that lands while History is already open
      shows up without a manual refresh. */
  conversionEpoch: number;
  onUnauthorized: () => void;
  onOpen: (kind: 'rec' | 'sum', id: string) => void;
}

/** null = the request failed; [] = the account genuinely has none. Never collapse the two. */
type List<T> = T[] | null;

export function HistoryPanel({ active, conversionEpoch, onUnauthorized, onOpen }: HistoryPanelProps) {
  const { t } = useI18n();

  const [recs, setRecs] = useState<List<RecordingRow>>([]);
  const [sums, setSums] = useState<List<SummaryRow>>([]);
  const [clips, setClips] = useState<List<TtsRow>>([]);
  const [convs, setConvs] = useState<List<ConversionRow>>([]);

  const player = useRef<PlayerHandle>(null);
  const playingPath = useRef('');
  const [loadingClip, setLoadingClip] = useState('');

  /* Read a JSON list defensively: an error body or an expired-token payload must never reach
     `.map` / `.length`. `apiGetOrNull` would swallow the 401 this page has to act on, so the
     three-value contract is rebuilt here around `apiGet`. */
  const listJson = useCallback(async <T,>(path: string): Promise<List<T>> => {
    try {
      const d = await apiGet<unknown>(path, { scope: 'user' });
      return Array.isArray(d) ? (d as T[]) : null;
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) onUnauthorized();
      return null;
    }
  }, [onUnauthorized]);

  const loadConversions = useCallback(async () => {
    setConvs(await listJson<ConversionRow>('/convert/history?limit=25'));
  }, [listJson]);

  const load = useCallback(() => {
    void (async () => setRecs(await listJson<RecordingRow>('/recordings?limit=25')))();
    void (async () => setSums(await listJson<SummaryRow>('/summaries?limit=25')))();
    void (async () => setClips(await listJson<TtsRow>('/tts/history?limit=25')))();
    void loadConversions();
  }, [listJson, loadConversions]);

  useEffect(() => { if (active) load(); }, [active, load]);
  // A batch that finished while this tab was already open.
  useEffect(() => { if (active && conversionEpoch) void loadConversions(); }, [conversionEpoch, active, loadConversions]);

  /* The clip route is scope-checked, so the <audio> cannot fetch it by URL — the bytes come
     down with the session header once and play from an object URL. One player for the tab,
     re-pointed; a second one would leave a second play bar on the page. */
  async function playClip(path: string) {
    if (playingPath.current === path) { player.current?.toggle(); return; }
    setLoadingClip(path);
    try {
      const blob = await apiBlob(path, { scope: 'user' });
      playingPath.current = path;
      player.current?.load(URL.createObjectURL(blob), 'speech.mp3');
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) onUnauthorized();
      // Not yours, never existed and already purged are ONE answer from the server (404), so
      // the only honest thing to say is that the clip is no longer there.
      else toast(e instanceof ApiError && e.status === 404 ? t('ac.hist.gone') : apiMessage(e, t), 'err');
    } finally {
      setLoadingClip('');
    }
  }

  /** The empty / could-not-load half of every list. Returns null when there ARE rows. */
  const placeholder = (rows: List<unknown>, emptyKey: string) => {
    if (rows === null) return <div className="msg err">{t('err.unavailable')}</div>;
    if (!rows.length) return <div className="empty">{t(emptyKey)}</div>;
    return null;
  };

  /* A history row is a link to something, so the whole row is the target rather than a tiny
     "open" affordance in the last column. `tabIndex` and Enter are new: the legacy row was
     mouse-only, and this page is the one an account holder lives on. */
  const rowProps = (kind: 'rec' | 'sum', id: string) => ({
    className: styles.actRow,
    tabIndex: 0,
    onClick: () => onOpen(kind, id),
    onKeyDown: (e: ReactKeyboardEvent) => { if (e.key === 'Enter') { e.preventDefault(); onOpen(kind, id); } },
  });

  const dot = (on: boolean | undefined, key: string) => (
    <span className={`pill ${on ? 'ready' : 'notinkb'}`}>{t(key)}</span>
  );

  return (
    <>
      <div className="card">
        <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
          <h3 style={{ margin: 0 }}>{t('ac.hist.recordings')}</h3>
          <button type="button" className="ghost" onClick={load}>{t('btn.refresh')}</button>
        </div>
        <div style={{ marginTop: 12 }}>
          {placeholder(recs, 'ac.hist.none.rec') || (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>{t('th.file')}</th>
                    <th>{t('ac.th.source')}</th>
                    <th className="hide-md">{t('th.lang')}</th>
                    <th className="hide-md">{t('ac.th.duration')}</th>
                    <th>{t('ac.th.ran')}</th>
                    <th>{t('th.when')}</th>
                  </tr>
                </thead>
                <tbody>
                  {(recs || []).map(r => (
                    <tr key={r.id} {...rowProps('rec', r.id)}>
                      <td>{r.filename || t('ac.hist.pasted')}</td>
                      <td>{t(r.source === 'text' ? 'ac.src.text' : 'ac.src.audio')}</td>
                      <td className="hide-md">{r.language || '—'}</td>
                      <td className="hide-md">{durationOrEmpty(r.duration_s)}</td>
                      <td>
                        <span className={styles.ranDots}>
                          {dot(r.ran?.score, 'wb.tab.score')}
                          {dot(r.ran?.semantic, 'wb.tab.semantic')}
                        </span>
                      </td>
                      <td className="hint">{dateTime(r.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      <div className="card">
        <h3>{t('ac.hist.summaries')}</h3>
        <div style={{ marginTop: 12 }}>
          {placeholder(sums, 'ac.hist.none.sum') || (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>{t('ac.th.summary')}</th>
                    <th>{t('ac.th.calls')}</th>
                    <th className="hide-md">{t('th.lang')}</th>
                    <th>{t('th.when')}</th>
                  </tr>
                </thead>
                <tbody>
                  {(sums || []).map(r => (
                    <tr key={r.id} {...rowProps('sum', r.id)}>
                      <td>{(r.short_summary || '').slice(0, 140) || '—'}</td>
                      <td>{Number(r.call_count) || 0}</td>
                      <td className="hide-md">{r.language || '—'}</td>
                      <td className="hint">{dateTime(r.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      <div className="card">
        <h3>{t('ac.hist.tts')}</h3>
        <div style={{ marginTop: 12 }}>
          {placeholder(clips, 'ac.hist.none.tts') || (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>{t('f.text')}</th>
                    <th className="hide-md">{t('th.lang')}</th>
                    <th>{t('th.when')}</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {(clips || []).map(r => (
                    <tr key={r.id}>
                      <td>{r.text || '—'}</td>
                      <td className="hide-md">{r.language_code || '—'}</td>
                      <td className="hint">{dateTime(r.created_at)}</td>
                      <td>
                        {r.has_audio && r.audio_url ? (
                          <button
                            type="button" className="act"
                            title={t('ac.hist.play')} aria-label={t('ac.hist.play')}
                            disabled={loadingClip === r.audio_url}
                            onClick={() => void playClip(r.audio_url as string)}
                          >
                            ▶
                          </button>
                        ) : (
                          <span className="hint">{t('ac.hist.gone')}</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
        <AudioPlayer ref={player} />
      </div>

      <div className="card">
        <h3>{t('ac.hist.conversions')}</h3>
        <div style={{ marginTop: 12 }}>
          {placeholder(convs, 'ac.hist.none.conv') || (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>{t('cv.format')}</th>
                    <th>{t('ac.th.files')}</th>
                    <th className="hide-md">{t('th.size')}</th>
                    <th>{t('th.when')}</th>
                    <th>{t('ac.th.expires')}</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {(convs || []).map(r => (
                    <tr key={r.token}>
                      <td>{r.format || '—'}</td>
                      <td>{Number(r.file_count) || 0}</td>
                      <td className="hide-md">{human(r.total_bytes)}</td>
                      <td className="hint">{dateTime(r.created_at)}</td>
                      <td className="hint">{expiryLabel(r.expires_at, t) || '—'}</td>
                      <td>
                        {r.download_path ? (
                          <button
                            type="button" className="act"
                            title={t('cv.download')} aria-label={t('cv.download')}
                            onClick={() => {
                              void downloadAuthed(r.download_path as string, 'converted.zip', { scope: 'user' })
                                .catch((e: unknown) => {
                                  if (e instanceof ApiError && e.status === 401) { onUnauthorized(); return; }
                                  toast(apiMessage(e, t), 'err');
                                });
                            }}
                          >
                            {/* Same glyph as the player's download control, and an inline SVG
                                for the same reason: U+2B73 is missing from the Georgian and
                                Russian font stacks and rendered as a tofu box. */}
                            <svg
                              className="cq-i" viewBox="0 0 16 16" width="15" height="15" aria-hidden="true"
                              focusable="false" fill="none" stroke="currentColor" strokeWidth="1.5"
                              strokeLinecap="round" strokeLinejoin="round"
                            >
                              <path d="M8 2.5v7.5m0 0L5.2 7.2M8 10l2.8-2.8" />
                              <path d="M2.8 12.2v.8a1.2 1.2 0 0 0 1.2 1.2h8a1.2 1.2 0 0 0 1.2-1.2v-.8" />
                            </svg>
                          </button>
                        ) : (
                          <span className="hint">{t('ac.hist.expired')}</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </>
  );
}
