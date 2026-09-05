'use client';
import { useEffect, useState } from 'react';
import { LANGS, type Lang } from '@/lib/i18n';
import { readSession, signOut, type Role } from '@/lib/session';
import { useI18n } from '@/lib/useI18n';
import { useTheme } from '@/lib/useTheme';

/* The one navigation bar.

   Its shape follows the visitor's ROLE, never the page they are on — the legacy app grew a
   hand-written nav per page and the answer to "where can I go from here" changed as you
   moved. Ported pages link to clean paths (/usage); pages that have not migrated yet are
   still .html files, and both are listed here so the bar is complete during the migration.
   As each page ports, its href loses the extension and nothing else changes. */

interface Item {
  key: string;
  href: string;
  roles: Role[] | 'all';
}

const ITEMS: Item[] = [
  { key: 'nav.public', href: '/index.html', roles: 'all' },
  { key: 'nav.editor', href: '/editor.html', roles: 'all' },
  { key: 'nav.usage', href: '/usage', roles: ['superadmin'] },
  { key: 'nav.aicfg', href: '/ai-config', roles: ['superadmin'] },
  { key: 'nav.console', href: '/admin.html', roles: ['superadmin'] },
  { key: 'nav.kb', href: '/tenant.html', roles: ['superadmin'] },
  { key: 'nav.workspace', href: '/tenant.html', roles: ['tenant'] },
  { key: 'nav.account', href: '/account.html', roles: ['user'] },
];

export default function Header({ tag }: { tag?: string }) {
  const { lang, t, setLang } = useI18n();
  const { theme, toggle } = useTheme();
  const [role, setRole] = useState<Role>('anonymous');
  const [path, setPath] = useState('');

  // Session and location are read after mount: this page is prerendered at BUILD time, where
  // neither exists, and reading them during render would make the markup disagree on hydrate.
  useEffect(() => {
    setRole(readSession().role);
    setPath(window.location.pathname);
  }, []);

  const visible = ITEMS.filter(i => i.roles === 'all' || i.roles.includes(role));
  const signedIn = role !== 'anonymous';

  return (
    <header className="app-header">
      <a className="brand" href="/index.html">
        <img className="brand-logo on-dark" src="/cq-logo-on-dark.png" alt="CommuniQ" />
        <img className="brand-logo on-light" src="/cq-logo.png" alt="CommuniQ" />
        {tag ? <span className="brand-tag">{tag}</span> : null}
      </a>

      <nav className="app-nav">
        {visible.map(i => (
          <a key={i.key} href={i.href} className={path === i.href ? 'active' : undefined}>
            {t(i.key)}
          </a>
        ))}
        {signedIn ? (
          <a
            href="#"
            onClick={e => {
              e.preventDefault();
              signOut();
              window.location.href = '/index.html';
            }}
          >
            {t('nav.logout')}
          </a>
        ) : (
          <>
            <a href="/tenant.html">{t('nav.signin')}</a>
            <a href="/account.html">{t('nav.create')}</a>
          </>
        )}

        <div className="lang-switch" role="group" aria-label="Language">
          {LANGS.map(l => (
            <button
              key={l}
              type="button"
              className={l === lang ? 'active' : undefined}
              onClick={() => setLang(l as Lang)}
            >
              {l.toUpperCase()}
            </button>
          ))}
        </div>
        <button className="icon-btn" type="button" onClick={toggle} aria-label="Theme">
          {theme === 'light' ? '☾' : '☀'}
        </button>
      </nav>
    </header>
  );
}
