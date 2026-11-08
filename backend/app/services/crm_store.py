"""Persistence for CRM forms (`crm_forms`) and filled forms (`crm_results`).

Every statement here is scoped by `client_id` — `tests/test_crm_store_sql.py` reads this file
as an AST and fails any query that is not. The default form is the one exception, and it
issues no SQL from this module: it is an `app_settings` blob read through settings_store.

FORMS. A tenant owns any number of forms, each a `form_key` with a history of versions and
one active row. A tenant that has never saved one fills the DEFAULT form, which is returned
here as `form_key "default"`, `version 0`, `is_default True` — the same convention as the
default rubric. The default is never copied for a tenant until they act: saving it makes it
theirs, and creating a different form first copies the default in beside it, so the form
they were using does not silently disappear from their list.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

import asyncpg

from ..db import pool
from . import crm_form, crm_presets, settings_store

DEFAULT_KEY = "default"
DEFAULT_FORM_SETTING = "default_crm_form"


def _json(value):
    return json.loads(value) if isinstance(value, str) else value


# --------------------------------------------------------------------------- #
# The default form
# --------------------------------------------------------------------------- #
async def get_default_form() -> dict:
    """{definition, source: stored|builtin, updated_at, updated_by}. A stored blob that no
    longer validates (the schema tightened under it) falls back to the built-in default
    rather than handing every tenant a form the editor would refuse."""
    stored = await settings_store.get_blob(DEFAULT_FORM_SETTING)
    if isinstance(stored.get("definition"), dict):
        defn, errors, _ = crm_form.normalize_definition(stored["definition"])
        if not errors:
            return {"definition": defn, "source": "stored",
                    "updated_at": stored.get("updated_at"), "updated_by": stored.get("updated_by")}
    defn, _, _ = crm_form.normalize_definition(crm_presets.BUILTIN_DEFAULT)
    return {"definition": defn, "source": "builtin", "updated_at": None, "updated_by": None}


async def set_default_form(raw, updated_by: str) -> tuple[dict, list[dict]]:
    defn, warnings = crm_form.require_valid(raw)
    await settings_store.set_blob(DEFAULT_FORM_SETTING, {
        "definition": defn, "updated_at": datetime.now(timezone.utc).isoformat(),
        "updated_by": updated_by})
    return await get_default_form(), warnings


async def reset_default_form() -> dict:
    """Back to the built-in default. An empty object, not a deleted key: `set_blob` is the
    only writer settings_store offers, and `get_default_form` reads `{}` as "never saved"."""
    await settings_store.set_blob(DEFAULT_FORM_SETTING, {})
    return await get_default_form()


def _as_default_form(d: dict) -> dict:
    return {"form_key": DEFAULT_KEY, "version": 0, "name": d["definition"]["name"],
            "definition": d["definition"], "is_primary": True, "is_default": True,
            "updated_at": d["updated_at"], "updated_by": d["updated_by"]}


# --------------------------------------------------------------------------- #
# A tenant's forms
# --------------------------------------------------------------------------- #
def _row(row) -> dict:
    return {"form_key": row["form_key"], "version": row["version"], "name": row["name"],
            "definition": _json(row["definition"]), "is_primary": row["is_primary"],
            "is_default": False,
            "updated_at": row["created_at"].isoformat() if row["created_at"] else None,
            "updated_by": row["created_by"]}


async def _own_forms(client_id: str) -> list[dict]:
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT form_key, version, name, definition, is_primary, created_at, created_by
            FROM crm_forms WHERE client_id = $1 AND is_active
            ORDER BY is_primary DESC, lower(name), form_key
            """, client_id)
    return [_row(r) for r in rows]


async def list_forms(client_id: str) -> list[dict]:
    """The tenant's forms, primary first — or the default alone when they have none."""
    own = await _own_forms(client_id)
    if own:
        return own
    return [_as_default_form(await get_default_form())]


async def get_form(client_id: str, form_key: str) -> dict | None:
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT form_key, version, name, definition, is_primary, created_at, created_by
            FROM crm_forms WHERE client_id = $1 AND form_key = $2 AND is_active
            """, client_id, form_key)
        if row:
            return _row(row)
        if form_key != DEFAULT_KEY:
            return None
        has_own = await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM crm_forms WHERE client_id = $1 AND is_active)", client_id)
    if has_own:
        return None
    return _as_default_form(await get_default_form())


async def primary_form(client_id: str) -> dict:
    forms = await list_forms(client_id)
    return next((f for f in forms if f["is_primary"]), forms[0])


async def get_version(client_id: str, form_key: str, version: int) -> dict | None:
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT form_key, version, name, definition, is_primary, created_at, created_by
            FROM crm_forms WHERE client_id = $1 AND form_key = $2 AND version = $3
            """, client_id, form_key, version)
    return _row(row) if row else None


