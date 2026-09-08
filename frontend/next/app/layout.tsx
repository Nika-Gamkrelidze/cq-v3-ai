import type { Metadata } from 'next';
import { ModalHost } from '@/components/ui/Modal';
import { ToastHost } from '@/components/ui/Toast';
import './globals.css';

export const metadata: Metadata = {
  title: 'CommuniQ',
  description: 'Call analysis, knowledge bases and audio tools for support teams.',
};

/* The theme is stamped on <html> BEFORE first paint by the inline script below, exactly as
   the legacy pages do it. Doing it in React instead would render the dark default first and
   flash it at every light-theme user on every navigation — a static export has no server to
   read the preference for us. */
const THEME_BOOTSTRAP = `try{document.documentElement.setAttribute('data-theme',localStorage.getItem('cq_theme')||'dark')}catch(e){}`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" data-theme="dark">
      <head>
        <link rel="icon" href="/favicon.png" />
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
        <link
          href="https://fonts.googleapis.com/css2?family=Manrope:wght@600;700;800&family=Inter:wght@400;500;600;700&family=Noto+Sans+Georgian:wght@400;500;600;700&display=swap"
          rel="stylesheet"
        />
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOTSTRAP }} />
      </head>
      {/* The two singleton hosts. They live in the LAYOUT, not on each page, because
          `toast()` and `confirmDialog()` are module-level functions any code can call — a page
          that forgot to mount them would swallow its own notices, and a page that mounted its
          own copy would render every toast twice. The layout is also what makes them survive a
          client-side navigation: a confirm awaited across one keeps its promise. */}
      <body>
        {children}
        <ToastHost />
        <ModalHost />
      </body>
    </html>
  );
}
