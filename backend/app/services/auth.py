"""Authentication & principal resolution.

Five principal kinds:
  * superadmin     — X-Admin-Token == settings.admin_token (or a superadmin login token)
  * integration    — X-CQ-Key + X-CQ-Tenant, verified against the grant table (chat_credentials)
  * tenant (user)  — Authorization: Bearer <signed token> from POST /auth/login
  * tenant (apikey)— X-API-Key matches clients.api_key
  * anonymous      — no credentials; identified by client IP for rate limiting

Two rules govern how those combine, and both exist because the alternative is a *silent* tenant
switch rather than an error:

  * **Exclusivity.** Presenting more than one credential header is a 400, not a
    highest-privilege-wins. A caller holding two credentials for two different tenants must never
    have the resolution order decide which one takes effect.
  * **No silent downgrade.** An invalid or expired Bearer is a hard 401. It used to fall through
    to the X-API-Key branch, so a caller with a stale default `Authorization` header resolved to
    one tenant and *flipped* to another the moment the token expired — same request, different
    tenant, decided by wall clock.

The ordinary anonymous path still never raises: no credentials at all is a Principal, not an
error. Only *presenting* a broken or ambiguous credential is.

Passwords: stdlib PBKDF2. Tokens: stdlib HMAC-signed JSON (no external deps).
"""
import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass, field

from fastapi import Header, HTTPException, Request

from ..config import settings
from ..db import pool

# ---- password hashing ------------------------------------------------------
_PBKDF2_ROUNDS = 200_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${_PBKDF2_ROUNDS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, rounds, salt_hex, hash_hex = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(rounds))
        return hmac.compare_digest(dk.hex(), hash_hex)
    except (ValueError, AttributeError):
        return False


# ---- signed tokens ---------------------------------------------------------
def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make_token(payload: dict, ttl_hours: int | None = None) -> str:
    body = dict(payload)
    body["exp"] = _now() + int((ttl_hours or settings.token_ttl_hours) * 3600)
    raw = _b64(json.dumps(body, separators=(",", ":")).encode())
    sig = _b64(hmac.new(settings.jwt_secret.encode(), raw.encode(), hashlib.sha256).digest())
    return f"{raw}.{sig}"