async def versions(client_id: str, form_key: str) -> list[dict]:
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT version, name, is_active, created_at, created_by
            FROM crm_forms WHERE client_id = $1 AND form_key = $2 ORDER BY version DESC LIMIT 50
            """, client_id, form_key)
    return [{"version": r["version"], "name": r["name"], "is_active": r["is_active"],
             "created_at": r["created_at"].isoformat(), "created_by": r["created_by"]} for r in rows]


async def unique_key(client_id: str, name: str) -> str:
    """A key for a NEW form: from its name when that has Latin letters, else `form_2`…,
    never one this tenant has used before (a deleted form's history still owns its key)."""
    base = crm_form.slugify(name, "form")[:40]
    async with pool().acquire() as conn:
        taken = {r["form_key"] for r in await conn.fetch(
            "SELECT DISTINCT form_key FROM crm_forms WHERE client_id = $1", client_id)}
    # Reserved even while unused: creating a form while still on the default copies the
    # default in under this key, and a new form must not land on top of it.
    taken.add(DEFAULT_KEY)
    if base not in taken:
        return base
    n = 2
    while f"{base}_{n}" in taken:
        n += 1
    return f"{base}_{n}"


async def _lock_tenant(conn, client_id: str) -> None:
    """Serialise this tenant's form writes. Every statement that moves `is_primary` takes it:
    two first saves racing would both see "no forms yet" and both try to become primary, and a
    save racing a set_primary or a delete would either trip `uq_crm_forms_primary` (a 500 the
    caller cannot retry its way out of) or leave the tenant with no primary at all. Row locks
    are not enough — a save RETIRES the active row and inserts a new one, so the row another
    transaction locked and re-checked is already inactive and the replacement is invisible to
    its snapshot."""
    await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1::text))", f"crm:{client_id}")


async def save_form(client_id: str, form_key: str, raw, updated_by: str) -> tuple[dict, list[dict]]:
    """Validate, then store a new active version of `form_key`. ((form, warnings) or
    FormInvalid.) A tenant with no forms yet keeps the default beside a new form: see the
    module docstring."""
    if not crm_form.KEY_RE.match(form_key or ""):
        raise crm_form.FormInvalid([{"path": "key", "message": f"`{form_key}` is not a valid form key."}])
    defn, warnings = crm_form.require_valid(raw)
    # Read the default BEFORE a connection is held. It is wanted only on the copy-the-default
    # path below, but reading it there asks the pool for a SECOND connection from inside the
    # advisory lock — and the saves queued on that lock are holding the very connections it
    # would wait for. Neither wait has a timeout, so `db_pool_max` concurrent first saves for
    # one tenant empty the pool for the whole process, not just for CRM.
    inherited = None if form_key == DEFAULT_KEY else (await get_default_form())["definition"]
    for _attempt in range(3):
        try:
            async with pool().acquire() as conn:
                async with conn.transaction():
                    await _lock_tenant(conn, client_id)
                    has_own = await conn.fetchval(
                        "SELECT EXISTS(SELECT 1 FROM crm_forms WHERE client_id = $1 AND is_active)",
                        client_id)
                    if not has_own and inherited is not None:
                        await _insert(conn, client_id, DEFAULT_KEY, inherited, updated_by, primary=True)
                    current = await conn.fetchrow(
                        "SELECT is_primary FROM crm_forms WHERE client_id = $1 AND form_key = $2 AND is_active",
                        client_id, form_key)
                    primary = current["is_primary"] if current else not has_own and form_key == DEFAULT_KEY
                    await _insert(conn, client_id, form_key, defn, updated_by, primary=primary)
            break
        except asyncpg.UniqueViolationError:
            if _attempt == 2:
                raise
    return await get_form(client_id, form_key), warnings


async def _insert(conn, client_id: str, form_key: str, defn: dict, updated_by: str, *, primary: bool) -> None:
    next_ver = await conn.fetchval(
        "SELECT COALESCE(MAX(version), 0) + 1 FROM crm_forms WHERE client_id = $1 AND form_key = $2",
        client_id, form_key)
    await conn.execute(
        "UPDATE crm_forms SET is_active = false, is_primary = false "
        "WHERE client_id = $1 AND form_key = $2 AND is_active", client_id, form_key)
    await conn.execute(
        """
        INSERT INTO crm_forms (client_id, form_key, version, name, definition, is_active,
                               is_primary, created_by)
        VALUES ($1, $2, $3, $4, $5::jsonb, true, $6, $7)
        """, client_id, form_key, next_ver, defn["name"], json.dumps(defn), primary, updated_by)


