'use client';
import { useCallback, useEffect, useState } from 'react';
import { Select } from '@/components/ui/Select';
import { confirmDialog } from '@/components/ui/Modal';
import { toast } from '@/components/ui/Toast';
import { useI18n } from '@/lib/useI18n';
import { SessionExpired, adminGet, adminSend, errText } from './api';
import type { Tenant, TenantUser } from './api';
import { Msg, type Note } from './parts';

/* Tenants: create, edit, delete, the API key, and the workspace's own login accounts.

   Each row can open ONE sub-panel underneath it — key, users or edit — which is what the
   `<tr><td colspan=7><div id="sub-…">` was in the legacy markup. Here the sub-panel is a
   component with its own state instead of a div being written into, which is the whole of
   docs/MIGRATION.md defect 6: `tenantAction('users', id, name); loadTenants();` fired both
   without awaiting either, and `loadTenants()` rebuilt the entire tbody — destroying the node
   `tenantAction` was concurrently writing its freshly-loaded user list into. Whether the
   operator saw their new user depended on which request answered first.

   THE FIX IS ORDERING, MADE EXPLICIT (see `UsersPanel.add`): the sub-panel reloads its own list
   and awaits it, and only then asks the parent to refresh the table — which now merely re-renders
   a row's user COUNT and cannot delete anything the sub-panel owns. */

