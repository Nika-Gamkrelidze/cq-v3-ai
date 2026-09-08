'use client';
/* KB HEALTH — the curation review queue.
   =====================================
   Cards are per-cluster, not per-message: a tenant sees ~6–10 of these, never a feed. The
   verbatim quotes are the whole point — they are what make a card auditable rather than "the
   machine says so", so they are never summarised away. */

import { useCallback, useEffect, useState } from 'react';
import { confirmDialog, showModal } from '@/components/ui/Modal';
import { Tip } from '@/components/ui/Tip';
import { toast } from '@/components/ui/Toast';
import { EXTERNAL_REL, safeUrl } from '@/lib/safeUrl';
import { failMessage, STALE, useWs, type T, type Ws } from './ctx';
import { diff, side, type DiffRun } from './diff';
import s from './workspace.module.css';

interface Evidence {
  channel?: string; locale?: string; occurred_at?: string; url?: string;
  metadata?: { url?: string } | null; source_kind?: string; source_id?: string;
  same_tenant?: boolean; question?: string; answer?: string;
}
interface Proposal {
  id: string; op?: string; title?: string; occurrences?: number; distinct_sources?: number;
  priority?: number | null; confidence?: number | null; risk?: string | null; state?: string;
  rationale?: string | null; apply_error?: string | null; evidence?: Evidence[];
  proposed_content?: string; suggested_tags?: string[];
  target?: { title?: string; content?: string } | null;
}

const CH_ICON: Record<string, string> = {
  web: '🌐', instagram: '📸', messenger: '💬', whatsapp: '🟩', phone: '☎️', call: '☎️', email: '✉️',
};
const OP_PILL: Record<string, string> = { add: 'ready', update: 'processing', remove: 'contradicted' };

const CUR = '/v1/curation/proposals';

