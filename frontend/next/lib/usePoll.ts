import { useEffect, useRef } from 'react';

/* Polling that stops with the tab. `document.hidden` gates every tick; coming back into view
   fires one at once so the operator never looks at a strip that is minutes stale after an
   alt-tab. The callback is read through a ref so a language switch or a range change does
   not tear the interval down and up again. */
export function usePoll(fn: () => void, intervalMs: number, deps: unknown[]) {
  const ref = useRef(fn);
  useEffect(() => { ref.current = fn; });
  useEffect(() => {
    ref.current();
    const id = setInterval(() => { if (!document.hidden) ref.current(); }, intervalMs);
    const onVis = () => { if (!document.hidden) ref.current(); };
    document.addEventListener('visibilitychange', onVis);
    return () => {
      clearInterval(id);
      document.removeEventListener('visibilitychange', onVis);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intervalMs, ...deps]);
}
