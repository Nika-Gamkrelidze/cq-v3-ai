'use client';
/* The bot's scope — answer policy, what the business does, opening hours, off-topic questions.
   ===========================================================================================
   Rendered by BOTH bot forms: the workspace's BOT tab (a customer's own bot) and the console's
   Default bot tab (the baseline every workspace inherits). Same fields, same words, so a knob
   cannot mean one thing to a customer and another to the operator. The rules — reading a
   config, building the payload, the pre-save check — are pure functions in
   `app/console/logic.ts`, where the node test runner covers them; these components only draw.

   Controlled, like `Select`: the form comes in, an updater goes out. The updater takes a
   FUNCTION of the current form, because the policy change resolves after an awaited confirm
   dialog and must not write back a form captured before the dialog opened. */

import { useMemo } from 'react';
import type { JSX } from 'react';
import { confirmDialog } from '@/components/ui/Modal';
import { Select } from '@/components/ui/Select';
import { Tip } from '@/components/ui/Tip';
import {
  BOT_LANGS, DAY_KEYS, SCOPE_LIMITS, timezoneOptions,
  type AnswerPolicy, type BuiltinCopy, type DayForm, type DayKey, type ScopeError, type ScopeForm,
} from '@/app/console/logic';

export type T = (key: string, vars?: Record<string, string | number>) => string;

export interface ScopeProps {
  form: ScopeForm;
  update: (fn: (s: ScopeForm) => ScopeForm) => void;
  t: T;
  /** Members see the settings without being able to change them. */
  readonly?: boolean;
  /** Keeps element ids unique per surface (`b` in the workspace, `db` in the console). */
  idPrefix: string;
}

/** `checkScope`'s result as a sentence. The day travels as a key, so it is named in the
    visitor's language rather than as `mon`. */
export function scopeErrorText(err: ScopeError, t: T): string {
  return err.day ? t(err.key, { day: t(`bot.hours.day.${err.day}`) }) : t(err.key);
}

/** The policy picker, as the card's heading. The ⓘ explains the option currently chosen. */
export function AnswerPolicyField({ form, update, t, readonly, idPrefix }: ScopeProps): JSX.Element {
  const choose = async (value: string) => {
    const next: AnswerPolicy = value === 'general' ? 'general' : 'kb_only';
    if (next === form.answerPolicy) return;
    // Letting the bot say things nobody at the company wrote is a deliberate risk decision, so
    // it costs a confirmation. Narrowing back to the documents never does — the safe direction
    // is always one click.
    if (next === 'general'
      && !(await confirmDialog(t('bot.policy.confirm'), { ok: t('bot.policy.confirm.ok') }))) return;
    update(s => ({ ...s, answerPolicy: next }));
  };
  return (
    <>
      <h3><span>{t('bot.policy')}</span><Tip text={t(`bot.policy.${form.answerPolicy}.hint`)} /></h3>
      <Select
        id={`${idPrefix}_policy`}
        value={form.answerPolicy}
        onChange={v => void choose(v)}
        disabled={readonly}
        ariaLabel={t('bot.policy')}
        options={[
          { value: 'kb_only', label: t('bot.policy.kb_only') },
          { value: 'general', label: t('bot.policy.general') },
        ]}
      />
    </>
  );
}

/** "About the business" — the sentence triage reads to tell a related question from an
    unrelated one. */
export function BusinessScopeField({ form, update, t, readonly, idPrefix }: ScopeProps): JSX.Element {
  const id = `${idPrefix}_business_scope`;
  return (
    <>
      <label htmlFor={id}>{t('bot.scope')}</label>
      <textarea
        id={id}
        style={{ minHeight: 70 }}
        maxLength={SCOPE_LIMITS.businessScope}
        placeholder={t('bot.scope.ph')}
        value={form.businessScope}
        disabled={readonly}
        onChange={e => { const v = e.target.value; update(s => ({ ...s, businessScope: v })); }}
      />
      <div className="hint">{t('bot.scope.hint')}</div>
    </>
  );
}

