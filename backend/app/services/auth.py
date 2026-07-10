"""Authentication & principal resolution.

Four principal kinds:
  * superadmin     — X-Admin-Token == settings.admin_token (or a superadmin login token)
  * tenant (user)  — Authorization: Bearer <signed token> from POST /auth/login
  * tenant (apikey)— X-API-Key matches clients.api_key
  * anonymous      — no credentials; identified by client IP for rate limiting

Passwords: stdlib PBKDF2. Tokens: stdlib HMAC-signed JSON (no external deps).
"""
import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass

from fastapi import Header, Request

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
    kind: str                    # superadmin | tenant | anonymous
    client_id: str | None = None
    user_id: str | None = None
    role: str | None = None
    via: str | None = None       # token | apikey | admin | none
    anon_key: str | None = None

    @property
    def is_tenant(self) -> bool:
        return self.kind == "tenant" and self.client_id is not None

    @property
    def is_superadmin(self) -> bool:
        return self.kind == "superadmin"


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
) -> Principal:
    # 1. Superadmin via admin token
    if x_admin_token and hmac.compare_digest(x_admin_token, settings.admin_token):
        return Principal(kind="superadmin", role="superadmin", via="admin")

    # 2. Tenant user via bearer token
    if authorization.lower().startswith("bearer "):
        payload = verify_token(authorization[7:].strip())
        if payload:
            if payload.get("role") == "superadmin":
                return Principal(kind="superadmin", role="superadmin", via="token")
            return Principal(kind="tenant", client_id=payload.get("client_id"),
                             user_id=payload.get("user_id"), role=payload.get("role", "member"),
                             via="token")

    # 3. Tenant via API key
    if x_api_key:
        row = await _client_by_api_key(x_api_key)
        if row:
            return Principal(kind="tenant", client_id=str(row["id"]), role="apikey", via="apikey")

    # 4. Anonymous — keyed by client IP
    ip = (request.headers.get("x-forwarded-for", "").split(",")[0].strip()
          or (request.client.host if request.client else "unknown"))
    return Principal(kind="anonymous", via="none", anon_key=ip)
