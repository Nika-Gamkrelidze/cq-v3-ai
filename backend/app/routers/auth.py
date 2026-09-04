"""Unified authentication endpoint (tenant users, registered users, superadmin).

One `POST /auth/login` serves every account kind and returns a `scope` the UI routes on:
`admin` (superadmin), `tenant` (tenant_users) or `user` (app_users — the self-service
email + password accounts).

Registered users have no verification mail because no mail provider exists: sign-up returns a
session token straight away, and the only recovery path is a superadmin resetting the password
from the console (routers/admin.py). Sign-ups can be closed with the registered tier's `enabled`
switch; that switch never locks out an EXISTING account — those are disabled one at a time via
`app_users.is_active`, which `/auth/me` and every `limits.reserve()` re-check because session
tokens are stateless and would otherwise outlive the deactivation.

With no verification step, the two things standing between an open sign-up form and an attacker
minting quota (or burning CPU) at will are both here and both cheap: a per-IP daily meter on
sign-ups, and every PBKDF2 hash run in a thread so one flood cannot stall this single-worker
process. A tenant credential always wins over a registered one at login, but only a WORKING
tenant credential — see `login`.
"""
import asyncio
import re
import secrets

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ..config import settings
from ..db import pool
from ..services import auth, limits, settings_store
from ..services.auth import Principal, client_ip, resolve_principal

router = APIRouter(tags=["auth"])

# Deliberately loose: it rejects what cannot be an address (no @, whitespace, a bare domain), not
# what a full RFC 5322 parser would. With no verification mail there is nothing to gain from
# strictness — a typo is a typo either way — while a strict pattern is what locks out real
# addresses with unusual local parts.
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
EMAIL_MAX = 200
PASSWORD_MIN = 8
DISPLAY_NAME_MAX = 120

_USER_COLS = "id, email, display_name, password_hash, is_active"

# Sign-ups are the only unauthenticated WRITE in the app, and each one mints a whole registered
# tier of daily quota (analyses, TTS, conversions) plus Score/Semantic/Summarise — things the
# anonymous kind is refused outright. Unmetered, a loop of throwaway addresses is therefore a
# quota-authorization bypass with an unbounded multiplier on provider spend, not spam. Counted
# per IP on the general counter (`usage_counters`, scope `anon:<ip>`, kind `registrations`)
# rather than on `anon_usage`, whose columns are the three paid kinds. An operator can retune it
# with `max_registrations_per_day` on the registered blob; the default lives here because
# `settings_store.REGISTERED_DEFAULTS` is not this router's to extend.
REGISTRATIONS_PER_DAY = 10

# A real hash of a value nobody can guess. Verifying an unknown identifier against it costs the
# same PBKDF2 as a real account, so "no such account" cannot be told from "wrong password" with
# a stopwatch (measured: 2 ms vs 43 ms before this).
_DUMMY_HASH = auth.hash_password(secrets.token_urlsafe(32))


def _registration_cap(cfg: dict) -> int:
    """Sign-ups allowed per IP per day. 0 = uncapped (still counted), like every other cap
    here; an unparseable setting falls back to the built-in rather than to "no limit"."""
    raw = cfg.get("max_registrations_per_day")
    if raw in (None, "") or isinstance(raw, bool):
        return REGISTRATIONS_PER_DAY
    try:
        return max(int(raw), 0)
    except (TypeError, ValueError):
        return REGISTRATIONS_PER_DAY


async def _hash(password: str) -> str:
    """PBKDF2 (200k rounds, ~40 ms) OFF the event loop.

    This process is a single uvicorn worker, so a blocking hash inside an `async def` handler
    stalls every other coroutine in it. On an unauthenticated route that makes ~25 requests a
    second from one attacker the whole API's throughput. `hashlib.pbkdf2_hmac` releases the
    GIL, so a thread genuinely parallelises it, and the default executor's thread cap then
    bounds concurrent hashing instead of the event loop being the queue.
    """
    return await asyncio.to_thread(auth.hash_password, password)


