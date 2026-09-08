'use client';
import { useEffect, useState } from 'react';
import { Select } from '@/components/ui/Select';
import { Tip } from '@/components/ui/Tip';
import { toast } from '@/components/ui/Toast';
import { useI18n } from '@/lib/useI18n';
import { SessionExpired, adminGet, adminSend } from './api';
import { Msg, type Note } from './parts';

/* The embeddings provider.

   The ⓘ on the dimension is the whole safety story of this panel: the pgvector column's width
   must equal `EMBEDDING_DIM`, `services/migrate.py` only auto-migrates it while `kb_chunks` is
   empty, and changing the number afterwards means re-embedding every knowledge base there is. */

interface Embeddings {
  provider?: string;
  model?: string;
  base_url?: string;
  dim?: number;
  api_key_set?: boolean;
  api_key_hint?: string;
}

const PROVIDERS = [
  { value: 'tei', label: 'tei (self-hosted BGE-M3)' },
  { value: 'openai', label: 'openai' },
];

export default function EmbeddingsTab() {
  const { t } = useI18n();
  const [provider, setProvider] = useState('tei');
  const [model, setModel] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [dim, setDim] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [hint, setHint] = useState('');
  const [note, setNote] = useState<Note | null>(null);
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; detail: string } | null>(null);

  const load = async () => {
    try {
      const d = await adminGet<Embeddings>('/admin/embeddings');
      setProvider(d.provider || 'tei');
      setModel(d.model || '');
      setBaseUrl(d.base_url || '');
      setDim(d.dim ? String(d.dim) : '');
      setHint(d.api_key_set ? `API key set (${d.api_key_hint}).` : 'No API key set.');
    } catch { /* as the legacy `if (!r.ok) return;` — leave the panel as it was */ }
  };

  useEffect(() => { void load(); }, []);

  const save = async () => {
    setNote(null);
    const patch: Record<string, unknown> = {
      provider,
      model,
      base_url: baseUrl,
      // `|| undefined`, not `|| 0`: an unparseable box means "do not change the dimension",
      // and `exclude_none` on the server drops the key rather than storing a zero-width vector.
      dim: parseInt(dim, 10) || undefined,
    };
    if (apiKey.trim()) patch.api_key = apiKey.trim();
    try {
      await adminSend('PUT', '/admin/embeddings', patch);
      setApiKey('');
      await load();
      setNote({ kind: 'ok', text: t('toast.saved') });
      toast(t('toast.saved'), 'ok');
    } catch (e) {
      if (e instanceof SessionExpired) return;
      // The legacy handler shows the generic failure here rather than the server's `detail`.
      setNote({ kind: 'err', text: t('toast.error') });
    }
  };

  const runTest = async () => {
    setResult(null);
    setTesting(true);
    try {
      const d = await adminSend<{ ok?: boolean; detail?: string }>('POST', '/admin/embeddings/test');
      setResult({ ok: !!d.ok, detail: d.detail || '' });
    } catch (e) {
      if (e instanceof SessionExpired) return;
      setResult({ ok: false, detail: e instanceof Error ? e.message : '' });
    } finally {
      setTesting(false);
    }
  };

  return (
    <div className="card">
      <h3>{t('adm.embprov')}</h3>
      <div className="row">
        <div>
          <label htmlFor="e_provider">{t('f.provider')}</label>
          <Select
            id="e_provider"
            value={provider}
            onChange={setProvider}
            options={PROVIDERS}
            ariaLabel={t('f.provider')}
          />
        </div>
        {/* The label text sits in its own span: the tip is a sibling of the words, not part
            of them, so a language switch rewrites the label without disturbing the ⓘ. */}
        <div>
          <label htmlFor="e_dim">
            <span>{t('f.dimension')}</span>
            <Tip text={t('adm.embnote')} />
          </label>
          <input id="e_dim" type="number" value={dim} onChange={e => setDim(e.target.value)} />
        </div>
      </div>
      <label htmlFor="e_model">{t('f.model')}</label>
      <input id="e_model" value={model} placeholder="BAAI/bge-m3" onChange={e => setModel(e.target.value)} />
      <label htmlFor="e_base_url">{t('f.baseurl')}</label>
      <input
        id="e_base_url"
        value={baseUrl}
        placeholder="http://embeddings:80"
        onChange={e => setBaseUrl(e.target.value)}
      />
      <label htmlFor="e_api_key">{t('f.openaikey')}</label>
      <input
        id="e_api_key"
        type="password"
        value={apiKey}
        autoComplete="off"
        onChange={e => setApiKey(e.target.value)}
      />
      <div className="hint">{hint}</div>
      <div className="actions">
        <button className="primary" type="button" onClick={save}>{t('btn.save')}</button>
        <button className="ghost" type="button" onClick={runTest} disabled={testing}>
          {testing ? <><span className="spinner" />{t('btn.test')}…</> : t('btn.test')}
        </button>
      </div>
      {result ? <div className="test">{result.ok ? '✅' : '❌'} {result.detail}</div> : null}
      <Msg note={note} />
    </div>
  );
}
