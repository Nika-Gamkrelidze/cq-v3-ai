"""The CRM form definition: validation, the option tree, conditions, templates, values, and
the two file formats. Pure — no database, no model."""
import json
import time

import pytest

from app.services import crm_form, crm_presets, crm_xlsx
from app.services.crm_form import (apply_implies, applies, coerce, normalize_definition,
                                   offered_options, parse_when_text, render_template)


def _form(*fields, **extra):
    return {"name": "T", "fields": list(fields), **extra}


def _ok(raw):
    defn, errors, warnings = normalize_definition(raw)
    assert errors == [], errors
    return defn


def _errors(raw):
    return [e["message"] for e in normalize_definition(raw)[1]]


# --------------------------------------------------------------------------- presets
@pytest.mark.parametrize("pid", sorted(crm_presets.PRESETS))
def test_every_preset_is_valid_and_warning_free(pid):
    defn, errors, warnings = normalize_definition(crm_presets.preset(pid))
    assert errors == [] and warnings == []
    assert defn["label_field"]


@pytest.mark.parametrize("pid", sorted(crm_presets.PRESETS))
def test_normalising_twice_changes_nothing(pid):
    once = _ok(crm_presets.preset(pid))
    assert _ok(json.loads(json.dumps(once))) == once


def test_the_builtin_default_is_a_preset():
    assert crm_presets.BUILTIN_DEFAULT is crm_presets.PRESETS["generic_en"]["form"]


def test_preset_returns_a_copy():
    a = crm_presets.preset("insurance_ka")
    a["fields"].clear()
    assert crm_presets.preset("insurance_ka")["fields"]


# --------------------------------------------------------------------------- validation
def test_a_form_needs_a_name_and_a_field():
    msgs = _errors({"fields": []})
    assert any("name" in m for m in msgs) and any("at least one field" in m for m in msgs)


def test_keys_are_checked_and_unique():
    assert any("must start with a letter" in m for m in _errors(_form({"key": "1x", "label": "A"})))
    assert any("Duplicate field key" in m for m in _errors(_form({"key": "a"}, {"key": "a"})))


def test_crm_style_keys_are_allowed():
    _ok(_form({"key": "REQ_TYPE.custom-12", "label": "x"}))


def test_a_missing_key_is_generated_with_a_warning():
    defn, errors, warnings = normalize_definition(_form({"label": "Customer name"}, {"label": "ტელეფონი"}))
    assert errors == []
    assert [f["key"] for f in defn["fields"]] == ["customer_name", "field_2"]
    assert len(warnings) == 2


def test_type_aliases_and_unknown_types():
    defn = _ok(_form({"key": "a", "type": "select", "options": ["x"]}, {"key": "b", "type": "bool"}))
    assert [f["type"] for f in defn["fields"]] == ["choice", "boolean"]
    assert any("Unknown type" in m for m in _errors(_form({"key": "a", "type": "colour"})))


def test_choice_needs_options_unless_external():
    assert any("at least one option" in m for m in _errors(_form({"key": "a", "type": "choice"})))
    _ok(_form({"key": "a", "type": "choice", "source": "external"}))


def test_option_tree_parent_cycle_and_depth():
    bad_parent = _form({"key": "a", "type": "choice", "options": [{"key": "x", "parent": "nope"}]})
    assert any("not an option" in m for m in _errors(bad_parent))
    cycle = _form({"key": "a", "type": "choice",
                   "options": [{"key": "x", "parent": "y"}, {"key": "y", "parent": "x"}]})
    assert any("own ancestor" in m for m in _errors(cycle))
    deep = [{"key": "o0"}] + [{"key": f"o{i}", "parent": f"o{i - 1}"} for i in range(1, 7)]
    assert any("levels deep" in m for m in _errors(_form({"key": "a", "type": "choice", "options": deep})))


def test_condition_references_are_checked():
    f = _form({"key": "a", "type": "choice", "options": ["x"]},
              {"key": "b", "when": {"rules": [{"field": "a", "op": "is", "value": "nope"}]}},
              {"key": "c", "when": {"rules": [{"field": "zzz", "op": "filled"}]}})
    msgs = _errors(f)
    assert any("`nope` is not an option of `a`" in m for m in msgs)
    assert any("`zzz`" in m for m in msgs)


def test_condition_cycles_are_refused():
    f = _form({"key": "a", "when": "b is filled"}, {"key": "b", "when": "a is filled"})
    assert any("circle" in m for m in _errors(f))


