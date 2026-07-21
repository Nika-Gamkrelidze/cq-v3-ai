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
        return None
    key_id, secret = parts

    async with pool().acquire() as conn:
        row = await conn.fetchrow(_RESOLVE_SQL, key_id, tenant_sel)
        # Compare unconditionally, including on a miss, so a wrong key_id and a wrong secret
        # take the same time and the endpoint is not a credential-enumeration oracle.
        stored = row["secret_hash"] if row else _DUMMY_HASH
        ok = hmac.compare_digest(_sha256(secret), stored)
        if not row or not ok:
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

    `grants` are tenant selectors (uuid or slug). P1 callers pass exactly one — the single-grant
    rule is enforced in the router, not here, so the data model stays capable of multi-tenant
    credentials without a code change later.
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