export function OpeningHoursCard({ form, update, t, readonly, idPrefix }: ScopeProps): JSX.Element {
  // ~420 zones: built once per stored value, not on every keystroke elsewhere in the form.
  const zones = useMemo(
    () => timezoneOptions(form.timezone).map(z => ({ value: z, label: z.replace(/_/g, ' ') })),
    [form.timezone],
  );
  return (
    <div className="card">
      <h3>{t('bot.hours')}</h3>
      <label htmlFor={`${idPrefix}_tz`}>{t('bot.hours.tz')}</label>
      <Select
        id={`${idPrefix}_tz`}
        value={form.timezone}
        onChange={v => update(s => ({ ...s, timezone: v }))}
        disabled={readonly}
        ariaLabel={t('bot.hours.tz')}
        options={zones}
        style={{ maxWidth: 360 }}
      />
      <div className="hint">{t('bot.hours.tz.hint')}</div>

      <label className="inline" style={{ gap: 8, marginTop: 14 }}>
        <input
          type="checkbox"
          checked={form.hoursOn}
          disabled={readonly}
          onChange={e => { const on = e.target.checked; update(s => ({ ...s, hoursOn: on })); }}
        />
        <span>{t('bot.hours.on')}</span>
      </label>
      {/* Off sends `opening_hours: null` ("not configured"). The week below is kept in the form
          either way — the stored one, or Mon–Fri 09:00–18:00 when nothing is stored — so
          ticking the box shows a sensible starting point instead of seven blank rows. */}
      {form.hoursOn ? (
        <>
          <div style={{ marginTop: 6 }}>
            {DAY_KEYS.map(d => (
              <DayRow key={d} d={d} day={form.days[d]} update={update} t={t} readonly={readonly} idPrefix={idPrefix} />
            ))}
          </div>
          <div className="hint">{t('bot.hours.hint')}</div>
        </>
      ) : null}

      <label htmlFor={`${idPrefix}_hours_note`}>{t('bot.hours.note')}</label>
      <textarea
        id={`${idPrefix}_hours_note`}
        style={{ minHeight: 60 }}
        maxLength={SCOPE_LIMITS.hoursNote}
        placeholder={t('bot.hours.note.ph')}
        value={form.hoursNote}
        disabled={readonly}
        onChange={e => { const v = e.target.value; update(s => ({ ...s, hoursNote: v })); }}
      />
    </div>
  );
}

/** One weekday: open or not, and ONE interval. A stored day with more intervals (a split shift
    written through the API) shows its first here; the others are listed read-only and sent
    back unchanged while the day stays open (see `DayForm.rest`). */
function DayRow({
  d, day, update, t, readonly, idPrefix,
}: { d: DayKey; day: DayForm } & Omit<ScopeProps, 'form'>): JSX.Element {
  const name = t(`bot.hours.day.${d}`);
  const set = (patch: Partial<DayForm>) =>
    update(s => ({ ...s, days: { ...s.days, [d]: { ...s.days[d], ...patch } } }));
  const id = `${idPrefix}_hours_${d}`;
  return (
    <div className="inline" style={{ gap: 10, flexWrap: 'wrap', rowGap: 6, marginTop: 8, minHeight: 44 }}>
      <span style={{ minWidth: 120, fontWeight: 600 }}>{name}</span>
      <label className="inline" style={{ gap: 6, margin: 0 }}>
        <input
          type="checkbox"
          id={`${id}_open`}
          checked={day.open}
          disabled={readonly}
          onChange={e => set({ open: e.target.checked })}
        />
        <span>{t('bot.hours.open')}</span>
      </label>
      {day.open ? (
        <>
          <input
            type="time"
            id={`${id}_from`}
            aria-label={`${name}: ${t('bot.hours.from')}`}
            title={t('bot.hours.from')}
            style={{ width: 'auto', minWidth: 120 }}
            value={day.from}
            disabled={readonly}
            onChange={e => set({ from: e.target.value })}
          />
          <span aria-hidden="true">–</span>
          <input
            type="time"
            id={`${id}_to`}
            aria-label={`${name}: ${t('bot.hours.to')}`}
            title={t('bot.hours.to')}
            style={{ width: 'auto', minWidth: 120 }}
            value={day.to}
            disabled={readonly}
            onChange={e => set({ to: e.target.value })}
          />
          {day.rest.length ? (
            <span className="hint" style={{ marginTop: 0 }}>
              {t('bot.hours.more', { ranges: day.rest.map(r => `${r.open}–${r.close}`).join(', ') })}
            </span>
          ) : null}
        </>
      ) : (
        <span className="hint" style={{ marginTop: 0 }}>{t('bot.hours.closed')}</span>
      )}
    </div>
  );
}

