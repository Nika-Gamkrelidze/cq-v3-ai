"""A CRM form as a spreadsheet, both ways.

Tenants keep these forms in Excel already (the request lists, the words operators type, the
text templates), so a workbook is the import format they can actually produce. One form per
workbook, four sheets:

  Form      setting | value          name, description, language, output_language,
                                     instructions, label_field (and key, on export)
  Fields    one row per field        key, label, type, source, required, instruction, when,
                                     default, unit, max_length, filter_by, template,
                                     template_from, slots
  Options   one row per option       field, key, label, parent, description, keywords,
                                     implies, template
  Help      what the columns mean    (ignored on import)

Cells use the plain-text forms `crm_form` already reads: keywords one per line (or `;`),
`implies` as `field=value` per line (the same field on two lines sets both values, which is how
a multichoice target is written), slots as `name: hint` per line, and a condition as
`request in (a, b); outcome = x` (see `crm_form.parse_when_text`). The parser only turns the
workbook into a raw definition — validation is `normalize_definition`'s, exactly as for JSON.
"""
from __future__ import annotations

import io
import re

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill

from .crm_form import MAX_FIELDS, MAX_OPTIONS_TOTAL, format_when_text

FIELD_COLS = ["key", "label", "type", "source", "required", "instruction", "when", "default",
              "unit", "max_length", "filter_by", "template", "template_from", "slots"]
OPTION_COLS = ["field", "key", "label", "parent", "description", "keywords", "implies", "template"]
FORM_SETTINGS = ["key", "name", "description", "language", "output_language", "instructions",
                 "label_field"]

HELP = [
    ("Sheet", "Column", "Meaning"),
    ("Form", "name", "The form's name."),
    ("Form", "language", "ka, en or ru — the language the labels are written in."),
    ("Form", "output_language", "call = write values in the call's language; form = in the form's language."),
    ("Form", "instructions", "Anything the AI should know about your company and how you fill the form."),
    ("Form", "label_field", "The choice field that labels the call (its category). Defaults to the first choice field."),
    ("Fields", "key", "A stable code, e.g. the field code in your CRM. Letters, digits, _ . - ; starts with a letter."),
    ("Fields", "type", "text, textarea, number, money, percent, date, datetime, time, boolean, phone, email, "
                       "choice, multichoice, template"),
    ("Fields", "source", "ai (filled from the call), derived (set by another field's option via implies), "
                         "external (your CRM fills it; left blank), fixed (always `default`)."),
    ("Fields", "required", "yes / no. A required field the call does not answer is flagged for the reviewer."),
    ("Fields", "instruction", "How the AI should fill this field."),
    ("Fields", "when", "When the field applies, e.g.  request in (a, b); outcome = follow_up   or   any: x = 1; y is filled"),
    ("Fields", "filter_by", "Dependent dropdown: only offer the options under the top-level option whose key "
                            "equals the answer of this other choice field."),
    ("Fields", "template", "Text with {slots}; [[optional part with {slot}]] is dropped when a slot inside is empty."),
    ("Fields", "template_from", "A choice field whose chosen option's template is used instead."),
    ("Fields", "slots", "One per line:  slot name: hint for the AI"),
    ("Options", "field", "The key of the choice field this option belongs to."),
    ("Options", "parent", "The key of the option this one sits under (tree), or empty."),
    ("Options", "description", "How this option sounds in a real call."),
    ("Options", "keywords", "Signal words, one per line. Hints for the AI, not exact matches."),
    ("Options", "implies", "Values this option sets on other fields, one per line:  category=medical. "
                           "Repeat the field to set several values on it."),
    ("Options", "template", "The text template used when this option is chosen (see template_from)."),
]

_HEAD = Font(bold=True, color="FFFFFF")
_FILL = PatternFill("solid", fgColor="3B4A6B")
_WRAP = Alignment(wrap_text=True, vertical="top")

# How many rows of one sheet we are willing to read. The real bounds are crm_form's, but
# `normalize_definition` applies them long after the whole workbook has been turned into dicts,
# and an .xlsx is a zip: 5 MB of upload (the router's cap) is hundreds of thousands of rows of
# inline strings. Parsing them all on the way to throwing all but eighty away froze the api's
# single worker for minutes for every other tenant. We read a little past the limit so a form
# that is merely too big still gets `normalize_definition`'s "At most 80 fields per form"
# instead of a parse error, and refuse anything beyond that.
_ROW_SLACK = 200
MAX_FIELD_ROWS = MAX_FIELDS + _ROW_SLACK
MAX_OPTION_ROWS = MAX_OPTIONS_TOTAL + _ROW_SLACK
MAX_SETTING_ROWS = _ROW_SLACK