export default function TenantsTab() {
  const { t } = useI18n();
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [open, setOpen] = useState<{ id: string; kind: 'key' | 'users' | 'edit' } | null>(null);

  const [name, setName] = useState('');
  const [industry, setIndustry] = useState('');
  const [region, setRegion] = useState('');
  const [created, setCreated] = useState<{ name: string; key: string } | null>(null);
  const [createNote, setCreateNote] = useState<Note | null>(null);

  const load = useCallback(async () => {
    try {
      setTenants(await adminGet<Tenant[]>('/admin/tenants'));
    } catch { /* the legacy `if (!r.ok) return;` — keep the last good table */ }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const create = async () => {
    setCreateNote(null);
    setCreated(null);
    try {
      const d = await adminSend<{ name: string; api_key: string }>('POST', '/admin/tenants', {
        name: name.trim(), industry: industry.trim(), region: region.trim(),
      });
      // The api_key is in the clear here because this is the only response that carries it;
      // it stays readable in the card rather than in a toast that fades after 3.6 seconds.
      setCreated({ name: d.name, key: d.api_key });
      toast(t('toast.created'), 'ok');
      setName(''); setIndustry(''); setRegion('');
      await load();
    } catch (e) {
      if (e instanceof SessionExpired) return;
      setCreateNote({ kind: 'err', text: errText(e, t) });
    }
  };

  const remove = async (x: Tenant) => {
    if (!(await confirmDialog(t('adm.deltenant.confirm', { name: x.name }), { ok: t('btn.delete') }))) return;
    try {
      await adminSend('DELETE', `/admin/tenants/${x.id}`);
      toast(t('toast.deleted'), 'ok');
      if (open?.id === x.id) setOpen(null);
      await load();
    } catch (e) {
      if (e instanceof SessionExpired) return;
      toast(errText(e, t), 'err');
    }
  };

  const show = (id: string, kind: 'key' | 'users' | 'edit') =>
    setOpen(cur => (cur && cur.id === id && cur.kind === kind ? null : { id, kind }));

  return (
    <>
      <div className="card">
        <h3>{t('adm.createtenant')}</h3>
        <div className="row">
          <div>
            <label htmlFor="t_name">{t('f.name')}</label>
            <input id="t_name" placeholder="Acme Bank" value={name} onChange={e => setName(e.target.value)} />
          </div>
          <div>
            <label htmlFor="t_industry">{t('f.industry')}</label>
            <input id="t_industry" placeholder="banking" value={industry} onChange={e => setIndustry(e.target.value)} />
          </div>
          <div>
            <label htmlFor="t_region">{t('f.region')}</label>
            <input id="t_region" placeholder="ge" value={region} onChange={e => setRegion(e.target.value)} />
          </div>
        </div>
        <div className="actions">
          <button className="primary" type="button" onClick={create}>{t('btn.create')}</button>
        </div>
        {created ? (
          <div className="msg ok">
            {t('toast.created')}: <b>{created.name}</b> · {t('btn.apikey')}: <code>{created.key}</code>
          </div>
        ) : null}
        <Msg note={createNote} />
      </div>

      <div className="card">
        <div className="inline" style={{ justifyContent: 'space-between' }}>
          <h3 style={{ margin: 0 }}>{t('adm.tenants')}</h3>
          <button className="ghost" type="button" onClick={() => void load()}>{t('btn.refresh')}</button>
        </div>
        <div style={{ marginTop: 12 }}>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>{t('th.name')}</th>
                  <th>{t('th.slug')}</th>
                  <th>{t('th.industry')}</th>
                  <th>{t('th.active')}</th>
                  <th>{t('th.users')}</th>
                  <th>{t('th.docs')}</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {tenants.map(x => (
                  <TenantRows
                    key={x.id}
                    tenant={x}
                    open={open?.id === x.id ? open.kind : null}
                    onShow={kind => show(x.id, kind)}
                    onDelete={() => remove(x)}
                    onChanged={load}
                  />
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </>
  );
}

function TenantRows({
  tenant, open, onShow, onDelete, onChanged,
}: {
  tenant: Tenant;
  open: 'key' | 'users' | 'edit' | null;
  onShow: (kind: 'key' | 'users' | 'edit') => void;
  onDelete: () => void;
  onChanged: () => Promise<void>;
}) {
  const { t } = useI18n();
  return (
    <>
      <tr>
        <td>{tenant.name}</td>
        <td><code>{tenant.slug}</code></td>
        <td>{tenant.industry || '—'}</td>
        <td><span className={`pill ${tenant.is_active ? 'on' : 'off'}`}>{tenant.is_active ? '●' : '○'}</span></td>
        <td>{tenant.users}</td>
        <td>{tenant.documents}</td>
        <td className="inline">
          <button className="ghost" type="button" onClick={() => onShow('key')}>{t('btn.apikey')}</button>
          <button className="ghost" type="button" onClick={() => onShow('users')}>{t('btn.users')}</button>
          <button className="ghost" type="button" onClick={() => onShow('edit')}>{t('kba.edit')}</button>
          <button className="danger" type="button" onClick={onDelete}>{t('btn.delete')}</button>
        </td>
      </tr>
      {open ? (
        <tr>
          <td colSpan={7}>
            {open === 'key' ? <KeyPanel tenant={tenant} /> : null}
            {open === 'edit' ? <EditPanel tenant={tenant} onSaved={onChanged} /> : null}
            {open === 'users' ? <UsersPanel tenant={tenant} onCountChanged={onChanged} /> : null}
          </td>
        </tr>
      ) : null}
    </>
  );
}

/* ------------------------------------------------------------------ the API key */

function KeyPanel({ tenant }: { tenant: Tenant }) {
  const { t } = useI18n();
  const [key, setKey] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const d = await adminGet<{ api_key: string | null }>(`/admin/tenants/${tenant.id}/key`);
      setKey(d.api_key || '—');
    } catch (e) {
      if (e instanceof SessionExpired) return;
      setKey('—');
    }
  }, [tenant.id]);

  useEffect(() => { void load(); }, [load]);

  const rotate = async () => {
    if (!(await confirmDialog(t('adm.rotate.confirm'), { ok: t('btn.rotate') }))) return;
    try {
      await adminSend('POST', `/admin/tenants/${tenant.id}/rotate-key`);
      toast(t('adm.rotate.done'), 'ok');
      await load();
    } catch (e) {
      if (e instanceof SessionExpired) return;
      toast(t('toast.error'), 'err');
    }
  };

  return (
    <div className="sub inline" style={{ justifyContent: 'space-between' }}>
      <span>{t('btn.apikey')}: <code>{key ?? '…'}</code></span>
      <button className="ghost" type="button" onClick={rotate}>{t('btn.rotate')}</button>
    </div>
  );
}

/* --------------------------------------------------------------- edit a tenant */

function EditPanel({ tenant, onSaved }: { tenant: Tenant; onSaved: () => Promise<void> }) {
  const { t } = useI18n();
  const [name, setName] = useState(tenant.name || '');
  const [industry, setIndustry] = useState(tenant.industry || '');
  const [region, setRegion] = useState(tenant.region || '');
  const [active, setActive] = useState(tenant.is_active);
  const [note, setNote] = useState<Note | null>(null);

  const save = async () => {
    setNote(null);
    try {
      await adminSend('PUT', `/admin/tenants/${tenant.id}`, {
        name: name.trim(), industry: industry.trim(), region: region.trim(), is_active: active,
      });
      toast(t('toast.saved'), 'ok');
      await onSaved();
    } catch (e) {
      if (e instanceof SessionExpired) return;
      setNote({ kind: 'err', text: errText(e, t) });
    }
  };

  return (
    <div className="sub">
      <h4 style={{ marginTop: 0 }}>{t('kba.edit')} · {tenant.name}</h4>
      <div className="row" style={{ alignItems: 'flex-end' }}>
        <div>
          <label htmlFor={`te-name-${tenant.id}`}>{t('f.name')}</label>
          <input id={`te-name-${tenant.id}`} value={name} onChange={e => setName(e.target.value)} />
        </div>
        <div>
          <label htmlFor={`te-ind-${tenant.id}`}>{t('f.industry')}</label>
          <input id={`te-ind-${tenant.id}`} value={industry} onChange={e => setIndustry(e.target.value)} />
        </div>
        <div>
          <label htmlFor={`te-reg-${tenant.id}`}>{t('f.region')}</label>
          <input id={`te-reg-${tenant.id}`} value={region} onChange={e => setRegion(e.target.value)} />
        </div>
        <div style={{ flex: 0 }}>
          <label className="inline" style={{ gap: 5 }}>
            <input type="checkbox" checked={active} onChange={e => setActive(e.target.checked)} />
            <span>{t('th.active')}</span>
          </label>
        </div>
        <div style={{ flex: 0 }}>
          <button className="primary" type="button" onClick={save}>{t('btn.save')}</button>
        </div>
      </div>
      <Msg note={note} />
    </div>
  );
}

/* ------------------------------------------------------- the workspace's accounts */

const ROLE_KEYS = [['member', 'role.member'], ['owner', 'role.owner']] as const;

function UsersPanel({ tenant, onCountChanged }: { tenant: Tenant; onCountChanged: () => Promise<void> }) {
  const { t } = useI18n();
  const [users, setUsers] = useState<TenantUser[] | null>(null);
  const [note, setNote] = useState<Note | null>(null);
  const [busy, setBusy] = useState('');

  // The new-account form.
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [role, setRole] = useState('member');

  const roleOptions = ROLE_KEYS.map(([value, key]) => ({ value, label: t(key) }));

  const load = useCallback(async () => {
    try {
      setUsers(await adminGet<TenantUser[]>(`/admin/tenants/${tenant.id}/users`));
    } catch (e) {
      if (e instanceof SessionExpired) return;
      setUsers([]);
    }
  }, [tenant.id]);

  useEffect(() => { void load(); }, [load]);

  const add = async () => {
    setNote(null);
    try {
      await adminSend('POST', `/admin/tenants/${tenant.id}/users`, {
        username: username.trim(), password, role,
      });
      toast(t('toast.created'), 'ok');
      setUsername(''); setPassword('');
      /* THE ORDERING FIX (docs/MIGRATION.md defect 6). This list is reloaded and AWAITED
         first, and only then does the table above refresh its user count. The legacy code
         started both and awaited neither, and the table rebuild wiped the node this list was
         being written into. */
      await load();
      await onCountChanged();
    } catch (e) {
      if (e instanceof SessionExpired) return;
      setNote({ kind: 'err', text: errText(e, t) });
    }
  };

  const remove = async (u: TenantUser) => {
    if (!(await confirmDialog(t('adm.rmuser.confirm', { u: u.username }), { ok: t('btn.remove') }))) return;
    try {
      await adminSend('DELETE', `/admin/tenants/${tenant.id}/users/${u.id}`);
      toast(t('toast.deleted'), 'ok');
      // Same order as `add`, and for the same reason. The count refresh is new here: the
      // legacy delete path reloaded only this list, leaving the table's Users column one too
      // high until something else happened to reload it.
      await load();
      await onCountChanged();
    } catch (e) {
      if (e instanceof SessionExpired) return;
      setNote({ kind: 'err', text: errText(e, t) });
    }
  };

  return (
    <div className="sub">
      <h4 style={{ marginTop: 0 }}>{t('btn.users')} · {tenant.name}</h4>
      <div>
        {users === null ? <span className="hint">…</span>
          : !users.length ? <span className="hint">—</span>
            : users.map(u => (
              <UserRow
                key={u.id}
                tenantId={tenant.id}
                user={u}
                roleOptions={roleOptions}
                busy={busy === u.id}
                setBusy={setBusy}
                onNote={setNote}
                onRemove={() => remove(u)}
              />
            ))}
      </div>

      <div className="row" style={{ marginTop: 12, alignItems: 'flex-end' }}>
        <div>
          <label htmlFor={`nu-${tenant.id}`}>{t('f.username')}</label>
          <input id={`nu-${tenant.id}`} value={username} onChange={e => setUsername(e.target.value)} />
        </div>
        <div>
          <label htmlFor={`np-${tenant.id}`}>{t('f.password')}</label>
          <input
            id={`np-${tenant.id}`}
            type="password"
            autoComplete="new-password"
            value={password}
            onChange={e => setPassword(e.target.value)}
          />
        </div>
        <div className="w-sel">
          <label htmlFor={`nr-${tenant.id}`}>{t('f.role')}</label>
          <Select id={`nr-${tenant.id}`} value={role} onChange={setRole} options={roleOptions} ariaLabel={t('f.role')} />
        </div>
        <div style={{ flex: 0 }}>
          <button className="primary" type="button" onClick={add}>{t('btn.adduser')}</button>
        </div>
      </div>
      <Msg note={note} />
    </div>
  );
}

function UserRow({
  tenantId, user, roleOptions, busy, setBusy, onNote, onRemove,
}: {
  tenantId: string;
  user: TenantUser;
  roleOptions: { value: string; label: string }[];
  busy: boolean;
  setBusy: (id: string) => void;
  onNote: (n: Note | null) => void;
  onRemove: () => void;
}) {
  const { t } = useI18n();
  const [role, setRole] = useState(user.role);
  const [active, setActive] = useState(user.is_active);
  const [password, setPassword] = useState('');

  // The row re-mounts on every list reload (the key is the user id), but a role change made
  // upstream still has to land if the same row survives.
  useEffect(() => { setRole(user.role); setActive(user.is_active); }, [user.role, user.is_active]);

  const save = async () => {
    onNote(null);
    const body: Record<string, unknown> = { role, is_active: active };
    // Only when one was typed: the route hashes whatever it is given, so sending an empty
    // string would set the account's password to nothing.
    if (password) body.password = password;
    setBusy(user.id);
    try {
      await adminSend('PUT', `/admin/tenants/${tenantId}/users/${user.id}`, body);
      toast(t('adm.user.saved'), 'ok');
      setPassword('');
    } catch (e) {
      if (e instanceof SessionExpired) return;
      onNote({ kind: 'err', text: errText(e, t) });
    } finally {
      setBusy('');
    }
  };

  return (
    <div
      className="inline wrap"
      style={{
        justifyContent: 'space-between',
        padding: '6px 0',
        borderBottom: '1px solid color-mix(in oklab,var(--hairline) 60%,transparent)',
      }}
    >
      <span style={{ minWidth: 140 }}><b>{user.username}</b></span>
      <span className="inline wrap" style={{ gap: 8 }}>
        <Select
          value={role}
          onChange={setRole}
          options={roleOptions}
          ariaLabel={t('f.role')}
          style={{ width: 'auto', minWidth: 140 }}
        />
        <label className="inline" style={{ gap: 5, margin: 0 }}>
          <input type="checkbox" checked={active} onChange={e => setActive(e.target.checked)} />
          <span>{t('th.active')}</span>
        </label>
        <input
          type="password"
          placeholder={t('adm.user.newpw')}
          autoComplete="new-password"
          style={{ width: 210 }}
          value={password}
          onChange={e => setPassword(e.target.value)}
        />
        <button className="ghost" type="button" onClick={save} disabled={busy}>{t('btn.save')}</button>
        <button
          className="act danger"
          type="button"
          title={t('btn.remove')}
          aria-label={t('btn.remove')}
          onClick={onRemove}
        >
          🗑
        </button>
      </span>
    </div>
  );
}
