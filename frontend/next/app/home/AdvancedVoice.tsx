'use client';
import { useI18n } from '@/lib/useI18n';
import { Select } from '@/components/ui/Select';
import styles from './home.module.css';
import { advFmt, advReset, type AdvState, type ModelCaps, type TtsModel } from './ttsAdvanced';

/* Advanced voice settings — the controls.
   ======================================
   A <details> so the form's resting shape is one extra line and the primary button stays where
   the visitor's eye already went. WHICH controls exist depends on the model in play, which is
   why the legacy version rebuilt this markup by hand on every change:

     * Eleven v3 has a three-way stability PRESET (Creative / Natural / Robust — the API rejects
       any other value) and no style or speed control at all: it takes emotion from the bracket
       tags in the text and paces itself.
     * Only the Flash and Turbo families enforce `language_code`, so only they get the
       "force the selected language" switch.

   Everything sits behind "Customise" because a voice's own settings are the ones its creator
   tuned: until that is ticked the request carries no `voice_settings` at all and the clip is
   byte-for-byte what it was before this panel existed.

   The bracket-tag tip belongs to the MODEL rather than to the Customise switch and therefore
   lives one level up, next to the textarea it is about: v3 reads `[whispers]` out of the text
   whether or not the sliders are in play. */

interface Props {
  adv: AdvState;
  onChange: (next: AdvState) => void;
  models: TtsModel[];
  /** True when `GET /tts/models` failed. Said once as a hint rather than as a toast: the form
      still works, the visitor just cannot pick a model this session. */
  modelsFailed: boolean;
  caps: ModelCaps;
}

/* Every numeric control writes the same shape, and so does every checkbox — but a computed key
   over a union of field names needs the write to be typed, or `{ ...adv, [key]: v }` widens to
   an index signature and stops being an `AdvState`. */
type NumKey = 'stability' | 'similarity' | 'style' | 'speed';
type BoolKey = 'boost' | 'forcelang';

export function AdvancedVoice({ adv, onChange, models, modelsFailed, caps }: Props) {
  const { t } = useI18n();
  const set = (patch: Partial<AdvState>) => onChange({ ...adv, ...patch });
  const setNum = (key: NumKey, v: number) => { const next = { ...adv }; next[key] = v; onChange(next); };
  const setBool = (key: BoolKey, v: boolean) => { const next = { ...adv }; next[key] = v; onChange(next); };

  const range = (
    key: NumKey,
    i18n: string,
    min: number, max: number, step: number,
    hintKey?: string,
    ends?: boolean,
  ) => {
    const id = `adv_${key}`;
    return (
      <div className={styles.row}>
        <label htmlFor={id}>{t(i18n)}</label>
        <input
          type="range" id={id} min={min} max={max} step={step} value={adv[key]}
          onChange={e => setNum(key, Number(e.target.value))}
        />
        <output className={styles.val} htmlFor={id}>{advFmt(key, adv[key])}</output>
        {ends ? (
          <div className={styles.ends}>
            <span>{t('tts.stability.expressive')}</span>
            <span>{t('tts.stability.stable')}</span>
          </div>
        ) : null}
        {hintKey ? <div className="hint">{t(hintKey)}</div> : null}
      </div>
    );
  };

  const check = (key: BoolKey, i18n: string, hintKey: string) => {
    const id = `adv_${key}`;
    return (
      <div className={`${styles.row} ${styles.rowCheck}`}>
        <label className={styles.check} htmlFor={id}>
          <input
            type="checkbox" id={id} checked={adv[key]}
            onChange={e => setBool(key, e.target.checked)}
          />
          <span>{t(i18n)}</span>
        </label>
        <div className="hint">{t(hintKey)}</div>
      </div>
    );
  };

  const modelOptions = [
    { value: '', label: t('tts.model.auto') },
    ...models.map(m => ({ value: m.model_id, label: m.name || m.model_id })),
  ];

  return (
    <details
      className={styles.adv}
      open={adv.open}
      onToggle={e => set({ open: (e.currentTarget as HTMLDetailsElement).open })}
    >
      <summary><span>{t('tts.adv')}</span></summary>
      <div className={styles.advBody}>
        <div className={styles.row}>
          <label htmlFor="ttsModel">{t('tts.model')}</label>
          <Select
            id="ttsModel"
            value={adv.model}
            onChange={v => set({ model: v })}
            options={modelOptions}
            ariaLabel={t('tts.model')}
          />
          {modelsFailed
            ? <div className="hint">{t('tts.models.loadfail')}</div>
            : <div className="hint">{t('tts.model.hint')}</div>}
        </div>

        <div className={`${styles.row} ${styles.rowCheck}`}>
          <label className={styles.check} htmlFor="ttsCustom">
            <input
              type="checkbox" id="ttsCustom" checked={adv.custom}
              onChange={e => set({ custom: e.target.checked })}
            />
            <span>{t('tts.custom')}</span>
          </label>
          <div className="hint">{t('tts.custom.hint')}</div>
        </div>

        {adv.custom ? (
          <div className={styles.controls}>
            {caps.presets ? (
              <div className={styles.row}>
                <span className={styles.lbl} id="adv_preset_lbl">{t('tts.stability')}</span>
                <div className={styles.seg} role="group" aria-labelledby="adv_preset_lbl">
                  {([[0, 'tts.preset.creative'], [0.5, 'tts.preset.natural'], [1, 'tts.preset.robust']] as const).map(
                    ([v, key]) => (
                      <button
                        key={key} type="button"
                        aria-pressed={adv.preset === v}
                        onClick={() => set({ preset: v })}
                      >
                        {t(key)}
                      </button>
                    ),
                  )}
                </div>
                <div className="hint">{t('tts.preset.hint')}</div>
              </div>
            ) : (
              range('stability', 'tts.stability', 0, 100, 1, 'tts.stability.hint', true)
            )}

            {range('similarity', 'tts.similarity', 0, 100, 1, 'tts.similarity.hint')}
            {caps.style ? range('style', 'tts.style', 0, 100, 1, 'tts.style.hint') : null}
            {caps.speaker_boost ? check('boost', 'tts.speakerboost', 'tts.speakerboost.hint') : null}
            {caps.speed ? range('speed', 'tts.speed', 0.7, 1.2, 0.05) : null}
            {caps.language_code === 'enforced' ? check('forcelang', 'tts.forcelang', 'tts.forcelang.hint') : null}

            <div className={`${styles.row} ${styles.rowReset}`}>
              <button type="button" className={styles.link} onClick={() => onChange(advReset(adv))}>
                {t('tts.reset')}
              </button>
            </div>
          </div>
        ) : null}
      </div>
    </details>
  );
}
