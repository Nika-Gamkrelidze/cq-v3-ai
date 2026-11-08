"""The CRM analyser's run: pass planning, what each prompt contains, and how answers become
values — with `llm.call_tool` replaced by a scripted fake, so no key and no network."""
import asyncio
from datetime import date

import pytest

from app.services import crm_extract, crm_presets, llm
from app.services.crm_form import normalize_definition
from app.services.providers.llm_base import translate_schema

TRANSCRIPT = "\n".join([
    "speaker_0: გამარჯობა, რით შემიძლია დაგეხმაროთ?",
    "speaker_1: მინდა გავიგო, ფიზიოთერაპიას რამდენით ფარავს პოლისი, ევექსის კლინიკაში ვაპირებ.",
    "speaker_0: გეგმიური ამბულატორია ფინანსდება 80%-ით პროვაიდერ კლინიკაში.",
    "speaker_1: გმადლობთ.",
])


def _insurance():
    defn, errors, _ = normalize_definition(crm_presets.preset("insurance_ka"))
    assert not errors
    return defn


def test_insurance_form_needs_two_passes():
    passes = crm_extract.plan_passes(_insurance())
    assert len(passes) == 2
    first, second = map(set, passes)
    assert {"request", "reaction", "provider", "issued_group", "requested_info"} <= first
    assert {"clinic", "issued_scenario", "issued_info", "forwarded_to"} <= second
    # Derived and external fields are never asked.
    assert not ({"category", "subcategory", "insurance_type"} & (first | second))


def test_a_form_without_dependencies_is_one_call():
    defn, _, _ = normalize_definition({"name": "x", "fields": [{"key": "a"}, {"key": "b", "type": "number"}]})
    assert crm_extract.plan_passes(defn) == [["a", "b"]]


def test_the_tool_schema_translates_for_every_provider():
    tool = crm_extract._tool(["a", "b"])
    for dialect in ("anthropic", "openai", "gemini"):
        translate_schema(tool["input_schema"], dialect)


class FakeModel:
    """Answers each pass from a table keyed by field, and records what it was asked."""

    def __init__(self, answers: dict):
        self.answers = answers
        self.calls: list[dict] = []

    async def __call__(self, **kw):
        keys = kw["tool"]["input_schema"]["properties"]["fields"]["items"]["properties"]["key"]["enum"]
        self.calls.append({"keys": keys, **kw})
        out = []
        for k in keys:
            a = self.answers.get(k)
            if a is None:
                out.append({"key": k, "values": [], "slots": [], "confidence": "low", "evidence": []})
            else:
                out.append({"key": k, "values": a.get("values", []), "slots": a.get("slots", []),
                            "confidence": "high", "evidence": [{"quote": "80%-ით", "segments": [2]}]})
        return {"fields": out}


@pytest.fixture
def fake(monkeypatch):
    def install(answers):
        model = FakeModel(answers)
        monkeypatch.setattr(llm, "call_tool", model)
        return model
    return install


def _run(defn, **kw):
    return asyncio.run(crm_extract.fill(
        defn, form_key="ins", form_version=3, transcript=TRANSCRIPT, segments=None,
        api_key="k", model="m", client_id="c", call_date=date(2026, 11, 3), **kw))


