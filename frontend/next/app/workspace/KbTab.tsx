'use client';
/* KB — the workspace's own knowledge-base console.
   ===============================================
   Everything the operator's console could do, scoped by the server to one workspace: there is
   no tenant id in any of these URLs, so there is nothing for a browser to tamper with.

   Every section stays mounted and is hidden with `.hidden`, as the legacy `kbsec` divs were —
   which is what keeps a half-typed import form alive while somebody checks the documents list.
   Each one loads when it becomes the visible section (and again when the workspace changes
   under it). `import` and `retrieval` are input-only: there is nothing to fetch until the user
   acts. */

import { useCallback, useState } from 'react';
import { useWs } from './ctx';
import { Activity } from './kb/Activity';
import { Chunks, type ChunkRequest } from './kb/Chunks';
import { Documents } from './kb/Documents';
import { Duplicates } from './kb/Duplicates';
import { Import } from './kb/Import';
import { Maint } from './kb/Maint';
import { Overview } from './kb/Overview';
import { Retrieval } from './kb/Retrieval';

type Section = 'overview' | 'documents' | 'import' | 'chunks' | 'retrieval' | 'duplicates' | 'activity' | 'maint';

const SECTIONS: { key: Section; label: string }[] = [
  { key: 'overview', label: 'kba.tab.overview' },
  { key: 'documents', label: 'kba.tab.documents' },
  { key: 'import', label: 'kba.tab.import' },
  { key: 'chunks', label: 'kba.chunks' },
  { key: 'retrieval', label: 'pg.tab.retrieval' },
  { key: 'duplicates', label: 'kba.tab.duplicates' },
  { key: 'activity', label: 'kba.tab.activity' },
  { key: 'maint', label: 'tkb.tab.maint' },
];

export function KbTab({ on, gen }: { on: boolean; gen: number }) {
  const ws = useWs();
  const { t } = ws;
  const [sec, setSec] = useState<Section>('overview');
  /** The document the Chunks section should open on when it is reached from a row or a hit. */
  const [chunkReq, setChunkReq] = useState<ChunkRequest>({ id: '', seq: 0 });
  /** Bumped by an import, so the documents list reloads without re-fetching everything else. */
  const [docGen, setDocGen] = useState(0);

  const openChunksFor = useCallback((docId: string) => {
    setChunkReq(prev => ({ id: docId, seq: prev.seq + 1 }));
    setSec('chunks');
  }, []);

  if (!ws.ready) return <div className="empty">{t('con.tenant.pick')}</div>;

  const shown = (k: Section) => on && sec === k;

  return (
    <>
      <div className="subtabs" role="tablist">
        {SECTIONS.map(x => (
          <button
            key={x.key} type="button" role="tab" aria-selected={sec === x.key}
            className={`subtab${sec === x.key ? ' active' : ''}`} onClick={() => setSec(x.key)}
          >{t(x.label)}</button>
        ))}
      </div>

      <div className={sec === 'overview' ? undefined : 'hidden'}>
        <Overview on={shown('overview')} gen={gen} />
      </div>
      <div className={sec === 'documents' ? undefined : 'hidden'}>
        <Documents on={shown('documents')} gen={gen} reload={docGen} onChunks={openChunksFor} />
      </div>
      <div className={sec === 'import' ? undefined : 'hidden'}>
        <Import onImported={() => setDocGen(v => v + 1)} />
      </div>
      <div className={sec === 'chunks' ? undefined : 'hidden'}>
        <Chunks on={shown('chunks')} gen={gen} req={chunkReq} />
      </div>
      <div className={sec === 'retrieval' ? undefined : 'hidden'}>
        <Retrieval onChunks={openChunksFor} />
      </div>
      <div className={sec === 'duplicates' ? undefined : 'hidden'}>
        <Duplicates on={shown('duplicates')} gen={gen} />
      </div>
      <div className={sec === 'activity' ? undefined : 'hidden'}>
        <Activity on={shown('activity')} gen={gen} />
      </div>
      <div className={sec === 'maint' ? undefined : 'hidden'}>
        <Maint on={shown('maint')} gen={gen} />
      </div>
    </>
  );
}
