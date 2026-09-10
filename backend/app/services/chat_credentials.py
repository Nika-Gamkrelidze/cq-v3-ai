"""Integration credentials — the chat site's key, and the grant table that bounds it.

Every other credential in this codebase belongs to exactly one tenant: `clients.api_key` IS a
tenant, a login token carries its `client_id`. The chat site is the first consumer that is
legitimately *multi-tenant* — one deployment answering for many CQ customers — so it cannot be
modelled that way without either issuing it N keys it would have to route between, or giving it
one key that means "any tenant".

So the invariant here is deliberately NOT "client_id never comes from the request" — the request
is the only thing that knows which tenant a given chat thread belongs to, and no amount of design
changes that. The invariant is **"client_id is never TRUSTED from the request"**:

  * the caller sends a *selector* (`X-CQ-Tenant`: a uuid or a `clients.slug`);
  * `resolve()` intersects that selector against `integration_grants` in the SAME query that
    verifies the secret — an ungranted or inactive selector matches zero rows, which is
    indistinguishable from a bad key: `None` → 401;
  * `Principal.client_id` is then assigned from `row["client_id"]` — the value the DB handed
    back — and never from the header string. That assignment is the security boundary; it is
    marked below and there is exactly one of it.

Fail-closed by construction: `integrations` has no `client_id` column (see db/chat.sql), so there
is no "home" tenant to fall back to. A stripped `X-CQ-Tenant` header cannot resolve to anybody —
it 401s. Onboarding a tenant is one INSERT into `integration_grants`; revoking is one UPDATE, or
deactivating the tenant (the query JOINs `clients ON c.is_active`).

Key format: `cqi_<key_id>.<secret>`. `key_id` is a public lookup handle (indexed, unique);
`secret` is 256 bits of CSPRNG and is stored only as `sha256(secret)`. There is no PBKDF2 here
on purpose — that exists to make *low-entropy human passwords* expensive to brute-force, and a
full-entropy random secret needs no stretching, while credential verify sits on the 25 ms warm
chat path where a 200k-round KDF would dominate the request.

Two live secrets per integration give dual-key rotation: `rotate()` issues a new one and sets a
`expires_at` overlap on the old, so the caller can cut over without a flag day.
"""
import hashlib
import hmac
import json
import logging
import secrets as _secrets

from ..db import pool

log = logging.getLogger("cq")

KEY_PREFIX = "cqi_"

# The complete set of scopes an integration credential may ever hold. Deliberately read-only
# with respect to tenant knowledge: kb:write, kb:delete, scoring:write and admin:* are NEVER
# issued here. The predictable scope creep is "just let curation apply proposals directly",
# which would make this credential exactly as dangerous as the plaintext tenant key it replaces —
# applying a curation proposal must stay a tenant-admin-authenticated review action.
SCOPES = ("chat:turn", "chat:suggest", "chat:answer", "chat:sync")

# Never issuable, asserted at issuance time so a typo in an admin call cannot widen the grant.
FORBIDDEN_SCOPE_PREFIXES = ("kb:write", "kb:delete", "scoring:write", "admin:")

# The nine conditions above collapse into ONE 401 for the caller — that opacity is the security
# property and does not move. But the operator holding the superadmin token is a different
# principal entirely, and "401" alone has cost this project whole afternoons of guessing between
# "wrong key" and "workspace not granted". These are the names that failure gets in the server
# log and in POST /admin/integrations/diagnose; they never reach an integration-authenticated
# response. Order matters: it is the order the conditions are evaluated in, so the FIRST failure
# is the one reported.
REASONS = (
    "bad_key_shape",            # not cqi_<key_id>.<secret>
    "missing_tenant_selector",  # no X-CQ-Tenant — there is no default tenant, by design
    "unknown_key_id",           # no integration_secrets row with that key_id
    "secret_revoked",
    "secret_expired",
    "integration_inactive",
    "tenant_not_found",         # selector matches no clients.id / clients.slug
    "tenant_inactive",
    "no_grant",                 # the key exists and the tenant exists, but not together
    "grant_inactive",           # the grant row was revoked per-tenant
    "secret_mismatch",          # everything structural holds; the sha256 does not match
)

# Not a REASON: what the log says when the diagnosis query itself failed. Naming a refusal must
# never be able to turn a 401 into a 500.
_REASON_UNKNOWN = "unknown"