def test_implies_is_coerced_and_checked():
    defn = _ok(_form(
        {"key": "req", "type": "choice", "options": [{"key": "x", "implies": {"cat": "Medical", "n": "5"}}]},
        {"key": "cat", "type": "choice", "source": "derived", "options": [{"key": "med", "label": "Medical"}]},
        {"key": "n", "type": "number", "source": "derived"}))
    assert defn["fields"][0]["options"][0]["implies"] == {"cat": "med", "n": 5}
    bad = _form({"key": "req", "type": "choice", "options": [{"key": "x", "implies": {"cat": "nope"}}]},
                {"key": "cat", "type": "choice", "source": "derived", "options": ["med"]})
    assert any("not a valid value" in m for m in _errors(bad))


def test_an_implies_row_with_no_value_is_dropped_and_warned_about():
    defn, errors, warnings = normalize_definition(_form(
        {"key": "req", "type": "choice", "options": [
            {"key": "med", "implies": {"dept": "m"}},
            {"key": "planned", "parent": "med", "implies": {"dept": None}}]},
        {"key": "dept", "type": "choice", "source": "derived", "options": ["m"]}))
    assert errors == []
    assert any("Implies no value" in w["message"] for w in warnings)
    assert defn["fields"][0]["options"][1]["implies"] == {}
    values, sources = {"req": "planned"}, {}
    apply_implies(defn, values, sources)
    assert values["dept"] == "m"           # the half-finished row does not erase the parent's


def test_a_blank_implies_no_longer_hides_the_derived_warning():
    defn, errors, warnings = normalize_definition(_form(
        {"key": "req", "type": "choice", "options": [{"key": "x", "implies": {"dept": ""}}]},
        {"key": "dept", "source": "derived"}))
    assert errors == []
    assert any("always be empty" in w["message"] for w in warnings)


def test_template_rules():
    assert any("needs a template" in m for m in _errors(_form({"key": "t", "type": "template"})))
    assert any("must be `ai`" in m for m in _errors(_form({"key": "t", "type": "template",
                                                            "template": "{x}", "source": "fixed",
                                                            "default": "a"})))


def test_fixed_needs_a_valid_default():
    assert any("needs a default" in m for m in _errors(_form({"key": "a", "source": "fixed"})))
    assert any("not a valid number" in m for m in _errors(_form({"key": "a", "type": "number",
                                                                  "source": "fixed", "default": "abc"})))


def test_limits():
    many = _form(*[{"key": f"f{i}"} for i in range(crm_form.MAX_FIELDS + 1)])
    assert any("At most" in m for m in _errors(many))


def _keywords(defn):
    return defn["fields"][0]["options"][0]["keywords"]


def test_keywords_keep_the_first_of_each_in_the_order_they_were_written():
    defn = _ok(_form({"key": "a", "type": "choice",
                      "options": [{"key": "x", "keywords": ["b", "a", "b", " a ", ""]}]}))
    assert _keywords(defn) == ["b", "a"]
    assert _keywords(_ok(_form({"key": "a", "type": "choice",
                                "options": [{"key": "x", "keywords": "b\na;b"}]}))) == ["b", "a"]


def test_a_huge_keywords_cell_costs_the_cap_not_the_cell():
    # One untrusted spreadsheet cell: de-duplicated against a list and truncated only at the
    # end, 60 000 lines took ~40 s of the api's single event loop to keep forty keywords.
    cell = "\n".join(f"word{i}" for i in range(60_000))
    start = time.perf_counter()
    defn = _ok(_form({"key": "a", "type": "choice", "options": [{"key": "x", "keywords": cell}]}))
    elapsed = time.perf_counter() - start
    assert _keywords(defn) == [f"word{i}" for i in range(crm_form.MAX_KEYWORDS)]
    assert elapsed < 1.0, f"normalising one oversized cell took {elapsed:.1f}s"


def test_label_field_defaults_to_first_choice_and_must_be_a_choice():
    defn = _ok(_form({"key": "a"}, {"key": "b", "type": "choice", "options": ["x"]}))
    assert defn["label_field"] == "b"
    assert any("label_field" in m for m in _errors(_form({"key": "a"}, label_field="a")))


def test_filter_by_must_name_another_choice():
    assert any("filter_by" in m for m in _errors(
        _form({"key": "a", "type": "choice", "options": ["x"], "filter_by": "a"})))


def test_one_renamed_branch_is_warned_about_beside_its_matching_siblings():
    _defn, errors, warnings = normalize_definition(_form(
        {"key": "req", "type": "choice", "options": [
            {"key": "medical", "label": "Medical"}, {"key": "fin", "label": "Finance"}]},
        {"key": "sub", "type": "choice", "filter_by": "req", "options": [
            {"key": "med", "label": "Medical"}, {"key": "a", "label": "A", "parent": "med"},
            {"key": "fin", "label": "Finance"}, {"key": "b", "label": "B", "parent": "fin"}]}))
    assert errors == []
    assert [w["path"] for w in warnings if "`med`" in w["message"]] == ["fields[1].filter_by"]