export function HealthTab({ on, gen }: { on: boolean; gen: number }) {
  const ws = useWs();
  const { t } = ws;
  const [items, setItems] = useState<Proposal[] | null>(null);
  const [state, setState] = useState<'idle' | 'loading' | 'failed'>('idle');
  const [err, setErr] = useState('');
  const [sel, setSel] = useState<Set<string>>(new Set());

  const load = useCallback(async () => {
    setSel(new Set());
    setState('loading');
    /* These cards quote a customer's own callers verbatim. Rendering a reply that arrived after
       the operator switched workspace would print one tenant's quotes under another tenant's
       name — the same failure the KB loads are guarded against. */
    const at = ws.gen();
    const d = await ws.json<Proposal[] | { items?: Proposal[] }>(`${CUR}?state=pending`);
    if (d === STALE) return;
    if (d === null) { setState('failed'); setErr(t('cur.loadfail')); setItems(null); return; }
    // An expired token / error body must never reach .map — a fixed QA bug.
    const listed = Array.isArray(d) ? d : (Array.isArray(d.items) ? d.items : []);
    if (!listed.length) { setState('idle'); setItems([]); return; }
    /* The list is deliberately light; body, quotes and the live target text come from the
       per-proposal GET. The open queue is hard-capped at ~10 cards, so this stays cheap. */
    const full = await Promise.all(listed.map(async p => {
      const one = await ws.json<Proposal>(`${CUR}/${p.id}`);
      return one && one !== STALE && one.id ? one : p;
    }));
    if (at !== ws.gen()) return;
    setState('idle');
    setItems(full);
  }, [ws, t]);

  useEffect(() => { if (on && ws.ready) void load(); }, [on, gen, ws.ready, load]);

  const act = async (action: 'accept' | 'edit' | 'decline', id: string) => {
    const p = items?.find(x => x.id === id);
    if (!p) return;
    const op = (p.op || '').toLowerCase();

    if (action === 'decline') {
      const reason = await showModal(close => <DeclineBody t={t} close={close} />, { maxWidth: '460px' });
      if (!reason) return;
      const r = await ws.send(`POST`, `${CUR}/${id}/decline`, { reason });
      if (!r.ok) { toast(failMessage(r, t), 'err'); return; }
      toast(t('cur.declinedok'), 'ok');
      void load();
      return;
    }

    // Accept-with-edits posts the edited body to the SAME endpoint — `content` present == edited.
    let content: string | undefined;
    if (action === 'edit') {
      const edited = await showModal(close => <EditBody t={t} proposal={p} close={close} />);
      if (edited == null) return;
      content = String(edited);
    }
    // `remove` is never bulk and never a one-click action: it takes content out of every answer.
    if (op === 'remove') {
      const ok = await showModal(close => <TypedConfirm t={t} close={close} />, { maxWidth: '460px' });
      if (ok !== true) return;
    }
    const r = await ws.send('POST', `${CUR}/${id}/accept`, content == null ? {} : { content });
    if (!r.ok) { toast(failMessage(r, t), 'err'); return; }
    toast(t('cur.applied'), 'ok');
    void load();
  };

  const bulkAccept = async () => {
    const ids = [...sel];
    if (!ids.length) return;
    if (!(await confirmDialog(t('cur.bulk.confirm', { n: ids.length }), { ok: t('cur.accept'), danger: false }))) return;
    const r = await ws.send('POST', `${CUR}/bulk`, { action: 'accept', ids });
    if (!r.ok) { toast(failMessage(r, t), 'err'); return; }
    toast(t('cur.applied'), 'ok');
    void load();
  };

  if (!ws.ready) return <div className="empty">{t('con.tenant.pick')}</div>;

  return (
    <div className="card">
      <div className="row" style={{ justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div style={{ flex: 1 }}><h3 style={{ margin: 0 }}>{t('cur.heading')}</h3></div>
        <div style={{ flex: 0 }}>
          <button type="button" className="ghost" onClick={() => void load()}>{t('btn.refresh')}</button>
        </div>
      </div>

      {sel.size > 0 && (
        <div className={s.selbar}>
          <span><b>{sel.size}</b> <span>{t('kba.selected')}</span></span>
          <button type="button" className="primary" onClick={() => void bulkAccept()}>{t('cur.bulk.accept')}</button>
          <Tip text={t('cur.bulk.note')} />
        </div>
      )}

      <div style={{ marginTop: 12 }}>
        {state === 'loading' ? <div className="empty"><span className="spinner" /></div>
          : state === 'failed' ? <div className="msg err">{err}</div>
            : items === null ? null
              : !items.length ? <div className="empty">{t('cur.none')}</div>
                : items.map(p => (
                  <Card
                    key={p.id} ws={ws} p={p}
                    checked={sel.has(p.id)}
                    onCheck={(v) => setSel(prev => {
                      const next = new Set(prev);
                      if (v) next.add(p.id); else next.delete(p.id);
                      return next;
                    })}
                    onAct={act}
                  />
                ))}
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- one card */

function Card({ ws, p, checked, onCheck, onAct }: {
  ws: Ws; p: Proposal; checked: boolean; onCheck: (v: boolean) => void;
  onAct: (action: 'accept' | 'edit' | 'decline', id: string) => void;
}) {
  const { t } = ws;
  const op = (p.op || 'add').toLowerCase();
  const bulkable = op !== 'remove';
  const pct = (v: number | null | undefined) => (v == null ? '—' : Math.round(v * 100) + '%');

  return (
    <div className="card">
      <div className="row" style={{ justifyContent: 'space-between', alignItems: 'flex-start', gap: 10 }}>
        <div className="w-name">
          <div className="inline" style={{ gap: 8, flexWrap: 'wrap' }}>
            {bulkable ? (
              <input
                type="checkbox" style={{ width: 'auto' }} aria-label={p.title}
                checked={checked} onChange={e => onCheck(e.target.checked)}
              />
            ) : null}
            <span className={`pill ${OP_PILL[op] || 'notinkb'}`}>{t('cur.op.' + op)}</span>
            <b>{p.title}</b>
          </div>
          <div className={s.curMeta} style={{ marginTop: 8 }}>
            <span className="chip">{t('cur.asked', { n: p.occurrences ?? 0 })}</span>
            <span>{t('cur.sources', { n: p.distinct_sources ?? 0 })}</span>
            <span>· {t('cur.priority')} {p.priority != null ? Number(p.priority).toFixed(2) : '—'}</span>
            <span>· {t('cur.confidence')} {pct(p.confidence)}</span>
            {p.risk ? <span>· {t('cur.risk')} {p.risk}</span> : null}
            {p.state && p.state !== 'pending'
              ? <span className={`pill ${p.state === 'apply_failed' ? 'error' : 'notinkb'}`}>{t('cur.st.' + p.state)}</span>
              : null}
          </div>
        </div>
      </div>

      {p.rationale ? <p className="hint" style={{ margin: '10px 0 0' }}>{p.rationale}</p> : null}
      {p.apply_error ? <div className="msg err">{p.apply_error}</div> : null}

      <h4>{t('cur.evidence')}</h4>
      <EvidenceList ws={ws} evs={p.evidence} />

      <h4>{op === 'update' ? t('cur.diff') : t('cur.proposed')}</h4>
      {p.target?.title ? <div className="hint">{t('cur.target')}: {p.target.title}</div> : null}
      <Body t={t} p={p} op={op} />

      {(p.suggested_tags || []).map(tg => <span className="chip" key={tg}>{tg}</span>)}
      {op === 'remove' ? <div className="hint" style={{ marginTop: 8 }}>{t('cur.remove.note')}</div> : null}

      <div className="actions">
        <button type="button" className="ghost" onClick={() => onAct('decline', p.id)}>{t('cur.decline')}</button>
        <button type="button" className="ghost" onClick={() => onAct('edit', p.id)}>{t('cur.acceptedit')}</button>
        <button type="button" className="primary" onClick={() => onAct('accept', p.id)}>{t('cur.accept')}</button>
      </div>
    </div>
  );
}

/** `target.content` is the CURRENT text, read server-side at view time — never a snapshot taken
    when the card was mined — so the reviewer approves a diff against what is really in the KB
    right now. If it is missing we show the proposal alone rather than a fake diff. */
function Body({ t, p, op }: { t: T; p: Proposal; op: string }) {
  const proposed = p.proposed_content || '';
  if (op !== 'update') return <div className={s.curDiff}>{proposed}</div>;
  const cur = p.target && typeof p.target.content === 'string' ? p.target.content : null;
  if (cur === null) {
    return (
      <div className={s.curDiff}>
        <span className="hint">{t('cur.diff.nochunk')}</span>{'\n\n'}{proposed}
      </div>
    );
  }
  const d = diff(cur, proposed);
  return (
    <div className={s.diffSplit}>
      <Pane t={t} which="old" runs={side(d, 'old')} />
      <Pane t={t} which="new" runs={side(d, 'new')} />
    </div>
  );
}

function Pane({ t, which, runs }: { t: T; which: 'old' | 'new'; runs: DiffRun[] }) {
  return (
    <div className={s.diffPane}>
      <div className={`${s.diffHead} ${which === 'old' ? s.old : s.new}`}>
        <span className={s.dot} />{t(which === 'old' ? 'cur.diff.current' : 'cur.diff.proposed')}
      </div>
      <div className={s.curDiff}>
        {runs.map((r, i) => (
          r.op === '-' ? <del key={i}>{r.text}</del>
            : r.op === '+' ? <ins key={i}>{r.text}</ins>
              : <span key={i}>{r.text}</span>
        ))}
      </div>
    </div>
  );
}

function EvidenceList({ ws, evs }: { ws: Ws; evs?: Evidence[] }) {
  const { t } = ws;
  if (!Array.isArray(evs) || !evs.length) return <div className="hint">{t('cur.evidence.none')}</div>;
  return (
    <>
      {evs.slice(0, 3).map((ev, i) => {
        const ch = (ev.channel || '').toLowerCase();
        const when = ev.occurred_at ? new Date(ev.occurred_at).toLocaleString() : '';
        // Only ever follow http(s) links the API gave us; anything else renders as plain text.
        const url = safeUrl(ev.url || ev.metadata?.url);
        const jobId = ev.source_kind === 'audio_job' && ev.source_id ? ev.source_id : '';
        /* The source-call link opens the workbench on `/recordings`. It stays hidden for an
           operator, exactly as it was: the guard predates act-as scoping and is preserved
           rather than quietly widened, because turning it on is a behaviour change nobody has
           tested and MIGRATION.md does not list it as a defect. */
        const link = (jobId && !ws.operator) ? (
          <a href="#" onClick={e => { e.preventDefault(); ws.openRecording(jobId); }}>{t('cur.openjob')} ↗</a>
        ) : url ? (
          <a href={url} target="_blank" rel={EXTERNAL_REL}>{t('cur.opensource')} ↗</a>
        ) : null;
        return (
          <div className={s.curQ} key={i}>
            <div className={s.curSrc}>
              <span>
                {CH_ICON[ch] || '💬'} {ev.channel || ev.source_kind || ''}
                {ev.locale ? ' · ' + ev.locale : ''}{when ? ' · ' + when : ''}
                {ev.same_tenant === false ? <> <span className="warn-flag">{t('cur.foreign')}</span></> : null}
              </span>
              <span>{link}</span>
            </div>
            <q>{ev.question}</q>
            {ev.answer ? <div className={s.curA}>{ev.answer}</div> : null}
          </div>
        );
      })}
    </>
  );
}

/* ---------------------------------------------------------------- the modals */

const DECLINE_REASONS: [string, string][] = [
  ['not_true', 'cur.decline.r.nottrue'],
  ['already_covered', 'cur.decline.r.covered'],
  ['dont_want', 'cur.decline.r.dontsay'],
  ['temporary', 'cur.decline.r.temporary'],
];

function DeclineBody({ t, close }: { t: T; close: (v?: unknown) => void }) {
  const [reason, setReason] = useState('');
  const [err, setErr] = useState('');
  return (
    <>
      <h3>{t('cur.decline.heading')}</h3>
      {DECLINE_REASONS.map(([value, key]) => (
        <label className={s.curReason} key={value}>
          <input
            type="radio" name="dr" value={value} style={{ width: 'auto' }}
            checked={reason === value} onChange={() => setReason(value)}
          />
          <span>{t(key)}</span>
        </label>
      ))}
      <div className="msg err">{err}</div>
      <div className="actions">
        <button type="button" className="ghost" onClick={() => close()}>{t('btn.cancel')}</button>
        <button
          type="button" className="danger"
          onClick={() => { if (!reason) { setErr(t('cur.decline.pick')); return; } close(reason); }}
        >{t('cur.decline')}</button>
      </div>
    </>
  );
}

function TypedConfirm({ t, close }: { t: T; close: (v?: unknown) => void }) {
  const word = t('cur.remove.word');
  const [value, setValue] = useState('');
  const [err, setErr] = useState('');
  return (
    <>
      <h3>{t('cur.remove.heading')}</h3>
      <p className="hint">{t('cur.remove.note')}</p>
      <label htmlFor="tc_input">{t('cur.remove.confirm', { word })}</label>
      <input id="tc_input" autoComplete="off" autoFocus value={value} onChange={e => setValue(e.target.value)} />
      <div className="msg err">{err}</div>
      <div className="actions">
        <button type="button" className="ghost" onClick={() => close(false)}>{t('btn.cancel')}</button>
        <button
          type="button" className="danger"
          onClick={() => {
            if (value.trim().toLowerCase() !== word.toLowerCase()) { setErr(t('cur.remove.mismatch')); return; }
            close(true);
          }}
        >{t('cur.op.remove')}</button>
      </div>
    </>
  );
}

function EditBody({ t, proposal, close }: { t: T; proposal: Proposal; close: (v?: unknown) => void }) {
  const [body, setBody] = useState(proposal.proposed_content || '');
  return (
    <>
      <h3>{t('cur.edit.heading')}</h3>
      <p className="hint">{t('cur.edit.hint')}</p>
      <label>{t('cur.proposed')}</label>
      <textarea style={{ minHeight: 220 }} value={body} onChange={e => setBody(e.target.value)} />
      <div className="actions">
        <button type="button" className="ghost" onClick={() => close()}>{t('btn.cancel')}</button>
        <button type="button" className="primary" onClick={() => close(body)}>{t('cur.accept')}</button>
      </div>
    </>
  );
}
