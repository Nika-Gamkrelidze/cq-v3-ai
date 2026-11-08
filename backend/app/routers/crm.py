"""The CRM analyser's HTTP surface: a workspace's CRM forms, filling one from a call, review.

Routes (all resolve the principal; who each admits is stated on the handler):

  Forms — any workspace credential reads; owners (`may_configure_workspace`) write:
    GET    /crm/forms                       the forms, primary first (the default when none)
    POST   /crm/forms                       create: blank, from a preset, or a copy
    GET    /crm/forms/{key}                 one form, active version
    PUT    /crm/forms/{key}                 save a new version
    DELETE /crm/forms/{key}                 retire (history kept)
    POST   /crm/forms/{key}/primary         the form "Analyse a call" opens with
    GET    /crm/forms/{key}/versions
    GET    /crm/forms/{key}/export?format=json|xlsx[&version=]
    GET    /crm/export                      every form, one JSON bundle
    POST   /crm/import/preview              multipart file → per-form validation + diff
    POST   /crm/import                      the previewed definitions → saved

  Workspace or superadmin (no tenant data involved):
    POST   /crm/validate                    live validation for the editor
    GET    /crm/presets, GET /crm/presets/{id}

  Filling — any workspace credential (one `analyses` unit per run):
    POST   /recordings/{job_id}/crm         fill a form from a stored recording
    GET    /recordings/{job_id}/crm         the latest filled form per form key
    POST   /crm/test                        fill an unsaved form from pasted text (no row)
    PATCH  /crm/results/{id}                a reviewer's values + draft|approved
    GET    /crm/results/{id}/record         the flat record a CRM import wants

  Superadmin:
    GET/PUT /admin/crm/default-form, POST /admin/crm/default-form/reset,
    GET    /admin/crm/default-form/export?format=json|xlsx

Workspace-only on purpose. A registered user of the public workbench has no workspace and so
no CRM; an integration credential is not `is_tenant` and never reaches any of this.
"""
from __future__ import annotations

import asyncio
import json
import logging
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from ..services import crm_extract, crm_form, crm_presets, crm_store, crm_xlsx
from ..services.auth import Principal, resolve_principal
from .recordings import (_actor_name, _load, _pay_for_run, _settings, _transcript_of,
                         _upstream)

router = APIRouter(tags=["crm"])

log = logging.getLogger("cq")

MAX_IMPORT_BYTES = 5 * 1024 * 1024
MAX_IMPORT_FORMS = 20
MAX_TEST_CHARS = 200_000
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


# --------------------------------------------------------------------------- #
# Who may do what
# --------------------------------------------------------------------------- #
def _workspace(principal: Principal = Depends(resolve_principal)) -> Principal:
    if principal.is_tenant:
        return principal
    if principal.is_user:
        raise HTTPException(status_code=403,
                            detail="CRM forms belong to a workspace; a personal account has none.")
    raise HTTPException(status_code=401, detail="Workspace login or API key required")


def _owner(principal: Principal = Depends(_workspace)) -> Principal:
    if not principal.may_configure_workspace:
        raise HTTPException(status_code=403, detail="Owner role required to change the CRM forms")
    return principal


def _superadmin(principal: Principal = Depends(resolve_principal)) -> Principal:
    if not principal.is_superadmin:
        raise HTTPException(status_code=403, detail="Superadmin only")
    return principal


def _workspace_or_admin(principal: Principal = Depends(resolve_principal)) -> Principal:
    if principal.is_tenant or principal.is_superadmin:
        return principal
    raise HTTPException(status_code=401, detail="Workspace login or superadmin required")


def _by(principal: Principal) -> str:
    """Who saved a form, in the vocabulary the rubric's `updated_by` uses: CQ staff named as
    such, never as the customer."""
    if principal.is_operator or principal.is_superadmin:
        return "superadmin"
    return principal.audit_actor


def _invalid(exc: crm_form.FormInvalid) -> HTTPException:
    return HTTPException(status_code=422, detail={"message": str(exc), "errors": exc.errors})


def _brief(form: dict) -> dict:
    """A list row: everything but the definition, plus its counts."""
    return {k: v for k, v in form.items() if k != "definition"} | {
        "summary": crm_form.summary(form["definition"]),
        "description": form["definition"].get("description", "")}