async def _verify(password: str, stored: str) -> bool:
    """`auth.verify_password` off the event loop, for the same reason as `_hash`: a
    credential-stuffing burst must not be able to stall live transcriptions."""
    return await asyncio.to_thread(auth.verify_password, password, stored)


class LoginBody(BaseModel):
    username: str
    password: str


class RegisterBody(BaseModel):
    email: str
    password: str
    display_name: str | None = None


class ProfilePatch(BaseModel):
    display_name: str | None = None
    current_password: str | None = None
    new_password: str | None = None


# ---- registered-user helpers -----------------------------------------------
def _clean_email(raw: str) -> str:
    """Lower-cased and trimmed: the unique index is on lower(email), and a user must be able to
    log in with whatever capitalisation they type."""
    email = (raw or "").strip().lower()
    if not email or len(email) > EMAIL_MAX or not _EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="Enter a valid email address")
    return email


def _check_password(password: str) -> str:
    if not isinstance(password, str) or len(password) < PASSWORD_MIN:
        raise HTTPException(status_code=400,
                            detail=f"Password must be at least {PASSWORD_MIN} characters")
    return password


def _clean_display_name(raw: str | None) -> str | None:
    """Empty means "no name": the profile form sends "" to clear it, and NULL is what the UI
    already treats as "show the email instead"."""
    name = (raw or "").strip()
    return name[:DISPLAY_NAME_MAX] or None


def _user_public(row) -> dict:
    return {"id": str(row["id"]), "email": row["email"], "display_name": row["display_name"]}


def _session(row) -> dict:
    return {"scope": "user", "token": auth.make_user_token(row["id"]), "user": _user_public(row)}


async def _fetch_user(user_id: str):
    """The caller's own account row, or the reason it can no longer act.

    Tokens are stateless, so a deactivated or deleted account still carries a valid signature
    for up to token_ttl_hours; this is where that token stops working. 401 for a row that is
    gone (the client should drop the token), 403 for one the operator switched off.
    """
    async with pool().acquire() as conn:
        row = await conn.fetchrow(f"SELECT {_USER_COLS} FROM app_users WHERE id = $1", user_id)
    if not row:
        raise HTTPException(status_code=401, detail="Account not found")
    if not row["is_active"]:
        raise HTTPException(status_code=403, detail="Account disabled")
    return row


async def _login_user(conn, identifier: str, password: str) -> dict:
    """The app_users branch of /auth/login, reached when no tenant CREDENTIAL matched.

    Unknown email, disabled account and wrong password all produce the SAME 401 as the tenant
    branch, so the response never says which of the three it was — and the hash is verified
    even when there is no row (against `_DUMMY_HASH`) so the timing does not say it either.
    """
    row = await conn.fetchrow(
        f"SELECT {_USER_COLS} FROM app_users WHERE lower(email) = lower($1)",
        (identifier or "").strip())
    ok = await _verify(password, row["password_hash"] if row else _DUMMY_HASH)
    if not row or not row["is_active"] or not ok:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    await conn.execute("UPDATE app_users SET last_login_at = now() WHERE id = $1", row["id"])
    return _session(row)


# ---- routes ----------------------------------------------------------------
@router.post("/auth/register")
async def register(request: Request, body: RegisterBody):
    """Create a registered account and sign it in.

    409 on a duplicate email is decided by the unique index, not by a SELECT first: two sign-ups
    for the same address racing each other would otherwise both pass the check and one would
    surface as a 500.
    """
    cfg = await settings_store.get_registered_config()
    if not cfg.get("enabled", True):
        raise HTTPException(status_code=403, detail="Registration is closed")
    email = _clean_email(body.email)
    password = _check_password(body.password)
    # Metered BEFORE the hash, and BEFORE the insert: this is the counter that bounds both the
    # quota an anonymous visitor can mint for themselves and the CPU one request can ask for.
    # A duplicate email spends a unit on purpose — an enumeration sweep is exactly the traffic
    # this is here to make expensive.
    await limits.reserve_counter(f"anon:{client_ip(request)}", "registrations",
                                 _registration_cap(cfg))
    password_hash = await _hash(password)
    async with pool().acquire() as conn:
        try:
            row = await conn.fetchrow(
                """
                INSERT INTO app_users (email, password_hash, display_name, last_login_at)
                VALUES ($1, $2, $3, now())
                RETURNING id, email, display_name
                """, email, password_hash, _clean_display_name(body.display_name))
        except asyncpg.UniqueViolationError:
            raise HTTPException(status_code=409,
                                detail="An account with this email already exists") from None
    return _session(row)