def test_full_insurance_fill(fake):
    model = fake({
        "request": {"values": ["planned_outpatient"]},
        "reaction": {"values": ["first_call"]},
        "provider": {"values": ["სხვა"]},                  # a label, matched to its key
        "issued_group": {"values": ["informed"]},
        "state_insurance_informed": {"values": ["no"]},
        "requested_info": {"slots": [{"name": "მომსახურება", "value": "ფიზიოთერაპია"},
                                     {"name": "კლინიკა", "value": "ევექსი"},
                                     {"name": "საკითხი", "value": "დაფარვის %"}]},
        "clinic": {"values": ["ევექსი"]},
        "coverage_percent": {"values": ["80"]},
        "issued_scenario": {"values": ["po_informed"]},
        "issued_info": {"slots": [{"name": "მომსახურება", "value": "ფიზიოთერაპია"},
                                  {"name": "პროცენტი", "value": "80"},
                                  {"name": "კლინიკა", "value": ""}, {"name": "სქემა", "value": ""}]},
    })
    res = _run(_insurance())
    assert res["model_calls"] == 2 and len(model.calls) == 2
    f = {x["key"]: x for x in res["fields"]}

    # implied by the chosen request
    assert f["category"]["value"] == "medical" and f["category"]["source"] == "derived"
    assert f["subcategory"]["display"] == "ინფორმაციული"
    # the CRM fills it; never asked
    assert f["insurance_type"]["status"] == "external"
    assert f["provider"]["value"] == "other"
    assert f["clinic"]["status"] == "filled"
    assert f["state_insurance_informed"]["value"] is False
    # templates rendered by code; optional pieces with empty slots dropped
    assert f["requested_info"]["value"] == ("დაზღვეულს სურს გეგმიური ამბულატორიული მომსახურება: "
                                            "ფიზიოთერაპია, ევექსი; აინტერესებს დაფარვის %.")
    assert f["issued_info"]["value"] == "ეცნო გეგმიური ამბულატორიის ქმედება: ფიზიოთერაპია ფინანსდება 80%-ით."
    # conditions that turned out false
    assert f["application_number"]["status"] == "not_applicable"
    assert f["forwarded_to"]["status"] == "not_applicable"
    assert f["appointment_at"]["status"] == "not_applicable"
    assert res["label"]["key"] == "planned_outpatient"
    assert res["missing_required"] == []
    assert f["coverage_percent"]["evidence"][0]["quote"] == "80%-ით"

    # Pass 2 offers only the scenarios under the chosen request, and skips fields whose
    # condition is already known to be false.
    second = model.calls[1]
    assert "po_informed" in second["user"] and "or_status" not in second["user"]
    assert "application_number" not in second["keys"] and "forwarded_to" not in second["keys"]
    assert "Already established" in second["user"] and "planned_outpatient" in second["user"]
    # the transcript is in the cached system block, the same for both passes
    assert model.calls[0]["system"] == second["system"] and "<transcript>" in second["system"]
    assert second["cache_system"] is True and second["feature"] == crm_extract.FEATURE
    assert "2026-11-03" in second["system"]


def test_unanswered_required_fields_are_flagged_and_invalid_answers_kept(fake):
    fake({"request": {"values": ["something else entirely"]}, "reaction": {"values": ["first_call"]}})
    res = _run(_insurance())
    f = {x["key"]: x for x in res["fields"]}
    assert f["request"]["status"] == "invalid" and f["request"]["raw"] == "something else entirely"
    assert "request" in res["missing_required"] and "provider" in res["missing_required"]
    assert f["category"]["status"] == "empty"      # nothing chose a request, so nothing implied it
    assert res["label"] is None


def test_model_failure_is_a_crm_error(monkeypatch):
    async def boom(**kw):
        raise llm.LLMError("upstream down")
    monkeypatch.setattr(llm, "call_tool", boom)
    with pytest.raises(crm_extract.CrmError, match="upstream down"):
        _run(_insurance())


def test_empty_transcript_is_refused():
    with pytest.raises(crm_extract.CrmError):
        asyncio.run(crm_extract.fill(_insurance(), form_key="k", form_version=1, transcript="  ",
                                     segments=None, api_key="k", model="m", client_id=None))