async def set_primary(client_id: str, form_key: str) -> bool:
    async with pool().acquire() as conn:
        async with conn.transaction():
            await _lock_tenant(conn, client_id)
            exists = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM crm_forms WHERE client_id = $1 AND form_key = $2 AND is_active)",
                client_id, form_key)
            if not exists:
                return False
            await conn.execute(
                "UPDATE crm_forms SET is_primary = false WHERE client_id = $1 AND is_active AND is_primary",
                client_id)
            await conn.execute(
                "UPDATE crm_forms SET is_primary = true WHERE client_id = $1 AND form_key = $2 AND is_active",
                client_id, form_key)
    return True


async def delete_form(client_id: str, form_key: str) -> bool:
    """Retire a form: no active row, history kept. When it was the primary, the most recently
    saved remaining form becomes primary; with none left the tenant is back on the default."""
    async with pool().acquire() as conn:
        async with conn.transaction():
            await _lock_tenant(conn, client_id)
            row = await conn.fetchrow(
                "UPDATE crm_forms SET is_active = false, is_primary = false "
                "WHERE client_id = $1 AND form_key = $2 AND is_active RETURNING is_primary",
                client_id, form_key)
            if row is None:
                return False
            has_primary = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM crm_forms WHERE client_id = $1 AND is_active AND is_primary)",
                client_id)
            if not has_primary:
                await conn.execute(
                    """
                    UPDATE crm_forms SET is_primary = true
                    WHERE id = (SELECT id FROM crm_forms WHERE client_id = $1 AND is_active
                                ORDER BY created_at DESC LIMIT 1) AND client_id = $1
                    """, client_id)
    return True


# --------------------------------------------------------------------------- #
# Filled forms
# --------------------------------------------------------------------------- #
def _label_of(result: dict, final: dict | None) -> str | None:
    lf = result.get("label_field")
    if not lf:
        return None
    value = (final or {}).get(lf) if final and lf in final else (result.get("label") or {}).get("key")
    return str(value) if value not in (None, "", []) else None


def _result_row(row) -> dict:
    return {"id": str(row["id"]), "job_id": str(row["job_id"]) if row["job_id"] else None,
            "form_key": row["form_key"], "form_version": row["form_version"],
            "form_name": row["form_name"], "label": row["label"],
            "result": _json(row["result"]), "final": _json(row["final"]),
            "status": row["status"],
            "created_at": row["created_at"].isoformat(), "created_by": row["created_by"],
            "reviewed_at": row["reviewed_at"].isoformat() if row["reviewed_at"] else None,
            "reviewed_by": row["reviewed_by"]}


_RESULT_COLS = ("id, job_id, form_key, form_version, form_name, label, result, final, status, "
                "created_at, created_by, reviewed_at, reviewed_by")


async def save_result(client_id: str, job_id: str | None, result: dict, created_by: str | None) -> dict:
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            f"""
            INSERT INTO crm_results (client_id, job_id, form_key, form_version, form_name, label,
                                     result, created_by)
            VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8)
            RETURNING {_RESULT_COLS}
            """, client_id, job_id, result["form_key"], result["form_version"],
            result.get("form_name"), _label_of(result, None), json.dumps(result), created_by)
    return _result_row(row)


async def latest_results(client_id: str, job_id: str) -> list[dict]:
    """The newest filled form per form key for one recording."""
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT DISTINCT ON (form_key) {_RESULT_COLS}
            FROM crm_results WHERE client_id = $1 AND job_id = $2
            ORDER BY form_key, created_at DESC
            """, client_id, job_id)
    return sorted((_result_row(r) for r in rows), key=lambda r: r["created_at"], reverse=True)


async def get_result(client_id: str, result_id: str) -> dict | None:
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT {_RESULT_COLS} FROM crm_results WHERE client_id = $1 AND id = $2",
            client_id, result_id)
    return _result_row(row) if row else None


async def save_review(client_id: str, result_id: str, final: dict, status: str,
                      reviewed_by: str | None) -> dict | None:
    if status not in ("draft", "approved"):
        raise ValueError("status must be draft or approved")
    current = await get_result(client_id, result_id)
    if current is None:
        return None
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            f"""
            UPDATE crm_results
            SET final = $3::jsonb, status = $4, label = $5, reviewed_at = now(), reviewed_by = $6
            WHERE client_id = $1 AND id = $2
            RETURNING {_RESULT_COLS}
            """, client_id, result_id, json.dumps(final), status,
            _label_of(current["result"], final), reviewed_by)
    return _result_row(row) if row else None


def safe_filename(name: str) -> str:
    """A download name from a form name: Georgian letters are kept (browsers handle UTF-8
    filenames through `filename*`), anything a file system or a header dislikes is not."""
    s = re.sub(r"[\\/:*?\"<>|\r\n]+", " ", name or "").strip()
    return (s or "crm-form")[:80]