# --------------------------------------------------------------------------- tree + conditions
_TREE = _ok(_form(
    {"key": "req", "type": "choice", "options": [
        {"key": "med", "label": "Medical"},
        {"key": "outpatient", "label": "Outpatient", "parent": "med"},
        {"key": "dental", "label": "Dental", "parent": "med"},
        {"key": "travel", "label": "Travel"}]},
    {"key": "clinic", "when": {"rules": [{"field": "req", "op": "is", "value": "med"}]}},
    {"key": "scenario", "type": "choice", "filter_by": "req", "options": [
        {"key": "med", "label": "Medical scenarios"},
        {"key": "m1", "label": "Coverage", "parent": "med"},
        {"key": "travel", "label": "Travel scenarios"},
        {"key": "t1", "label": "Lost luggage", "parent": "travel"}]}))
_BY = {f["key"]: f for f in _TREE["fields"]}


def test_option_path_reads_the_branch():
    assert crm_form.option_path(_BY["req"], "dental") == "Medical › Dental"


def test_a_rule_on_a_parent_matches_its_descendants():
    assert applies(_BY["clinic"], _BY, {"req": "outpatient"})
    assert not applies(_BY["clinic"], _BY, {"req": "travel"})
    assert not applies(_BY["clinic"], _BY, {})


def test_dependent_dropdown_offers_only_the_chosen_branch():
    keys = [o["key"] for o in offered_options(_BY["scenario"], _BY, {"req": "dental"})]
    assert keys == ["med", "m1"]
    assert len(offered_options(_BY["scenario"], _BY, {})) == 4


def test_any_vs_all_and_empty_ops():
    defn = _ok(_form({"key": "a"}, {"key": "b"},
                     {"key": "c", "when": "any: a is filled; b = x"},
                     {"key": "d", "when": "a is empty; b != x"}))
    by = {f["key"]: f for f in defn["fields"]}
    assert applies(by["c"], by, {"b": "X"})
    assert applies(by["d"], by, {"b": "y"})
    assert not applies(by["d"], by, {"a": "1"})


def test_implies_fill_derived_always_and_others_only_when_empty():
    defn = _ok(_form(
        {"key": "req", "type": "choice", "options": [
            {"key": "med", "implies": {"cat": "m", "prio": "high"}},
            {"key": "out", "parent": "med", "implies": {"cat": "o"}}]},
        {"key": "cat", "type": "choice", "source": "derived", "options": ["m", "o"]},
        {"key": "prio", "type": "choice", "options": ["low", "high"]}))
    values, sources = {"req": "out", "cat": "stale", "prio": "low"}, {}
    apply_implies(defn, values, sources)
    assert values["cat"] == "o"            # the leaf overrides its parent
    assert values["prio"] == "low"         # an AI answer is not overridden
    values = {"req": "out"}
    apply_implies(defn, values, sources)
    assert values["prio"] == "high" and sources["prio"] == "derived"


def test_a_chain_of_implies_fills_whichever_order_the_fields_are_declared_in():
    sub = {"key": "sub", "type": "choice", "source": "derived",
           "options": [{"key": "s1", "implies": {"note": "from s1"}}]}
    req = {"key": "req", "type": "choice", "options": [{"key": "r1", "implies": {"sub": "s1"}}]}
    note = {"key": "note", "source": "derived"}
    for order in ([sub, req, note], [req, sub, note]):
        defn = _ok(_form(*order))
        values, sources = {"req": "r1"}, {}
        apply_implies(defn, values, sources)
        assert values == {"req": "r1", "sub": "s1", "note": "from s1"}, [f["key"] for f in order]


# --------------------------------------------------------------------------- templates
def test_template_rendering():
    tpl = "Booked {doctor}[[ at {clinic}]] on {date}."
    assert render_template(tpl, {"doctor": "Dr A", "date": "05/11", "clinic": ""}) == "Booked Dr A on 05/11."
    assert render_template(tpl, {"doctor": "Dr A", "clinic": "X", "date": "05/11"}) == "Booked Dr A at X on 05/11."
    assert render_template(tpl, {"doctor": "Dr A"}) == "Booked Dr A on [date]."


def test_template_slot_names_may_be_georgian():
    assert crm_form.slot_names("ეცნო {მომსახურება} {X}%-ით") == ["მომსახურება", "X"]
    assert render_template("ფინანსდება {X}%-ით", {"X": "80"}) == "ფინანსდება 80%-ით"