def verify_token(token: str) -> dict | None:
    try:
        raw, sig = token.split(".")
        expected = _b64(hmac.new(settings.jwt_secret.encode(), raw.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(_unb64(raw))
        if payload.get("exp", 0) < _now():
            return None
        return payload
    except (ValueError, json.JSONDecodeError):
        return None


def _now() -> int:
    # time.time is fine here (tokens are not part of a resumable workflow).
    import time
    return int(time.time())


# ---- principal -------------------------------------------------------------
@dataclass
class Principal:
    kind: str                    # superadmin | integration | tenant | anonymous
    client_id: str | None = None
    user_id: str | None = None
    role: str | None = None
    via: str | None = None       # token | apikey | admin | integration | none
    anon_key: str | None = None
    integration_id: str | None = None
    # The selector the caller used (uuid or slug) — echoed back so assert_expected_tenant can
    # accept the vocabulary the caller actually speaks without a per-write lookup. NEVER used
    # for authorization: client_id above is the only thing any query is scoped by.
    tenant_sel: str | None = None
    # Effective scopes, only ever populated for kind == "integration". default_factory, not
    # `= []`: a mutable default is shared by every instance of the dataclass.
    scopes: list[str] = field(default_factory=list)

    @property
    def is_tenant(self) -> bool:
        # Deliberately FALSE for an integration principal even though it carries a client_id.
        # Every existing tenant route gates on this, and a chat credential must not become a
        # skeleton key for /kb, /analyze or /scoring just by being added to the resolver.
        return self.kind == "tenant" and self.client_id is not None

    @property
    def is_superadmin(self) -> bool:
        return self.kind == "superadmin"

    @property
    def is_integration(self) -> bool:
        return self.kind == "integration" and self.client_id is not None

    def has_scope(self, scope: str) -> bool:
        return scope in (self.scopes or [])


async def _client_by_api_key(api_key: str):
    async with pool().acquire() as conn:
        return await conn.fetchrow(
            "SELECT id, name FROM clients WHERE api_key = $1 AND is_active = true", api_key
        )


async def resolve_principal(
    request: Request,
    authorization: str = Header(default=""),
    x_api_key: str = Header(default=""),
    x_admin_token: str = Header(default=""),
    x_cq_key: str = Header(default=""),
    x_cq_tenant: str = Header(default=""),
) -> Principal:
    # 0. Credential exclusivity. Ambiguity is rejected before anything is verified, so the
    # resolution ORDER below can never be the thing that picks a tenant.
    presented = [n for n, v in (("X-CQ-Key", x_cq_key), ("X-Admin-Token", x_admin_token),
                                ("Authorization", authorization), ("X-API-Key", x_api_key))
                 if (v or "").strip()]
    if len(presented) > 1:
        raise HTTPException(status_code=400,
                            detail=f"Present exactly one credential; got {', '.join(presented)}.")

    # 1. Superadmin via admin token
    if x_admin_token:
        if hmac.compare_digest(x_admin_token, settings.admin_token):
            return Principal(kind="superadmin", role="superadmin", via="admin")
        raise HTTPException(status_code=401, detail="Invalid admin token")

    # 2.5 Integration (the chat site). Placed AFTER the admin check and BEFORE the Bearer branch
    # so an integration key can never be shadowed by another branch's interpretation of it, and
    # so a failure here is terminal rather than a fall-through to a different tenant.
    if x_cq_key:
        from . import chat_credentials      # imported late: chat_credentials imports Principal
        principal = await chat_credentials.resolve(x_cq_key, x_cq_tenant)
        if principal is None:
            # One message for every failure mode — bad key, revoked key, ungranted tenant,
            # inactive tenant, missing selector. The caller must not be able to tell them apart.
            raise HTTPException(status_code=401,
                                detail="Invalid integration credential or tenant selector.")
        return principal

    # 2. Tenant user via bearer token
    if authorization:
        payload = verify_token(authorization[7:].strip()) \
            if authorization.lower().startswith("bearer ") else None
        if not payload:
            # HARD 401 — see the "no silent downgrade" note in the module docstring. This must
            # not fall through to the X-API-Key branch.
            raise HTTPException(status_code=401, detail="Invalid or expired session token")
        if payload.get("role") == "superadmin":
            return Principal(kind="superadmin", role="superadmin", via="token")
        return Principal(kind="tenant", client_id=payload.get("client_id"),
                         user_id=payload.get("user_id"), role=payload.get("role", "member"),
                         via="token")

    # 3. Tenant via API key
    if x_api_key:
        row = await _client_by_api_key(x_api_key)
        if not row:
            # Same rule as the Bearer branch: a presented-but-invalid credential is an error,
            # never a quiet demotion to the anonymous quota bucket.
            raise HTTPException(status_code=401, detail="Invalid API key")
        return Principal(kind="tenant", client_id=str(row["id"]), role="apikey", via="apikey")

    # 4. Anonymous — keyed by client IP.
    # The IP is a quota key, so a caller must not be able to choose it. Both nginx configs set
    # X-Real-IP from $remote_addr (the real peer), so that is the trusted source. They also set
    # X-Forwarded-For with $proxy_add_x_forwarded_for, which *appends* our peer to whatever the
    # client sent — so the FIRST element is attacker-supplied (reading it let anyone mint a fresh
    # quota bucket per request) while the LAST element is the one our own proxy added. Hence:
    # X-Real-IP, then the last XFF element, then the socket peer.
    # App-side fix only: no nginx change and no container recreate needed to close this.
    return Principal(kind="anonymous", via="none", anon_key=client_ip(request))


def client_ip(request: Request) -> str:
    """The caller's real IP, by the same rule the anonymous quota key uses.

    Shared rather than re-derived per call site: this is a trust decision (X-Real-IP, then the
    LAST X-Forwarded-For element, then the socket peer — never the first XFF element, which the
    client controls), and a second hand-written copy is how one of them ends up reading the
    spoofable end of the header.
    """
    xff = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
    return (request.headers.get("x-real-ip", "").strip()
            or (xff[-1] if xff else "")
            or (request.client.host if request.client else "unknown"))


def assert_expected_tenant(principal: Principal, expected: str | None) -> None:
    """Fail the request unless the caller's stated tenant matches the credential-derived one.

    This is the only defence against the *chat site's own* mapping bugs. Everything else here
    protects CQ from a malicious caller; this protects a tenant from an honest caller that
    resolved the wrong thread. The chat site declares which tenant it BELIEVES it is writing for
    (`X-CQ-Expect-Tenant`); we compare that against the client_id the grant join produced and
    403 on disagreement, so a mis-routed conversation is a loud failure instead of a silent
    cross-tenant write that is only discovered by reading somebody else's customers.

    Mandatory on writes. `expected` may be the tenant uuid or the slug the caller selected with —
    both are compared, because the caller is not required to know CQ's internal ids.
    """
    if expected is None or not str(expected).strip():
        raise HTTPException(status_code=400,
                            detail="X-CQ-Expect-Tenant is required on write requests.")
    expected = str(expected).strip()
    if not principal.client_id:
        raise HTTPException(status_code=403, detail="Tenant expectation mismatch.")
    # Accepted in either vocabulary: the uuid the grant join returned, or the selector the caller
    # authenticated with (which the same join already validated against integration_grants — it
    # is a rephrasing of the SAME verified row, not a second, weaker credential).
    if expected == str(principal.client_id) or (principal.tenant_sel
                                                and expected == principal.tenant_sel):
        return
    raise HTTPException(status_code=403, detail="Tenant expectation mismatch.")