export function OffTopicCard({
  form, update, t, readonly, idPrefix, builtin,
}: ScopeProps & { builtin: BuiltinCopy }): JSX.Element {
  return (
    <div className="card">
      <h3>{t('bot.offtopic')}</h3>
      <div className="row">
        <div>
          <label htmlFor={`${idPrefix}_ot_warn`}>{t('bot.offtopic.warn_after')}</label>
          <input
            id={`${idPrefix}_ot_warn`}
            type="number"
            min={0}
            max={SCOPE_LIMITS.warnMax}
            step={1}
            value={form.warnAfter}
            disabled={readonly}
            onChange={e => { const v = e.target.value; update(s => ({ ...s, warnAfter: v })); }}
          />
        </div>
        <div>
          <label htmlFor={`${idPrefix}_ot_cutoff`}>{t('bot.offtopic.cutoff_after')}</label>
          <input
            id={`${idPrefix}_ot_cutoff`}
            type="number"
            min={0}
            max={SCOPE_LIMITS.cutoffMax}
            step={1}
            value={form.cutoffAfter}
            disabled={readonly}
            onChange={e => { const v = e.target.value; update(s => ({ ...s, cutoffAfter: v })); }}
          />
        </div>
      </div>
      <div className="hint">{t('bot.offtopic.hint')}</div>

      {/* Empty = the built-in wording, which is what the greyed placeholder shows. Only
          languages with text are sent, exactly like the refusal copy. */}
      <h4>{t('bot.offtopic.warning')}</h4>
      <LangTextareas
        idPrefix={`${idPrefix}_ot_warning`}
        value={form.offTopicWarning}
        placeholders={builtin.offTopicWarning}
        onChange={(l, v) => update(s => ({ ...s, offTopicWarning: { ...s.offTopicWarning, [l]: v } }))}
        t={t}
        readonly={readonly}
      />
      <h4>{t('bot.offtopic.cutoff')}</h4>
      <LangTextareas
        idPrefix={`${idPrefix}_ot_cutoff`}
        value={form.offTopicCutoff}
        placeholders={builtin.offTopicCutoff}
        onChange={(l, v) => update(s => ({ ...s, offTopicCutoff: { ...s.offTopicCutoff, [l]: v } }))}
        t={t}
        readonly={readonly}
      />
    </div>
  );
}

/** One trilingual copy field. `.stack-md` stacks the three boxes on a narrow screen instead of
    shrinking them to three unusable columns. */
function LangTextareas({
  idPrefix, value, placeholders, onChange, t, readonly,
}: {
  idPrefix: string;
  value: Record<string, string>;
  placeholders: Record<string, string>;
  onChange: (lang: string, v: string) => void;
  t: T;
  readonly?: boolean;
}): JSX.Element {
  return (
    <div className="row stack-md">
      {BOT_LANGS.map(l => (
        <div key={l}>
          <label htmlFor={`${idPrefix}_${l}`}>{t(`bot.lang.${l}`)}</label>
          <textarea
            id={`${idPrefix}_${l}`}
            maxLength={SCOPE_LIMITS.copy}
            placeholder={placeholders[l] || undefined}
            value={value[l] || ''}
            disabled={readonly}
            onChange={e => onChange(l, e.target.value)}
          />
        </div>
      ))}
    </div>
  );
}
