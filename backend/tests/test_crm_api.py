"""The CRM analyser end to end over HTTP, against a real database.

Skips without Postgres, like the rest of the integration suite. Uses the session's two seeded
tenants (`conftest.seed`), so every assertion about one workspace has a second workspace to
leak into — and the isolation tests below check that it never does, on every route that
takes an id.

The model is a scripted fake (`llm.call_tool` is patched on the module object, which is what
`crm_extract` calls), and `_settings` is patched so the run does not depend on a provider key
being configured in this database.
"""
import asyncio
import io
import json
import uuid

import pytest

from app.config import settings
from app.services import crm_presets, llm

from conftest import sql


def _h(tenant: dict) -> dict:
    return {"X-API-Key": tenant["api_key"]}


ADMIN = {"X-Admin-Token": settings.admin_token}


@pytest.fixture
def scripted_model(monkeypatch):
    """Answer every asked field from a table; record what was asked."""
    from app.routers import crm as crm_router

    calls = []
    answers = {
        "category": ["billing"], "reason": ["Wants to know why the bill doubled"],
        "outcome": ["follow_up"], "priority": ["high"], "reference": ["INV-2231"],
        "next_step": ["Billing team calls back"], "due_date": ["2026-11-05"],
        "summary": ["Customer disputes the November invoice; billing will call back."],
    }

    async def fake(**kw):
        keys = kw["tool"]["input_schema"]["properties"]["fields"]["items"]["properties"]["key"]["enum"]
        calls.append(keys)
        return {"fields": [{"key": k, "values": answers.get(k, []), "slots": [], "confidence": "high",
                            "evidence": [{"quote": "the bill doubled", "segments": [0]}] if k in answers else []}
                           for k in keys]}

    async def fake_settings(*needs):
        return {"anthropic_api_key": "test-key", "llm_model": "test-model"}

    monkeypatch.setattr(llm, "call_tool", fake)
    monkeypatch.setattr(crm_router, "_settings", fake_settings)
    return calls


def _recording(client_id: str) -> str:
    async def _ins(conn):
        return await conn.fetchval(
            """INSERT INTO audio_jobs (client_id, status, transcript, source, filename, principal_type)
               VALUES ($1, 'ready', $2, 'text', 'transcript.txt', 'tenant') RETURNING id""",
            uuid.UUID(client_id), "speaker_1: Why has my bill doubled? Invoice INV-2231.\nspeaker_0: I will ask billing to call you back by Thursday.")
    return str(sql(_ins))


# --------------------------------------------------------------------------- forms
def test_a_workspace_without_forms_sees_the_default(api, seed):
    r = api.get("/crm/forms", headers=_h(seed["b"]))
    assert r.status_code == 200
    forms = r.json()
    assert len(forms) == 1 and forms[0]["is_default"] and forms[0]["form_key"] == "default"
    assert forms[0]["summary"]["fields"] > 0 and "definition" not in forms[0]
    full = api.get("/crm/forms/default", headers=_h(seed["b"])).json()
    assert full["version"] == 0 and full["definition"]["fields"]


def test_create_save_version_primary_delete(api, seed):
    h = _h(seed["a"])
    r = api.post("/crm/forms", headers=h, json={"preset": "insurance_ka", "name": "Insurance"})
    assert r.status_code == 200, r.text
    key = r.json()["form"]["form_key"]
    assert key == "insurance" and r.json()["form"]["version"] == 1

    # The default this workspace was on is copied in beside the new form, and stays primary.
    forms = {f["form_key"]: f for f in api.get("/crm/forms", headers=h).json()}
    assert set(forms) >= {"default", "insurance"}
    assert forms["default"]["is_primary"] and not forms["default"]["is_default"]

    defn = api.get(f"/crm/forms/{key}", headers=h).json()["definition"]
    bad = {**defn, "fields": defn["fields"] + [{"key": "x", "when": "nope is filled"}]}
    r = api.put(f"/crm/forms/{key}", headers=h, json={"definition": bad})
    assert r.status_code == 422
    assert any("nope" in e["message"] for e in r.json()["detail"]["errors"])

    defn["description"] = "edited"
    r = api.put(f"/crm/forms/{key}", headers=h, json={"definition": defn})
    assert r.status_code == 200 and r.json()["form"]["version"] == 2
    assert [v["version"] for v in api.get(f"/crm/forms/{key}/versions", headers=h).json()] == [2, 1]

    assert api.post(f"/crm/forms/{key}/primary", headers=h).status_code == 200
    forms = {f["form_key"]: f for f in api.get("/crm/forms", headers=h).json()}
    assert forms[key]["is_primary"] and not forms["default"]["is_primary"]

    assert api.delete(f"/crm/forms/{key}", headers=h).status_code == 200
    forms = {f["form_key"]: f for f in api.get("/crm/forms", headers=h).json()}
    assert key not in forms and forms["default"]["is_primary"]
    # A deleted key is never reused: its history still owns it.
    again = api.post("/crm/forms", headers=h, json={"preset": "insurance_ka", "name": "Insurance"}).json()
    assert again["form"]["form_key"] == "insurance_2"


