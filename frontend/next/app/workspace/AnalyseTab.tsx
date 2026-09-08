'use client';
/* ANALYSE — the call workbench.
   ============================
   One upload (audio OR a pasted transcript), one player, and each analyser a toggleable lane
   on the same timeline. The panel is entirely `<Workbench>`; this page only supplies the
   scope, the link back to the Rubric tab and the workspace's sentiment guidance, and opens
   stored recordings into it from History.

   It is mounted for the whole session — History opens rows into it (`wb.open`) from a tab that
   may be visited before Analyse ever is — but NOT before a workspace is chosen: the workbench
   starts fetching as soon as it mounts, and a call issued before then would carry no scope
   header at all. The backend's owner predicate for an unscoped superadmin on `/recordings` is
   literally `True`, so that call would list every tenant's recordings. */

import { useMemo, type RefObject } from 'react';
import { Workbench, type WorkbenchHandle } from '@/components/Workbench';
import { apiGet, apiSend } from '@/lib/session';
import { SCOPE, useWs } from './ctx';

interface SentimentCfg { enabled?: boolean; guidance?: string }

export function AnalyseTab({ wbRef }: { wbRef: RefObject<WorkbenchHandle | null> }) {
  const ws = useWs();
  const { canConfigure, funnel, ready, unauthorized } = ws;

  /* The Guidance block inside the Semantic tab is the old Playground's sentiment form.
     `readonly` follows the same owner|apikey policy the server enforces on PUT, so a member
     sees the guidance the analyser is actually using without a Save button that would 403.

     `enabled` is READ AND SENT BACK UNCHANGED. The flag is the workspace's sentiment on/off
     switch and it shares this one route with the guidance text, so a PUT that left it out
     would silently reset it — the legacy page round-tripped it through a checkbox it injected
     into the workbench's own markup, which the ported panel has no seam for. */
  const sentimentConfig = useMemo(() => {
    let enabled = true;
    return {
      get: async () => {
        try {
          const d = await apiGet<SentimentCfg>('/sentiment/config', { scope: SCOPE });
          enabled = d.enabled !== false;
          return { guidance: d.guidance || '', readonly: !canConfigure };
        } catch (e) { funnel(e); throw e; }
      },
      put: async ({ guidance }: { guidance: string }) => {
        try {
          const d = await apiSend<SentimentCfg>('PUT', '/sentiment/config', { enabled, guidance }, { scope: SCOPE });
          enabled = !!d && d.enabled !== false;
          return d;
        } catch (e) { funnel(e); throw e; }
      },
    };
  }, [canConfigure, funnel]);

  if (!ready) return <div className="empty">{ws.t('con.tenant.pick')}</div>;

  return (
    <Workbench
      ref={wbRef}
      features={['factcheck', 'score', 'semantic', 'summarise']}
      scope="tenant"
      // A link to a TAB, not a page: the capture-phase handler on <main> turns it back into a
      // tab switch, and the hash is what makes it work if the browser follows it anyway.
      rubricHref="/workspace#rubric"
      // Overriding a score changes how an agent's work is judged, so it is the owner's call.
      // The server enforces this too; hiding the control just avoids offering a member a button
      // that would only ever answer 403.
      canEditScores={canConfigure}
      sentimentConfig={sentimentConfig}
      // XHR uploads bypass the page's own error path, so the workbench reports its 401s back
      // here — one expired session, one gate, exactly as everywhere else on this page.
      onUnauthorized={unauthorized}
    />
  );
}
