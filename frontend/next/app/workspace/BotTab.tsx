'use client';
/* BOT — the workspace's own autopilot configuration.
   =================================================
   Reads/writes `chat_configs` through `/chat/config`, the tenant-scoped mirror of the
   superadmin route, exactly as `/scoring/config` mirrors its admin twin. GET answers the merged
   config plus `is_default` (this workspace has never saved its own bot) and `killed` (the
   operator's kill switch). A 404/405 is NAMED as the server's absence rather than thrown: a
   tenant seeing a dead tab must be told it is the server, not their browser. */

import { useCallback, useEffect, useState } from 'react';
import { confirmDialog } from '@/components/ui/Modal';
import { Select } from '@/components/ui/Select';
import { Tip } from '@/components/ui/Tip';
import { toast } from '@/components/ui/Toast';
import { ApiError, apiGet } from '@/lib/session';
import { failMessage, SCOPE, useWs } from './ctx';

const LANGS = ['en', 'ka', 'ru'] as const;
type Lang = typeof LANGS[number];
const DISCLOSURE_MODES = ['first', 'always', 'off'] as const;

interface Limits {
  tenant_per_minute?: number; enduser_per_hour?: number;
  answer_tenant_per_minute?: number; answer_enduser_per_hour?: number;
}
interface Settings {
  limits?: Limits;
  escalation_keywords?: string[] | string;
  max_reply_chars?: number;
  allow_general_knowledge?: boolean;
  handoff_summary?: boolean;
  disclosure_mode?: string;
  disclosure?: Record<string, string>;
  [k: string]: unknown;
}
interface BotConfig {
  autopilot_enabled?: boolean;
  persona?: string | null;
  greeting?: Record<string, string>;
  refusal_copy?: Record<string, string>;
  languages?: string[];
  canned?: unknown[];
  min_score?: number; min_hits?: number; top_k?: number; suggestion_count?: number;
  settings?: Settings;
  version?: number | null;
  is_default?: boolean;
  killed?: boolean;
}

type Trio = Record<Lang, string>;
const blankTrio = (): Trio => ({ en: '', ka: '', ru: '' });

