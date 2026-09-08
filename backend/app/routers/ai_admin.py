"""The AI provider registry, superadmin side: /admin/ai/*.

Connections (named provider credentials, one default per capability), the per-workspace
assignment dropdowns, and the catalog the console renders them from. Every route sits behind
the same `X-Admin-Token` gate as the rest of /admin; a scoped operator (`X-Act-As-Tenant`)
is a tenant-shaped principal and is refused here like everywhere under /admin.

Nothing here returns a key. The service layer seals keys on write and answers with `has_key`
+ a masked `key_hint`; a refused write is `ai_registry.RegistryError`, which main.py renders
as `{"detail", "code", "field"}`.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..services import ai_registry
from ..services.providers import catalog
from .admin import require_admin

router = APIRouter(prefix="/admin/ai", tags=["admin"], dependencies=[Depends(require_admin)])

ACTOR = "superadmin"


@router.get("/providers")
async def providers():
    """The catalog: per capability, per provider — label, known (not exhaustive) models,
    whether a base URL is accepted, and the extra settings fields the provider takes."""
    return catalog.CATALOG


# --------------------------------------------------------------------------- #
# Connections
# --------------------------------------------------------------------------- #
@router.get("/connections")
async def list_connections(capability: str | None = None):
    return await ai_registry.list_connections(capability or None)


class ConnectionCreate(BaseModel):
    name: str
    capability: str
    provider: str
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    settings: dict | None = None


@router.post("/connections")
async def create_connection(body: ConnectionCreate):
    return await ai_registry.create_connection(
        name=body.name, capability=body.capability, provider=body.provider,
        model=body.model, base_url=body.base_url, api_key=body.api_key,
        settings=body.settings, updated_by=ACTOR)


class ConnectionUpdate(BaseModel):
    name: str | None = None
    capability: str | None = None
    provider: str | None = None
    model: str | None = None
    base_url: str | None = None
    # Only sent when SETTING a new key. Absent means "leave whatever is stored alone", which
    # is why clearing needs its own flag: the console cannot read the key back.
    api_key: str | None = None
    clear_key: bool = False
    settings: dict | None = None
    is_active: bool | None = None


@router.put("/connections/{conn_id}")
async def update_connection(conn_id: str, body: ConnectionUpdate):
    # A field the client did not send is left alone; one sent as null is cleared. Pydantic's
    # `model_fields_set` is what tells the two apart.
    sent = body.model_fields_set

    def pick(field: str):
        return getattr(body, field) if field in sent else ai_registry.KEEP

    return await ai_registry.update_connection(
        conn_id, name=pick("name"), capability=pick("capability"), provider=pick("provider"),
        model=pick("model"), base_url=pick("base_url"), api_key=body.api_key,
        clear_key=body.clear_key, settings=pick("settings"), is_active=pick("is_active"),
        updated_by=ACTOR)


@router.delete("/connections/{conn_id}")
async def delete_connection(conn_id: str):
    """Deactivates. Never a hard delete: llm_usage rows reference the connection."""
    return await ai_registry.deactivate_connection(conn_id, updated_by=ACTOR)


@router.post("/connections/{conn_id}/test")
async def test_connection(conn_id: str):
    return await ai_registry.test_connection(conn_id)


@router.post("/connections/{conn_id}/default")
async def make_default(conn_id: str):
    return await ai_registry.set_default(conn_id, updated_by=ACTOR)


# --------------------------------------------------------------------------- #
# Assignments: which connection a workspace runs on, per capability
# --------------------------------------------------------------------------- #
async def _tenant_or_404(tenant_id: str) -> None:
    if not await ai_registry.tenant_exists(tenant_id):
        raise HTTPException(status_code=404, detail="Tenant not found")


@router.get("/assignments/{tenant_id}")
async def get_assignments(tenant_id: str):
    await _tenant_or_404(tenant_id)
    return await ai_registry.get_assignments(tenant_id)


class AssignmentsBody(BaseModel):
    llm: str | None = None
    stt: str | None = None
    tts: str | None = None


@router.put("/assignments/{tenant_id}")
async def put_assignments(tenant_id: str, body: AssignmentsBody):
    """`{llm?: id|null, stt?: id|null, tts?: id|null}` — absent leaves it, null clears it."""
    await _tenant_or_404(tenant_id)
    patch = {cap: getattr(body, cap) for cap in body.model_fields_set}
    return await ai_registry.set_assignments(tenant_id, patch, updated_by=ACTOR)