# A syntactically valid but non-existent key_id must cost the same as a real one, so the sha256
# comparison runs even when the lookup found nothing. This is the value it is compared against.
_DUMMY_HASH = hashlib.sha256(b"cq-credential-miss").hexdigest()

# last_used_at is telemetry, not authorization. Writing it on every request would put an UPDATE
# on the warm path of the highest-volume endpoint in the system, so it is refreshed at most
# once per interval.
_LAST_USED_REFRESH_S = 300


def _split(raw_key: str) -> tuple[str, str] | None:
    """`cqi_<key_id>.<secret>` -> (key_id, secret), or None if it is not that shape."""
    if not raw_key or not raw_key.startswith(KEY_PREFIX):
        return None
    body = raw_key[len(KEY_PREFIX):]
    key_id, sep, secret = body.partition(".")
    if not sep or not key_id or not secret:
        return None
    return key_id, secret


def _sha256(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def _new_secret() -> tuple[str, str, str]:
    """-> (key_id, secret, plaintext). The plaintext is returned to the operator exactly once."""
    key_id = _secrets.token_hex(8)
    secret = _secrets.token_urlsafe(32)
    return key_id, secret, f"{KEY_PREFIX}{key_id}.{secret}"


# --------------------------------------------------------------------------- #
# Verify
# --------------------------------------------------------------------------- #
# ONE query does credential lookup, liveness, tenant-grant intersection and tenant liveness.
# Splitting it would create a window where each part is individually true and the conjunction is
# not, and would also let "valid key, ungranted tenant" be distinguished from "bad key" by
# timing. The tenant selector is matched as TEXT against both id and slug: casting an
# attacker-supplied string to uuid raises asyncpg.DataError, which is a 400 that leaks whether
# the selector was uuid-shaped.
_RESOLVE_SQL = """
    SELECT s.id            AS secret_id,
           s.secret_hash   AS secret_hash,
           s.last_used_at  AS last_used_at,
           i.id            AS integration_id,
           i.name          AS integration_name,
           g.client_id     AS client_id,
           CASE WHEN coalesce(cardinality(g.scopes), 0) = 0
                THEN i.scopes
                ELSE ARRAY(SELECT unnest(i.scopes) INTERSECT SELECT unnest(g.scopes))
           END             AS scopes
      FROM integration_secrets s
      JOIN integrations i        ON i.id = s.integration_id AND i.is_active
      JOIN integration_grants g  ON g.integration_id = i.id AND g.is_active
      JOIN clients c             ON c.id = g.client_id AND c.is_active
     WHERE s.key_id = $1
       AND s.revoked_at IS NULL
       AND (s.expires_at IS NULL OR s.expires_at > now())
       AND (c.id::text = $2 OR c.slug = $2)
     LIMIT 1
"""


async def resolve(raw_key: str, tenant_sel: str):
    """Verify `raw_key` and return an integration Principal for `tenant_sel`, or None.

    None means 401 for every failure mode — bad shape, unknown key_id, wrong secret, revoked,
    expired, ungranted tenant, deactivated integration, deactivated tenant. The caller must not
    be able to tell those apart.
    """
    # Imported here, not at module scope: auth.py imports this module for step 2.5, so a
    # top-level import would be circular.
    from .auth import Principal

    parts = _split(raw_key)
    tenant_sel = (tenant_sel or "").strip()
    if parts is None or not tenant_sel:
        # Mandatory selector. There is no default tenant, by design — see the module docstring.
        # No key_id to log: a malformed key must not be echoed even in part, since the half we
        # would be guessing at may be the secret.
        _log_refusal("-", "bad_key_shape" if parts is None else "missing_tenant_selector",
                     tenant_sel)
        return None
    key_id, secret = parts

    async with pool().acquire() as conn:
        row = await conn.fetchrow(_RESOLVE_SQL, key_id, tenant_sel)
        # Compare unconditionally, including on a miss, so a wrong key_id and a wrong secret
        # take the same time and the endpoint is not a credential-enumeration oracle.
        stored = row["secret_hash"] if row else _DUMMY_HASH
        ok = hmac.compare_digest(_sha256(secret), stored)
        if not row or not ok:
            # The request is already lost, so the widened queries below are free: they run ONLY
            # here, never on the success path, and only AFTER the constant-time compare has
            # already happened. Nothing about what the caller sees changes — same None, same
            # 401, same body — this only writes the operator a sentence they can act on.
            _log_refusal(key_id, await _diagnose(conn, key_id, tenant_sel), tenant_sel)
            return None
        await _touch(conn, row["secret_id"], row["last_used_at"])

    # THE SECURITY BOUNDARY: client_id comes from the row the database returned after the grant
    # join — never from the X-CQ-Tenant header, which was only ever a selector.
    return Principal(
        kind="integration",
        client_id=str(row["client_id"]),
        integration_id=str(row["integration_id"]),
        tenant_sel=tenant_sel,
        scopes=list(row["scopes"] or []),
        role="integration",
        via="integration",
    )


async def _touch(conn, secret_id, last_used_at) -> None:
    """Refresh last_used_at at most once per _LAST_USED_REFRESH_S. Never fatal."""
    try:
        await conn.execute(
            """
            UPDATE integration_secrets
               SET last_used_at = now()
             WHERE id = $1
               AND (last_used_at IS NULL OR last_used_at < now() - ($2 || ' seconds')::interval)
            """,
            secret_id, str(_LAST_USED_REFRESH_S),
        )
    except Exception as exc:  # noqa: BLE001 — telemetry must never fail authentication
        log.warning("integration last_used_at update failed: %s", exc)


# --------------------------------------------------------------------------- #
# Why the 401 happened (operator-facing only)
#
# Everything below runs off the warm path: from resolve()'s failure branch, where the request is
# already refused, and from POST /admin/integrations/diagnose, where the caller is the superadmin.
# It must never be reachable with an integration credential and must never influence the 401.
#
# The queries here are deliberately the SAME predicates as _RESOLVE_SQL, one join at a time, so
# that "which join dropped the row" is answerable. Keep them in step with it: a new condition
# added to _RESOLVE_SQL without a matching step here reappears as an unexplained secret_mismatch.
# --------------------------------------------------------------------------- #
def _log_refusal(key_id: str, reason: str, tenant_sel: str) -> None:
    """One line per refused request. The secret half and its hash are never arguments here.

    The tenant selector is operator-supplied configuration (a uuid or a slug), not a credential,
    so it is safe to log — truncated because it arrives from the network.
    """
    log.warning("[cq] integration auth refused | key_id=%s reason=%s tenant_sel=%s",
                key_id or "-", reason, (tenant_sel or "-")[:64] or "-")


# One secret per key_id (unique index); the join is LEFT so a secret orphaned by a hand-run
# DELETE still reports the key as known rather than vanishing into unknown_key_id.
_DIAG_SECRET_SQL = """
    SELECT s.secret_hash,
           s.revoked_at IS NOT NULL                                   AS is_revoked,
           s.expires_at IS NOT NULL AND s.expires_at <= now()         AS is_expired,
           i.id       AS integration_id,
           i.name     AS integration_name,
           i.is_active AS integration_active,
           i.scopes   AS integration_scopes
      FROM integration_secrets s
      LEFT JOIN integrations i ON i.id = s.integration_id
     WHERE s.key_id = $1
     LIMIT 1
"""

# Matched as TEXT against both columns for the same reason as _RESOLVE_SQL: casting an arbitrary
# string to uuid raises asyncpg.DataError, and here that would turn a diagnosis into a 400.
_DIAG_TENANT_SQL = """
    SELECT id, slug, name, is_active FROM clients
     WHERE id::text = $1 OR slug = $1 LIMIT 1
"""

_DIAG_GRANT_SQL = """
    SELECT is_active, scopes FROM integration_grants
     WHERE integration_id = $1 AND client_id = $2 LIMIT 1
"""

# Only live grants: this answers "which workspaces may this key act for", which is the single
# question an operator staring at a 401 actually has.
_DIAG_GRANTS_SQL = """
    SELECT c.id AS client_id, c.slug, c.name, c.is_active
      FROM integration_grants g
      JOIN clients c ON c.id = g.client_id
     WHERE g.integration_id = $1 AND g.is_active
     ORDER BY c.slug
"""


async def _facts(conn, key_id: str, tenant_sel: str) -> dict:
    """Read every row _RESOLVE_SQL's joins would have needed, without joining them."""
    sec = await conn.fetchrow(_DIAG_SECRET_SQL, key_id)
    ten = await conn.fetchrow(_DIAG_TENANT_SQL, tenant_sel)
    grant = granted = None
    if sec and sec["integration_id"]:
        granted = await conn.fetch(_DIAG_GRANTS_SQL, sec["integration_id"])
        if ten:
            grant = await conn.fetchrow(_DIAG_GRANT_SQL, sec["integration_id"], ten["id"])
    return {
        "secret": dict(sec) if sec else None,
        "tenant": dict(ten) if ten else None,
        "grant": dict(grant) if grant else None,
        "granted_tenants": [dict(r) for r in (granted or [])],
    }


def _tenant_view(row: dict | None) -> dict | None:
    if not row:
        return None
    return {"client_id": str(row.get("client_id") or row.get("id")),
            "slug": row.get("slug"), "name": row.get("name"),
            "is_active": bool(row.get("is_active"))}


def _report(key_id: str, tenant_sel: str, secret_ok: bool | None, facts: dict) -> dict:
    """Pure: rows in, operator report out. Takes the ALREADY-COMPARED verdict, not the secret,
    so no code path here can put a secret (or its hash) into something that gets serialized."""
    sec = facts.get("secret") or None
    ten = facts.get("tenant") or None
    grant = facts.get("grant") or None

    checks: list[dict] = []

    def add(step: str, ok: bool | None, detail: str) -> None:
        checks.append({"step": step, "ok": ok, "detail": detail})

    add("key_id_known", bool(sec),
        f"key_id {key_id} found" if sec else f"no credential with key_id {key_id} — "
        "the key was never issued here, or this is a different CQ deployment")

    if sec:
        live = not sec.get("is_revoked") and not sec.get("is_expired")
        add("secret_live", live,
            "revoked" if sec.get("is_revoked") else
            "expired (a rotation overlap has run out)" if sec.get("is_expired") else "live")
        add("integration_active", bool(sec.get("integration_active")),
            "active" if sec.get("integration_active") else "the integration was deactivated")
    else:
        add("secret_live", None, "not evaluated — no such key_id")
        add("integration_active", None, "not evaluated — no such key_id")

    add("tenant_found", bool(ten),
        f"selector matches {ten['slug']}" if ten else
        f"no workspace has id or slug {tenant_sel[:64]!r}")
    add("tenant_active", bool(ten and ten.get("is_active")) if ten else None,
        ("active" if ten and ten.get("is_active") else "the workspace is deactivated")
        if ten else "not evaluated — no such workspace")

    if sec and ten:
        add("grant_exists", bool(grant),
            "granted" if grant else
            "this credential has no grant for that workspace — the fix is one grant row, "
            "not a new key")
        add("grant_active", bool(grant and grant.get("is_active")) if grant else None,
            ("active" if grant and grant.get("is_active") else "the grant was revoked")
            if grant else "not evaluated — no grant row")
    else:
        add("grant_exists", None, "not evaluated — key_id or workspace unknown")
        add("grant_active", None, "not evaluated — key_id or workspace unknown")

    add("secret_matches", secret_ok,
        "not evaluated — only a key_id was supplied, no secret to check" if secret_ok is None
        else "the secret matches the stored hash" if secret_ok
        else "the secret does not match the stored hash — truncated paste, or a rotated key")

    # Effective scopes exactly as _RESOLVE_SQL computes them: the grant narrows the integration,
    # and an empty grant scope array means "everything the integration holds".
    held = list((sec or {}).get("integration_scopes") or [])
    gs = list((grant or {}).get("scopes") or [])
    effective = [s for s in held if s in gs] if gs else held
    add("scopes", bool(effective) if (sec and grant) else None,
        ", ".join(effective) if effective else
        "not evaluated" if not (sec and grant) else "no scopes in the intersection")

    failed = {c["step"]: c for c in checks if c["ok"] is False}
    if "key_id_known" in failed:
        verdict = "unknown_key_id"
    elif "secret_live" in failed:
        verdict = "secret_revoked" if sec.get("is_revoked") else "secret_expired"
    elif "integration_active" in failed:
        verdict = "integration_inactive"
    elif "tenant_found" in failed:
        verdict = "tenant_not_found"
    elif "tenant_active" in failed:
        verdict = "tenant_inactive"
    elif "grant_exists" in failed:
        verdict = "no_grant"
    elif "grant_active" in failed:
        verdict = "grant_inactive"
    elif secret_ok is False:
        verdict = "secret_mismatch"
    else:
        # Everything a request would have needed holds. With no secret supplied that is as far
        # as the operator's check can go, and saying so is more honest than "ok".
        verdict = "ok" if secret_ok else "ok_structurally"

    return {
        "key_id": key_id,
        "tenant": tenant_sel,
        "secret_checked": secret_ok is not None,
        "verdict": verdict,
        "checks": checks,
        "integration": None if not sec else {
            "id": str(sec.get("integration_id") or ""),
            "name": sec.get("integration_name"),
            "is_active": bool(sec.get("integration_active")),
            "scopes": held,
        },
        "tenant_match": _tenant_view(ten),
        "granted_tenants": [_tenant_view(t) for t in facts.get("granted_tenants") or []],
        "effective_scopes": effective,
    }


async def _diagnose(conn, key_id: str, tenant_sel: str) -> str:
    """The reason a resolve() failed, as one REASONS literal. Never raises."""
    try:
        facts = await _facts(conn, key_id, tenant_sel)
    except Exception as exc:  # noqa: BLE001 — a diagnosis must never become the failure
        log.warning("integration diagnosis failed: %s", exc)
        return _REASON_UNKNOWN
    # Reaching resolve()'s failure branch with everything structural intact means the compare
    # is what failed, so the verdict is computed with secret_ok=False rather than "unknown".
    return _report(key_id, tenant_sel, False, facts)["verdict"]


def split_diagnosed_key(raw: str) -> tuple[str, str | None]:
    """Operator input -> (key_id, secret or None). Accepts a bare key_id, `cqi_<key_id>`, or a
    full `cqi_<key_id>.<secret>` — an operator pastes whichever of those they have to hand.

    Split at the FIRST dot immediately: the secret half leaves this function only as the input to
    a hash comparison, and everything downstream is built from `key_id` alone.
    """
    raw = (raw or "").strip()
    if raw.startswith(KEY_PREFIX):
        raw = raw[len(KEY_PREFIX):]
    key_id, sep, secret = raw.partition(".")
    return key_id.strip(), (secret if sep and secret else None)


async def diagnose(raw: str, tenant_sel: str) -> dict:
    """Superadmin-only: why does this key + workspace pair 401? See routers/admin.py."""
    key_id, secret = split_diagnosed_key(raw)
    tenant_sel = (tenant_sel or "").strip()
    if not key_id or not tenant_sel:
        reason = "bad_key_shape" if not key_id else "missing_tenant_selector"
        return {"key_id": key_id, "tenant": tenant_sel, "secret_checked": False,
                "verdict": reason, "checks": [{"step": "key_id_known", "ok": False,
                                               "detail": "a key_id and a workspace selector "
                                                         "are both required"}],
                "integration": None, "tenant_match": None, "granted_tenants": [],
                "effective_scopes": []}

    async with pool().acquire() as conn:
        facts = await _facts(conn, key_id, tenant_sel)

    secret_ok = None
    if secret is not None:
        stored = (facts["secret"] or {}).get("secret_hash") or _DUMMY_HASH
        secret_ok = hmac.compare_digest(_sha256(secret), stored) and facts["secret"] is not None
    report = _report(key_id, tenant_sel, secret_ok, facts)
    log.info("[cq] integration diagnosed | key_id=%s tenant_sel=%s verdict=%s",
             key_id, tenant_sel[:64], report["verdict"])
    return report


def has_scope(principal, scope: str) -> bool:
    """True if this principal carries `scope`. Non-integration principals have no scopes."""
    return scope in (getattr(principal, "scopes", None) or [])


# --------------------------------------------------------------------------- #
# Issuance (superadmin only — see routers/admin.py)
# --------------------------------------------------------------------------- #
def validate_scopes(scopes: list[str]) -> list[str]:
    """Normalize + reject anything outside SCOPES. Raises ValueError."""
    out = []
    for s in scopes or []:
        s = (s or "").strip()
        if not s:
            continue
        if s.startswith(FORBIDDEN_SCOPE_PREFIXES) or s not in SCOPES:
            raise ValueError(f"Scope not issuable to an integration: {s[:40]}")
        if s not in out:
            out.append(s)
    if not out:
        raise ValueError("At least one scope is required.")
    return out


async def issue(name: str, scopes: list[str], grants: list[str]) -> tuple[str, str]:
    """Create an integration + its first secret + its grant rows. -> (key_id, plaintext ONCE).

    `grants` are tenant selectors (uuid or slug), one or more. The P1 pilot capped this at one
    in the router; since 2026-09-08 one credential legitimately carries many (see
    routers/admin.py::create_integration), and `add_grant` / `remove_grant` below change the
    set after issuance without minting a new key.
    """
    scopes = validate_scopes(scopes)
    sel = [g.strip() for g in (grants or []) if (g or "").strip()]
    if not sel:
        raise ValueError("At least one tenant grant is required.")

    key_id, secret, plaintext = _new_secret()
    async with pool().acquire() as conn:
        async with conn.transaction():
            # Resolve every selector FIRST: an unknown tenant must abort the whole issuance
            # rather than silently produce a key with fewer grants than the operator asked for.
            client_ids = []
            for s in sel:
                cid = await conn.fetchval(
                    "SELECT id FROM clients WHERE (id::text = $1 OR slug = $1) AND is_active", s)
                if not cid:
                    raise ValueError(f"Unknown or inactive tenant: {s[:40]}")
                client_ids.append(cid)

            integration_id = await conn.fetchval(
                """INSERT INTO integrations (name, kind, scopes, is_active)
                   VALUES ($1, 'chat', $2, true) RETURNING id""",
                name.strip() or "chat integration", scopes,
            )
            for cid in client_ids:
                await conn.execute(
                    """INSERT INTO integration_grants (integration_id, client_id, scopes, created_by)
                       VALUES ($1, $2, $3, 'superadmin')
                       ON CONFLICT (integration_id, client_id) DO UPDATE SET scopes = EXCLUDED.scopes""",
                    integration_id, cid, scopes,
                )
            await conn.execute(
                """INSERT INTO integration_secrets (integration_id, key_id, secret_hash, label)
                   VALUES ($1, $2, $3, 'initial')""",
                integration_id, key_id, _sha256(secret),
            )
    log.info("issued integration credential %s for %s tenant(s)", key_id, len(client_ids))
    return key_id, plaintext


async def rotate(integration_id: str, overlap_days: int = 7) -> str:
    """Issue a second live secret and expire the current ones after `overlap_days`.

    Both keys verify during the overlap (see the expires_at clause in _RESOLVE_SQL), so the
    caller can redeploy on its own schedule instead of at the instant of rotation.
    """
    key_id, secret, plaintext = _new_secret()
    async with pool().acquire() as conn:
        async with conn.transaction():
            live = await conn.fetchval(
                "SELECT 1 FROM integrations WHERE id = $1 AND is_active", integration_id)
            if not live:
                raise ValueError("Unknown or inactive integration")
            await conn.execute(
                """UPDATE integration_secrets
                      SET expires_at = LEAST(coalesce(expires_at, 'infinity'::timestamptz),
                                             now() + ($2 || ' days')::interval)
                    WHERE integration_id = $1 AND revoked_at IS NULL""",
                integration_id, str(max(0, int(overlap_days))),
            )
            await conn.execute(
                """INSERT INTO integration_secrets (integration_id, key_id, secret_hash, label)
                   VALUES ($1, $2, $3, 'rotated')""",
                integration_id, key_id, _sha256(secret),
            )
    log.info("rotated integration %s -> key_id %s (overlap %sd)", integration_id, key_id, overlap_days)
    return plaintext


async def deactivate(integration_id: str) -> bool:
    """Soft-delete: the integration stops verifying, but its rows stay for audit."""
    async with pool().acquire() as conn:
        async with conn.transaction():
            updated = await conn.execute(
                "UPDATE integrations SET is_active = false, updated_at = now() WHERE id = $1",
                integration_id,
            )
            await conn.execute(
                "UPDATE integration_secrets SET revoked_at = now() "
                "WHERE integration_id = $1 AND revoked_at IS NULL",
                integration_id,
            )
    return updated.endswith(" 1")


class UnknownIntegration(ValueError):
    """The integration in the request PATH does not exist — a 404, where every other ValueError
    raised here describes the request BODY and is a 400. Kept a ValueError so callers that only
    care about "the operator asked for something impossible" still catch it in one clause."""


async def add_grant(integration_id: str, tenant_sel: str, scopes: list[str] | None = None) -> dict:
    """Let an existing integration act for one more tenant. -> the grant as list_integrations shows it.

    Onboarding a tenant onto the chat service is this one row — no new key, no redeploy of the
    chat site. The grant's scopes may only NARROW the integration's: a request for a scope the
    integration itself does not hold is dropped rather than honoured, because _RESOLVE_SQL
    intersects the two anyway and a stored grant that promises more than the key can deliver
    would mislead whoever reads the operator view.

    Re-granting a tenant that already has a row (active or revoked) re-activates it in place via
    ON CONFLICT, so remove_grant → add_grant is a clean round trip and the pair is idempotent.
    Raises UnknownIntegration for the integration, ValueError for the tenant or the scopes.
    """
    sel = (tenant_sel or "").strip()
    if not sel:
        raise ValueError("A tenant selector (uuid or slug) is required.")
    async with pool().acquire() as conn:
        async with conn.transaction():
            held = await conn.fetchval(
                "SELECT scopes FROM integrations WHERE id = $1", integration_id)
            if held is None:
                raise UnknownIntegration("Unknown integration")
            held = list(held or [])
            if scopes is None:
                effective = held
            else:
                wanted = validate_scopes(scopes)
                effective = [s for s in wanted if s in held]
                if not effective:
                    raise ValueError("None of the requested scopes are held by this integration.")
            tenant = await conn.fetchrow(
                "SELECT id, slug, name FROM clients WHERE (id::text = $1 OR slug = $1) AND is_active",
                sel)
            if not tenant:
                raise ValueError(f"Unknown or inactive tenant: {sel[:40]}")
            row = await conn.fetchrow(
                """INSERT INTO integration_grants (integration_id, client_id, scopes, created_by)
                   VALUES ($1, $2, $3, 'superadmin')
                   ON CONFLICT (integration_id, client_id)
                   DO UPDATE SET scopes = EXCLUDED.scopes, is_active = true
                   RETURNING client_id, scopes, is_active""",
                integration_id, tenant["id"], effective,
            )
    log.info("granted integration %s -> tenant %s (%s)", integration_id, tenant["slug"], effective)
    return {"client_id": str(row["client_id"]), "slug": tenant["slug"], "name": tenant["name"],
            "scopes": list(row["scopes"] or []), "is_active": bool(row["is_active"])}


async def remove_grant(integration_id: str, client_id: str) -> bool:
    """Stop an integration acting for one tenant. False when no such grant row exists.

    Deactivate, never delete — the row is the audit trail of who was ever reachable with this
    key, the same reasoning as deactivate(). _RESOLVE_SQL joins on g.is_active, so this takes
    effect on the very next request; there is no cache to clear. The other grants, and the key
    itself, keep working — that is the whole point of revoking per tenant instead of rotating.
    """
    async with pool().acquire() as conn:
        updated = await conn.execute(
            """UPDATE integration_grants SET is_active = false
                WHERE integration_id = $1 AND client_id = $2""",
            integration_id, client_id,
        )
    return updated.endswith(" 1")


async def list_integrations() -> list[dict]:
    """Operator view. Never returns a secret — only its public key_id and lifecycle stamps."""
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT i.id, i.name, i.kind, i.scopes, i.is_active, i.created_at,
                   coalesce((SELECT json_agg(json_build_object(
                                'client_id', g.client_id, 'slug', c.slug, 'name', c.name,
                                'scopes', g.scopes, 'is_active', g.is_active)
                             ORDER BY c.slug)
                               FROM integration_grants g
                               JOIN clients c ON c.id = g.client_id
                              WHERE g.integration_id = i.id), '[]'::json) AS grants,
                   coalesce((SELECT json_agg(json_build_object(
                                'key_id', s.key_id, 'label', s.label,
                                'created_at', s.created_at, 'expires_at', s.expires_at,
                                'revoked_at', s.revoked_at, 'last_used_at', s.last_used_at)
                             ORDER BY s.created_at DESC)
                               FROM integration_secrets s
                              WHERE s.integration_id = i.id), '[]'::json) AS secrets
              FROM integrations i
             ORDER BY i.created_at DESC
            """
        )
    out = []
    for r in rows:
        d = dict(r)
        d["id"] = str(r["id"])
        d["created_at"] = r["created_at"].isoformat() if r["created_at"] else None
        for k in ("grants", "secrets"):
            if isinstance(d.get(k), str):
                d[k] = json.loads(d[k])
        out.append(d)
    return out


async def integration_for_key_id(key_id: str) -> str | None:
    async with pool().acquire() as conn:
        v = await conn.fetchval(
            "SELECT integration_id FROM integration_secrets WHERE key_id = $1", key_id)
    return str(v) if v else None
