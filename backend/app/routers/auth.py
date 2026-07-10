"""Unified authentication endpoint (tenant users + superadmin)."""
import secrets

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..config import settings
from ..db import pool
from ..services import auth
from ..services.auth import Principal, resolve_principal

router = APIRouter(tags=["auth"])


class LoginBody(BaseModel):
    username: str
    password: str


@router.post("/auth/login")
async def login(body: LoginBody):
    """One login entry point. Superadmin credentials return scope 'admin'; valid tenant
    users return scope 'tenant'. Everything else fails with the same generic error so the
    response never reveals whether an admin (or any specific account) exists."""
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
    if not row or not row["is_active"] or not row["client_active"] \
            or not auth.verify_password(body.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    token = auth.make_token({
        "client_id": str(row["client_id"]), "user_id": str(row["id"]), "role": row["role"],
    })
    return {
        "scope": "tenant", "token": token, "role": row["role"],
        "client": {"id": str(row["client_id"]), "name": row["client_name"], "slug": row["client_slug"]},
    }


@router.get("/auth/me")
async def me(principal: Principal = Depends(resolve_principal)):
    info = {"kind": principal.kind, "role": principal.role, "via": principal.via,
            "client_id": principal.client_id}
    if principal.client_id:
        async with pool().acquire() as conn:
            c = await conn.fetchrow("SELECT name, slug FROM clients WHERE id = $1", principal.client_id)
        if c:
            info["client"] = {"name": c["name"], "slug": c["slug"]}
    return info
