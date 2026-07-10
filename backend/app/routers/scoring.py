"""Scoring-rubric config endpoints.

Two surfaces, both tenant-isolated:
  • Superadmin, tenant-parameterized:  GET/PUT /admin/scoring/{tenant_id}/config
  • Tenant self-serve (owner):          GET/PUT /scoring/config   (scoped to the caller)
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..db import pool
from ..services import scoring_store
from ..services.auth import Principal, resolve_principal

router = APIRouter(tags=["scoring"])


class Dimension(BaseModel):
    key: str | None = None
    name: str
    description: str | None = ""
    guidance: str | None = ""
    weight: float = 0.0


class ConfigBody(BaseModel):
    dimensions: list[Dimension]
    rubric: str | None = ""


def _dump(dims: list[Dimension]) -> list[dict]:
    return [d.model_dump() for d in dims]


# --------------------------------------------------------------------------- #
# Superadmin, tenant-parameterized
# --------------------------------------------------------------------------- #
async def _scope(tenant_id: str, principal: Principal = Depends(resolve_principal)) -> str:
    if not principal.is_superadmin:
        raise HTTPException(status_code=401, detail="Superadmin required")
    try:
        uuid.UUID(tenant_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Tenant not found")
    async with pool().acquire() as conn:
        if not await conn.fetchval("SELECT 1 FROM clients WHERE id = $1", tenant_id):
            raise HTTPException(status_code=404, detail="Tenant not found")
    return tenant_id


@router.get("/admin/scoring/{tenant_id}/config")
async def admin_get(tid: str = Depends(_scope)):
    return await scoring_store.get_active_config(tid) or {"version": None, "dimensions": [],
                                                          "weights": {}, "rubric": "", "is_active": False}


@router.put("/admin/scoring/{tenant_id}/config")
async def admin_put(body: ConfigBody, tid: str = Depends(_scope)):
    try:
        return await scoring_store.save_config(tid, _dump(body.dimensions), body.rubric or "", "superadmin")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# --------------------------------------------------------------------------- #
# Tenant self-serve — scoped to the authenticated tenant
# --------------------------------------------------------------------------- #
def _tenant(principal: Principal = Depends(resolve_principal)) -> str:
    if not principal.is_tenant:
        raise HTTPException(status_code=401, detail="Tenant login or API key required")
    return principal.client_id


@router.get("/scoring/config")
async def tenant_get(client_id: str = Depends(_tenant)):
    return await scoring_store.get_active_config(client_id) or {"version": None, "dimensions": [],
                                                                "weights": {}, "rubric": "", "is_active": False}


@router.put("/scoring/config")
async def tenant_put(body: ConfigBody, client_id: str = Depends(_tenant)):
    try:
        return await scoring_store.save_config(client_id, _dump(body.dimensions), body.rubric or "", "tenant")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
