'use client';
import { useEffect, useState } from 'react';
import { confirmDialog } from '@/components/ui/Modal';
import { Select } from '@/components/ui/Select';
import { Tip } from '@/components/ui/Tip';
import { toast } from '@/components/ui/Toast';
import { ApiError } from '@/lib/session';
import { dateTime } from '@/lib/format';
import { useI18n } from '@/lib/useI18n';
import { SessionExpired, adminGet, adminSend, errText } from './api';
import {
  BOT_CAPS, BOT_LANGS, BOT_SOURCE, formFromConfig, payloadFromForm, sourcePill,
  type BotConfig, type BotForm, type DisclosureMode,
} from './logic';
import { CheckRow, Msg, type Note } from './parts';

/* DEFAULT BOT — the baseline every workspace inherits.

   The tenant portal's Bot form one level up: what a workspace runs on until it saves settings of
   its own. Field for field the same form, so a knob cannot mean one thing to a customer and
   another to the operator — with two deliberate differences:

     * NO AUTOPILOT SWITCH. The default may shape HOW a bot answers, never WHETHER one is
       answering. That stays a per-workspace decision, taken after the workspace has shared at
       least one document with the bot, and the route here has no such field to send.
     * THE ANSWER CAPS EXIST ONLY HERE. The portal shows the copilot (draft) caps; how fast an
       UNATTENDED bot may talk to the public is an operator's decision, not a customer's.

   Loaded on tab activation rather than with the rest of the console: this route ships with the
   chat feature, and a console that failed to open because one tab's endpoint is not deployed yet
   would take the kill switch down with it. */

/** The five numeric knobs, as [form field, label key, input attributes]. Typed to the string
    fields only, so `set(field, …)` stays a string assignment rather than a cast. */
type NumField = 'minScore' | 'minHits' | 'topK' | 'suggestions' | 'maxChars';

const RETRIEVAL: readonly (readonly [NumField, string, { step?: number; min?: number; max?: number }])[] = [
  ['minScore', 'bot.minscore', { step: 0.01, min: 0, max: 1 }],
  ['minHits', 'bot.minhits', { min: 0 }],
  ['topK', 'bot.topk', { min: 1 }],
  ['suggestions', 'bot.suggestions', { min: 1 }],
  ['maxChars', 'bot.maxchars', { min: 1 }],
];

