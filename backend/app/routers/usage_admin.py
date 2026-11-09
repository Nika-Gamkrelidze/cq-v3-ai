"""Detailed AI usage for the superadmin console: /admin/usage/{overview,calls,recordings,
conversations}.

The two older endpoints (`/admin/usage/tenants`, `/admin/usage/tenants/{id}`) stay in
`admin.py`, unchanged. These are the page's drill-downs: every workspace down to one recording's
fact-check, and one chat conversation down to the customer question each bot call answered.

Every route here is superadmin-only (the router dependency) and read-only. The filters are
parsed once, by `usage.parse_filter`, so a malformed date or workspace id is a 400 with a
sentence and never reaches SQL; sort keys are whitelisted inside the service modules.
"""
import uuid

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from ..services import pricing, usage, usage_drill, usage_report
from .admin import require_admin

router = APIRouter(prefix="/admin/usage", tags=["admin"], dependencies=[Depends(require_admin)])


def _filter(window: str | None, from_: str | None, to: str | None, client_id: str | None,
            group: str | None, feature: str | None, capability: str | None,
            provider: str | None, model: str | None, actor: str | None, status: str | None,
            q: str | None) -> usage.UsageFilter:
    try:
        return usage.parse_filter(window=window, from_=from_, to=to, client_id=client_id,
                                  group=group, feature=feature, capability=capability,
                                  provider=provider, model=model, actor=actor, status=status,
                                  q=q)
    except usage.FilterError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _uuid(value: str, what: str) -> str:
    try:
        return str(uuid.UUID(value))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"That is not a valid {what} id.") from exc


# FastAPI cannot take `from` as a parameter name, hence the alias on every route.
_FROM = Query(default=None, alias="from")


@router.get("/overview")
async def overview(window: str | None = None, from_: str | None = _FROM, to: str | None = None,
                   client_id: str | None = None, group: str | None = None,
                   feature: str | None = None, capability: str | None = None,
                   provider: str | None = None, model: str | None = None,
                   actor: str | None = None, status: str | None = None):
    """Totals, the breakdowns (workspace, analyser, feature, provider, model, user), a time
    series, and the facets the filter bar offers."""
    f = _filter(window, from_, to, client_id, group, feature, capability, provider, model,
                actor, status, None)
    return await usage_report.overview(f)


@router.get("/calls")
async def calls(window: str | None = None, from_: str | None = _FROM, to: str | None = None,
                client_id: str | None = None, group: str | None = None,
                feature: str | None = None, capability: str | None = None,
                provider: str | None = None, model: str | None = None,
                actor: str | None = None, status: str | None = None, q: str | None = None,
                sort: str | None = None, dir: str | None = None,
                limit: int = usage.DEFAULT_LIMIT, offset: int = 0):
    """One row per AI call, filtered, sorted and paginated."""
    f = _filter(window, from_, to, client_id, group, feature, capability, provider, model,
                actor, status, q)
    return await usage_report.calls(f, sort=sort, dir_=dir, limit=limit, offset=offset)


@router.get("/recordings")
async def recordings(window: str | None = None, from_: str | None = _FROM, to: str | None = None,
                     client_id: str | None = None, group: str | None = None,
                     feature: str | None = None, capability: str | None = None,
                     provider: str | None = None, model: str | None = None,
                     actor: str | None = None, status: str | None = None,
                     q: str | None = None, sort: str | None = None, dir: str | None = None,
                     limit: int = usage.DEFAULT_LIMIT, offset: int = 0):
    """One row per recording, with what each analyser spent on it."""
    f = _filter(window, from_, to, client_id, group, feature, capability, provider, model,
                actor, status, q)
    return await usage_drill.recordings(f, sort=sort, dir_=dir, limit=limit, offset=offset)


@router.get("/recordings/{job_id}")
async def recording_detail(job_id: str):
    """Every AI call one recording caused, grouped by analyser."""
    out = await usage_drill.recording_detail(_uuid(job_id, "recording"))
    if out is None:
        raise HTTPException(status_code=404, detail="No usage recorded for that recording.")
    return out


@router.get("/conversations")
async def conversations(window: str | None = None, from_: str | None = _FROM,
                        to: str | None = None, client_id: str | None = None,
                        group: str | None = None, feature: str | None = None,
                        capability: str | None = None, provider: str | None = None,
                        model: str | None = None, actor: str | None = None,
                        status: str | None = None, q: str | None = None,
                        channel: str | None = None, sort: str | None = None,
                        dir: str | None = None, limit: int = usage.DEFAULT_LIMIT,
                        offset: int = 0):
    """One row per chat conversation, with what the bot and the copilot spent on it."""
    f = _filter(window, from_, to, client_id, group, feature, capability, provider, model,
                actor, status, q)
    return await usage_drill.conversations(f, channel=(channel or "").strip() or None,
                                           sort=sort, dir_=dir, limit=limit, offset=offset)


@router.get("/conversations/{conversation_id}")
async def conversation_detail(conversation_id: str):
    """One conversation, turn by turn: each message and the AI calls it caused."""
    out = await usage_drill.conversation_detail(_uuid(conversation_id, "conversation"))
    if out is None:
        raise HTTPException(status_code=404, detail="No usage recorded for that conversation.")
    return out


# ---------------------------------------------------------------------------------------------
# Prices and markup — what turns the usage above into money (services/pricing.py). Same
# superadmin-only router: a workspace never sees what it costs us or what we add on top.
# ---------------------------------------------------------------------------------------------
_ACTOR = "superadmin"


async def _priced(call):
    try:
        out = await call
    except pricing.Conflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except pricing.PricingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if out is None or out is False:
        raise HTTPException(status_code=404, detail="Not found.")
    return out


@router.get("/pricing")
async def pricing_overview():
    """Every price and markup version, plus the models that have calls with no price."""
    return await pricing.overview()


@router.post("/prices", status_code=201)
async def add_price(body: dict = Body(...)):
    return await _priced(pricing.add_price(body, _ACTOR))


@router.put("/prices/{price_id}")
async def update_price(price_id: str, body: dict = Body(...)):
    return await _priced(pricing.update_price(price_id, body, _ACTOR))


@router.delete("/prices/{price_id}")
async def delete_price(price_id: str):
    await _priced(pricing.delete_price(price_id))
    return {"deleted": True}


@router.post("/markups", status_code=201)
async def add_markup(body: dict = Body(...)):
    return await _priced(pricing.add_markup(body, _ACTOR))


@router.put("/markups/{markup_id}")
async def update_markup(markup_id: str, body: dict = Body(...)):
    return await _priced(pricing.update_markup(markup_id, body, _ACTOR))


@router.delete("/markups/{markup_id}")
async def delete_markup(markup_id: str):
    await _priced(pricing.delete_markup(markup_id))
    return {"deleted": True}
