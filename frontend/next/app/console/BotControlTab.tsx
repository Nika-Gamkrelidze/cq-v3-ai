'use client';
import { useCallback, useEffect, useRef, useState } from 'react';
import { confirmDialog } from '@/components/ui/Modal';
import { Tip } from '@/components/ui/Tip';
import { toast } from '@/components/ui/Toast';
import { ApiError } from '@/lib/session';
import { useI18n } from '@/lib/useI18n';
import { SessionExpired, adminGet, adminSend } from './api';
import type { Tenant } from './api';
import ChatConnections from './ChatConnections';
import { killListAfter, killRowState, type KillRow, type KillState } from './logic';
import { Msg, type Note } from './parts';

/* BOT CONTROL — the 3am brake. State first, one click to act, no settings blob.

   The switch itself is one `app_settings` blob: `{global_disabled, disabled_clients:[id]}`, read
   (5 s-cached) on every autopilot turn. The per-tenant column beside it is read from each
   workspace's OWN chat config, so this page shows the two facts an operator needs at 3am — is it
   allowed to answer, and is it stopped — with no third place to look.

   N+1 BY DESIGN: one request per workspace for its `autopilot_enabled`. The tenant list is tens
   of rows, not thousands, and a workspace whose config cannot be read is shown, flagged and
   still stoppable rather than dropped from the table. */

export default function BotControlTab() {
  const { t } = useI18n();
  const [state, setState] = useState<KillState>({ global_disabled: false, disabled_clients: [] });
  const [rows, setRows] = useState<KillRow[]>([]);
  const [reachable, setReachable] = useState(true);
  const [overviewFailed, setOverviewFailed] = useState(false);
  const [note, setNote] = useState<Note | null>(null);

  const loadTenants = useCallback(async () => {
    setOverviewFailed(false);
    let tenants: Tenant[];
    try {
      tenants = await adminGet<Tenant[]>('/admin/tenants');
    } catch (e) {
      if (e instanceof SessionExpired) return;
      setOverviewFailed(true);
      return;
    }
    setRows(await Promise.all((Array.isArray(tenants) ? tenants : []).map(async x => {
      try {
        const c = await adminGet<{ autopilot_enabled?: boolean }>(`/admin/chat/${x.id}/config`);
        return { id: String(x.id), name: x.name, autopilot: !!c?.autopilot_enabled, reachable: true };
      } catch {
        return { id: String(x.id), name: x.name, autopilot: false, reachable: false };
      }
    })));
  }, []);

  const load = useCallback(async () => {
    setNote(null);
    try {
      const d = await adminGet<{ global_disabled?: boolean; disabled_clients?: unknown }>(
        '/admin/chat/kill-switch');
      setState({
        global_disabled: !!d?.global_disabled,
        disabled_clients: Array.isArray(d?.disabled_clients) ? d.disabled_clients.map(String) : [],
      });
      setReachable(true);
    } catch (e) {
      if (e instanceof SessionExpired) return;
      setReachable(false);
      setNote({
        kind: 'err',
        text: e instanceof ApiError && e.status === 404 ? t('kill.unavailable') : t('kill.loadfail'),
      });
    }
    await loadTenants();
  }, [loadTenants, t]);

  /* Once, on mount — and read through a ref so a LANGUAGE SWITCH does not re-run it. `load`
     depends on `t` (for its two failure messages), so a plain `[load]` dependency would re-issue
     the kill-switch read and one config request per workspace every time somebody presses KA. */
  const loadRef = useRef(load);
  useEffect(() => { loadRef.current = load; });
  useEffect(() => { void loadRef.current(); }, []);

  const put = async (patch: Record<string, unknown>) => {
    setNote(null);
    try {
      await adminSend('PUT', '/admin/chat/kill-switch', patch);
    } catch (e) {
      if (e instanceof SessionExpired) return;
      const text = e instanceof ApiError && e.status === 404 ? t('kill.unavailable') : t('toast.error');
      setNote({ kind: 'err', text });
      toast(text, 'err');
      return;
    }
    setNote({ kind: 'ok', text: t('kill.saved') });
    toast(t('kill.saved'), 'ok');
    await load();
  };

  const flipGlobal = async () => {
    const next = !state.global_disabled;
    const message = next ? t('kill.confirm.global') : t('kill.confirm.resume.global');
    if (!(await confirmDialog(message, { ok: next ? t('kill.stop') : t('kill.resume'), danger: next }))) return;
    await put({ global_disabled: next });
  };

  const flipTenant = async (row: KillRow, stop: boolean) => {
    const message = (stop ? t('kill.confirm.tenant') : t('kill.confirm.resume'))
      .replace('{name}', row.name || '');
    if (!(await confirmDialog(message, { ok: stop ? t('kill.stop') : t('kill.resume'), danger: stop }))) return;
    await put({ disabled_clients: killListAfter(state, row.id, stop) });
  };

  return (
    <>
      <div className="card">
        <div className="row" style={{ justifyContent: 'space-between', alignItems: 'flex-start' }}>
          <div style={{ flex: 1 }}>
            <h3 style={{ margin: 0 }}>
              <span>{t('kill.heading')}</span>
              <Tip text={t('kill.desc')} />
            </h3>
          </div>
          <div className="inline" style={{ flex: 0, gap: 8 }}>
            {/* An unreachable brake shows "—", never "running": the one thing this pill may
                not do is claim a bot is allowed to talk when nobody could ask. */}
            <span className={`pill ${!reachable || state.global_disabled ? 'error' : 'ready'}`}>
              {!reachable ? '—' : state.global_disabled ? t('kill.global.on') : t('kill.global.off')}
            </span>
            <button className="ghost" type="button" onClick={() => void load()}>{t('btn.refresh')}</button>
          </div>
        </div>
        <div className="actions">
          <button
            className={state.global_disabled ? 'primary' : 'danger'}
            type="button"
            disabled={!reachable}
            onClick={flipGlobal}
          >
            {state.global_disabled ? t('kill.resume') : t('kill.global')}
          </button>
        </div>
        <Msg note={note} />
      </div>

      <div className="card">
        <h3>{t('kill.tenants')}</h3>
        <div style={{ marginTop: 12 }}>
          {overviewFailed ? <div className="msg err">{t('kill.overviewfail')}</div>
            : !rows.length ? <div className="empty">—</div> : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>{t('th.name')}</th>
                      <th>{t('th.autopilot')}</th>
                      <th>{t('th.status')}</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map(x => {
                      const st = killRowState(x, state);
                      const stopped = state.disabled_clients.includes(x.id);
                      return (
                        <tr key={x.id}>
                          <td>
                            {x.name}
                            {x.reachable ? null : <span className="hint"> {t('kill.overviewfail')}</span>}
                          </td>
                          <td><span className={`pill ${x.autopilot ? 'on' : 'off'}`}>{x.autopilot ? '●' : '○'}</span></td>
                          <td><span className={`pill ${st.cls}`}>{t(st.key)}</span></td>
                          <td className="inline">
                            <button
                              className={stopped ? 'primary' : 'danger'}
                              type="button"
                              onClick={() => flipTenant(x, !stopped)}
                            >
                              {stopped ? t('kill.resume') : t('kill.stop')}
                            </button>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
        </div>
      </div>

      <ChatConnections />
    </>
  );
}