def _download(body: bytes, filename: str, media_type: str) -> Response:
    # `filename*` carries a Georgian name intact; `filename` is the ASCII fallback.
    ascii_name = filename.encode("ascii", "ignore").decode() or "crm-form"
    return Response(content=body, media_type=media_type, headers={
        "Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"})


def _export(form_key: str, defn: dict, version: int | None, fmt: str) -> Response:
    name = crm_store.safe_filename(defn.get("name") or form_key)
    if fmt == "xlsx":
        return _download(crm_xlsx.export_xlsx(form_key, defn), f"{name}.xlsx", XLSX)
    doc = crm_form.export_document(form_key, defn, version=version)
    return _download(json.dumps(doc, ensure_ascii=False, indent=2).encode("utf-8"),
                     f"{name}.json", "application/json")


# --------------------------------------------------------------------------- #
# Forms
# --------------------------------------------------------------------------- #
@router.get("/crm/forms")
async def list_forms(principal: Principal = Depends(_workspace)):
    return [_brief(f) for f in await crm_store.list_forms(principal.client_id)]


class CreateBody(BaseModel):
    name: str | None = None
    preset: str | None = None
    copy_from: str | None = None
    definition: dict | None = None


@router.post("/crm/forms")
async def create_form(body: CreateBody, principal: Principal = Depends(_owner)):
    """A new form from (in this order of precedence) a full definition, a copy of one of the
    workspace's forms, a preset, or a one-field blank. `name` overrides whichever it was."""
    cid = principal.client_id
    if body.definition is not None:
        raw = body.definition
    elif body.copy_from:
        src = await crm_store.get_form(cid, body.copy_from)
        if src is None:
            raise HTTPException(status_code=404, detail="Form not found")
        raw = {**src["definition"], "name": f"{src['definition']['name']} (copy)"}
    elif body.preset:
        raw = crm_presets.preset(body.preset)
        if raw is None:
            raise HTTPException(status_code=404, detail="Unknown preset")
    else:
        raw = {"name": "New form", "fields": [
            {"key": "summary", "label": "Summary", "type": "textarea",
             "instruction": "What the call was about, in two sentences."}]}
    if (body.name or "").strip():
        raw = {**raw, "name": body.name.strip()}
    # The key is derived one line before `save_form` validates, so `name` here is still
    # whatever the request sent: `slugify` runs `re.sub` over it and a number or an object
    # raised TypeError — a 500 where every other malformed definition is a 422. Coerce it the
    # way `normalize_definition` would and leave the complaining to the validator.
    key = await crm_store.unique_key(cid, str(raw.get("name") or "").strip() or "form")
    try:
        form, warnings = await crm_store.save_form(cid, key, raw, _by(principal))
    except crm_form.FormInvalid as exc:
        raise _invalid(exc) from exc
    return {"form": form, "warnings": warnings}


@router.get("/crm/forms/{form_key}")
async def get_form(form_key: str, principal: Principal = Depends(_workspace)):
    form = await crm_store.get_form(principal.client_id, form_key)
    if form is None:
        raise HTTPException(status_code=404, detail="Form not found")
    return form


class SaveBody(BaseModel):
    definition: dict


@router.put("/crm/forms/{form_key}")
async def save_form(form_key: str, body: SaveBody, principal: Principal = Depends(_owner)):
    cid = principal.client_id
    if await crm_store.get_form(cid, form_key) is None:
        raise HTTPException(status_code=404, detail="Form not found")
    try:
        form, warnings = await crm_store.save_form(cid, form_key, body.definition, _by(principal))
    except crm_form.FormInvalid as exc:
        raise _invalid(exc) from exc
    return {"form": form, "warnings": warnings}


@router.delete("/crm/forms/{form_key}")
async def delete_form(form_key: str, principal: Principal = Depends(_owner)):
    if not await crm_store.delete_form(principal.client_id, form_key):
        raise HTTPException(status_code=404, detail="Form not found")
    return {"ok": True}


@router.post("/crm/forms/{form_key}/primary")
async def set_primary(form_key: str, principal: Principal = Depends(_owner)):
    if not await crm_store.set_primary(principal.client_id, form_key):
        raise HTTPException(status_code=404, detail="Form not found")
    return {"ok": True}