def test_export_and_import_round_trip(api, seed):
    h = _h(seed["a"])
    key = api.post("/crm/forms", headers=h, json={"preset": "generic_en", "name": "Roundtrip"}).json()["form"]["form_key"]

    for fmt in ("json", "xlsx"):
        r = api.get(f"/crm/forms/{key}/export?format={fmt}", headers=h)
        assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
        files = {"file": (f"form.{fmt}", io.BytesIO(r.content))}
        pv = api.post("/crm/import/preview", headers=h, files=files)
        assert pv.status_code == 200, pv.text
        form = pv.json()["forms"][0]
        assert form["key"] == key and form["exists"] and form["errors"] == []
        d = form["diff"]
        assert not (d["added"] or d["removed"] or d["changed"] or d["settings"])

    r = api.post("/crm/import", headers=h, json={"items": [
        {"definition": form["definition"], "mode": "replace", "key": key},
        {"definition": form["definition"], "mode": "new"}]})
    assert r.status_code == 200, r.text
    assert [s["version"] for s in r.json()["saved"]] == [2, 1]

    bundle = api.get("/crm/export", headers=h)
    assert json.loads(bundle.content)["format"] == "cq.crm-forms"


def test_import_is_all_or_nothing(api, seed):
    h = _h(seed["a"])
    before = len(api.get("/crm/forms", headers=h).json())
    good = crm_presets.preset("generic_en")
    r = api.post("/crm/import", headers=h, json={"items": [
        {"definition": good, "mode": "new"}, {"definition": {"name": "", "fields": []}, "mode": "new"}]})
    assert r.status_code == 422
    assert len(api.get("/crm/forms", headers=h).json()) == before


def test_preview_explains_a_file_that_is_not_a_form(api, seed):
    r = api.post("/crm/import/preview", headers=_h(seed["a"]),
                 files={"file": ("x.json", io.BytesIO(b'{"hello": 1}'))})
    assert r.status_code == 400 and "form definition" in r.json()["detail"]


def test_a_file_nested_too_deeply_is_a_clean_400(api, seed):
    """`RecursionError` is a RuntimeError, so the decoder's depth limit used to escape the
    JSONDecodeError guard and answer 500 — on a 6 KB file."""
    deep = b"[" * 3000 + b"]" * 3000
    r = api.post("/crm/import/preview", headers=_h(seed["a"]),
                 files={"file": ("deep.json", io.BytesIO(deep))})
    assert r.status_code == 400, r.text
    assert "deeply" in r.json()["detail"]


def test_preview_refuses_a_bundle_import_would_not_take(api, seed):
    """Preview and apply share one ceiling: an all-green preview the apply route then
    refuses is the divergence the preview exists to prevent."""
    one = crm_presets.preset("generic_en")
    bundle = {"format": "cq.crm-forms", "forms": [{"key": f"f{i}", "form": one} for i in range(21)]}
    r = api.post("/crm/import/preview", headers=_h(seed["a"]),
                 files={"file": ("many.json", io.BytesIO(json.dumps(bundle).encode()))})
    assert r.status_code == 400, r.text
    assert "21" in r.json()["detail"] and "20" in r.json()["detail"]
    apply = api.post("/crm/import", headers=_h(seed["a"]),
                     json={"items": [{"definition": one, "mode": "new"} for _ in range(21)]})
    assert apply.status_code == 400 and "20" in apply.json()["detail"]


