"""A workspace's own AI settings: /ai/config — its own provider keys, per capability.

OPERATOR ONLY (since 2026-11-10, at the owner's request). These are the ordinary tenant-shaped
routes, but the only principal they answer is a superadmin acting on one workspace
(`X-Act-As-Tenant`, `Principal.is_operator`) — that is how the console's workspace page sets a
workspace's own key. A workspace's owner, members and API key get a 403: which provider and
whose key a workspace spends on is CommuniQ's decision, not the customer's. Registered users
(kind "user") get a 403 as before; no credential is a 401.

Keys an owner saved before the change stay on file and keep resolving
(`ai_resolve.resolve`); the operator sees and removes them here.

One thing this route never sets, whoever calls it: a `base_url`. A PUT that carries it —
top-level or smuggled inside `settings` — is a 400 with code `base_url_not_allowed`, before
any write; routing a workspace through a gateway is /admin/ai-config/{tenant_id}.

Nothing here returns a key: the override view carries `has_key` and a masked `key_hint`.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..services import ai_registry
from ..services.ai_registry import RegistryError
from ..services.auth import Principal, resolve_principal
from ..services.providers import catalog

router = APIRouter(prefix="/ai", tags=["ai"])


def _reader(principal: Principal = Depends(resolve_principal)) -> Principal:
    """The operator acting on the workspace — nobody else, not even its owner."""
    if principal.is_operator:
        return principal
    if principal.is_tenant or principal.is_user:
        raise HTTPException(status_code=403,
                            detail="AI provider settings are managed by CommuniQ.")
    raise HTTPException(status_code=401, detail="Superadmin login required")


# Every reader is an operator, and an operator may configure the workspace; the predicate
# stays so that widening `_reader` again can never widen the writes with it.
def _editor(principal: Principal = Depends(_reader)) -> Principal:
    if not principal.may_configure_workspace:
        raise HTTPException(status_code=403, detail="AI provider settings are managed by CommuniQ.")
    return principal


async def _capability_view(client_id: str, capability: str) -> dict:
    row = await ai_registry.get_override(client_id, capability)
    return {"effective": await ai_registry.effective(client_id, capability),
            "override": ai_registry.public_override(row),
            "providers": list(catalog.providers_for(capability))}


@router.get("/config")
async def get_config(principal: Principal = Depends(_reader)):
    """Per capability: what the workspace is really running on (`effective`), its own row if
    any (`override`, key masked), and the provider ids it may choose from."""
    return {cap: await _capability_view(principal.client_id, cap)
            for cap in ai_registry.CAPABILITIES}


class OverrideBody(BaseModel):
    provider: str
    model: str | None = None
    # Only sent when SETTING a new key; absent keeps the stored one, clear_key removes it.
    api_key: str | None = None
    clear_key: bool = False
    settings: dict | None = None
    # Absent means "on": a workspace that saves its own key means to use it. Sent explicitly,
    # it lets an owner keep a key on file while running on the assigned connection.
    enabled: bool | None = None
    notes: str | None = None
    # Declared so that a tenant sending it is refused with a named code rather than having
    # the field silently dropped by validation — the refusal is the contract.
    base_url: str | None = None


def _refuse_base_url(body: OverrideBody) -> None:
    if body.base_url is not None or (body.settings and "base_url" in body.settings):
        raise RegistryError(
            "A workspace cannot set a base URL; ask the operator if a gateway is needed.",
            code="base_url_not_allowed", field="base_url")


@router.put("/config/{capability}")
async def put_config(capability: str, body: OverrideBody,
                     principal: Principal = Depends(_editor)):
    ai_registry.check_capability(capability)
    _refuse_base_url(body)
    sent = body.model_fields_set
    await ai_registry.save_override(
        principal.client_id, capability, provider=body.provider,
        model=body.model if "model" in sent else ai_registry.KEEP,
        api_key=body.api_key, clear_key=body.clear_key,
        settings=body.settings if "settings" in sent else ai_registry.KEEP,
        enabled=True if body.enabled is None else body.enabled,
        notes=body.notes if "notes" in sent else ai_registry.KEEP,
        # Never from a tenant: whatever the row holds (an operator's choice) stays as it is.
        base_url=ai_registry.KEEP,
        updated_by=principal.audit_actor)
    return await _capability_view(principal.client_id, capability)


@router.delete("/config/{capability}")
async def delete_config(capability: str, principal: Principal = Depends(_editor)):
    ai_registry.check_capability(capability)
    deleted = await ai_registry.delete_override(principal.client_id, capability)
    return {"deleted": deleted, **await _capability_view(principal.client_id, capability)}


@router.post("/config/{capability}/test")
async def test_config(capability: str, principal: Principal = Depends(_editor)):
    """Probe the workspace's OWN row — even one not yet enabled — so an owner can check a
    key before switching to it."""
    ai_registry.check_capability(capability)
    return await ai_registry.test_override(principal.client_id, capability)