@router.get("/crm/forms/{form_key}/versions")
async def form_versions(form_key: str, principal: Principal = Depends(_workspace)):
    return await crm_store.versions(principal.client_id, form_key)


@router.get("/crm/forms/{form_key}/export")
async def export_form(form_key: str, format: str = Query("json", pattern="^(json|xlsx)$"),
                      version: int | None = None, principal: Principal = Depends(_workspace)):
    cid = principal.client_id
    form = (await crm_store.get_version(cid, form_key, version) if version
            else await crm_store.get_form(cid, form_key))
    if form is None:
        raise HTTPException(status_code=404, detail="Form not found")
    return _export(form_key, form["definition"], form["version"] or None, format)


@router.get("/crm/export")
async def export_all(principal: Principal = Depends(_workspace)):
    forms = await crm_store.list_forms(principal.client_id)
    doc = {"format": crm_form.BUNDLE_FORMAT, "schema_version": crm_form.SCHEMA_VERSION,
           "forms": [crm_form.export_document(f["form_key"], f["definition"], version=f["version"] or None)
                     for f in forms]}
    return _download(json.dumps(doc, ensure_ascii=False, indent=2).encode("utf-8"),
                     "crm-forms.json", "application/json")


async def _parse_upload(file: UploadFile) -> list[dict]:
    """An uploaded JSON or XLSX → [{key, form}] raw definitions, or 400 saying why not."""
    data = await file.read(MAX_IMPORT_BYTES + 1)
    if not data:
        raise HTTPException(status_code=400, detail="The file is empty.")
    if len(data) > MAX_IMPORT_BYTES:
        raise HTTPException(status_code=413, detail="The file is larger than 5 MB.")
    name = (file.filename or "").lower()
    try:
        if name.endswith((".xlsx", ".xlsm")) or data[:2] == b"PK":
            # openpyxl unzips and walks the whole workbook synchronously, and a hostile 5 MB
            # one costs seconds of CPU; inline it would hold the single uvicorn worker's event
            # loop for every other tenant (same fix as routers/kb.py's text extraction).
            key, raw = await asyncio.to_thread(crm_xlsx.parse_xlsx, data)
            return [{"key": key, "form": raw}]
        try:
            doc = json.loads(data.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Not a JSON file or an Excel workbook ({exc}).") from None
        return crm_form.documents_from_json(doc)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except RecursionError:
        # A RecursionError is a RuntimeError, so it slipped past both clauses above: CPython's
        # JSON decoder recurses once per nesting level, and `documents_from_json` recurses
        # again over a bundle of bundles, so 6 KB of open brackets answered 500 where every
        # other unreadable file answers 400 and says why.
        raise HTTPException(status_code=400,
                            detail="The file is nested too deeply to read.") from None


@router.post("/crm/import/preview")
async def import_preview(file: UploadFile = File(...),
                         principal: Principal = Depends(_workspace_or_admin)):
    """What importing this file would do, form by form: problems with their paths, and for a
    key the workspace already has, what would change. Nothing is saved."""
    items = await _parse_upload(file)
    if not items:
        raise HTTPException(status_code=400, detail="The file holds no forms.")
    # The same ceiling the apply route enforces, named here because this is where a person
    # decides: a bundle previewed all-green and then refused by `POST /crm/import` is the
    # preview/apply divergence the preview exists to prevent, and normalising an unbounded
    # file would hand the dialog a response far larger than the upload.
    if len(items) > MAX_IMPORT_FORMS:
        raise HTTPException(status_code=400, detail=(
            f"The file holds {len(items)} forms; import at most {MAX_IMPORT_FORMS} at a time."))
    current = {}
    if principal.is_tenant:
        current = {f["form_key"]: f for f in await crm_store.list_forms(principal.client_id)}
    out = []
    for item in items:
        defn, errors, warnings = crm_form.normalize_definition(item["form"])
        key = (item.get("key") or "").strip() or None
        existing = current.get(key) if key else None
        out.append({"key": key, "name": defn.get("name"), "definition": defn,
                    "errors": errors, "warnings": warnings,
                    "exists": existing is not None,
                    "diff": crm_form.diff(existing["definition"] if existing else None, defn),
                    "summary": crm_form.summary(defn)})
    return {"forms": out}


class ImportItem(BaseModel):
    definition: dict
    # `replace` writes a new version of `key`; `new` creates a form beside the existing ones.
    mode: str = "new"
    key: str | None = None


class ImportBody(BaseModel):
    items: list[ImportItem]


@router.post("/crm/import")
async def import_forms(body: ImportBody, principal: Principal = Depends(_owner)):
    """Save previewed definitions. Validated again here — the preview is advice to a person,
    not a token the server trusts — and all-or-nothing on validation: one invalid form saves
    none, so a half-applied bundle never needs untangling."""
    if not body.items or len(body.items) > MAX_IMPORT_FORMS:
        raise HTTPException(status_code=400,
                            detail=f"Import between 1 and {MAX_IMPORT_FORMS} forms at a time.")
    cid = principal.client_id
    plans = []
    for i, item in enumerate(body.items):
        defn, errors, _ = crm_form.normalize_definition(item.definition)
        if errors:
            raise HTTPException(status_code=422, detail={
                "message": f"Form {i + 1} ({defn.get('name') or 'unnamed'}) is not valid.",
                "errors": errors})
        if item.mode == "replace":
            if not item.key or await crm_store.get_form(cid, item.key) is None:
                raise HTTPException(status_code=404, detail=f"Form {i + 1}: there is no form `{item.key}` to replace.")
        elif item.mode != "new":
            raise HTTPException(status_code=400, detail="mode must be `replace` or `new`.")
        plans.append((item, defn))
    saved = []
    for item, defn in plans:
        key = item.key if item.mode == "replace" else await crm_store.unique_key(cid, defn["name"])
        form, _ = await crm_store.save_form(cid, key, defn, _by(principal))
        saved.append(_brief(form))
    return {"saved": saved}


class ValidateBody(BaseModel):
    definition: dict


@router.post("/crm/validate")
async def validate(body: ValidateBody, _p: Principal = Depends(_workspace_or_admin)):
    defn, errors, warnings = crm_form.normalize_definition(body.definition)
    return {"definition": defn, "errors": errors, "warnings": warnings,
            "summary": crm_form.summary(defn)}


@router.get("/crm/presets")
async def list_presets(_p: Principal = Depends(_workspace_or_admin)):
    out = []
    for pid, p in crm_presets.PRESETS.items():
        defn, _, _ = crm_form.normalize_definition(p["form"])
        out.append({"id": pid, "title": p["title"], "name": defn["name"],
                    "description": defn["description"], "language": defn["language"],
                    "summary": crm_form.summary(defn)})
    return out


@router.get("/crm/presets/{preset_id}")
async def get_preset(preset_id: str, _p: Principal = Depends(_workspace_or_admin)):
    raw = crm_presets.preset(preset_id)
    if raw is None:
        raise HTTPException(status_code=404, detail="Unknown preset")
    defn, _, _ = crm_form.normalize_definition(raw)
    return {"id": preset_id, "definition": defn}


# --------------------------------------------------------------------------- #
# Filling a form
# --------------------------------------------------------------------------- #
async def _run(principal: Principal, defn: dict, *, form_key: str, form_version: int,
               transcript: str, segments, call_date) -> dict:
    cfg = await _settings("llm")
    await _pay_for_run(principal)
    try:
        return await crm_extract.fill(
            defn, form_key=form_key, form_version=form_version, transcript=transcript,
            segments=segments, api_key=cfg["anthropic_api_key"], model=cfg["llm_model"],
            client_id=principal.client_id, call_date=call_date)
    except crm_extract.CrmError as exc:
        raise _upstream(exc) from exc


class FillBody(BaseModel):
    form_key: str | None = None


@router.post("/recordings/{job_id}/crm")
async def fill_recording(job_id: str, body: FillBody | None = None,
                         principal: Principal = Depends(_workspace)):
    """Fill one of the workspace's forms (the primary one unless named) from a stored
    recording, and keep the result as a draft for review."""
    cid = principal.client_id
    row = await _load(job_id, principal, "id, transcript, segments, created_at")
    transcript, segs = _transcript_of(row)
    key = (body.form_key if body else None) or None
    form = await crm_store.get_form(cid, key) if key else await crm_store.primary_form(cid)
    if form is None:
        raise HTTPException(status_code=404, detail="Form not found")
    result = await _run(principal, form["definition"], form_key=form["form_key"],
                        form_version=form["version"], transcript=transcript, segments=segs,
                        call_date=crm_extract.call_date_of(row.get("created_at")))
    return await crm_store.save_result(cid, job_id, result, await _actor_name(principal))


@router.get("/recordings/{job_id}/crm")
async def recording_results(job_id: str, principal: Principal = Depends(_workspace)):
    await _load(job_id, principal, "id")              # 404 for a recording the caller cannot see
    return await crm_store.latest_results(principal.client_id, job_id)


class TestBody(BaseModel):
    definition: dict
    transcript: str


@router.post("/crm/test")
async def test_form(body: TestBody, principal: Principal = Depends(_owner)):
    """The editor's playground: fill a form — saved or not — from pasted text. Nothing is
    stored. Owner-only because it is part of editing the form, and it costs a unit."""
    text = (body.transcript or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Paste a transcript to test the form on.")
    if len(text) > MAX_TEST_CHARS:
        raise HTTPException(status_code=413, detail="The transcript is too long to test with.")
    try:
        defn, _ = crm_form.require_valid(body.definition)
    except crm_form.FormInvalid as exc:
        raise _invalid(exc) from exc
    return await _run(principal, defn, form_key="test", form_version=0, transcript=text,
                      segments=None, call_date=None)


class ReviewBody(BaseModel):
    values: dict = {}
    status: str = "draft"


@router.patch("/crm/results/{result_id}")
async def review(result_id: str, body: ReviewBody, principal: Principal = Depends(_workspace)):
    """A reviewer's values over the model's, as a draft or approved. Any workspace user may
    review — filling the CRM form is the agent's own job, not a setting.

    A PATCH carries only what this reviewer changed: a field it omits keeps the value the
    review already holds, and falls back to the model's guess only where nobody has reviewed
    it yet."""
    cid = principal.client_id
    current = await crm_store.get_result(cid, result_id)
    if current is None:
        raise HTTPException(status_code=404, detail="Result not found")
    edits = body.values or {}
    try:
        final, _shown = crm_extract.apply_review(current["result"], edits)
    except crm_extract.ReviewInvalid as exc:
        raise HTTPException(status_code=422, detail={"message": str(exc), "fields": exc.fields}) from None
    # `apply_review` fills every key it was not given from the MODEL's own value, and
    # `save_review` replaces the whole `final` column, so without this merge a second tab
    # approving a draft it loaded before a colleague's correction wrote that correction back
    # to the model's number — silently, with its own name on `reviewed_by`. The guard used to
    # live in the browser (ResultView merges `row.final` into the body), which is exactly why
    # a stale tab or any server-to-server caller lost the correction.
    stored = current["final"] or {}
    final = {k: (v if k in edits else stored.get(k, v)) for k, v in final.items()}
    try:
        return await crm_store.save_review(cid, result_id, final, body.status,
                                           await _actor_name(principal))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.get("/crm/results/{result_id}/record")
async def result_record(result_id: str, principal: Principal = Depends(_workspace)):
    current = await crm_store.get_result(principal.client_id, result_id)
    if current is None:
        raise HTTPException(status_code=404, detail="Result not found")
    return {**crm_extract.as_record(current["result"], current["final"]),
            "status": current["status"], "job_id": current["job_id"]}


# --------------------------------------------------------------------------- #
# Superadmin: the default form
# --------------------------------------------------------------------------- #
@router.get("/admin/crm/default-form", dependencies=[Depends(_superadmin)])
async def admin_get_default():
    return await crm_store.get_default_form()


@router.put("/admin/crm/default-form", dependencies=[Depends(_superadmin)])
async def admin_put_default(body: SaveBody):
    try:
        form, warnings = await crm_store.set_default_form(body.definition, "superadmin")
    except crm_form.FormInvalid as exc:
        raise _invalid(exc) from exc
    return {**form, "warnings": warnings}


@router.post("/admin/crm/default-form/reset", dependencies=[Depends(_superadmin)])
async def admin_reset_default():
    return await crm_store.reset_default_form()


@router.get("/admin/crm/default-form/export", dependencies=[Depends(_superadmin)])
async def admin_export_default(format: str = Query("json", pattern="^(json|xlsx)$")):
    d = await crm_store.get_default_form()
    return _export(crm_store.DEFAULT_KEY, d["definition"], None, format)
