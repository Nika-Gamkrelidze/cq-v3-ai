"""The tenant portal's own bot settings: GET/PUT /chat/config.

This is the mirror that was never built. The portal's BOT tab (tenant.html) was wired to call
`/chat/config` with the tenant's own credential, "exactly as /scoring/config mirrors its admin
twin" — but the only `/config` the codebase had was `/v1/chat/config` in routers/chat.py, which
answers an INTEGRATION credential (`require_chat`) with the read-only view the chat site needs.
A customer opening the tab would have got a 404 and the tab's "unavailable" message.

Tenant-only, on purpose:
  * a registered user (kind "user") has no knowledge base and therefore no bot — 403, not 401,
    because the credential is valid and the feature simply is not theirs;
  * an integration key already has `/v1/chat/config` and must not gain a write path to the
    persona or the autopilot toggle by being handed a second route;
  * an operator reaches this through `X-Act-As-Tenant` and needs nothing special here — the
    resolver hands them a tenant-shaped principal whose role passes `may_configure_workspace`,
    and `audit_actor` names them `tenant:superadmin`, never one of the customer's own people.

The autopilot pre-check is the admin route's, verbatim, and it stays a 409: the portal turns
that exact status into the "share a document with the bot first" explainer.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..services import chat_store, settings_store
from ..services.auth import Principal, resolve_principal
from .kb import count_public_documents

router = APIRouter(tags=["chat"])


class ChatConfigBody(BaseModel):
    # Same fields as admin.py's ChatConfigBody: the portal and the console post one shape, so
    # a knob added to one side is a knob the other side already accepts.
    persona: str | None = None
    greeting: dict = {}              # {en,ka,ru}
    refusal_copy: dict = {}          # {en,ka,ru}
    languages: list[str] = ["en", "ka", "ru"]
    canned: list = []
    autopilot_enabled: bool = False
    settings: dict = {}


def _bot_reader(principal: Principal = Depends(resolve_principal)) -> Principal:
    """Any credential of the workspace may read its bot: members see the state, owners edit."""
    if principal.is_tenant:
        return principal
    if principal.is_user:
        raise HTTPException(status_code=403, detail="Bot settings belong to a workspace.")
    raise HTTPException(status_code=401, detail="Tenant login or API key required")


def _bot_editor(principal: Principal = Depends(_bot_reader)) -> Principal:
    """Changing what a public bot says needs the workspace's full authority — the same
    owner|apikey|superadmin predicate the rubric and the sentiment config use."""
    if not principal.may_configure_workspace:
        raise HTTPException(status_code=403, detail="Owner role required to edit the bot settings")
    return principal


async def _with_killed(cfg: dict, client_id: str) -> dict:
    # The kill switch is the operator's brake, stored outside chat_configs so a tenant cannot
    # clear it by saving. The portal still has to SHOW it — a bot that is "live" in the form
    # and silent in production is a support ticket — so it rides along on the read.
    kill = await settings_store.get_autopilot_kill_switch()
    return {**cfg, "killed": settings_store.autopilot_killed(kill, str(client_id))}


@router.get("/chat/config")
async def get_config(principal: Principal = Depends(_bot_reader)):
    """The caller's active bot config merged over the defaults, plus `killed`.

    Everything else in the body — `is_default` included — is whatever `chat_store.get_chat_config`
    returns; this route adds one key and computes nothing of its own.
    """
    cfg = await chat_store.get_chat_config(principal.client_id)
    return await _with_killed(cfg, principal.client_id)


@router.put("/chat/config")
async def put_config(body: ChatConfigBody, principal: Principal = Depends(_bot_editor)):
    """Save a new active version and answer with the same merged shape GET does, so the portal
    can refill the form from the response instead of re-fetching."""
    # Verbatim from admin.py's put_chat_config: autopilot answers the public with no human in
    # between, and a KB with nothing published would refuse every customer question — so the
    # write is refused with a 409 naming the missing thing rather than silently downgraded.
    if body.autopilot_enabled and not await count_public_documents(principal.client_id):
        raise HTTPException(
            status_code=409,
            detail="Cannot enable autopilot: this tenant has no public knowledge-base "
                   "documents. Publish at least one document (visibility='public') first.")
    try:
        cfg = await chat_store.save_chat_config(
            principal.client_id, persona=body.persona, greeting=body.greeting,
            refusal_copy=body.refusal_copy, languages=body.languages, canned=body.canned,
            autopilot_enabled=body.autopilot_enabled, settings=body.settings,
            # `tenant:<user_id>` for a person, `tenant:apikey` for a key, `tenant:superadmin`
            # for an operator — the vocabulary kb_events already uses, so the console can say
            # who last changed the bot without guessing.
            updated_by=principal.audit_actor)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await _with_killed(cfg, principal.client_id)