export default function DefaultBotTab() {
  const { t } = useI18n();
  const [cfg, setCfg] = useState<BotConfig | null>(null);
  const [form, setForm] = useState<BotForm>(() => formFromConfig(null));
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<Note | null>(null);

  const set = <K extends keyof BotForm>(k: K, v: BotForm[K]) => setForm(f => ({ ...f, [k]: v }));
  const setLangText = (field: 'greeting' | 'refusal' | 'disclosure', lang: string, v: string) =>
    setForm(f => ({ ...f, [field]: { ...f[field], [lang]: v } }));

  useEffect(() => {
    let live = true;
    adminGet<BotConfig>('/admin/chat/default-config')
      .then(d => { if (!live) return; setCfg(d); setForm(formFromConfig(d)); })
      .catch(e => {
        if (e instanceof SessionExpired || !live) return;
        // A 404/405 is a server without this route, not a broken console — say which.
        const notThere = e instanceof ApiError && (e.status === 404 || e.status === 405);
        setNote({ kind: 'err', text: t(notThere ? 'bot.unavailable' : 'toast.error') });
      });
    return () => { live = false; };
    // Once, on mount: a language switch must not re-fetch and discard an unsaved edit.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /* Turning general knowledge ON is a deliberate risk decision — here, one taken for every
     workspace that never chose otherwise — so it costs a confirmation. Turning it OFF never
     does: the safe direction is always one click. */
  const setGeneral = async (next: boolean) => {
    if (!next) { set('allowGeneral', false); return; }
    if (await confirmDialog(t('bot.general.confirm'), { ok: t('bot.general.on') })) {
      set('allowGeneral', true);
    }
  };

  const toggleLang = (lang: string, on: boolean) =>
    setForm(f => ({
      ...f,
      languages: on
        ? (f.languages.includes(lang) ? f.languages : [...f.languages, lang])
        : f.languages.filter(l => l !== lang),
    }));

  const save = async () => {
    setNote(null);
    if (!form.languages.length) {
      setNote({ kind: 'err', text: t('bot.languages.pickone') });
      return;
    }
    setBusy(true);
    try {
      const d = await adminSend<BotConfig>('PUT', '/admin/chat/default-config', payloadFromForm(form, cfg));
      setCfg(d);
      setForm(formFromConfig(d));
      setNote({ kind: 'ok', text: t('pb.defbot.saved') });
      toast(t('pb.defbot.saved'), 'ok');
    } catch (e) {
      if (e instanceof SessionExpired) return;
      const text = errText(e, t);
      setNote({ kind: 'err', text });
      toast(text, 'err');
    } finally {
      setBusy(false);
    }
  };

  const pill = sourcePill(BOT_SOURCE, cfg?.source, 'builtin');

  return (
    <>
      <div className="card">
        <div
          className="inline"
          style={{ justifyContent: 'space-between', alignItems: 'baseline', gap: 10, flexWrap: 'wrap', rowGap: 6 }}
        >
          <h3 style={{ margin: 0 }}>{t('pb.defbot.heading')}</h3>
          <span className="inline" style={{ gap: 8 }}>
            {/* "—" until something has loaded: the pill must not claim where the defaults came
                from before it knows. */}
            <span className={`pill ${cfg ? pill.cls : ''}`}>{cfg ? t(pill.key) : '—'}</span>
            <span className="hint">
              {cfg?.updated_at
                ? t('pb.defbot.updated', { when: dateTime(cfg.updated_at), who: cfg.updated_by || '—' })
                : ''}
            </span>
          </span>
        </div>
        <p className="hint">{t('pb.defbot.desc')}</p>
        <p className="hint">{t('bot.autopilot.default.note')}</p>
      </div>

      <div className="card">
        <label htmlFor="db_persona">{t('bot.persona')}</label>
        <textarea
          id="db_persona"
          style={{ minHeight: 90 }}
          placeholder={t('bot.persona.ph')}
          value={form.persona}
          onChange={e => set('persona', e.target.value)}
        />
        <h4>{t('bot.languages')}</h4>
        <div className="inline" style={{ gap: 18 }}>
          {BOT_LANGS.map(l => (
            <CheckRow
              key={l}
              checked={form.languages.includes(l)}
              onChange={on => toggleLang(l, on)}
              style={{ gap: 6 }}
            >
              <span>{t(`bot.lang.${l}`)}</span>
            </CheckRow>
          ))}
        </div>
      </div>

      {/* Greeting and refusal in all three languages, as in the portal: a missing translation in
          the DEFAULT is a missing translation for every workspace that never wrote its own. */}
      <div className="card">
        <h3>{t('bot.greeting')}</h3>
        <LangRow field="greeting" form={form} onChange={setLangText} t={t} />
      </div>

      <div className="card">
        <h3><span>{t('bot.refusal')}</span><Tip text={t('bot.refusal.hint')} /></h3>
        <LangRow field="refusal" form={form} onChange={setLangText} t={t} />
      </div>

      <div className="card">
        <h3><span>{t('bot.escalation')}</span><Tip text={t('bot.escalation.hint')} /></h3>
        <input
          id="db_escalation"
          placeholder={t('bot.escalation.ph')}
          value={form.escalation}
          onChange={e => set('escalation', e.target.value)}
        />
        <h4>{t('bot.retrieval')}</h4>
        <div className="row">
          {RETRIEVAL.map(([field, label, attrs]) => (
            <div key={field}>
              <label htmlFor={`db_${field}`}>{t(label)}</label>
              <input
                id={`db_${field}`}
                type="number"
                step={attrs.step}
                min={attrs.min}
                max={attrs.max}
                value={form[field]}
                onChange={e => set(field, e.target.value)}
              />
            </div>
          ))}
        </div>
        <h4>{t('bot.caps')}</h4>
        <div className="row">
          {BOT_CAPS.slice(0, 2).map(([field]) => (
            <CapField key={field} field={field} form={form} setForm={setForm} t={t} />
          ))}
        </div>
        <div className="row">
          {BOT_CAPS.slice(2).map(([field]) => (
            <CapField key={field} field={field} form={form} setForm={setForm} t={t} />
          ))}
        </div>
        <div className="hint">{t('bot.cap.hint')}</div>
      </div>

      {/* The disclosure line is appended by code AFTER generation, so it is the one line of bot
          copy a customer's prompt cannot talk the model out of — which is why it is set here. */}
      <div className="card">
        <h3><span>{t('bot.disclosure')}</span><Tip text={t('bot.disclosure.hint')} /></h3>
        <label htmlFor="db_disclosure_mode">{t('bot.disclosure.mode')}</label>
        <Select
          id="db_disclosure_mode"
          value={form.disclosureMode}
          onChange={v => set('disclosureMode', v as DisclosureMode)}
          ariaLabel={t('bot.disclosure.mode')}
          options={[
            { value: 'first', label: t('bot.disclosure.first') },
            { value: 'always', label: t('bot.disclosure.always') },
            { value: 'off', label: t('bot.disclosure.off') },
          ]}
        />
        <div className="hint" style={{ marginTop: 10 }}>{t('bot.disclosure.text')}</div>
        <LangRow field="disclosure" form={form} onChange={setLangText} t={t} />
      </div>

      <div className="card">
        <CheckRow checked={form.allowGeneral} onChange={v => void setGeneral(v)}>
          <>
            <span>{t('bot.general')}</span>
            <Tip text={t('bot.general.risk')} />
          </>
        </CheckRow>
        <CheckRow
          checked={form.handoffSummary}
          onChange={v => set('handoffSummary', v)}
          style={{ marginTop: 14 }}
        >
          <>
            <span>{t('bot.handoff')}</span>
            <Tip text={t('bot.handoff.hint')} />
          </>
        </CheckRow>
      </div>

      <div className="card">
        <div className="actions">
          <button className="primary" type="button" onClick={save} disabled={busy}>
            {busy ? <span className="spinner" /> : t('sc.save')}
          </button>
        </div>
        <Msg note={note} />
      </div>
    </>
  );
}

/** One trilingual field. `.stack-md` is what makes the three boxes stack on a narrow screen
    instead of shrinking to three unusable columns. */
function LangRow({
  field, form, onChange, t,
}: {
  field: 'greeting' | 'refusal' | 'disclosure';
  form: BotForm;
  onChange: (field: 'greeting' | 'refusal' | 'disclosure', lang: string, v: string) => void;
  t: (k: string) => string;
}) {
  return (
    <div className="row stack-md">
      {BOT_LANGS.map(l => (
        <div key={l}>
          <label htmlFor={`db_${field}_${l}`}>{t(`bot.lang.${l}`)}</label>
          <textarea
            id={`db_${field}_${l}`}
            value={form[field][l] || ''}
            onChange={e => onChange(field, l, e.target.value)}
          />
        </div>
      ))}
    </div>
  );
}

function CapField({
  field, form, setForm, t,
}: {
  field: string;
  form: BotForm;
  setForm: React.Dispatch<React.SetStateAction<BotForm>>;
  t: (k: string) => string;
}) {
  return (
    <div>
      <label htmlFor={`db_cap_${field}`}>{t(`bot.cap.${field}`)}</label>
      <input
        id={`db_cap_${field}`}
        type="number"
        min={0}
        value={form.caps[field] ?? ''}
        onChange={e => setForm(f => ({ ...f, caps: { ...f.caps, [field]: e.target.value } }))}
      />
    </div>
  );
}