def test_a_workbook_is_parsed_off_the_event_loop(api, seed, monkeypatch):
    """openpyxl is synchronous and a hostile workbook costs seconds of it, so the parse must
    not run on the one worker's event loop: in a thread there is no running loop to find."""
    from app.services import crm_xlsx

    seen = {}

    def fake_parse(data):
        try:
            asyncio.get_running_loop()
            seen["on_loop"] = True
        except RuntimeError:
            seen["on_loop"] = False
        return "book", crm_presets.preset("generic_en")

    monkeypatch.setattr(crm_xlsx, "parse_xlsx", fake_parse)
    r = api.post("/crm/import/preview", headers=_h(seed["a"]),
                 files={"file": ("f.xlsx", io.BytesIO(b"PK\x03\x04 not really a workbook"))})
    assert r.status_code == 200, r.text
    assert seen == {"on_loop": False}


def test_a_definition_named_with_a_number_is_validated_not_crashed(api, seed):
    """The form key is slugged from the RAW request body, one line before the validator that
    would have made the name a string; `slugify` used to raise TypeError on anything else."""
    r = api.post("/crm/forms", headers=_h(seed["a"]), json={"definition": {
        "name": 123, "fields": [{"key": "a", "label": "A", "type": "text"}]}})
    assert r.status_code == 200, r.text
    assert r.json()["form"]["name"] == "123"


