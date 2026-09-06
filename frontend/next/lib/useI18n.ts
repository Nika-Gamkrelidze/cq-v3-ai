'use client';
import { useCallback, useEffect, useState } from 'react';
import { currentLang, setLang as persist, translate, type Lang } from './i18n';

/** Language as React state, kept in step with the legacy pages.

    The stored key and the `cq:lang` event are shared with brand.js, so switching language on
    a migrated page and then navigating to one that has not moved yet keeps the choice. The
    initial value is read in an effect rather than during render: the page is prerendered at
    BUILD time, where there is no localStorage, and reading it during render would make the
    server and client markup disagree. */
export function useI18n() {
  const [lang, setLangState] = useState<Lang>('en');

  // BOTH targets. A `CustomEvent` is non-bubbling unless asked, and brand.js dispatches
  // `cq:lang` on `document` with the default — so a document dispatch never reaches a window
  // listener, and vice versa. Listening on one side only means the two stacks cannot tell each
  // other about a language change at all. It costs nothing to hear both: the handler re-reads
  // the stored value, so a doubled event sets identical state and React drops the re-render.
  useEffect(() => {
    setLangState(currentLang());
    const onLang = () => setLangState(currentLang());
    document.addEventListener('cq:lang', onLang as EventListener);
    window.addEventListener('cq:lang', onLang as EventListener);
    return () => {
      document.removeEventListener('cq:lang', onLang as EventListener);
      window.removeEventListener('cq:lang', onLang as EventListener);
    };
  }, []);

  const t = useCallback(
    (key: string, vars?: Record<string, string | number>) => translate(lang, key, vars),
    [lang],
  );

  const change = useCallback((next: Lang) => { persist(next); setLangState(next); }, []);

  return { lang, t, setLang: change };
}
