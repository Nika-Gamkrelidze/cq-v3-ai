"""Transcription settings: the deployment default and the per-workspace override.

Two audiences, one resolution chain (services/transcription.py):

  GET/PUT    /admin/transcription/defaults   superadmin — what every workspace inherits
  GET/PUT/DEL /transcription/config          the workspace — what it changes about that

The per-FILE layer is not here: it rides on the upload routes themselves as an optional
`transcription` form field, because it belongs to one request rather than to any stored config.

Read is wider than write on purpose. A workspace member who cannot edit the settings still has
to be able to SEE which language the recordings they upload are being transcribed as — an
invisible setting is how "why did it hear years instead of months" becomes unanswerable. A
registered user has no workspace to override, so they get the system layer with
`is_default: true` and `can_edit: false`: the same body shape, so the UI needs no second path.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..services import transcription
from ..services.audio import STT_FORMATS
from ..services.auth import Principal, resolve_principal
from .admin import require_admin

router = APIRouter(tags=["transcription"])


class TranscriptionBody(BaseModel):
    """Every field optional and every absence meaningful: a layer stores only what it changes.

    `model_fields_set` (not the values) is what the routes read, so `language_code: null` — the
    one field whose null is a value, "detect automatically" — is distinguishable from a field
    the form never sent.
    """

    language_code: str | None = None
    diarize: bool | None = None
    keyterms: list[str] | None = None
    audio_format: str | None = None

    def patch(self) -> dict:
        return {k: getattr(self, k) for k in self.model_fields_set}


# ---------------------------------------------------------------------------
# Superadmin — the deployment default
# ---------------------------------------------------------------------------
@router.get("/admin/transcription/defaults", dependencies=[Depends(require_admin)])
async def get_defaults():
    """The default every workspace inherits. Bypasses the 5 s cache — an operator who just
    saved must see their own save, not a value that was fresh enough for a request path."""
    cfg = await transcription.get_default(force=True)
    return {
        "language_code": cfg["language_code"],
        "diarize": cfg["diarize"],
        "keyterms": list(cfg["keyterms"]),
        "audio_format": cfg["audio_format"],
        # Nothing is stored yet ⇒ this IS the built-in floor, and `inherited` is that floor.
        "is_default": cfg.get("source") != "stored",
        "inherited": {**{k: (list(v) if isinstance(v, list) else v)
                         for k, v in transcription.CODE_DEFAULTS.items()},
                      "source": "system"},
        "formats": sorted(STT_FORMATS),
        "source": cfg.get("source"),
        "updated_at": cfg.get("updated_at"),
        "updated_by": cfg.get("updated_by"),
    }


@router.put("/admin/transcription/defaults", dependencies=[Depends(require_admin)])
async def put_defaults(body: TranscriptionBody):
    """Replace the deployment default. A field the body omits falls back to the CODE default —
    this layer is the floor's replacement, not a patch on top of the last save, so an operator
    can never end up with a setting they cannot see in the form."""
    await transcription.set_default(body.patch(), updated_by="superadmin")
    return await get_defaults()


# ---------------------------------------------------------------------------
# The workspace — its override on top of that
# ---------------------------------------------------------------------------
def _reader(principal: Principal = Depends(resolve_principal)) -> Principal:
    if principal.is_tenant or principal.is_user:
        return principal
    raise HTTPException(status_code=401, detail="Sign in to see the transcription settings")


def _editor(principal: Principal = Depends(_reader)) -> Principal:
    """Changing how a workspace's calls are transcribed changes what fact-check and the rubric
    are later run against — the same owner|apikey|superadmin authority the rubric and the bot
    settings need."""
    if not principal.is_tenant:
        raise HTTPException(status_code=403,
                            detail="Transcription settings belong to a workspace.")
    if not principal.may_configure_workspace:
        raise HTTPException(status_code=403,
                            detail="Owner role required to edit the transcription settings")
    return principal


async def _view(principal: Principal) -> dict:
    cfg = await transcription.effective_for_tenant(
        principal.client_id if principal.is_tenant else None)
    cfg["formats"] = sorted(STT_FORMATS)
    cfg["can_edit"] = bool(principal.is_tenant and principal.may_configure_workspace)
    return cfg


@router.get("/transcription/config")
async def get_config(principal: Principal = Depends(_reader)):
    """The EFFECTIVE settings for this workspace, plus `is_default` (true = nothing of its own,
    inheriting) and `inherited` (the layer underneath, so the UI can show the fallback)."""
    return await _view(principal)


@router.put("/transcription/config")
async def put_config(body: TranscriptionBody, principal: Principal = Depends(_editor)):
    """Save the workspace's override — only the fields the body actually sends. Anything it
    leaves out goes back to being inherited, which is what the reset control relies on."""
    await transcription.set_tenant_override(principal.client_id, body.patch())
    return await _view(principal)


@router.delete("/transcription/config")
async def delete_config(principal: Principal = Depends(_editor)):
    """Drop the override entirely; the workspace inherits every field again."""
    await transcription.clear_tenant_override(principal.client_id)
    return await _view(principal)