@router.post("/auth/login")
async def login(body: LoginBody):
    """One login entry point. Superadmin credentials return scope 'admin'; valid tenant
    users return scope 'tenant'; registered accounts (looked up by email) return scope 'user'.
    Everything else fails with the same generic error so the response never reveals whether an
    admin (or any specific account) exists.

    A working TENANT credential always wins — a registered account can never shadow a workspace
    one. But a tenant row whose password does NOT verify falls through to app_users instead of
    ending the request: the two identifier spaces overlap (tenant usernames are free-form, so an
    operator may well use email addresses), and refusing there locked the real owner of that
    email out permanently — even a superadmin password reset could not reach them, because this
    branch answered first.
    """
    # Superadmin — checked server-side; scope drives client routing.
    if secrets.compare_digest(body.username, settings.superadmin_username) \
            and secrets.compare_digest(body.password, settings.superadmin_password):
        return {"scope": "admin", "token": settings.admin_token}

    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT u.id, u.client_id, u.password_hash, u.role, u.is_active,
                   c.name AS client_name, c.slug AS client_slug, c.is_active AS client_active
            FROM tenant_users u JOIN clients c ON c.id = u.client_id
            WHERE u.username = $1
            """, body.username)
        if row is not None and row["is_active"] and row["client_active"] \
                and await _verify(body.password, row["password_hash"]):
            token = auth.make_token({
                "client_id": str(row["client_id"]), "user_id": str(row["id"]),
                "role": row["role"],
            })
            return {
                "scope": "tenant", "token": token, "role": row["role"],
                "client": {"id": str(row["client_id"]), "name": row["client_name"],
                           "slug": row["client_slug"]},
            }
        return await _login_user(conn, body.username, body.password)


@router.get("/auth/me")
async def me(principal: Principal = Depends(resolve_principal)):
    if principal.kind == "user":
        row = await _fetch_user(principal.user_id)
        return {"kind": "user", "role": "user", "via": principal.via, "user": _user_public(row)}
    info = {"kind": principal.kind, "role": principal.role, "via": principal.via,
            "client_id": principal.client_id}
    if principal.client_id:
        async with pool().acquire() as conn:
            c = await conn.fetchrow("SELECT name, slug FROM clients WHERE id = $1", principal.client_id)
        if c:
            info["client"] = {"name": c["name"], "slug": c["slug"]}
    return info


@router.put("/auth/me")
async def update_me(body: ProfilePatch, principal: Principal = Depends(resolve_principal)):
    """Registered users only: rename, and/or change the password.

    A password change needs `current_password` even though the caller already holds a valid
    token — a token left in a shared browser must not be enough to lock the real owner out.
    """
    if principal.kind != "user":
        raise HTTPException(status_code=403,
                            detail="Only registered accounts can edit their profile here")
    row = await _fetch_user(principal.user_id)
    sets, vals = [], []
    if body.display_name is not None:
        vals.append(_clean_display_name(body.display_name))
        sets.append(f"display_name = ${len(vals)}")
    if body.new_password is not None:
        new_hash = await _hash(_check_password(body.new_password))
        if not body.current_password \
                or not await _verify(body.current_password, row["password_hash"]):
            raise HTTPException(status_code=403, detail="Current password is incorrect")
        vals.append(new_hash)
        sets.append(f"password_hash = ${len(vals)}")
    if not sets:
        raise HTTPException(status_code=400, detail="Nothing to update")
    vals.append(row["id"])
    async with pool().acquire() as conn:
        updated = await conn.fetchrow(
            f"UPDATE app_users SET {', '.join(sets)} WHERE id = ${len(vals)} "
            f"RETURNING id, email, display_name", *vals)
    return {"kind": "user", "role": "user", "via": principal.via, "user": _user_public(updated)}
