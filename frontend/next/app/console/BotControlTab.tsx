'use client';
import { Fragment, useCallback, useEffect, useRef, useState } from 'react';
import { confirmDialog } from '@/components/ui/Modal';
import { Tip } from '@/components/ui/Tip';
import { toast } from '@/components/ui/Toast';
import { copyText } from '@/lib/clipboard';
import { ApiError, setActingTenant } from '@/lib/session';
import { useI18n } from '@/lib/useI18n';
import { SessionExpired, adminGet, adminSend, errText } from './api';
import type { Tenant } from './api';
import ChatConnections from './ChatConnections';
import { killListAfter, killRowState, type KillRow, type KillState } from './logic';
import { Msg, type Note } from './parts';

/* BOT CONTROL — the 3am brake. State first, one click to act, no settings blob.

   The switch itself is one `app_settings` blob: `{global_disabled, disabled_clients:[id]}`, read
   (5 s-cached) on every autopilot turn. The per-tenant column beside it is read from each
   workspace's OWN chat config, so this page shows the two facts an operator needs at 3am — is it
   allowed to answer, and is it stopped — with no third place to look.

   TWO SWITCHES PER ROW, and they are not the same thing. The Autopilot checkbox is the
   workspace's own `autopilot_enabled` — whether its bot answers customers at all — and Stop /
   Resume is the operator brake that silences a bot which is on without touching that setting.
   They are worded, placed and styled apart on purpose: confusing them at 3am either leaves a bot
   talking or silently rewrites a customer's configuration.

   The checkbox calls `PUT /admin/chat/{id}/autopilot`, never `PUT /admin/chat/{id}/config`. The
   config route INSERTs a whole new version from the body, so a body carrying only the flag would
   wipe the tenant's persona, greeting, refusal copy and canned replies; and reading the MERGED
   config back to send it whole would freeze whatever the tenant currently inherits from the
   default bot into its own row, so later default-bot edits would stop reaching it. The dedicated
   route copies the tenant's raw row and flips one field, which is the only write that can do
   neither.

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
  /* One in-flight flag PER ROW, not one for the page: a slow write for one workspace must not
     freeze every other row's switch, and a double click on the same row must not send two. */
  const [busy, setBusy] = useState<Record<string, boolean>>({});
  /* The row whose "share a document first" note is open. One at a time — two open notes would
     read as two different problems. */
  const [needPublic, setNeedPublic] = useState<string | null>(null);

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

  const flipAutopilot = async (row: KillRow, enabled: boolean) => {
    if (busy[row.id]) return;
    setNeedPublic(null);
    /* ON points a model at that workspace's customers, so it is confirmed and says so. OFF only
       ever makes the bot quieter and is not worth a dialog. */
    if (enabled && !(await confirmDialog(
      t('kill.confirm.autopilot.on', { name: row.name || '' }),
      { ok: t('kill.autopilot.on') },
    ))) return;
    setBusy(b => ({ ...b, [row.id]: true }));
    try {
      const r = await adminSend<{ autopilot_enabled?: boolean }>(
        'PUT', `/admin/chat/${row.id}/autopilot`, { enabled });
      const now = !!r?.autopilot_enabled;
      // Only this row, from the server's answer: the other rows did not change, and re-reading
      // every workspace's config for one checkbox would be the N+1 above for no reason.
      setRows(rs => rs.map(x => (x.id === row.id ? { ...x, autopilot: now, reachable: true } : x)));
      toast(t(now ? 'kill.autopilot.saved.on' : 'kill.autopilot.saved.off', { name: row.name || '' }), 'ok');
    } catch (e) {
      if (e instanceof SessionExpired) return;
      /* A 409 from THIS route is the gate — no document shared with the bot — and nothing else.
         Matched on the status because `ApiError` keeps only `detail`, not the body's `code`. A
         toggle that only fails is the difference between a broken product and one that
         teaches, so the row explains the reason and offers the fix. */
      if (e instanceof ApiError && e.status === 409) setNeedPublic(row.id);
      else toast(errText(e, t), 'err');
    } finally {
      setBusy(b => {
        const next = { ...b };
        delete next[row.id];
        return next;
      });
    }
  };

  /* Into that workspace's KB tab as its operator. `?tenant=` wins over the stored selection on
     /workspace and `#kb` picks the tab (see app/workspace/page.tsx); setting the acting tenant as
     well keeps the two in agreement. Same tab, not a new one: the admin token lives in this
     tab's sessionStorage, and a fresh tab would open the sign-in gate instead. */
  const openKb = (id: string) => {
    setActingTenant(id);
    location.assign(`/workspace?tenant=${encodeURIComponent(id)}#kb`);
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
                      <th>
                        <span>{t('th.selector')}</span>
                        <Tip text={t('kill.selector.hint')} />
                      </th>
                      <th>
                        <span>{t('th.autopilot')}</span>
                        <Tip text={t('kill.autopilot.hint')} />
                      </th>
                      <th>{t('th.status')}</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map(x => {
                      const st = killRowState(x, state);
                      const stopped = state.disabled_clients.includes(x.id);
                      return (
                        <Fragment key={x.id}>
                          <tr>
                            <td>
                              {x.name}
                              {x.reachable ? null : <span className="hint"> {t('kill.overviewfail')}</span>}
                            </td>
                            {/* The value a chat service sends as X-CQ-Tenant. Every request on this
                                page already used it and none displayed it, so operators pasted the
                                name, the slug, the integration id or the tenant API key into the chat
                                product instead. No await before copyText: production copies through
                                the gesture-bound fallback, see lib/clipboard.ts. */}
                            <td className="inline" style={{ gap: 8 }}>
                              <code style={{ fontSize: 12 }}>{x.id}</code>
                              <button
                                className="ghost"
                                type="button"
                                onClick={() => {
                                  copyText(x.id).then(ok => toast(
                                    ok ? t('pb.copied') : t('kill.selector.copyfail'), ok ? 'ok' : 'err'));
                                }}
                              >
                                {t('pb.copy')}
                              </button>
                            </td>
                            {/* Controlled by the server's answer, not the click: the box moves only
                                once the write lands, so a declined confirm or a 409 leaves it where
                                the workspace really is. */}
                            <td>
                              <input
                                type="checkbox"
                                style={{ width: 'auto' }}
                                checked={x.autopilot}
                                disabled={!!busy[x.id]}
                                aria-label={t('kill.autopilot.aria', { name: x.name || '' })}
                                onChange={e => void flipAutopilot(x, e.target.checked)}
                              />
                            </td>
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
                          {needPublic === x.id ? (
                            <tr>
                              <td colSpan={5}>
                                <div className="msg err">
                                  <b>{t('bot.needpublic.title')}</b>
                                  <div style={{ marginTop: 6 }}>{t('bot.needpublic.body')}</div>
                                  <div className="actions">
                                    <button type="button" className="ghost" onClick={() => openKb(x.id)}>
                                      {t('bot.needpublic.link')}
                                    </button>
                                  </div>
                                </div>
                              </td>
                            </tr>
                          ) : null}
                        </Fragment>
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
