'use client';
import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError, adminOnlyHeaders, apiBase, keyForStatus } from '@/lib/session';
import { readSseBody } from '@/lib/sse';
import { useI18n } from '@/lib/useI18n';
import { SessionExpired, adminGet, errText } from '../console/api';
import { SILENCE_MS, backoffMs, type StreamState } from './live';
import type { Noc } from './types';

/* The live NOC feed: one REST read to paint at once, then the server's snapshot stream.

   The collector on the server computes ONE snapshot per cycle for every viewer; this hook only
   listens. Reconnects back off 1, 2, 5, 10 s; a stream that goes silent for SILENCE_MS is
   treated as dead; a tab in the background holds no connection at all and reconnects the moment
   it is shown. A backend without the stream route (404/405) is polled every 10 s instead. */

const POLL_MS = 10_000;

export interface NocStream {
  data: Noc | null;
  state: StreamState;
  failed: string | null;
  /** `Date.now()` of the last snapshot received. */
  lastAt: number | null;
  reload: () => void;
}

export function useNocStream(enabled: boolean): NocStream {
  const { t } = useI18n();
  const tRef = useRef(t);
  useEffect(() => { tRef.current = t; });
  const [data, setData] = useState<Noc | null>(null);
  const [state, setState] = useState<StreamState>('connecting');
  const [failed, setFailed] = useState<string | null>(null);
  const [lastAt, setLastAt] = useState<number | null>(null);
  const [epoch, setEpoch] = useState(0);
  const reload = useCallback(() => setEpoch(n => n + 1), []);

  useEffect(() => {
    if (!enabled) return;
    let stop = false;
    let gotFrame = false;
    let polling = false;
    let attempt = 0;
    let ctl: AbortController | null = null;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let pollId: ReturnType<typeof setInterval> | undefined;

    const accept = (d: Noc) => { setData(d); setFailed(null); setLastAt(Date.now()); };
    const fail = (e: unknown) => {
      if (e instanceof SessionExpired) return;
      const tt = tRef.current;
      setFailed(e instanceof ApiError && e.status === 404 ? tt('noc.unavailable') : errText(e, tt, 'noc.loadfail'));
    };

    const pollOnce = () => {
      if (document.hidden) return;
      adminGet<Noc>('/admin/noc/overview').then(accept).catch(fail);
    };
    const startPolling = () => {
      polling = true;
      setState(document.hidden ? 'paused' : 'polling');
      pollOnce();
      pollId = setInterval(pollOnce, POLL_MS);
    };

    // Detach the current stream first so its own catch path knows it was replaced, not dropped.
    const drop = () => { const old = ctl; ctl = null; old?.abort(); };

    const connect = async () => {
      clearTimeout(retry);
      if (stop || polling || document.hidden) return;
      const mine = new AbortController();
      const old = ctl;
      ctl = mine;
      old?.abort();
      setState(attempt ? 'retrying' : 'connecting');
      let silent: ReturnType<typeof setTimeout> | undefined;
      const arm = () => { clearTimeout(silent); silent = setTimeout(() => mine.abort(), SILENCE_MS); };
      try {
        arm();
        const r = await fetch(`${apiBase()}/admin/noc/stream`, {
          headers: adminOnlyHeaders({ Accept: 'text/event-stream' }),
          signal: mine.signal,
        });
        // adminGet owns the expired-session redirect; ask it once so a 401 here ends the same way.
        if (r.status === 401) await adminGet('/admin/noc/overview');
        if (r.status === 404 || r.status === 405) { clearTimeout(silent); if (ctl === mine) ctl = null; startPolling(); return; }
        if (!r.ok) throw new ApiError({ status: r.status, detail: null, ...keyForStatus(r.status) });
        await readSseBody(r, (name, d) => {
          arm();
          if (name !== 'snapshot' || !d || ctl !== mine) return;
          gotFrame = true;
          attempt = 0;
          setState('live');
          accept(d as unknown as Noc);
        });
      } catch (e) {
        if (e instanceof SessionExpired) { stop = true; return; } // the page is already leaving
        if (ctl === mine && !(e instanceof DOMException && e.name === 'AbortError')) {
          if (!gotFrame) fail(e);
        }
      } finally {
        clearTimeout(silent);
      }
      if (stop || ctl !== mine) return; // paused, unmounted or replaced — not a failure
      ctl = null;
      setState('retrying');
      retry = setTimeout(connect, backoffMs(attempt++));
    };

    const onVis = () => {
      if (document.hidden) {
        clearTimeout(retry);
        drop();
        setState('paused');
      } else if (polling) {
        setState('polling');
        pollOnce();
      } else {
        attempt = 0;
        void connect();
      }
    };
    document.addEventListener('visibilitychange', onVis);

    if (document.hidden) setState('paused');
    else {
      // Paint from the plain read while the stream connects; a frame that got there first wins.
      adminGet<Noc>('/admin/noc/overview').then(d => { if (!gotFrame) accept(d); }).catch(e => { if (!gotFrame) fail(e); });
      void connect();
    }

    return () => {
      stop = true;
      clearTimeout(retry);
      clearInterval(pollId);
      drop();
      document.removeEventListener('visibilitychange', onVis);
    };
  }, [enabled, epoch]);

  return { data, state, failed, lastAt, reload };
}