def test_review_coerces_and_names_bad_values(fake):
    fake({"request": {"values": ["planned_outpatient"]}, "reaction": {"values": ["first_call"]}})
    res = _run(_insurance())
    final, shown = crm_extract.apply_review(res, {"request": "საგარანტიო წერილი", "coverage_percent": "90%"})
    assert final["request"] == "guarantee_letter" and final["coverage_percent"] == 90
    assert final["reaction"] == "first_call"               # untouched → the model's value
    assert shown["coverage_percent"] == "90%"
    with pytest.raises(crm_extract.ReviewInvalid, match="whenever") as bad:
        crm_extract.apply_review(res, {"appointment_at": "whenever"})
    assert bad.value.fields == ["appointment_at"]
    with pytest.raises(crm_extract.ReviewInvalid, match="not a field"):
        crm_extract.apply_review(res, {"nope": "x"})
    record = crm_extract.as_record(res, final)
    assert record["label"] == "guarantee_letter" and record["values"]["request"] == "guarantee_letter"


def test_a_bare_string_answer_is_one_value_not_one_character(fake):
    # `values` as a plain string instead of a one-item array used to be iterated per CHARACTER:
    # "80" coerced to 8, an option key to its first letter, both stored as confidently filled.
    # `slots` as a {name: value} mapping iterated to its keys and left the template empty.
    fake({
        "request": {"values": "planned_outpatient"},
        "reaction": {"values": "first_call"},
        "provider": {"values": "სხვა"},
        "issued_group": {"values": "informed"},
        "clinic": {"values": "ევექსი"},
        "coverage_percent": {"values": "80"},
        "requested_info": {"slots": {"მომსახურება": "ფიზიოთერაპია", "საკითხი": "დაფარვის %"}},
    })
    res = _run(_insurance())
    f = {x["key"]: x for x in res["fields"]}
    assert f["request"]["value"] == "planned_outpatient" and f["request"]["status"] == "filled"
    assert f["provider"]["value"] == "other"
    assert f["coverage_percent"]["value"] == 80
    assert f["clinic"]["value"] == "ევექსი"
    assert f["requested_info"]["value"] == ("დაზღვეულს სურს გეგმიური ამბულატორიული მომსახურება: "
                                            "ფიზიოთერაპია; აინტერესებს დაფარვის %.")


def test_a_template_with_no_slots_is_still_rendered(fake):
    # `po_tech`'s sentence is complete as the tenant wrote it, so an obedient model returns no
    # slots at all — and the field used to come back empty and flagged required.
    fake({
        "request": {"values": ["planned_outpatient"]},
        "reaction": {"values": ["first_call"]},
        "provider": {"values": ["not_needed"]},
        "issued_group": {"values": ["tech_team"]},
        "issued_scenario": {"values": ["po_tech"]},
    })
    res = _run(_insurance())
    f = {x["key"]: x for x in res["fields"]}
    assert f["issued_info"]["value"] == ("მოთხოვნა გადაეგზავნა ტექნიკურ ჯგუფს ელ-ფოსტით. "
                                         "დაზღვეულს ეცნობება შედეგის შესახებ.")
    assert f["issued_info"]["status"] == "filled"
    assert "issued_info" not in res["missing_required"]


def test_slots_for_another_template_are_kept_raw_not_rendered_as_placeholders(fake):
    # The model filled the slots of the scenario it had in mind but left the scenario itself
    # unanswered, so the field falls back to its own template — whose slot names are different.
    # Rendering it would store "დაზღვეულს ეცნო [თემა]: [ინფორმაცია]." as a filled required field.
    fake({
        "request": {"values": ["planned_outpatient"]},
        "reaction": {"values": ["first_call"]},
        "provider": {"values": ["not_needed"]},
        "issued_group": {"values": ["informed"]},
        "issued_info": {"slots": [{"name": "პროცენტი", "value": "80"}]},
    })
    res = _run(_insurance())
    f = {x["key"]: x for x in res["fields"]}
    assert f["issued_info"]["status"] == "invalid" and f["issued_info"]["value"] is None
    assert "[თემა]" not in f["issued_info"]["display"]
    assert f["issued_info"]["raw"] == "პროცენტი: 80"
    assert "issued_info" in res["missing_required"]