# Excel's own limit on one cell, which is therefore the most a workbook can honestly carry; a
# hand-rolled writer can exceed it, and crm_form splits these cells into keywords, option trees
# and `field=value` pairs, so one unbounded cell is the row cap's loophole.
MAX_CELL = 32767

# The C0 characters openpyxl refuses to write (tab, newline and carriage return are fine). They
# reach us from legacy CRM dumps via JSON, where \u001a is just another escape, and crm_form's
# `_s` keeps them; openpyxl then raised IllegalCharacterError out of `export_xlsx`, which is not
# a ValueError and nothing on the export path catches, so one stray byte made every workbook
# export of that form a permanent 500. Dropping them is the only lossless-enough answer: Excel
# cannot hold them at all.
_ILLEGAL_RE = re.compile(r"[\000-\010\013\014\016-\037]")


def _scalar(value) -> str:
    """One value as the words this workbook uses for it (`yes`/`no`, empty for absent)."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def _cell(value) -> object:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, list):
        value = "\n".join(str(v) for v in value)
    if isinstance(value, str):
        return _ILLEGAL_RE.sub("", value)
    return value


def _sheet(wb: Workbook, title: str, header: list[str], rows: list[list], widths: list[int]) -> None:
    ws = wb.create_sheet(title)
    ws.append(header)
    for c in ws[1]:
        c.font, c.fill = _HEAD, _FILL
    for r in rows:
        ws.append([_cell(v) for v in r])
        for c in ws[ws.max_row]:
            # openpyxl types any string starting with `=` as a FORMULA, and we read workbooks
            # back with data_only=True, which returns a formula's cached result — openpyxl never
            # computes one, so an instruction like "=> always note the callback time" came back
            # None and the round trip deleted it without a word (and Excel offered to repair the
            # file). The text was never a formula; say so.
            if c.data_type == "f":
                c.data_type = "s"
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.alignment = _WRAP
    for i, w in enumerate(widths):
        ws.column_dimensions[chr(ord("A") + i)].width = w
    ws.freeze_panes = "A2"


def _implies_text(implies: dict | None) -> str:
    """`field=value`, one line per implied VALUE.

    An implies whose target is a multichoice field holds a list — normalisation coerces it —
    and `f"{k}={v}"` wrote that list as a Python repr (`tags=['x']`), which the importer could
    not match back to an option, so the workbook the product had just exported was refused on
    re-import. A key written on two lines is read back as a list by `_parse_implies`.
    """
    lines = []
    for k, v in (implies or {}).items():
        for item in (v if isinstance(v, list) else [v]):
            lines.append(f"{k}={_scalar(item)}")
    return "\n".join(lines)


def export_xlsx(form_key: str, defn: dict) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    settings = {"key": form_key, **{k: defn.get(k) for k in FORM_SETTINGS if k != "key"}}
    _sheet(wb, "Form", ["setting", "value"], [[k, settings.get(k)] for k in FORM_SETTINGS], [18, 90])
    field_rows, option_rows = [], []
    for f in defn["fields"]:
        field_rows.append([
            f["key"], f["label"], f["type"], f["source"], f["required"], f.get("instruction"),
            format_when_text(f.get("when")), f.get("default"), f.get("unit"), f.get("max_length"),
            f.get("filter_by"), f.get("template"), f.get("template_from"),
            "\n".join(f"{s['name']}: {s.get('instruction', '')}" for s in f.get("slots") or []),
        ])
        for o in f.get("options") or []:
            option_rows.append([
                f["key"], o["key"], o["label"], o.get("parent"), o.get("description"),
                o.get("keywords") or [],
                _implies_text(o.get("implies")),
                o.get("template"),
            ])
    _sheet(wb, "Fields", FIELD_COLS, field_rows, [18, 28, 12, 10, 9, 40, 34, 12, 8, 10, 14, 40, 14, 30])
    _sheet(wb, "Options", OPTION_COLS, option_rows, [16, 22, 32, 18, 40, 30, 24, 50])
    _sheet(wb, "Help", list(HELP[0]), [list(r) for r in HELP[1:]], [10, 16, 100])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _parse_implies(text) -> dict:
    """`field=value` per line (or `;`) → the dict `normalize_definition` expects.

    crm_form parses this cell itself when we hand it a string, but one `=` line can only carry
    one value there, so a multichoice target arrived as a single string. We parse it here
    instead and hand up a LIST for a key written more than once, which is what `coerce` wants
    for a multichoice field and what `_implies_text` writes.
    """
    out: dict[str, object] = {}
    for part in re.split(r"[\n;]", str(text or "")):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        k, v = k.strip(), v.strip()
        if not k:
            continue
        if k in out:
            prev = out[k]
            out[k] = ([*prev, v] if isinstance(prev, list) else [prev, v])
        else:
            out[k] = v
    return out


def _rows(ws, limit: int, what: str) -> list[dict]:
    """A sheet as dicts keyed by its (lower-cased, trimmed) header row; blank rows dropped.

    `limit` bounds the rows READ, not the rows kept — a blank row still costs the XML parse, so
    stopping on the kept ones would leave the whole sheet to walk.
    """
    it = ws.iter_rows(values_only=True)
    try:
        header = [str(h or "").strip().lower() for h in next(it)]
    except StopIteration:
        return []
    out = []
    for n, values in enumerate(it, 1):
        if n > limit:
            raise ValueError(f"The `{what}` sheet has more than {limit} rows, which is more than "
                             "a form can hold. Check for stray data below the last row.")
        row = {h: v for h, v in zip(header, values) if h}
        if any(v not in (None, "") for v in row.values()):
            out.append({k: (v.strip()[:MAX_CELL] if isinstance(v, str) else v)
                        for k, v in row.items()})
    return out


def _find(wb, name: str):
    for ws in wb.worksheets:
        if ws.title.strip().lower() == name:
            return ws
    return None


def parse_xlsx(data: bytes) -> tuple[str | None, dict]:
    """(form key or None, raw definition). Raises ValueError for a workbook that is not one."""
    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 — openpyxl raises half a dozen types for "not a workbook"
        raise ValueError(f"Could not open the spreadsheet: {exc}") from None
    try:
        fields_ws = _find(wb, "fields")
        if fields_ws is None:
            raise ValueError("The workbook has no `Fields` sheet. Export a form to see the layout.")
        settings: dict = {}
        form_ws = _find(wb, "form")
        if form_ws is not None:
            for r in _rows(form_ws, MAX_SETTING_ROWS, "Form"):
                k = str(r.get("setting") or "").strip().lower()
                if k:
                    settings[k] = r.get("value")
        fields = []
        for r in _rows(fields_ws, MAX_FIELD_ROWS, "Fields"):
            f = {c: r.get(c) for c in FIELD_COLS if r.get(c) not in (None, "")}
            f["options"] = []
            fields.append(f)
        by_key = {str(f.get("key") or "").strip(): f for f in fields}
        options_ws = _find(wb, "options")
        orphans = []
        if options_ws is not None:
            for r in _rows(options_ws, MAX_OPTION_ROWS, "Options"):
                target = by_key.get(str(r.get("field") or "").strip())
                if target is None:
                    orphans.append(str(r.get("field") or ""))
                    continue
                o = {c: r.get(c) for c in OPTION_COLS if c != "field" and r.get(c) not in (None, "")}
                if "implies" in o:
                    o["implies"] = _parse_implies(o["implies"])
                target["options"].append(o)
        if orphans:
            missing = ", ".join(sorted({o or "(empty)" for o in orphans}))
            raise ValueError(f"The Options sheet names fields the Fields sheet does not have: {missing}.")
        raw = {k: settings.get(k) for k in FORM_SETTINGS if k != "key" and settings.get(k) not in (None, "")}
        raw["fields"] = fields
        for f in fields:
            for num in ("max_length",):
                if isinstance(f.get(num), float) and f[num].is_integer():
                    f[num] = int(f[num])
        key = str(settings.get("key") or "").strip() or None
        return key, raw
    except ValueError:
        raise
    except Exception as exc:  # noqa: BLE001 — see below
        # `read_only=True` is lazy, so a workbook whose zip opens but whose sheet XML is damaged
        # fails HERE rather than in load_workbook, with ElementTree's ParseError — a SyntaxError,
        # not a ValueError. The router rescues ValueError only, so the same truncation one byte
        # earlier answered 400 while this one was an unexplained 500.
        raise ValueError(f"Could not read the spreadsheet: {exc}") from None
    finally:
        wb.close()
