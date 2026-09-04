"""Superadmin tenant management: clients, tenant users, API keys."""
import asyncio
import re
import secrets

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..db import pool
from ..services import auth
from ..services.auth import Principal, resolve_principal

router = APIRouter(prefix="/admin/tenants", tags=["tenants"])


def require_superadmin(principal: Principal = Depends(resolve_principal)) -> Principal:
    if not principal.is_superadmin:
        raise HTTPException(status_code=401, detail="Superadmin required")
    return principal


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "tenant"


class TenantCreate(BaseModel):
    name: str
    slug: str | None = None
    industry: str | None = None
    region: str | None = None


class TenantUpdate(BaseModel):
    name: str | None = None
    industry: str | None = None
    region: str | None = None
    is_active: bool | None = None
    settings: dict | None = None


@router.get("", dependencies=[Depends(require_superadmin)])
async def list_tenants():
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT c.id, c.slug, c.name, c.industry, c.region, c.is_active,
                   (c.api_key IS NOT NULL) AS has_api_key,
                   (SELECT count(*) FROM tenant_users u WHERE u.client_id = c.id) AS users,
                   (SELECT count(*) FROM kb_documents d WHERE d.client_id = c.id) AS documents
            FROM clients c ORDER BY c.created_at DESC NULLS LAST, c.name
            """)
    return [{**dict(r), "id": str(r["id"])} for r in rows]


@router.post("", dependencies=[Depends(require_superadmin)])
async def create_tenant(body: TenantCreate):
    slug = _slugify(body.slug or body.name)
    api_key = "cq_" + secrets.token_hex(24)
    async with pool().acquire() as conn:
        exists = await conn.fetchval("SELECT 1 FROM clients WHERE slug = $1", slug)
        if exists:
            raise HTTPException(status_code=409, detail=f"Slug '{slug}' already exists")
        cid = await conn.fetchval(
            """
            INSERT INTO clients (slug, name, industry, region, data_tier, api_key, is_active)
            VALUES ($1, $2, $3, $4, 'standard', $5, true) RETURNING id
            """, slug, body.name, body.industry, body.region, api_key)
    return {"id": str(cid), "slug": slug, "name": body.name, "api_key": api_key}


@router.put("/{tenant_id}", dependencies=[Depends(require_superadmin)])
async def update_tenant(tenant_id: str, body: TenantUpdate):
    import json
    fields, vals = [], []
    for i, (k, v) in enumerate(body.model_dump(exclude_none=True).items(), start=2):
        fields.append(f"{k} = ${i}::jsonb" if k == "settings" else f"{k} = ${i}")
        vals.append(json.dumps(v) if k == "settings" else v)
    if not fields:
        return {"updated": False}
    async with pool().acquire() as conn:
        res = await conn.execute(
            f"UPDATE clients SET {', '.join(fields)} WHERE id = $1", tenant_id, *vals)
    if res.endswith("0"):
        raise HTTPException(status_code=404, detail="Tenant not found")
    return {"updated": True}


@router.post("/{tenant_id}/rotate-key", dependencies=[Depends(require_superadmin)])
async def rotate_key(tenant_id: str):
    api_key = "cq_" + secrets.token_hex(24)
    async with pool().acquire() as conn:
        res = await conn.execute("UPDATE clients SET api_key = $2 WHERE id = $1", tenant_id, api_key)
    if res.endswith("0"):
        raise HTTPException(status_code=404, detail="Tenant not found")
    return {"api_key": api_key}


@router.get("/{tenant_id}/key", dependencies=[Depends(require_superadmin)])
async def get_key(tenant_id: str):
    async with pool().acquire() as conn:
        key = await conn.fetchval("SELECT api_key FROM clients WHERE id = $1", tenant_id)
    return {"api_key": key}


@router.delete("/{tenant_id}", dependencies=[Depends(require_superadmin)])
async def delete_tenant(tenant_id: str):
    async with pool().acquire() as conn:
        res = await conn.execute("DELETE FROM clients WHERE id = $1", tenant_id)
    if res.endswith("0"):
        raise HTTPException(status_code=404, detail="Tenant not found")
    return {"deleted": True}


# ---- tenant users ----------------------------------------------------------
VALID_ROLES = ("member", "owner")


class UserCreate(BaseModel):
    username: str
    password: str
    role: str = "member"


class UserUpdate(BaseModel):
    """Everything editable about a tenant user after creation. All optional — only the
    supplied fields change. Username is deliberately immutable: it is the login identity
    and audit-trail anchor; recreate the user to rename."""
    password: str | None = None
    role: str | None = None
    is_active: bool | None = None


@router.get("/{tenant_id}/users", dependencies=[Depends(require_superadmin)])
async def list_users(tenant_id: str):
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, username, role, is_active, created_at FROM tenant_users WHERE client_id = $1 ORDER BY created_at",
            tenant_id)
    return [{**dict(r), "id": str(r["id"]), "created_at": r["created_at"].isoformat()} for r in rows]


@router.post("/{tenant_id}/users", dependencies=[Depends(require_superadmin)])
async def create_user(tenant_id: str, body: UserCreate):
    if len(body.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
    if body.role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail="Role must be member or owner")
    async with pool().acquire() as conn:
        if not await conn.fetchval("SELECT 1 FROM clients WHERE id = $1", tenant_id):
            raise HTTPException(status_code=404, detail="Tenant not found")
        if await conn.fetchval("SELECT 1 FROM tenant_users WHERE username = $1", body.username):
            raise HTTPException(status_code=409, detail="Username already taken")
        uid = await conn.fetchval(
            """
            INSERT INTO tenant_users (client_id, username, password_hash, role)
            VALUES ($1, $2, $3, $4) RETURNING id
            """, tenant_id, body.username,
            # PBKDF2 in a thread: ~40 ms of blocking hashing on the one event loop this
            # process has is 40 ms nobody else's request is served. Same rule as auth.py.
            await asyncio.to_thread(auth.hash_password, body.password), body.role)
    return {"id": str(uid), "username": body.username, "role": body.role}


@router.put("/{tenant_id}/users/{user_id}", dependencies=[Depends(require_superadmin)])
async def update_user(tenant_id: str, user_id: str, body: UserUpdate):
    """Edit a tenant user: reset password, change role, enable/disable. Tenant-scoped in
    the WHERE like every other query here — a user id from another tenant is a 404."""
    fields, vals = [], []
    if body.password is not None:
        if len(body.password) < 6:
            raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
        vals.append(await asyncio.to_thread(auth.hash_password, body.password))
        fields.append(f"password_hash = ${len(vals)+2}")
    if body.role is not None:
        if body.role not in VALID_ROLES:
            raise HTTPException(status_code=400, detail="Role must be member or owner")
        vals.append(body.role); fields.append(f"role = ${len(vals)+2}")
    if body.is_active is not None:
        vals.append(body.is_active); fields.append(f"is_active = ${len(vals)+2}")
    if not fields:
        raise HTTPException(status_code=400, detail="Nothing to update")
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            f"UPDATE tenant_users SET {', '.join(fields)} WHERE id = $1 AND client_id = $2 "
            "RETURNING id, username, role, is_active", user_id, tenant_id, *vals)
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    return {**dict(row), "id": str(row["id"])}


@router.delete("/{tenant_id}/users/{user_id}", dependencies=[Depends(require_superadmin)])
async def delete_user(tenant_id: str, user_id: str):
    async with pool().acquire() as conn:
        res = await conn.execute(
            "DELETE FROM tenant_users WHERE id = $1 AND client_id = $2", user_id, tenant_id)
    if res.endswith("0"):
        raise HTTPException(status_code=404, detail="User not found")
    return {"deleted": True}