# --------------------------------------------------------------------------- values
@pytest.mark.parametrize("ftype,raw,want", [
    ("number", "1.234,56", 1234.56), ("number", "1,234.5", 1234.5), ("number", "12,5", 12.5),
    ("number", "1,234", 1234), ("money", "350 ლარი", 350), ("percent", "100 %", 100),
    ("date", "05/11/2026", "2026-11-05"), ("date", "2026-11-05", "2026-11-05"),
    ("datetime", "05/11/2026 14:30", "2026-11-05T14:30"), ("time", "9.05", "09:05"),
    ("boolean", "კი", True), ("boolean", "нет", False), ("phone", "+995 (555) 12-34-56", "+995555123456"),
    ("email", "a@b.ge", "a@b.ge"),
])
def test_coerce(ftype, raw, want):
    assert coerce({"type": ftype}, raw) == (want, True)


@pytest.mark.parametrize("ftype,raw", [("number", "many"), ("date", "next week"),
                                       ("boolean", "maybe"), ("email", "nope"), ("percent", "5000")])
def test_coerce_refuses(ftype, raw):
    assert coerce({"type": ftype}, raw) == (None, False)


def test_choice_matches_key_label_and_path():
    f = _BY["req"]
    assert coerce(f, "dental") == ("dental", True)
    assert coerce(f, "DENTAL") == ("dental", True)
    assert coerce(f, "Dental") == ("dental", True)
    assert coerce(f, "Medical › Dental") == ("dental", True)
    assert coerce(f, "Cardiology") == (None, False)
    assert coerce({**f, "type": "multichoice"}, ["dental", "travel", "dental"]) == (["dental", "travel"], True)


def test_empty_is_a_valid_answer():
    assert coerce({"type": "number"}, "  ") == (None, True)


# --------------------------------------------------------------------------- formats
def test_when_text_round_trip():
    text = "request in (a, b); outcome != x; clinic is filled"
    assert crm_form.format_when_text(parse_when_text(text)) == text
    assert parse_when_text("any: a = 1")["match"] == "any"
    # `=` used to shadow `==` in the alternation, which read the value as "= active".
    assert parse_when_text("status == active")["rules"] == [
        {"field": "status", "op": "is", "value": "active"}]
    assert parse_when_text("status != active")["rules"][0] == {
        "field": "status", "op": "is_not", "value": "active"}
    with pytest.raises(ValueError):
        parse_when_text("request approximately a")


def test_documents_from_json_accepts_every_shape():
    defn = crm_presets.preset("generic_en")
    one = crm_form.export_document("x", defn)
    assert crm_form.documents_from_json(one)[0]["key"] == "x"
    assert len(crm_form.documents_from_json({"format": crm_form.BUNDLE_FORMAT, "forms": [one, one]})) == 2
    assert crm_form.documents_from_json(defn)[0]["form"] is defn
    with pytest.raises(ValueError):
        crm_form.documents_from_json({"hello": 1})


def test_diff():
    a = _ok(crm_presets.preset("generic_en"))
    b = json.loads(json.dumps(a))
    b["fields"] = [f for f in b["fields"] if f["key"] != "phone"]
    b["fields"][0]["options"].append({"key": "new", "label": "New", "parent": None, "description": "",
                                      "keywords": [], "implies": {}, "template": ""})
    b["fields"].append({**b["fields"][1], "key": "extra"})
    d = crm_form.diff(a, b)
    assert d["added"] == ["extra"] and d["removed"] == ["phone"]
    assert d["changed"][0]["key"] == "category" and d["changed"][0]["options_added"] == 1
    assert crm_form.diff(None, b)["new_form"] is True


@pytest.mark.parametrize("pid", sorted(crm_presets.PRESETS))
def test_xlsx_round_trip(pid):
    defn = _ok(crm_presets.preset(pid))
    key, raw = crm_xlsx.parse_xlsx(crm_xlsx.export_xlsx("my_form", defn))
    assert key == "my_form"
    assert _ok(raw) == defn


def test_xlsx_import_reports_options_for_unknown_fields():
    defn = _ok(crm_presets.preset("generic_en"))
    data = crm_xlsx.export_xlsx("k", defn)
    from openpyxl import load_workbook
    import io
    wb = load_workbook(io.BytesIO(data))
    wb["Options"].append(["ghost", "x", "X"])
    buf = io.BytesIO()
    wb.save(buf)
    with pytest.raises(ValueError, match="ghost"):
        crm_xlsx.parse_xlsx(buf.getvalue())


def test_xlsx_rejects_non_workbooks():
    with pytest.raises(ValueError):
        crm_xlsx.parse_xlsx(b"PK not really a zip")