# --------------------------------------------------------------------------- filling + review
def test_fill_review_and_record(api, seed, scripted_model):
    h = _h(seed["b"])
    job = _recording(seed["b"]["client_id"])
    r = api.post(f"/recordings/{job}/crm", headers=h, json={})
    assert r.status_code == 200, r.text
    row = r.json()
    res = row["result"]
    fields = {f["key"]: f for f in res["fields"]}
    assert row["status"] == "draft" and row["label"] == "billing" and row["form_key"] == "default"
    assert fields["due_date"]["value"] == "2026-11-05"
    assert fields["escalated_to"]["status"] == "not_applicable"     # outcome is follow_up
    assert fields["customer_id"]["status"] == "external"
    assert res["missing_required"] == []
    # Two passes: the fields that wait on `outcome` were asked second.
    assert len(scripted_model) == 2 and "due_date" in scripted_model[1] and "outcome" in scripted_model[0]

    listed = api.get(f"/recordings/{job}/crm", headers=h).json()
    assert [x["id"] for x in listed] == [row["id"]]
    ran = {x["id"]: x["ran"] for x in api.get("/recordings", headers=h).json()}
    assert ran[job]["crm"] is True

    bad = api.patch(f"/crm/results/{row['id']}", headers=h, json={"values": {"due_date": "someday"}})
    assert bad.status_code == 422 and bad.json()["detail"]["fields"] == ["due_date"]

    ok = api.patch(f"/crm/results/{row['id']}", headers=h,
                   json={"values": {"category": "Complaint", "due_date": "06.11.2026"}, "status": "approved"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "approved" and ok.json()["label"] == "complaint"
    assert ok.json()["final"]["due_date"] == "2026-11-06"

    rec = api.get(f"/crm/results/{row['id']}/record", headers=h).json()
    assert rec["label"] == "complaint" and rec["values"]["reference"] == "INV-2231" and rec["status"] == "approved"


def test_a_second_reviewer_does_not_revert_the_first(api, seed, scripted_model):
    """A PATCH that omits a field keeps the stored review, not the model's guess: two tabs
    on one recording is the normal case, and the second one approving a draft it loaded
    first used to write the first reviewer's correction back to the model's answer."""
    h = _h(seed["b"])
    job = _recording(seed["b"]["client_id"])
    row = api.post(f"/recordings/{job}/crm", headers=h, json={}).json()
    assert {f["key"]: f for f in row["result"]["fields"]}["due_date"]["value"] == "2026-11-05"

    first = api.patch(f"/crm/results/{row['id']}", headers=h, json={"values": {"due_date": "06.11.2026"}})
    assert first.status_code == 200 and first.json()["final"]["due_date"] == "2026-11-06"

    second = api.patch(f"/crm/results/{row['id']}", headers=h, json={"status": "approved"})
    assert second.status_code == 200, second.text
    assert second.json()["status"] == "approved"
    assert second.json()["final"]["due_date"] == "2026-11-06"
    rec = api.get(f"/crm/results/{row['id']}/record", headers=h).json()
    assert rec["values"]["due_date"] == "2026-11-06"

    # A value the later reviewer does send still wins, and still wins over the model's.
    third = api.patch(f"/crm/results/{row['id']}", headers=h, json={"values": {"due_date": "2026-11-07"}})
    assert third.status_code == 200 and third.json()["final"]["due_date"] == "2026-11-07"
    assert third.json()["final"]["reference"] == "INV-2231"


def test_the_test_run_stores_nothing(api, seed, scripted_model):
    h = _h(seed["b"])
    r = api.post("/crm/test", headers=h, json={"definition": crm_presets.preset("generic_en"),
                                               "transcript": "speaker_1: my bill doubled"})
    assert r.status_code == 200 and r.json()["form_key"] == "test"
    assert sql(lambda c: c.fetchval("SELECT count(*) FROM crm_results WHERE form_key = 'test'")) == 0


# --------------------------------------------------------------------------- isolation
def test_a_workspace_never_reaches_anothers_forms_or_results(api, seed, scripted_model):
    a, b = _h(seed["a"]), _h(seed["b"])
    key = api.post("/crm/forms", headers=a, json={"preset": "generic_en", "name": "Private A"}).json()["form"]["form_key"]
    assert key not in {f["form_key"] for f in api.get("/crm/forms", headers=b).json()}
    assert api.get(f"/crm/forms/{key}", headers=b).status_code == 404
    assert api.put(f"/crm/forms/{key}", headers=b, json={"definition": crm_presets.preset("generic_en")}).status_code == 404
    assert api.delete(f"/crm/forms/{key}", headers=b).status_code == 404
    assert api.get(f"/crm/forms/{key}/export", headers=b).status_code == 404

    job = _recording(seed["a"]["client_id"])
    row = api.post(f"/recordings/{job}/crm", headers=a, json={"form_key": key}).json()
    assert api.post(f"/recordings/{job}/crm", headers=b, json={}).status_code == 404
    assert api.get(f"/recordings/{job}/crm", headers=b).status_code == 404
    assert api.patch(f"/crm/results/{row['id']}", headers=b, json={"values": {}}).status_code == 404
    assert api.get(f"/crm/results/{row['id']}/record", headers=b).status_code == 404
    # B naming A's form key fills nothing of A's: it is "not found" in B's workspace.
    job_b = _recording(seed["b"]["client_id"])
    assert api.post(f"/recordings/{job_b}/crm", headers=b, json={"form_key": key}).status_code == 404


def test_who_may_do_what(api, seed):
    assert api.get("/crm/forms").status_code == 401
    assert api.get("/crm/forms", headers=ADMIN).status_code == 401          # no workspace chosen
    assert api.get("/crm/presets", headers=ADMIN).status_code == 200
    assert api.post("/crm/validate", headers=ADMIN, json={"definition": {}}).json()["errors"]
    assert api.get("/admin/crm/default-form", headers=_h(seed["a"])).status_code == 403
    integ = {"X-CQ-Key": seed["integration"]["api_key"], "X-CQ-Tenant": seed["a"]["client_id"]}
    assert api.get("/crm/forms", headers=integ).status_code in (401, 403)


# --------------------------------------------------------------------------- the default form
def test_superadmin_default_form(api, seed):
    try:
        cur = api.get("/admin/crm/default-form", headers=ADMIN).json()
        assert cur["definition"]["fields"]
        mine = {**crm_presets.preset("generic_ka"), "name": "Stored default"}
        r = api.put("/admin/crm/default-form", headers=ADMIN, json={"definition": mine})
        assert r.status_code == 200 and r.json()["source"] == "stored"
        # A workspace still on the default sees the stored one.
        fresh = sql(lambda c: c.fetchval(
            "INSERT INTO clients (slug, name, api_key) VALUES ($1,$1,$1) RETURNING id", f"crmdef-{uuid.uuid4().hex[:8]}"))
        try:
            key = sql(lambda c: c.fetchval("SELECT api_key FROM clients WHERE id = $1", fresh))
            forms = api.get("/crm/forms", headers={"X-API-Key": key}).json()
            assert forms[0]["name"] == "Stored default"
        finally:
            sql(lambda c: c.execute("DELETE FROM clients WHERE id = $1", fresh))
        x = api.get("/admin/crm/default-form/export?format=xlsx", headers=ADMIN)
        assert x.status_code == 200 and x.content[:2] == b"PK"
    finally:
        assert api.post("/admin/crm/default-form/reset", headers=ADMIN).json()["source"] == "builtin"
