"""The CRM form as a workbook: what a round trip must survive, and what a hostile or broken
workbook must not cost. Pure — no database, no model."""
import io
import zipfile

import pytest
from openpyxl import Workbook, load_workbook

from app.services import crm_xlsx
from app.services.crm_form import normalize_definition


def _ok(raw):
    defn, errors, warnings = normalize_definition(raw)
    assert errors == [], errors
    return defn


def _round_trip(defn):
    key, raw = crm_xlsx.parse_xlsx(crm_xlsx.export_xlsx("k", defn))
    assert key == "k"
    return _ok(raw)


class _Sheet:
    """Just enough worksheet to drive `_rows`, counting what it actually reads."""

    def __init__(self, rows):
        self._rows = rows
        self.read = 0

    def iter_rows(self, values_only=True):
        for r in self._rows:
            self.read += 1
            yield r


# --------------------------------------------------------------------------- size
def test_a_sheet_past_the_row_cap_is_refused():
    wb = Workbook()
    ws = wb.active
    ws.title = "Fields"
    ws.append(["key", "label", "type"])
    for i in range(crm_xlsx.MAX_FIELD_ROWS + 1):
        ws.append([f"f{i}", f"F{i}", "text"])
    buf = io.BytesIO()
    wb.save(buf)
    with pytest.raises(ValueError, match="more than"):
        crm_xlsx.parse_xlsx(buf.getvalue())


def test_the_row_cap_stops_reading_rather_than_counting_at_the_end():
    sheet = _Sheet([("key",)] + [(f"f{i}",) for i in range(50_000)])
    with pytest.raises(ValueError, match="Fields"):
        crm_xlsx._rows(sheet, 10, "Fields")
    assert sheet.read <= 12


def test_one_cell_cannot_outgrow_what_excel_can_hold():
    sheet = _Sheet([("instruction",), ("x" * (crm_xlsx.MAX_CELL + 5_000),)])
    assert len(crm_xlsx._rows(sheet, 10, "Fields")[0]["instruction"]) == crm_xlsx.MAX_CELL


# --------------------------------------------------------------------------- round trip
def test_an_option_implying_a_multichoice_value_round_trips():
    defn = _ok({"name": "T", "fields": [
        {"key": "category", "label": "Category", "type": "choice", "options": [
            {"key": "medical", "label": "Medical", "implies": {"tags": ["urgent", "clinic"]}},
            {"key": "other", "label": "Other", "implies": {"tags": "clinic"}},
        ]},
        {"key": "tags", "label": "Tags", "type": "multichoice", "source": "derived", "options": [
            {"key": "urgent", "label": "Urgent"}, {"key": "clinic", "label": "Clinic"},
        ]},
    ]})
    assert _round_trip(defn) == defn


def test_text_that_starts_with_an_equals_sign_is_not_a_formula():
    defn = _ok({"name": "T", "fields": [
        {"key": "callback", "label": "Callback", "type": "text",
         "instruction": "=> always write the callback time"},
    ]})
    assert _round_trip(defn) == defn


def test_a_control_character_does_not_break_the_export():
    defn = _ok({"name": "T", "fields": [
        {"key": "callback", "label": "Callback", "type": "text", "instruction": "bell\x07here"},
    ]})
    back = _round_trip(defn)
    assert back["fields"][0]["instruction"] == "bellhere"


# --------------------------------------------------------------------------- broken workbooks
def test_a_workbook_whose_sheet_xml_is_damaged_is_a_value_error():
    data = crm_xlsx.export_xlsx("k", _ok({"name": "T", "fields": [
        {"key": "a", "label": "A", "type": "text"}]}))
    src = zipfile.ZipFile(io.BytesIO(data))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as out:
        for item in src.infolist():
            blob = src.read(item.filename)
            if item.filename.startswith("xl/worksheets/"):
                blob = blob[:len(blob) // 2]
            out.writestr(item, blob)
    with pytest.raises(ValueError, match="Could not read the spreadsheet"):
        crm_xlsx.parse_xlsx(buf.getvalue())


def test_the_messages_for_a_workbook_we_understand_are_kept():
    wb = Workbook()
    wb.active.title = "Nothing"
    buf = io.BytesIO()
    wb.save(buf)
    with pytest.raises(ValueError, match="`Fields` sheet"):
        crm_xlsx.parse_xlsx(buf.getvalue())


def test_an_exported_workbook_still_reads_as_text_in_excel():
    data = crm_xlsx.export_xlsx("k", _ok({"name": "T", "fields": [
        {"key": "a", "label": "A", "type": "text", "instruction": "=SUM(A1:A2)"}]}))
    ws = load_workbook(io.BytesIO(data))["Fields"]
    cell = next(c for row in ws.iter_rows(min_row=2) for c in row if c.value == "=SUM(A1:A2)")
    assert cell.data_type == "s"
