"""scripts/crm_scenarios.py — the scenarios are answerable by the shipped form, and the grader is right.

The model run itself needs a key and is not part of the suite; what is pinned here is that a
scenario can only fail for the model's reasons, never because it expects an option the form does
not have, a sub-scenario under the wrong request, or a field the form would switch off."""
import importlib.util
import pathlib

import pytest

spec = importlib.util.spec_from_file_location(
    "crm_scenarios", pathlib.Path(__file__).resolve().parents[1] / "scripts" / "crm_scenarios.py")
sc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sc)

DEFN = sc.preset_definition()
FIELDS = {f["key"]: f for f in DEFN["fields"]}


def _values(exp):
    if isinstance(exp, list):
        return exp
    return [exp]


@pytest.mark.parametrize("s", sc.SCENARIOS, ids=[s["id"] for s in sc.SCENARIOS])
def test_every_expectation_names_a_real_field_and_option(s):
    for key, exp in s["expect"].items():
        assert key in FIELDS, f"{s['id']}: no field {key}"
        f = FIELDS[key]
        if f["type"] in ("choice", "multichoice") and not isinstance(exp, dict):
            options = {o["key"] for o in f["options"]}
            for v in _values(exp):
                assert v in options, f"{s['id']}: {key} has no option {v}"
    for key in s.get("texts", {}):
        assert FIELDS[key]["type"] in ("template", "text", "textarea") or key == "issued_info"


@pytest.mark.parametrize("s", [s for s in sc.SCENARIOS if "issued_scenario" in s["expect"]],
                         ids=lambda s: s["id"])
def test_sub_scenario_sits_under_the_expected_request(s):
    opts = {o["key"]: o for o in FIELDS["issued_scenario"]["options"]}
    parent = opts[s["expect"]["issued_scenario"]]["parent"]
    assert parent in _values(s["expect"]["request"]) or parent is None


def test_every_spreadsheet_sub_scenario_has_a_scenario():
    """The nineteen leaf sub-scenarios of the form, one test call each."""
    leaves = {o["key"] for o in FIELDS["issued_scenario"]["options"] if o["parent"]}
    covered = {s["expect"].get("issued_scenario") for s in sc.SCENARIOS}
    assert leaves <= covered, sorted(leaves - covered)


def test_every_issued_group_and_conditional_field_is_exercised():
    groups = {o["key"] for o in FIELDS["issued_group"]["options"]}
    seen = set()
    for s in sc.SCENARIOS:
        seen.update(_values(s["expect"].get("issued_group", [])))
    assert groups <= seen, sorted(groups - seen)
    for key in ("clinic", "application_number", "forwarded_to", "appointment_at",
                "coverage_percent", "state_insurance_informed"):
        assert any(key in s["expect"] for s in sc.SCENARIOS), key


def test_conditional_expectations_are_reachable():
    """A field expected filled must apply under the scenario's own expected values."""
    from app.services.crm_form import applies

    for s in sc.SCENARIOS:
        vals = {k: _values(v)[0] for k, v in s["expect"].items() if not isinstance(v, dict)}
        for key in s["expect"]:
            assert applies(FIELDS[key], FIELDS, vals), f"{s['id']}: {key} would be switched off"


@pytest.mark.parametrize("exp,actual,ok", [
    ("tech", "tech", True), ("tech", "manager", False),
    (["informed", "yes_no"], "yes_no", True),
    ({"contains": ["მზიურ"]}, "კლინიკა „მზიური“", True),
    ({"startswith": "2026-11-12T15:00"}, "2026-11-12T15:00:00", True),
    (70, "70", True), (70, 70.0, True), (70, None, False),
    (True, True, True), (True, "true", False),
])
def test_matchers(exp, actual, ok):
    assert sc.check_value(exp, actual) is ok


def test_grade_reports_each_field_and_requires_georgian_texts():
    s = {"id": "x", "expect": {"request": "planned_outpatient"}, "texts": {"issued_info": ["70"]}}
    good = {"fields": [
        {"key": "request", "value": "planned_outpatient"},
        {"key": "requested_info", "value": "დაზღვეულს აინტერესებს მრტ-ს დაფარვა."},
        {"key": "issued_info", "display": "დაზღვეულს ეცნო: ფინანსდება 70%-ით."},
    ], "missing_required": []}
    assert sc.grade(s, good)["ok"]
    english = {**good, "fields": good["fields"][:2] + [{"key": "issued_info", "display": "covered 70%"}]}
    g = sc.grade(s, english)
    assert not g["ok"] and [c["field"] for c in g["checks"] if not c["ok"]] == ["issued_info (Georgian)"]
    assert not sc.grade(s, {**good, "missing_required": ["reaction"]})["ok"]


def test_coverage_of_the_spreadsheet_has_no_gaps(tmp_path):
    """A miniature of the call centre's workbook: the trace must find every row in the form."""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "TOP 6"
    ws.append(["#", "მოთხოვნა", "კატეგორია", "ქვე კატეგორია", "რეაგირება", "დაზღვევის ტიპი",
               "პროვაიდერი", "მოთხოვნილი", "გაცემული"])
    ws.append([1, "საგარანტიო წერილი", "სამედიცინო დაზღვევა", "ინფორმაციული",
               "დასრულდა პირველივე ზარზე", "პოლისიდან", "„სხვა“ ან „არ საჭიროებს“",
               "განაცხადი #[ნომერი], [მომსახურება], კლინიკა: [დასახელება]; [სტატუსი].", ""])
    g = wb.create_sheet("TOP 6 გაცემული")
    g.append(["#"] * 9)
    g.append([1, "გადაეცა კოლეგას / სხვა ჯგუფს (მეილით)", "", "", "", "არა", "„მეილი“, „ტექ“", "", ""])
    s = wb.create_sheet("სცენარები")
    s.append(["მოთხოვნა", "ქვე-სცენარი", "სიგნალი", "რეაგირება", "შაბლონი"])
    s.append(["საგარანტიო წერილი", "ეცნო სტატუსი / ვადები", "„რა ეტაპზეა“, „როდის იქნება“",
              "დასრულდა პირველივე ზარზე", "ვადა [X სამუშაო დღე]. დოკუმენტი [არ არის / არის]."])
    wb.save(tmp_path / "top6.xlsx")
    rows, gaps = sc.coverage(tmp_path / "top6.xlsx", DEFN)
    assert gaps == 0, [r for r in rows if not r["ok"]]
    assert len(rows) > 10
