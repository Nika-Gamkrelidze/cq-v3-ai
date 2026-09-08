'use client';
import { Fragment } from 'react';
import { useI18n } from '@/lib/useI18n';
import { quotaView, type LimitsSnapshot } from './quota';

/* The anonymous allowance line, above everything else on the page.
   ===============================================================
   `quotaView` decides WHICH of the four states this is (see `quota.ts`, which is where the
   reasoning lives); this file only renders them.

   The sign-in link points at the workspace login, which is still `tenant.html` until that page
   ports to `/workspace`. `Header` already links to `/tenant.html` for the same destination, so
   the two agree; both change in one commit when the portal moves. */

export function QuotaBanner({ limits, signedIn }: { limits: LimitsSnapshot | null; signedIn: boolean }) {
  const { t } = useI18n();
  const view = quotaView(limits, signedIn);

  if (view.kind === 'none') return <div className="quota" />;

  if (view.kind === 'disabled') {
    return (
      <div className="quota warn show">
        {t('quota.disabled')} <a href="/tenant.html">{t('nav.signin')}</a>
      </div>
    );
  }

  /* The server cannot tell this visitor from any other, so the anonymous tier refuses every
     request with a 503. Saying "you have used your allowance" here would be a lie — the zeroes
     in `remaining` are the absence of a counter, not a spent one — and it would point at the
     wrong remedy: this is an operator's proxy configuration, not the visitor's usage. */
  if (view.kind === 'unavailable') {
    return (
      <div className="quota warn show">
        {t('hero.quota.unavailable')} <a href="/tenant.html">{t('nav.signin')}</a>
      </div>
    );
  }

  /* "7 transcriptions, 3 speech clips & 12 file conversions left today." — one sentence, so the
     last separator is an ampersand rather than a comma. `∞` is an uncapped allowance; a count
     that is genuinely zero turns the whole banner amber. */
  const { parts, warn } = view;
  return (
    <div className={warn ? 'quota show warn' : 'quota show'}>
      {t('quota.using')}{' '}
      {parts.map((p, i) => (
        <Fragment key={p.labelKey}>
          {i > 0 ? (i === parts.length - 1 ? ' & ' : ', ') : null}
          <b>{p.left == null ? '∞' : p.left}</b> {t(p.labelKey)}
        </Fragment>
      ))}{' '}
      {t('quota.left')} <a href="/tenant.html">{t('nav.signin')}</a> {t('quota.more')}
    </div>
  );
}