export function BotTab({ on, gen }: { on: boolean; gen: number }) {
  const ws = useWs();
  const { t } = ws;
  const readonly = !ws.canConfigure;

  const [cfg, setCfg] = useState<BotConfig | null>(null);
  const [loadErr, setLoadErr] = useState('');

  const [autopilot, setAutopilot] = useState(false);
  const [persona, setPersona] = useState('');
  const [langs, setLangs] = useState<string[]>([...LANGS]);
  const [greet, setGreet] = useState<Trio>(blankTrio);
  const [refusal, setRefusal] = useState<Trio>(blankTrio);
  const [disc, setDisc] = useState<Trio>(blankTrio);
  const [discMode, setDiscMode] = useState<string>('first');
  const [escalation, setEscalation] = useState('');
  const [minScore, setMinScore] = useState('');
  const [minHits, setMinHits] = useState('');
  const [topK, setTopK] = useState('');
  const [sugg, setSugg] = useState('');
  const [maxChars, setMaxChars] = useState('');
  const [capTenant, setCapTenant] = useState('');
  const [capEnduser, setCapEnduser] = useState('');
  const [capAnsTenant, setCapAnsTenant] = useState('');
  const [capAnsEnduser, setCapAnsEnduser] = useState('');
  const [general, setGeneral] = useState(false);
  const [handoff, setHandoff] = useState(true);

  const [needPublic, setNeedPublic] = useState(false);
  const [msg, setMsg] = useState<{ text: string; kind: '' | 'ok' | 'err' }>({ text: '', kind: '' });
  const [saving, setSaving] = useState(false);

  const fill = useCallback((c: BotConfig) => {
    setCfg(c);
    const s = (c.settings && typeof c.settings === 'object') ? c.settings : {};
    const g = (c.greeting && typeof c.greeting === 'object') ? c.greeting : {};
    const rf = (c.refusal_copy && typeof c.refusal_copy === 'object') ? c.refusal_copy : {};
    const limits = (s.limits && typeof s.limits === 'object') ? s.limits : {};
    setAutopilot(!!c.autopilot_enabled);
    setPersona(c.persona || '');
    setLangs(Array.isArray(c.languages) && c.languages.length ? c.languages : [...LANGS]);
    setGreet({ en: g.en || '', ka: g.ka || '', ru: g.ru || '' });
    setRefusal({ en: rf.en || '', ka: rf.ka || '', ru: rf.ru || '' });
    const kw = s.escalation_keywords;
    setEscalation(Array.isArray(kw) ? kw.join(', ') : (typeof kw === 'string' ? kw : ''));
    setMinScore(String(c.min_score ?? 0.35));
    setMinHits(String(c.min_hits ?? 1));
    setTopK(String(c.top_k ?? 8));
    setSugg(String(c.suggestion_count ?? 2));
    setMaxChars(String(s.max_reply_chars ?? 1200));
    setCapTenant(limits.tenant_per_minute == null ? '' : String(limits.tenant_per_minute));
    setCapEnduser(limits.enduser_per_hour == null ? '' : String(limits.enduser_per_hour));
    setCapAnsTenant(limits.answer_tenant_per_minute == null ? '' : String(limits.answer_tenant_per_minute));
    setCapAnsEnduser(limits.answer_enduser_per_hour == null ? '' : String(limits.answer_enduser_per_hour));
    // Never default this to true: the safe value is false and a missing key means false.
    setGeneral(s.allow_general_knowledge === true);
    setHandoff(s.handoff_summary !== false);
    // An unknown mode falls back to the engine's own default rather than a blank trigger.
    setDiscMode(DISCLOSURE_MODES.includes(s.disclosure_mode as typeof DISCLOSURE_MODES[number])
      ? (s.disclosure_mode as string) : 'first');
    const d = (s.disclosure && typeof s.disclosure === 'object') ? s.disclosure : {};
    setDisc({
      en: typeof d.en === 'string' ? d.en : '',
      ka: typeof d.ka === 'string' ? d.ka : '',
      ru: typeof d.ru === 'string' ? d.ru : '',
    });
    setNeedPublic(false);
  }, []);

  const load = useCallback(async () => {
    setLoadErr('');
    try {
      const d = await apiGet<BotConfig>('/chat/config', { scope: SCOPE });
      if (!d || typeof d !== 'object') { setLoadErr(t('bot.loadfail')); return; }
      fill(d);
    } catch (e) {
      ws.funnel(e);
      const status = e instanceof ApiError ? e.status : 0;
      setCfg(null);
      setLoadErr(status === 404 || status === 405 ? t('bot.unavailable') : t('bot.loadfail'));
    }
  }, [t, fill, ws]);

  useEffect(() => { if (on && ws.ready) void load(); }, [on, gen, ws.ready, load]);

  /* Turning general knowledge ON is a deliberate risk decision, so it costs a confirmation.
     Turning it OFF never does — the safe direction is always one click. */
  const toggleGeneral = async (next: boolean) => {
    if (!next) { setGeneral(false); return; }
    if (await confirmDialog(t('bot.general.confirm'), { ok: t('bot.general.on') })) setGeneral(true);
  };

  const save = async () => {
    setMsg({ text: '', kind: '' });
    setNeedPublic(false);
    if (!langs.length) { setMsg({ text: t('bot.languages.pickone'), kind: 'err' }); return; }
    const greeting: Record<string, string> = {};
    const refusalOut: Record<string, string> = {};
    LANGS.forEach(l => {
      const g = greet[l].trim();
      const r = refusal[l].trim();
      if (g) greeting[l] = g;
      if (r) refusalOut[l] = r;
    });
    // A public bot with no refusal copy in a language it answers in would improvise the most
    // frequently-read sentence in the product. Block it here rather than discover it live.
    if (autopilot) {
      const missing = langs.find(l => !refusalOut[l]);
      if (missing) {
        setMsg({ text: t('bot.refusal.missing', { lang: t('bot.lang.' + missing) }), kind: 'err' });
        return;
      }
    }
    const num = (v: string, dflt: number) => { const n = parseFloat(v); return Number.isFinite(n) ? n : dflt; };
    const optInt = (v: string) => { const n = parseInt(v, 10); return Number.isFinite(n) ? n : undefined; };
    const prev = (cfg && typeof cfg.settings === 'object' && cfg.settings) ? cfg.settings : {};
    const limits: Limits = {};
    if (optInt(capTenant) !== undefined) limits.tenant_per_minute = optInt(capTenant);
    if (optInt(capEnduser) !== undefined) limits.enduser_per_hour = optInt(capEnduser);
    if (optInt(capAnsTenant) !== undefined) limits.answer_tenant_per_minute = optInt(capAnsTenant);
    if (optInt(capAnsEnduser) !== undefined) limits.answer_enduser_per_hour = optInt(capAnsEnduser);
    // Only non-empty strings are sent: to the engine a key that is present but "" means
    // "suppress the disclosure in this language", which is not what clearing a box means here —
    // that asks for the built-in wording back, so the key must be absent.
    const disclosure: Record<string, string> = {};
    LANGS.forEach(l => { const v = disc[l].trim(); if (v) disclosure[l] = v; });

    const body = {
      persona: persona.trim() || null,
      greeting, refusal_copy: refusalOut, languages: langs,
      canned: Array.isArray(cfg?.canned) ? cfg!.canned : [],
      autopilot_enabled: autopilot,
      settings: {
        ...prev,                                   // unknown knobs survive a save
        min_score: num(minScore, 0.35), min_hits: num(minHits, 1),
        top_k: num(topK, 8), suggestion_count: num(sugg, 2),
        max_reply_chars: num(maxChars, 1200),
        escalation_keywords: escalation.split(',').map(x => x.trim()).filter(Boolean),
        allow_general_knowledge: general,
        handoff_summary: handoff,
        disclosure_mode: DISCLOSURE_MODES.includes(discMode as typeof DISCLOSURE_MODES[number]) ? discMode : 'first',
        disclosure, limits,
      },
    };

    setSaving(true);
    const r = await ws.send<BotConfig>('PUT', '/chat/config', body);
    setSaving(false);
    // 409 == "no published documents". Explain the reason and point at the fix; a toggle that
    // only fails is the difference between a broken product and one that teaches.
    if (r.status === 409) { setAutopilot(false); setNeedPublic(true); return; }
    if (!r.ok) {
      const text = failMessage(r, t);
      setMsg({ text, kind: 'err' });
      toast(text, 'err');
      return;
    }
    fill(r.data && typeof r.data === 'object' ? r.data : (body as BotConfig));
    setMsg({ text: t('bot.saved'), kind: 'ok' });
    toast(t('bot.saved'), 'ok');
  };

  if (!ws.ready) return <div className="empty">{t('con.tenant.pick')}</div>;

  const killed = !!cfg?.killed;                    // set by the superadmin kill switch
  const statePill = killed ? 'error' : autopilot ? 'ready' : 'notinkb';
  const stateText = killed ? t('bot.state.killed') : autopilot ? t('bot.state.live') : t('bot.state.off');

  const trio = (
    label: string,
    value: Trio,
    set: (v: Trio) => void,
  ) => (
    <div className="row stack-md">
      {LANGS.map(l => (
        <div key={l}>
          <label htmlFor={`${label}_${l}`}>{t('bot.lang.' + l)}</label>
          <textarea
            id={`${label}_${l}`} value={value[l]} disabled={readonly}
            onChange={e => set({ ...value, [l]: e.target.value })}
          />
        </div>
      ))}
    </div>
  );

  const numField = (id: string, label: string, value: string, set: (v: string) => void, extra: Record<string, unknown> = {}) => (
    <div>
      <label htmlFor={id}>{t(label)}</label>
      <input id={id} type="number" value={value} disabled={readonly}
             onChange={e => set(e.target.value)} {...extra} />
    </div>
  );

  return (
    <>
      <div className="card">
        <div className="row" style={{ justifyContent: 'space-between', alignItems: 'flex-start' }}>
          <div style={{ flex: 1 }}><h3 style={{ margin: 0 }}>{t('bot.heading')}</h3></div>
          <div className="inline" style={{ flex: 0, gap: 8 }}>
            <span className={`pill ${statePill}`}>{cfg ? stateText : '—'}</span>
            <button type="button" className="ghost" onClick={() => void load()}>{t('btn.refresh')}</button>
          </div>
        </div>
        {loadErr ? <div className="msg err">{loadErr}</div> : null}
        {/* Shown while `/chat/config` answers with is_default: the values on screen are the
            shared default, not this workspace's bot, and saving is what forks it. */}
        {cfg?.is_default === true ? <div className="msg">{t('bot.isdefault')}</div> : null}
      </div>

      {cfg ? (
        <>
          {/* Autopilot: the one switch that points a model at the public internet. */}
          <div className="card">
            <label className="inline" style={{ gap: 8 }}>
              <input
                type="checkbox" style={{ width: 'auto' }} checked={autopilot} disabled={readonly}
                onChange={e => { setNeedPublic(false); setAutopilot(e.target.checked); }}
              />
              <span>{t('bot.autopilot')}</span><Tip text={t('bot.autopilot.hint')} />
            </label>
            {killed ? <div className="msg err">{t('bot.killed.note')}</div> : null}
            {needPublic ? (
              <div className="msg err">
                <b>{t('bot.needpublic.title')}</b>
                <div style={{ marginTop: 6 }}>{t('bot.needpublic.body')}</div>
                <div className="actions">
                  <button type="button" className="ghost" onClick={() => ws.showTab('kb')}>
                    {t('bot.needpublic.link')}
                  </button>
                </div>
              </div>
            ) : null}
          </div>

          <div className="card">
            <label htmlFor="b_persona">{t('bot.persona')}</label>
            <textarea
              id="b_persona" placeholder={t('bot.persona.ph')} style={{ minHeight: 90 }}
              value={persona} disabled={readonly} onChange={e => setPersona(e.target.value)}
            />
            <h4>{t('bot.languages')}</h4>
            <div className="inline" style={{ gap: 18 }}>
              {LANGS.map(l => (
                <label className="inline" style={{ gap: 6 }} key={l}>
                  <input
                    type="checkbox" style={{ width: 'auto' }} disabled={readonly}
                    checked={langs.includes(l)}
                    onChange={e => setLangs(prev => (e.target.checked
                      ? [...prev.filter(x => x !== l), l]
                      : prev.filter(x => x !== l)))}
                  />
                  <span>{t('bot.lang.' + l)}</span>
                </label>
              ))}
            </div>
          </div>

          {/* Greeting + refusal side by side in all three languages: this is the copy a real
              customer reads, and a missing translation is invisible unless it is shown here. */}
          <div className="card">
            <h3>{t('bot.greeting')}</h3>
            {trio('b_greet', greet, setGreet)}
          </div>
          <div className="card">
            <h3><span>{t('bot.refusal')}</span><Tip text={t('bot.refusal.hint')} /></h3>
            {trio('b_ref', refusal, setRefusal)}
          </div>

          <div className="card">
            <h3><span>{t('bot.escalation')}</span><Tip text={t('bot.escalation.hint')} /></h3>
            <input
              id="b_escalation" placeholder={t('bot.escalation.ph')} value={escalation}
              disabled={readonly} onChange={e => setEscalation(e.target.value)}
            />
            <h4>{t('bot.retrieval')}</h4>
            <div className="row">
              {numField('b_minscore', 'bot.minscore', minScore, setMinScore, { step: 0.01, min: 0, max: 1 })}
              {numField('b_minhits', 'bot.minhits', minHits, setMinHits, { min: 0 })}
              {numField('b_topk', 'bot.topk', topK, setTopK, { min: 1 })}
              {numField('b_sugg', 'bot.suggestions', sugg, setSugg, { min: 1 })}
              {numField('b_maxchars', 'bot.maxchars', maxChars, setMaxChars, { min: 1 })}
            </div>
            <h4>{t('bot.caps')}</h4>
            <div className="row">
              {numField('b_cap_tenant', 'bot.cap.tenant', capTenant, setCapTenant, { min: 0 })}
              {numField('b_cap_enduser', 'bot.cap.enduser', capEnduser, setCapEnduser, { min: 0 })}
              {numField('b_cap_answer_tenant', 'bot.cap.answer_tenant', capAnsTenant, setCapAnsTenant, { min: 0 })}
              {numField('b_cap_answer_enduser', 'bot.cap.answer_enduser', capAnsEnduser, setCapAnsEnduser, { min: 0 })}
            </div>
            <div className="hint">{t('bot.cap.hint')}</div>
          </div>

          {/* The refusal-vs-general-knowledge choice, stated as the risk it is. Off by default. */}
          <div className="card">
            <label className="inline" style={{ gap: 8 }}>
              <input
                type="checkbox" style={{ width: 'auto' }} checked={general} disabled={readonly}
                onChange={e => void toggleGeneral(e.target.checked)}
              />
              <span>{t('bot.general')}</span><Tip text={t('bot.general.risk')} />
            </label>
            <label className="inline" style={{ gap: 8, marginTop: 14 }}>
              <input
                type="checkbox" style={{ width: 'auto' }} checked={handoff} disabled={readonly}
                onChange={e => setHandoff(e.target.checked)}
              />
              <span>{t('bot.handoff')}</span><Tip text={t('bot.handoff.hint')} />
            </label>
          </div>

          {/* The disclosure line is appended by code after generation, so it is the one piece of
              bot copy a customer cannot talk the model out of. Empty text means the built-in
              wording; "off" is for channels whose own interface already says "bot". */}
          <div className="card">
            <h3><span>{t('bot.disclosure')}</span><Tip text={t('bot.disclosure.hint')} /></h3>
            <label htmlFor="b_disclosure_mode">{t('bot.disclosure.mode')}</label>
            <Select
              id="b_disclosure_mode" value={discMode} onChange={setDiscMode} disabled={readonly}
              ariaLabel={t('bot.disclosure.mode')}
              options={DISCLOSURE_MODES.map(m => ({ value: m, label: t('bot.disclosure.' + m) }))}
            />
            <div style={{ marginTop: 14 }}>{trio('b_disc', disc, setDisc)}</div>
            <div className="hint">{t('bot.disclosure.text')}</div>
          </div>

          <div className="card">
            {/* Members see what the bot is doing without a Save button that would only 403 —
                the server's `may_configure_workspace` is the policy, this stops the UI
                promising more. */}
            {readonly ? null : (
              <div className="actions">
                <button type="button" className="primary" disabled={saving} onClick={() => void save()}>
                  {saving ? <span className="spinner" /> : t('bot.save')}
                </button>
              </div>
            )}
            <div className={`msg${msg.kind ? ' ' + msg.kind : ''}`} aria-live="polite">{msg.text}</div>
            <div className="hint">{cfg.version ? `${t('bot.version')} ${cfg.version}` : ''}</div>
          </div>
        </>
      ) : null}
    </>
  );
}
