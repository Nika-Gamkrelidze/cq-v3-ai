"""Authentication & principal resolution.

Six principal kinds:
  * superadmin     — X-Admin-Token == settings.admin_token (or a superadmin login token)
  * integration    — X-CQ-Key + X-CQ-Tenant, verified against the grant table (chat_credentials)
  * tenant (user)  — Authorization: Bearer <signed token> from POST /auth/login
  * tenant (apikey)— X-API-Key matches clients.api_key
  * user           — a self-service registered account (app_users): Bearer token whose payload
                     carries {"kind": "user"}. No client_id, ever — a registered user owns
                     their own rows (user_id) and never sees a tenant's knowledge base.
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


def make_user_token(user_id) -> str:
    """The ONE place a registered user's token is minted, so the payload discriminator the
    resolver keys on (`kind: user`) cannot drift between /auth/register and /auth/login.
    Tenant payloads deliberately carry no `kind`: their absence is what keeps every token
    issued before this field existed valid and tenant-shaped."""
    return make_token({"kind": "user", "user_id": str(user_id)})


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
    kind: str                    # superadmin | integration | tenant | user | anonymous
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
    def is_user(self) -> bool:
        # A registered account. `client_id` is None by construction, so a tenant route that
        # gates on `is_tenant` stays closed to it and a user-scoped query keys on `user_id`.
        return self.kind == "user" and self.user_id is not None

    @property
    def is_superadmin(self) -> bool:
        return self.kind == "superadmin"

    @property
    def is_operator(self) -> bool:
        """A superadmin acting on one workspace (see `X-Act-As-Tenant`).

        `is_tenant` is true for these, deliberately — that is the whole point: the operator
        drives the customer's own routes. This flag is what lets the code still tell the two
        apart where it matters: audit strings, and the few actions that belong to the
        account holder alone (resetting a rubric behind their own password).
        """
        return self.kind == "tenant" and self.role == "superadmin"

    @property
    def may_configure_workspace(self) -> bool:
        """Authority to change this workspace's settings — rubric, bands, bot, sentiment.

        One predicate instead of a `role not in ("owner", "apikey")` tuple repeated across
        six routers: a role added in one place and forgotten in another is how a permission
        gap gets shipped.
        """
        return self.role in ("owner", "apikey", "superadmin")

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


async def _superadmin(via: str, act_as: str) -> Principal:
    """The operator principal — scoped to one workspace when they asked for it.

    Without `X-Act-As-Tenant` this is the plain superadmin every `/admin/...` route expects.
    With it, the caller gets a TENANT-shaped principal for that one workspace, so the
    ordinary tenant routes serve the operator console exactly as they serve the customer.

    That is a real grant, so it is narrow. Only an already-verified superadmin credential
    reaches this function; the workspace must exist and be active; and `role="superadmin"`
    is carried through rather than faked to "owner", which keeps the audit trail honest —
    `kb_events` and friends derive their actor from `user_id or role`, so an operator's
    edits record as `tenant:superadmin` and never as one of the customer's own people.
    """
    if not act_as:
        return Principal(kind="superadmin", role="superadmin", via=via)
    # Matched as TEXT against both id and slug: casting a non-uuid selector to uuid would
    # raise instead of simply not matching (same shape as the integration selector).
    async with pool().acquire() as conn:
        client_id = await conn.fetchval(
            "SELECT id FROM clients WHERE (id::text = $1 OR slug = $1) AND is_active", act_as)
    if not client_id:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return Principal(kind="tenant", client_id=str(client_id), role="superadmin",
                     via=via, tenant_sel=act_as)


async def resolve_principal(
    request: Request,
    authorization: str = Header(default=""),
    x_api_key: str = Header(default=""),
    x_admin_token: str = Header(default=""),
    x_cq_key: str = Header(default=""),
    x_cq_tenant: str = Header(default=""),
    x_act_as_tenant: str = Header(default=""),
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
            return await _superadmin("admin", x_act_as_tenant.strip())
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
            return await _superadmin("token", x_act_as_tenant.strip())
        if payload.get("kind") == "user":
            # A registered-user token never carries a client_id, and must not be allowed to
            # become a tenant principal by falling through to the branch below. A user token
            # without its user_id is malformed (only make_user_token mints them) — hard 401,
            # same rule as an expired one.
            if not payload.get("user_id"):
                raise HTTPException(status_code=401, detail="Invalid or expired session token")
            return Principal(kind="user", user_id=str(payload["user_id"]), role="user",
                             via="token")
        # A PURPOSE-SCOPED token is not a session. `make_token` signs every kind of token
        # with one secret, and a chat stream ticket carries `{"scope": "chat_stream",
        # "client_id": ...}` — so without this guard it fell through here and became a full
        # tenant principal for that workspace. Those tickets are deliberately put in a URL
        # query string (EventSource cannot set headers), which means they reach nginx access
        # logs, the browser history of every visitor to a customer's public chat widget, and
        # the Referer of any subresource. Verified: such a ticket returned the tenant's
        # entire knowledge base from `GET /kb/documents`.
        #
        # Any future purpose-built token inherits the refusal simply by carrying a `scope`.
        if payload.get("scope"):
            raise HTTPException(status_code=401,
                                detail="This token is not valid for API access")
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
