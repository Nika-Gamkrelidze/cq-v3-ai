'use client';
import { useEffect, useState } from 'react';
import { Tip } from '@/components/ui/Tip';
import { toast } from '@/components/ui/Toast';
import { useI18n } from '@/lib/useI18n';
import { SessionExpired, adminGet, adminSend, errText } from './api';
import { Msg, type Note } from './parts';

/* Retention — one field, its own tab.

   It is here rather than under Anonymous because the number now decides when a PAYING TENANT'S
   call recording stops being replayable, and a control that dangerous should not be read as
   "settings for guests". The Anonymous panel keeps a line pointing at this tab, for the
   operator who remembers where the field used to be. */

interface Storage { retention_days?: number }

export default function StorageTab() {
  const { t } = useI18n();
  const [days, setDays] = useState('');
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<Note | null>(null);

  useEffect(() => {
    let live = true;
    adminGet<Storage>('/admin/storage')
      .then(d => { if (live) setDays(String(d.retention_days ?? 30)); })
      // Same as the legacy `if (!r.ok) return;` — a panel that could not load stays empty
      // rather than claiming a value nobody set.
      .catch(() => {});
    return () => { live = false; };
  }, []);

  const save = async () => {
    setNote(null);
    /* 0 IS A REAL, STORABLE ANSWER here — "keep forever" — so the value is parsed and
       range-checked rather than `||`-defaulted into 30 the way a truthiness test would. */
    const n = parseInt(days, 10);
    if (!Number.isFinite(n) || n < 0 || n > 3650) {
      setNote({ kind: 'err', text: t('toast.error') });
      return;
    }
    setBusy(true);
    try {
      const d = await adminSend<Storage>('PUT', '/admin/storage', { retention_days: n });
      setDays(String(d.retention_days ?? n));
      setNote({ kind: 'ok', text: t('toast.saved') });
      toast(t('toast.saved'), 'ok');
    } catch (e) {
      if (e instanceof SessionExpired) return;
      setNote({ kind: 'err', text: errText(e, t) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="card">
      <h3>{t('pb.storage.heading')}</h3>
      <p className="hint">{t('pb.storage.desc')}</p>
      <div className="row" style={{ marginTop: 6 }}>
        <div className="w-num">
          <label htmlFor="st_days">
            <span>{t('pb.storage.days')}</span>
            <Tip text={t('pb.storage.hint')} />
          </label>
          <input
            id="st_days"
            type="number"
            min={0}
            max={3650}
            value={days}
            onChange={e => setDays(e.target.value)}
          />
        </div>
      </div>
      <div className="actions">
        <button className="primary" type="button" onClick={save} disabled={busy}>
          {t('btn.save')}
        </button>
      </div>
      <Msg note={note} />
    </div>
  );
}
