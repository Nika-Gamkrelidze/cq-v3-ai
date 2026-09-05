'use client';
import { useCallback, useEffect, useState } from 'react';

type Theme = 'dark' | 'light';

/** Light/dark, stored under the same key and stamped on the same attribute as the legacy
    pages, so the choice survives moving between the two stacks. */
export function useTheme() {
  const [theme, setTheme] = useState<Theme>('dark');

  useEffect(() => {
    let stored: Theme = 'dark';
    try { stored = (localStorage.getItem('cq_theme') as Theme) || 'dark'; } catch { /* private */ }
    setTheme(stored);
    document.documentElement.setAttribute('data-theme', stored);
  }, []);

  const toggle = useCallback(() => {
    setTheme(prev => {
      const next: Theme = prev === 'light' ? 'dark' : 'light';
      try { localStorage.setItem('cq_theme', next); } catch { /* private */ }
      document.documentElement.setAttribute('data-theme', next);
      return next;
    });
  }, []);

  return { theme, toggle };
}
