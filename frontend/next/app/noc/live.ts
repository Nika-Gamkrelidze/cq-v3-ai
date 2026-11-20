/* The pure half of the live NOC feed — no imports, so the unit tests can load it straight
   from Node (the hook beside it pulls in `@/` aliases, which Node's resolver does not know). */

export type StreamState = 'connecting' | 'live' | 'retrying' | 'paused' | 'polling';

/** The server sends a keep-alive every 15 s; twice that with nothing at all means the pipe is dead. */
export const SILENCE_MS = 40_000;

const BACKOFF_MS = [1000, 2000, 5000, 10_000];

/** Wait before reconnect number `attempt` (0 = the first retry): 1, 2, 5, then 10 s for ever. */
export function backoffMs(attempt: number): number {
  return BACKOFF_MS[Math.min(Math.max(0, Math.floor(attempt) || 0), BACKOFF_MS.length - 1)];
}

/** Which dot and word the page shows. A "live" feed that has not delivered for longer than the
    silence limit is not live, whatever the state variable still says. */
export function liveIndicator(
  { state, ageSeconds }: { state: StreamState; ageSeconds: number | null },
): { key: StreamState; color: string } {
  const key: StreamState = state === 'live' && ageSeconds !== null && ageSeconds * 1000 > SILENCE_MS ? 'retrying' : state;
  const color = key === 'live' ? 'var(--ok)'
    : key === 'paused' ? 'var(--muted)'
    : key === 'polling' ? 'var(--beam)'
    : 'var(--pending)';
  return { key, color };
}
