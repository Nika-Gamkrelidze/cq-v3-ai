'use client';
import { useEffect, useState } from 'react';
import { toast } from '@/components/ui/Toast';
import { useI18n } from '@/lib/useI18n';
import { SessionExpired, adminGet, adminSend } from './api';
import { CheckRow, Msg, type Note } from './parts';

/* Two cards, one tab: what a visitor with no login may do, and the public site's sentiment
   config.

   RETENTION USED TO LIVE HERE as a fourth number. It governs every stored recording now, not
   just an anonymous visitor's, so it moved to its own tab rather than staying under a heading
   that understates what it deletes. The line pointing at that tab stays: an operator who knew
   where the field was needs to be told where it went, not left to conclude it was dropped. */

interface AnonLimits {
  enabled?: boolean;
  max_analyses_per_day?: number;
  max_audio_mb?: number;
  max_tts_per_day?: number;
  features?: { analyze?: boolean; tts?: boolean };
}

interface SentimentCfg { enabled?: boolean; guidance?: string }

export default function AnonymousTab() {
  const { t } = useI18n();

  const [enabled, setEnabled] = useState(false);
  const [analyses, setAnalyses] = useState('');
  const [mb, setMb] = useState('');
  const [tts, setTts] = useState('');
  const [featAnalyze, setFeatAnalyze] = useState(false);
  const [featTts, setFeatTts] = useState(false);
  const [note, setNote] = useState<Note | null>(null);

  const [snEnabled, setSnEnabled] = useState(false);
  const [snGuidance, setSnGuidance] = useState('');
  const [snNote, setSnNote] = useState<Note | null>(null);

  useEffect(() => {
    let live = true;
    adminGet<AnonLimits>('/admin/anonymous-limits').then(d => {
      if (!live) return;
      setEnabled(!!d.enabled);
      setAnalyses(String(d.max_analyses_per_day ?? 0));
      setMb(String(d.max_audio_mb ?? 0));
      setTts(String(d.max_tts_per_day ?? 0));
      setFeatAnalyze(!!d.features?.analyze);
      setFeatTts(!!d.features?.tts);
    }).catch(() => {});
    adminGet<SentimentCfg>('/admin/public-sentiment-config').then(d => {
      if (!live) return;
      setSnEnabled(!!d.enabled);
      setSnGuidance(d.guidance || '');
    }).catch(() => {});
    return () => { live = false; };
  }, []);

  const saveAnon = async () => {
    setNote(null);
    try {
      await adminSend('PUT', '/admin/anonymous-limits', {
        enabled,
        max_analyses_per_day: parseInt(analyses, 10) || 0,
        max_audio_mb: parseInt(mb, 10) || 0,
        max_tts_per_day: parseInt(tts, 10) || 0,
        // `kb: false` is sent explicitly rather than omitted: an anonymous visitor has no
        // workspace, so there is no knowledge base for them to reach, and saying so keeps the
        // stored blob honest about a feature that can never be turned on here.
        features: { analyze: featAnalyze, tts: featTts, kb: false },
      });
      setNote({ kind: 'ok', text: t('toast.saved') });
      toast(t('toast.saved'), 'ok');
    } catch (e) {
      if (e instanceof SessionExpired) return;
      setNote({ kind: 'err', text: t('toast.error') });
    }
  };

  const saveSentiment = async () => {
    setSnNote(null);
    try {
      await adminSend('PUT', '/admin/public-sentiment-config', {
        enabled: snEnabled,
        guidance: snGuidance,
      });
      setSnNote({ kind: 'ok', text: t('toast.saved') });
      toast(t('toast.saved'), 'ok');
    } catch (e) {
      if (e instanceof SessionExpired) return;
      setSnNote({ kind: 'err', text: t('toast.error') });
    }
  };

  return (
    <>
      <div className="card">
        <h3>{t('adm.anonheading')}</h3>
        <CheckRow checked={enabled} onChange={setEnabled}>
          <span>{t('adm.allowanon')}</span>
        </CheckRow>
        <div className="row" style={{ marginTop: 12 }}>
          <div>
            <label htmlFor="a_analyses">{t('adm.maxanalyses')}</label>
            <input id="a_analyses" type="number" value={analyses} onChange={e => setAnalyses(e.target.value)} />
          </div>
          <div>
            <label htmlFor="a_mb">{t('adm.maxmb')}</label>
            <input id="a_mb" type="number" value={mb} onChange={e => setMb(e.target.value)} />
          </div>
          <div>
            <label htmlFor="a_tts">{t('adm.maxtts')}</label>
            <input id="a_tts" type="number" value={tts} onChange={e => setTts(e.target.value)} />
          </div>
        </div>
        <p className="hint">{t('pb.storage.moved')}</p>
        <h4>{t('adm.features')}</h4>
        <div className="inline" style={{ gap: 18 }}>
          <CheckRow checked={featAnalyze} onChange={setFeatAnalyze} style={{ gap: 6 }}>
            <span>{t('feat.analyze')}</span>
          </CheckRow>
          <CheckRow checked={featTts} onChange={setFeatTts} style={{ gap: 6 }}>
            <span>{t('feat.tts')}</span>
          </CheckRow>
        </div>
        <div className="actions">
          <button className="primary" type="button" onClick={saveAnon}>{t('btn.savelimits')}</button>
        </div>
        <Msg note={note} />
      </div>

      {/* The PUBLIC site's standalone Sentiment tab only. A tenant's own sentiment config is
          edited by that tenant (or by an operator acting as them) in the workspace portal. */}
      <div className="card">
        <h3>{t('adm.sentiment.heading')}</h3>
        <CheckRow checked={snEnabled} onChange={setSnEnabled}>
          <span>{t('sn.enabled')}</span>
        </CheckRow>
        <label htmlFor="sn_guidance">{t('sn.guidance')}</label>
        <textarea
          id="sn_guidance"
          value={snGuidance}
          placeholder={t('sn.guidance.ph')}
          style={{ minHeight: 80 }}
          onChange={e => setSnGuidance(e.target.value)}
        />
        <div className="actions">
          <button className="primary" type="button" onClick={saveSentiment}>{t('sn.save')}</button>
        </div>
        <Msg note={snNote} />
      </div>
    </>
  );
}
