"""Scoring-rubric config endpoints + the answer-scoring playground.

Surfaces, all tenant-isolated:
  • Superadmin, tenant-parameterized:  GET/PUT /admin/scoring/{tenant_id}/config
                                       POST   /admin/scoring/{tenant_id}/score-text
  • Tenant self-serve (owner):          GET/PUT /scoring/config   (scoped to the caller)
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..db import pool
from ..services import factcheck, scoring, scoring_store, settings_store
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


class ScoreTextBody(BaseModel):
    text: str
    factcheck: bool = True   # also verify the answer's claims against the tenant's KB


async def _score_text(tid: str, text: str, do_factcheck: bool) -> dict:
    """Score a written operator answer against a tenant's active rubric (same engine the
    audio pipeline uses), optionally fact-checked against that tenant's own KB. Strictly
    scoped by tid — only this tenant's rubric and KB are ever used."""
    text = (text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    cfg = await scoring_store.get_active_config(tid)
    if not cfg or not cfg.get("dimensions"):
        raise HTTPException(status_code=400,
                            detail="No active scoring rubric for this tenant yet. Set one before scoring.")

    s = await settings_store.get_effective()
    try:
        scorecard = await scoring.run_scoring(text, cfg, s["anthropic_api_key"], s["llm_model"])
    except scoring.ScoringError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    # Ground "correctness" in the tenant's KB when there is one. Never blocks the score.
    kb_check = None
    if do_factcheck:
        try:
            async with pool().acquire() as conn:
                has_kb = await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM kb_chunks WHERE client_id = $1)", tid)
            if has_kb:
                kb_check = await factcheck.run_factcheck(text, tid, s["anthropic_api_key"], s["llm_model"])
        except Exception:  # noqa: BLE001 — fact-check must never block the scorecard
            kb_check = None

    return {"scoring": scorecard, "kb_check": kb_check, "config_version": cfg.get("version")}


@router.post("/admin/scoring/{tenant_id}/score-text")
async def admin_score_text(body: ScoreTextBody, tid: str = Depends(_scope)):
    """Answer-scoring playground (superadmin, per tenant)."""
    return await _score_text(tid, body.text, body.factcheck)


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


@router.post("/scoring/score-text")
async def tenant_score_text(body: ScoreTextBody, client_id: str = Depends(_tenant)):
    """Tenant playground: score a written answer against the caller's own rubric + KB."""
    return await _score_text(client_id, body.text, body.factcheck)
