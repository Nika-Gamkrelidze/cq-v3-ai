"""The CRM form definition: what a tenant's call form looks like, and the rules over it.

A tenant describes the form its agents fill after a call — the fields, their types, the
choices and the words that signal each choice — and the CRM analyser fills it from the
transcript (`crm_extract.py`). Everything here is PURE: no database, no model, no request.
That is deliberate. The definition arrives from four doors (the editor, a JSON import, a
spreadsheet import, the superadmin's default) and every one of them goes through
`normalize_definition`, so a form can never be stored in a shape one door would refuse.

The vocabulary is small on purpose, because it has to fit every tenant's business without
code changes:

  FIELD     key, label, type, where the value comes from (`source`), whether it is required,
            an instruction for the AI, and an optional condition (`when`) that decides whether
            the field applies to this call at all.
  OPTION    one choice of a `choice`/`multichoice` field. Options form a TREE through
            `parent` (category › subcategory › request) and each carries what the AI needs to
            recognise it: a description, signal keywords, and optionally values it IMPLIES for
            other fields and a text TEMPLATE.
  FILTER    `filter_by` makes a choice field a DEPENDENT dropdown: its top-level options are
            keyed like the options of another choice field, and only the branch under what
            that field chose is offered ("sub-scenario" lists differ per request type).
  WHEN      `{"match": "all"|"any", "rules": [{"field", "op", "value"}]}`. On a tree, `is`
            and `in` match the option OR ANY OF ITS DESCENDANTS, so "when request is under
            Medical" is one rule rather than a list of forty leaves.
  TEMPLATE  `{slot}` is filled from the call; `[[ ... ]]` is an optional piece dropped when
            any slot inside it is empty. CODE renders it, never the model — the model only
            supplies slot values, so the sentence the tenant wrote is the sentence that lands
            in their CRM.

Keywords are hints for the model, never string matches: Georgian is heavily inflected, and a
literal match on "მიმართვა" would miss "მიმართვის", "მიმართვაზე" and every other form an
operator actually says.
"""
from __future__ import annotations

import copy
import json
import re
from datetime import date, datetime

FORMAT = "cq.crm-form"
BUNDLE_FORMAT = "cq.crm-forms"
SCHEMA_VERSION = 1

FIELD_TYPES = ("text", "textarea", "number", "money", "percent", "date", "datetime", "time",
               "boolean", "phone", "email", "choice", "multichoice", "template")
CHOICE_TYPES = ("choice", "multichoice")
# Where a value comes from.
#   ai        the model fills it from the call
#   derived   set ONLY by another field's chosen option (`implies`); never asked of the model
#   external  the tenant's CRM knows it (a policy type, an account tier); left blank here
#   fixed     the same value on every call (`default`)
SOURCES = ("ai", "derived", "external", "fixed")
OPS = ("is", "is_not", "in", "not_in", "filled", "empty")
OUTPUT_LANGUAGES = ("call", "form")
LANGUAGES = ("ka", "en", "ru")

# Bounds on what one form may hold. Every option and keyword is prompt text on every call, so
# these are what keep one tenant's form from becoming a 50k-token system prompt.
MAX_FIELDS = 80
MAX_OPTIONS_PER_FIELD = 600
MAX_OPTIONS_TOTAL = 2000
MAX_DEPTH = 5
MAX_KEYWORDS = 40
MAX_RULES = 12
# Any other list read out of a cell or a JSON body (`in` values, a multichoice answer). A
# rule cannot sensibly name more values than a field has options, and the cap is what stops
# an untrusted cell from costing more work than the form it describes.
MAX_LIST_VALUES = MAX_OPTIONS_PER_FIELD
MAX_LABEL = 200
MAX_TEXT = 4000          # instruction, description, template, form instructions
MAX_NAME = 120
MAX_KEYWORD = 120

# Keys are what a tenant's CRM integration maps on, so they are allowed to look like the codes
# real CRMs use (`REQ_TYPE`, `custom.field-12`) rather than forced into snake_case.
KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.\-]{0,63}$")
SLOT_RE = re.compile(r"\{([^{}\n]{1,60})\}")
OPTIONAL_RE = re.compile(r"\[\[(.*?)\]\]", re.S)


class FormInvalid(ValueError):
    """A definition that cannot be stored. `errors` is a list of {path, message}."""

    def __init__(self, errors: list[dict]):
        self.errors = errors
        first = errors[0]["message"] if errors else "invalid form"
        super().__init__(f"{first}" + (f" (+{len(errors) - 1} more)" if len(errors) > 1 else ""))


# --------------------------------------------------------------------------- #
# Small coercions shared by normalisation and value handling
# --------------------------------------------------------------------------- #
def _s(value, limit: int = MAX_TEXT) -> str:
    if value is None:
        return ""
    return str(value).strip()[:limit]


def _bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("1", "true", "yes", "y", "on", "კი", "да", "x", "✓")


def _str_list(value, limit: int = MAX_KEYWORD, cap: int = MAX_LIST_VALUES) -> list[str]:
    """Keywords and similar lists: from a list, or from text split on newlines and `;`.

    The input is one untrusted cell of an uploaded workbook (or one field of an uploaded
    JSON body), so the cap is reached WHILE scanning and duplicates are checked against a
    set: de-duplicating against a list and truncating the finished list, as this did, made
    a 300 000-line cell cost minutes of the api's only event loop to keep forty keywords.
    The text is walked lazily for the same reason. First occurrence wins — callers and the
    spreadsheet round-trip read the order back."""
    if value is None:
        return []
    items = (m.group(0) for m in re.finditer(r"[^\n;]+", value)) if isinstance(value, str) \
        else (value if isinstance(value, (list, tuple)) else [value])
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        s = _s(item, limit)
        if s and s not in seen:
            seen.add(s)
            out.append(s)
            if len(out) >= cap:
                break
    return out


def slugify(text: str, fallback: str) -> str:
    """An ASCII key from a label, or `fallback` when the label has no Latin letters (a
    Georgian label slugs to nothing, and a transliterated key nobody chose is worse than an
    honest `field_3`)."""
    s = re.sub(r"[^A-Za-z0-9]+", "_", text or "").strip("_").lower()[:40]
    if not s or not s[0].isalpha():
        return fallback
    return s


# --------------------------------------------------------------------------- #
# Normalisation + validation
# --------------------------------------------------------------------------- #
class _Report:
    def __init__(self):
        self.errors: list[dict] = []
        self.warnings: list[dict] = []

    def err(self, path: str, message: str) -> None:
        self.errors.append({"path": path, "message": message})

    def warn(self, path: str, message: str) -> None:
        self.warnings.append({"path": path, "message": message})


def _norm_when(raw, path: str, rep: _Report) -> dict | None:
    if raw in (None, "", {}, []):
        return None
    if isinstance(raw, str):
        try:
            raw = parse_when_text(raw)
        except ValueError as exc:
            rep.err(path, str(exc))
            return None
        if raw is None:
            return None
    if isinstance(raw, list):
        raw = {"match": "all", "rules": raw}
    if not isinstance(raw, dict):
        rep.err(path, "A condition must be an object with `match` and `rules`.")
        return None
    match = str(raw.get("match") or "all").strip().lower()
    if match not in ("all", "any"):
        rep.err(f"{path}.match", "`match` must be `all` or `any`.")
        match = "all"
    rules = []
    for i, r in enumerate(raw.get("rules") or []):
        rp = f"{path}.rules[{i}]"
        if not isinstance(r, dict):
            rep.err(rp, "A rule must be an object with `field`, `op` and `value`.")
            continue
        field = _s(r.get("field"), 64)
        op = str(r.get("op") or "is").strip().lower().replace(" ", "_")
        op = {"=": "is", "==": "is", "!=": "is_not", "eq": "is", "neq": "is_not",
              "not": "is_not", "is_filled": "filled", "is_empty": "empty"}.get(op, op)
        if not field:
            rep.err(rp, "The rule names no field.")
            continue
        if op not in OPS:
            rep.err(f"{rp}.op", f"Unknown operator `{op}`. Use one of: {', '.join(OPS)}.")
            continue
        value = r.get("value")
        if op in ("in", "not_in"):
            value = _str_list(value, 200) if not isinstance(value, list) else [_s(v, 200) for v in value if _s(v, 200)]
            if not value:
                rep.err(f"{rp}.value", f"`{op}` needs at least one value.")
                continue
        elif op in ("is", "is_not"):
            if isinstance(value, list):
                value = value[0] if value else ""
            value = _s(value, 200) if not isinstance(value, bool) else value
            if value == "":
                rep.err(f"{rp}.value", f"`{op}` needs a value.")
                continue
        else:
            value = None
        rules.append({"field": field, "op": op, "value": value})
    if len(rules) > MAX_RULES:
        rep.err(path, f"At most {MAX_RULES} rules per condition.")
        rules = rules[:MAX_RULES]
    return {"match": match, "rules": rules} if rules else None


def _norm_options(raw, fpath: str, rep: _Report, used_total: list[int]) -> list[dict]:
    if not isinstance(raw, list):
        if raw not in (None, ""):
            rep.err(f"{fpath}.options", "Options must be a list.")
        return []
    if len(raw) > MAX_OPTIONS_PER_FIELD:
        rep.err(f"{fpath}.options", f"At most {MAX_OPTIONS_PER_FIELD} options per field.")
        raw = raw[:MAX_OPTIONS_PER_FIELD]
    out: list[dict] = []
    seen: set[str] = set()
    for i, o in enumerate(raw):
        op = f"{fpath}.options[{i}]"
        if isinstance(o, str):
            o = {"label": o}
        if not isinstance(o, dict):
            rep.err(op, "An option must be an object.")
            continue
        label = _s(o.get("label"), MAX_LABEL)
        key = _s(o.get("key"), 64)
        if not key:
            key = slugify(label, f"opt_{i + 1}")
            while key in seen:
                key = f"{key}_{i + 1}"
            rep.warn(op, f"No key given; using `{key}`.")
        if not KEY_RE.match(key):
            rep.err(f"{op}.key", f"Key `{key}` must start with a letter and use only letters, "
                                 "digits, `_`, `.` or `-` (max 64).")
            continue
        if key in seen:
            rep.err(f"{op}.key", f"Duplicate option key `{key}`.")
            continue
        seen.add(key)
        if not label:
            label = key
        implies = o.get("implies") or {}
        if isinstance(implies, str):
            implies = _parse_pairs(implies)
        if not isinstance(implies, dict):
            rep.err(f"{op}.implies", "`implies` must map field keys to values.")
            implies = {}
        out.append({
            "key": key,
            "label": label,
            "parent": _s(o.get("parent"), 64) or None,
            "description": _s(o.get("description")),
            "keywords": _str_list(o.get("keywords"), cap=MAX_KEYWORDS),
            "implies": {_s(k, 64): v for k, v in implies.items() if _s(k, 64)},
            "template": _s(o.get("template")),
        })
    used_total[0] += len(out)
    keys = {o["key"] for o in out}
    by_key = {o["key"]: o for o in out}
    for i, o in enumerate(out):
        if o["parent"] and o["parent"] not in keys:
            rep.err(f"{fpath}.options[{i}].parent", f"Parent `{o['parent']}` is not an option of this field.")
            o["parent"] = None
    # Cycles and depth: walk up from every option.
    for i, o in enumerate(out):
        depth, cur, seen_up = 1, o, {o["key"]}
        while cur["parent"]:
            cur = by_key[cur["parent"]]
            if cur["key"] in seen_up:
                rep.err(f"{fpath}.options[{i}].parent", f"Option `{o['key']}` is its own ancestor.")
                o["parent"] = None
                break
            seen_up.add(cur["key"])
            depth += 1
        if depth > MAX_DEPTH:
            rep.err(f"{fpath}.options[{i}]", f"Options may nest at most {MAX_DEPTH} levels deep.")
    return out


def _parse_pairs(text: str) -> dict:
    """`field=value; other=value` (or one per line) → dict. Used by spreadsheet cells."""
    out = {}
    for part in re.split(r"[\n;]", text or ""):
        if "=" in part:
            k, v = part.split("=", 1)
            if k.strip():
                out[k.strip()] = v.strip()
    return out


def _norm_field(raw, i: int, rep: _Report, seen: set[str], used_total: list[int]) -> dict | None:
    path = f"fields[{i}]"
    if not isinstance(raw, dict):
        rep.err(path, "A field must be an object.")
        return None
    label = _s(raw.get("label") or raw.get("name"), MAX_LABEL)
    key = _s(raw.get("key"), 64)
    if not key:
        key = slugify(label, f"field_{i + 1}")
        while key in seen:
            key = f"{key}_{i + 1}"
        rep.warn(path, f"No key given; using `{key}`.")
    if not KEY_RE.match(key):
        rep.err(f"{path}.key", f"Key `{key}` must start with a letter and use only letters, digits, "
                               "`_`, `.` or `-` (max 64).")
        return None
    if key in seen:
        rep.err(f"{path}.key", f"Duplicate field key `{key}`.")
        return None
    seen.add(key)
    ftype = str(raw.get("type") or "text").strip().lower()
    ftype = {"select": "choice", "dropdown": "choice", "multiselect": "multichoice",
             "string": "text", "longtext": "textarea", "bool": "boolean", "yesno": "boolean",
             "integer": "number", "float": "number", "currency": "money"}.get(ftype, ftype)
    if ftype not in FIELD_TYPES:
        rep.err(f"{path}.type", f"Unknown type `{ftype}`. Use one of: {', '.join(FIELD_TYPES)}.")
        return None
    source = str(raw.get("source") or "ai").strip().lower()
    if source not in SOURCES:
        rep.err(f"{path}.source", f"Unknown source `{source}`. Use one of: {', '.join(SOURCES)}.")
        source = "ai"
    field = {
        "key": key,
        "label": label or key,
        "type": ftype,
        "source": source,
        "required": _bool(raw.get("required")),
        "instruction": _s(raw.get("instruction") or raw.get("description")),
        "when": _norm_when(raw.get("when"), f"{path}.when", rep),
        "default": raw.get("default") if raw.get("default") not in ("", None) else None,
        "options": [],
        "template": "",
        "template_from": None,
        "filter_by": None,
        "slots": [],
        "unit": _s(raw.get("unit"), 16),
        "max_length": None,
    }
    if ftype in CHOICE_TYPES:
        field["options"] = _norm_options(raw.get("options"), path, rep, used_total)
        field["filter_by"] = _s(raw.get("filter_by"), 64) or None
        if not field["options"] and source in ("ai", "derived"):
            rep.err(f"{path}.options", "A choice field needs at least one option.")
    if ftype == "template":
        field["template"] = _s(raw.get("template"))
        field["template_from"] = _s(raw.get("template_from"), 64) or None
        slots = raw.get("slots") or []
        if isinstance(slots, str):
            slots = [{"name": k, "instruction": v} for k, v in _parse_slot_text(slots).items()]
        clean = []
        for s in slots if isinstance(slots, list) else []:
            if isinstance(s, dict) and _s(s.get("name"), 60):
                clean.append({"name": _s(s.get("name"), 60), "instruction": _s(s.get("instruction"), 500)})
        field["slots"] = clean
        if source != "ai":
            rep.err(f"{path}.source", "A template field is filled from the call, so its source must be `ai`.")
            field["source"] = "ai"
        if not field["template"] and not field["template_from"]:
            rep.err(f"{path}.template", "A template field needs a template, or `template_from` "
                                        "naming a choice field whose options carry templates.")
    if ftype in ("text", "textarea"):
        ml = raw.get("max_length")
        try:
            field["max_length"] = max(1, min(int(ml), 20000)) if ml not in (None, "") else None
        except (TypeError, ValueError):
            rep.err(f"{path}.max_length", "`max_length` must be a whole number.")
    if source == "fixed" and field["default"] is None:
        rep.err(f"{path}.default", "A fixed field needs a default value.")
    return field


def _parse_slot_text(text: str) -> dict:
    """`name: instruction` per line (spreadsheet cell) → dict."""
    out = {}
    for line in (text or "").splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            if k.strip():
                out[k.strip().strip("{}")] = v.strip()
    return out


def normalize_definition(raw) -> tuple[dict, list[dict], list[dict]]:
    """(definition, errors, warnings). Never raises: an importer wants every problem at once,
    with a path, rather than the first one. `require_valid` is the raising wrapper."""
    rep = _Report()
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            rep.err("", f"Not valid JSON: {exc}")
            return _empty(), rep.errors, rep.warnings
    if not isinstance(raw, dict):
        rep.err("", "A form definition must be a JSON object.")
        return _empty(), rep.errors, rep.warnings
    if isinstance(raw.get("form"), dict) and "fields" not in raw:
        raw = raw["form"]                      # an export document handed in whole
    name = _s(raw.get("name"), MAX_NAME)
    if not name:
        rep.err("name", "The form needs a name.")
    language = str(raw.get("language") or "ka").strip().lower()
    if language not in LANGUAGES:
        rep.warn("language", f"Unknown language `{language}`; using `ka`.")
        language = "ka"
    out_lang = str(raw.get("output_language") or "call").strip().lower()
    if out_lang not in OUTPUT_LANGUAGES:
        rep.warn("output_language", "`output_language` must be `call` or `form`; using `call`.")
        out_lang = "call"
    raw_fields = raw.get("fields")
    if not isinstance(raw_fields, list) or not raw_fields:
        rep.err("fields", "The form needs at least one field.")
        raw_fields = []
    if len(raw_fields) > MAX_FIELDS:
        rep.err("fields", f"At most {MAX_FIELDS} fields per form.")
        raw_fields = raw_fields[:MAX_FIELDS]
    seen: set[str] = set()
    used_total = [0]
    fields = [f for f in (_norm_field(f, i, rep, seen, used_total)
                          for i, f in enumerate(raw_fields)) if f is not None]
    if used_total[0] > MAX_OPTIONS_TOTAL:
        rep.err("fields", f"At most {MAX_OPTIONS_TOTAL} options across the whole form "
                          f"(this one has {used_total[0]}).")
    defn = {
        "name": name,
        "description": _s(raw.get("description")),
        "language": language,
        "output_language": out_lang,
        "instructions": _s(raw.get("instructions")),
        "label_field": _s(raw.get("label_field"), 64) or None,
        "fields": fields,
    }
    _cross_check(defn, rep)
    return defn, rep.errors, rep.warnings


def _empty() -> dict:
    return {"name": "", "description": "", "language": "ka", "output_language": "call",
            "instructions": "", "label_field": None, "fields": []}


def _cross_check(defn: dict, rep: _Report) -> None:
    """Everything that needs the whole field list: references, cycles, implied values."""
    by_key = {f["key"]: f for f in defn["fields"]}
    idx = {f["key"]: i for i, f in enumerate(defn["fields"])}

    lf = defn["label_field"]
    if lf and (lf not in by_key or by_key[lf]["type"] != "choice"):
        rep.err("label_field", f"`label_field` must name a single-choice field; `{lf}` is not one.")
        defn["label_field"] = None
    if not defn["label_field"]:
        first = next((f["key"] for f in defn["fields"] if f["type"] == "choice"), None)
        if first and lf is None:
            defn["label_field"] = first

    deps: dict[str, set[str]] = {k: set() for k in by_key}
    for f in defn["fields"]:
        path = f"fields[{idx[f['key']]}]"
        for j, r in enumerate((f["when"] or {}).get("rules", [])):
            rp = f"{path}.when.rules[{j}]"
            target = by_key.get(r["field"])
            if target is None:
                rep.err(rp, f"The condition refers to `{r['field']}`, which is not a field of this form.")
                continue
            if r["field"] == f["key"]:
                rep.err(rp, "A field's condition cannot refer to the field itself.")
                continue
            deps[f["key"]].add(r["field"])
            if target["type"] in CHOICE_TYPES and r["op"] in ("is", "is_not", "in", "not_in"):
                keys = {o["key"] for o in target["options"]}
                vals = r["value"] if isinstance(r["value"], list) else [r["value"]]
                for v in vals:
                    if str(v) not in keys:
                        rep.err(rp, f"`{v}` is not an option of `{target['key']}`.")
        if f["filter_by"]:
            src = by_key.get(f["filter_by"])
            if src is None or src["type"] != "choice" or src["key"] == f["key"]:
                rep.err(f"{path}.filter_by", f"`filter_by` must name another single-choice field; "
                                             f"`{f['filter_by']}` is not one.")
                f["filter_by"] = None
            else:
                deps[f["key"]].add(src["key"])
                roots = {o["key"] for o in f["options"] if not o["parent"]}
                strays = sorted(roots - {o["key"] for o in src["options"]})
                if strays == sorted(roots):
                    rep.warn(f"{path}.filter_by",
                             f"None of this field's top-level option keys is an option of "
                             f"`{src['key']}`, so the filter never narrows anything.")
                elif strays:
                    # Per root, not only when all of them miss: one option renamed on the source
                    # leaves its siblings matching, and that branch is then unreachable while
                    # the answer that should open it falls through to every option there is.
                    rep.warn(f"{path}.filter_by",
                             f"No option of `{src['key']}` matches "
                             f"{', '.join(f'`{k}`' for k in strays[:10])}, so that branch is "
                             f"never offered.")
        if f["template_from"]:
            deps[f["key"]].add(f["template_from"])
            src = by_key.get(f["template_from"])
            if src is None or src["type"] != "choice":
                rep.err(f"{path}.template_from", f"`template_from` must name a single-choice field; "
                                                 f"`{f['template_from']}` is not one.")
                f["template_from"] = None
            elif not any(o["template"] for o in src["options"]) and not f["template"]:
                rep.warn(f"{path}.template_from", f"No option of `{src['key']}` carries a template, "
                                                  "and this field has none of its own.")
        for oi, o in enumerate(f["options"]):
            for tk, tv in list(o["implies"].items()):
                op = f"{path}.options[{oi}].implies.{tk}"
                target = by_key.get(tk)
                if target is None:
                    rep.err(op, f"Implies a value for `{tk}`, which is not a field of this form.")
                    continue
                if tk == f["key"]:
                    rep.err(op, "An option cannot imply a value for its own field.")
                    continue
                if target["type"] == "template":
                    rep.err(op, "A template field cannot be implied; give the option a `template` instead.")
                    continue
                value, ok = coerce(target, tv)
                if not ok:
                    rep.err(op, f"`{tv}` is not a valid value for `{tk}` ({target['type']}).")
                    continue
                if value is None:
                    # `coerce` reads an empty input as (None, True), which here is a row the
                    # author started and never filled — not an instruction to clear `tk`.
                    # Kept, it would beat the value an ANCESTOR option implies (a leaf is
                    # applied last) and still count as "something implies this field", so it
                    # would also hide the derived-field warning below.
                    rep.warn(op, f"Implies no value for `{tk}`, so the row is ignored.")
                    del o["implies"][tk]
                    continue
                o["implies"][tk] = value
                deps[tk].add(f["key"])
    for f in defn["fields"]:
        if f["source"] == "derived" and not any(
                f["key"] in o["implies"] for g in defn["fields"] for o in g["options"]):
            rep.warn(f"fields[{idx[f['key']]}].source",
                     f"`{f['key']}` is derived, but no option implies a value for it, so it will always be empty.")
        if f["default"] is not None:
            value, ok = coerce(f, f["default"])
            if not ok:
                rep.err(f"fields[{idx[f['key']]}].default", f"`{f['default']}` is not a valid {f['type']}.")
            else:
                f["default"] = value
    cycle = _find_cycle(deps)
    if cycle:
        rep.err("fields", "Conditions go round in a circle: " + " → ".join(cycle) + ".")


def _find_cycle(deps: dict[str, set[str]]) -> list[str] | None:
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(n: str) -> list[str] | None:
        state[n] = 1
        stack.append(n)
        for m in sorted(deps.get(n, ())):
            if state.get(m) == 1:
                return stack[stack.index(m):] + [m]
            if state.get(m) is None:
                found = visit(m)
                if found:
                    return found
        stack.pop()
        state[n] = 2
        return None

    for n in deps:
        if state.get(n) is None:
            found = visit(n)
            if found:
                return found
    return None


def require_valid(raw) -> tuple[dict, list[dict]]:
    """(definition, warnings) or FormInvalid."""
    defn, errors, warnings = normalize_definition(raw)
    if errors:
        raise FormInvalid(errors)
    return defn, warnings


# --------------------------------------------------------------------------- #
# The option tree
# --------------------------------------------------------------------------- #
def option_map(field: dict) -> dict[str, dict]:
    return {o["key"]: o for o in field.get("options") or []}


def ancestors(field: dict, key: str) -> list[str]:
    """[key, parent, grandparent, ...] — the option and every node above it."""
    by = option_map(field)
    out, cur, guard = [], by.get(key), 0
    while cur and guard <= MAX_DEPTH + 1:
        out.append(cur["key"])
        cur = by.get(cur["parent"]) if cur["parent"] else None
        guard += 1
    return out


def option_path(field: dict, key: str) -> str:
    """"Medical › Informational › Planned outpatient" — how an option reads in a list."""
    by = option_map(field)
    return " › ".join(by[k]["label"] for k in reversed(ancestors(field, key)) if k in by)


def subtree(field: dict, root: str) -> list[str]:
    """`root` and every option below it, in definition order."""
    keep = {root}
    changed = True
    while changed:
        changed = False
        for o in field.get("options") or []:
            if o["parent"] in keep and o["key"] not in keep:
                keep.add(o["key"])
                changed = True
    return [o["key"] for o in field.get("options") or [] if o["key"] in keep]


def offered_options(field: dict, defn_by_key: dict[str, dict], values: dict) -> list[dict]:
    """The options worth offering for this call. All of them, unless `filter_by` names a
    field that has been answered: then the branches whose top-level key is that answer (or
    any node above it), so "sub-scenario" lists only the sub-scenarios of the request that
    was actually made. A filter that matches no branch offers everything rather than nothing
    — an empty list would force a wrong answer."""
    opts = field.get("options") or []
    src = defn_by_key.get(field.get("filter_by") or "")
    chosen = values.get(src["key"]) if src else None
    if not src or not chosen:
        return opts
    reach = set(ancestors(src, str(chosen)))
    keep: set[str] = set()
    for o in opts:
        if not o["parent"] and o["key"] in reach:
            keep.update(subtree(field, o["key"]))
    return [o for o in opts if o["key"] in keep] or opts


def apply_implies(defn: dict, values: dict, sources: dict) -> None:
    """Values that follow from a chosen option, written into `values` in place.

    Applied root-first along the chosen option's branch, so a leaf's implication overrides
    its parent's. A DERIVED field always takes the implied value — that is the only way it
    is ever filled. Any other field takes it only when nothing filled it: there it is a
    sensible default, and overriding what the model read in the call would hide the call.
    `sources[key]` is set to "derived" for every value written here.

    The walk repeats until `values` stops moving, because an implied choice can itself be
    the option that implies something else: a single pass in declaration order would fill a
    chain only when the filler happens to sit above its target in the field list, so the
    same form filled or silently did not fill depending on which way round a person had
    pressed the editor's ↑/↓ buttons. The round count is bounded (and a conflicting pair of
    options settles into the same state after its second round) so an `implies` circle that
    slipped past `_find_cycle` cannot spin here."""
    by_key = {f["key"]: f for f in defn["fields"]}
    for _ in range(len(defn["fields"]) + 1):
        before = dict(values)
        for f in defn["fields"]:
            if f["type"] not in CHOICE_TYPES:
                continue
            chosen = values.get(f["key"])
            picks = chosen if isinstance(chosen, list) else ([chosen] if chosen else [])
            by = option_map(f)
            for pick in picks:
                for k in reversed(ancestors(f, str(pick))):
                    for tk, tv in (by[k]["implies"] or {}).items():
                        target = by_key.get(tk)
                        if target is None:
                            continue
                        if target["source"] == "derived" or not _filled(values.get(tk)):
                            values[tk] = tv
                            sources[tk] = "derived"
        if values == before:
            return


# --------------------------------------------------------------------------- #
# Conditions
# --------------------------------------------------------------------------- #
def _filled(value) -> bool:
    return value not in (None, "", [], {})


def rule_holds(rule: dict, defn_by_key: dict[str, dict], values: dict) -> bool:
    target = defn_by_key.get(rule["field"])
    value = values.get(rule["field"])
    op = rule["op"]
    if op == "filled":
        return _filled(value)
    if op == "empty":
        return not _filled(value)
    wanted = rule["value"] if isinstance(rule["value"], list) else [rule["value"]]
    wanted = [str(w).strip().lower() for w in wanted]
    if target is not None and target["type"] in CHOICE_TYPES:
        chosen = value if isinstance(value, list) else ([value] if _filled(value) else [])
        # A chosen option matches a rule on itself OR on any node above it in the tree.
        reach = {a.lower() for c in chosen for a in ancestors(target, str(c))}
        hit = any(w in reach for w in wanted)
    elif target is not None and target["type"] == "boolean":
        hit = _filled(value) and any(_bool(w) == bool(value) for w in wanted)
    else:
        hit = _filled(value) and str(value).strip().lower() in wanted
    return hit if op in ("is", "in") else not hit


def applies(field: dict, defn_by_key: dict[str, dict], values: dict) -> bool:
    when = field.get("when")
    if not when or not when.get("rules"):
        return True
    results = [rule_holds(r, defn_by_key, values) for r in when["rules"]]
    return any(results) if when.get("match") == "any" else all(results)


def depends_on(field: dict) -> set[str]:
    """Every field whose answer must be known before this one can be asked well: its
    condition, its dependent-dropdown source, and the choice its template comes from."""
    out = {r["field"] for r in (field.get("when") or {}).get("rules", [])}
    for k in (field.get("filter_by"), field.get("template_from")):
        if k:
            out.add(k)
    return out


# --------------------------------------------------------------------------- #
# Templates
# --------------------------------------------------------------------------- #
def slot_names(template: str) -> list[str]:
    out: list[str] = []
    for m in SLOT_RE.finditer(template or ""):
        name = m.group(1).strip()
        if name and name not in out:
            out.append(name)
    return out


def render_template(template: str, slots: dict) -> str:
    """Fill `{slot}`s; drop a `[[ ... ]]` piece when any slot inside it is empty; leave a
    required slot that came back empty visible as `[slot]`, so a reviewer sees the gap
    instead of a sentence that silently reads as complete."""
    clean = {str(k).strip(): (str(v).strip() if v is not None else "") for k, v in (slots or {}).items()}

    def fill(text: str) -> str:
        return SLOT_RE.sub(lambda m: clean.get(m.group(1).strip()) or f"[{m.group(1).strip()}]", text)

    def optional(m: re.Match) -> str:
        inner = m.group(1)
        if any(not clean.get(n) for n in slot_names(inner)):
            return ""
        return fill(inner)

    text = OPTIONAL_RE.sub(optional, template or "")
    text = fill(text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\s+([.,;:])", r"\1", text)
    return text.strip()


def template_for(field: dict, defn_by_key: dict[str, dict], values: dict) -> str:
    """The template a template field uses for this call: the chosen option's when it has one
    (`template_from`), else the field's own."""
    src_key = field.get("template_from")
    if src_key:
        src = defn_by_key.get(src_key)
        chosen = values.get(src_key)
        if src and chosen:
            for k in ancestors(src, str(chosen)):
                tpl = option_map(src)[k]["template"]
                if tpl:
                    return tpl
    return field.get("template") or ""


# --------------------------------------------------------------------------- #
# Values
# --------------------------------------------------------------------------- #
_NUM_RE = re.compile(r"-?\d[\d\s.,]*")


def _number(raw) -> float | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    m = _NUM_RE.search(str(raw or ""))
    if not m:
        return None
    s = m.group(0).replace(" ", "").replace(" ", "")
    # "1.234,56" and "1,234.56" both read as 1234.56; a lone comma is a decimal comma.
    if "," in s and "." in s:
        s = s.replace(",", "") if s.rfind(".") > s.rfind(",") else s.replace(".", "").replace(",", ".")
    elif "," in s:
        head, _, tail = s.rpartition(",")
        s = f"{head.replace(',', '')}.{tail}" if len(tail) != 3 else s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d.%m.%Y", "%d-%m-%Y", "%d/%m/%y", "%d.%m.%y")
_DT_FORMATS = ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%d/%m/%Y %H:%M",
               "%d.%m.%Y %H:%M", "%d/%m/%y %H:%M")


def _date(raw) -> str | None:
    if isinstance(raw, datetime):
        return raw.date().isoformat()
    if isinstance(raw, date):
        return raw.isoformat()
    s = str(raw or "").strip()[:32]
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s).date().isoformat()
    except ValueError:
        return None


def _datetime(raw) -> str | None:
    if isinstance(raw, datetime):
        return raw.strftime("%Y-%m-%dT%H:%M")
    s = str(raw or "").strip()[:32]
    for fmt in _DT_FORMATS:
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%dT%H:%M")
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).strftime("%Y-%m-%dT%H:%M")
    except ValueError:
        return None


def _time(raw) -> str | None:
    m = re.search(r"(\d{1,2})[:.](\d{2})", str(raw or ""))
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    return f"{h:02d}:{mi:02d}" if h < 24 and mi < 60 else None


_YES = {"yes", "y", "true", "1", "კი", "დიახ", "ki", "да", "ага", "on"}
_NO = {"no", "n", "false", "0", "არა", "ara", "нет", "off"}


def _match_option(field: dict, raw) -> str | None:
    """A model's answer → an option key: the key itself, then case-insensitively, then the
    label, then the full path. Anything else is not an option of this field."""
    s = str(raw or "").strip()
    if not s:
        return None
    by = option_map(field)
    if s in by:
        return s
    low = s.lower()
    for k, o in by.items():
        if k.lower() == low:
            return k
    for k, o in by.items():
        if o["label"].strip().lower() == low:
            return k
    for k in by:
        if option_path(field, k).lower() == low:
            return k
    return None


def coerce(field: dict, raw) -> tuple[object, bool]:
    """(value, ok) — the canonical form of `raw` for this field. An empty input is
    `(None, True)`: absent is a valid answer, and `required` is judged elsewhere."""
    t = field["type"]
    if raw is None or (isinstance(raw, str) and not raw.strip()) or raw == []:
        return None, True
    if t in ("text", "textarea", "template"):
        s = str(raw).strip()
        ml = field.get("max_length")
        return (s[:ml] if ml else s[:20000]), True
    if t in ("number", "money", "percent"):
        n = _number(raw)
        if n is None:
            return None, False
        if t == "percent" and not 0 <= n <= 1000:
            return None, False
        return (int(n) if n.is_integer() else round(n, 4)), True
    if t == "date":
        d = _date(raw)
        return (d, True) if d else (None, False)
    if t == "datetime":
        d = _datetime(raw)
        return (d, True) if d else (None, False)
    if t == "time":
        d = _time(raw)
        return (d, True) if d else (None, False)
    if t == "boolean":
        if isinstance(raw, bool):
            return raw, True
        s = str(raw).strip().lower()
        if s in _YES:
            return True, True
        if s in _NO:
            return False, True
        return None, False
    if t == "phone":
        s = str(raw).strip()
        digits = re.sub(r"[^\d+]", "", s)
        digits = ("+" if digits.startswith("+") else "") + digits.replace("+", "")
        return (digits, True) if len(digits.lstrip("+")) >= 3 else (None, False)
    if t == "email":
        s = str(raw).strip()
        return (s, True) if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", s) else (None, False)
    if t == "choice":
        k = _match_option(field, raw[0] if isinstance(raw, list) else raw)
        return (k, True) if k else (None, False)
    if t == "multichoice":
        items = raw if isinstance(raw, list) else _str_list(raw, 200)
        keys, ok = [], True
        for item in items:
            k = _match_option(field, item)
            if k and k not in keys:
                keys.append(k)
            elif not k:
                ok = False
        return (keys or None), ok
    return None, False


def display(field: dict, value) -> str:
    """How a value reads to a person: an option's label path, yes/no, a joined list."""
    if value in (None, "", []):
        return ""
    t = field["type"]
    if t == "choice":
        return option_path(field, str(value)) or str(value)
    if t == "multichoice":
        return ", ".join(option_path(field, str(v)) or str(v) for v in value)
    if t == "boolean":
        return "✓" if value else "✗"
    if t in ("money",) and field.get("unit"):
        return f"{value} {field['unit']}"
    if t == "percent":
        return f"{value}%"
    return str(value)


# --------------------------------------------------------------------------- #
# The spreadsheet condition syntax
# --------------------------------------------------------------------------- #
# One cell, readable by the people who maintain these forms in Excel:
#     request in (planned_outpatient, medicines); outcome = monitoring
#     any: channel = phone; channel = chat
#     clinic is filled
_WHEN_RULE = re.compile(
    r"^\s*([A-Za-z][A-Za-z0-9_.\-]*)\s+"
    # Alternation is first-match, so every operator that another one is a prefix of comes
    # first: with `=` ahead of `==`, `status == active` parsed as `status is "= active"`.
    r"(==|=|!=|is not|is filled|is empty|not in|in|is)\s*(.*?)\s*$", re.I)


def parse_when_text(text: str) -> dict | None:
    text = (text or "").strip()
    if not text:
        return None
    match = "all"
    low = text.lower()
    for prefix, m in (("any:", "any"), ("all:", "all")):
        if low.startswith(prefix):
            match, text = m, text[len(prefix):]
            break
    rules = []
    for part in re.split(r"[;\n]", text):
        if not part.strip():
            continue
        m = _WHEN_RULE.match(part)
        if not m:
            raise ValueError(f"Cannot read the condition `{part.strip()}`. Write it as "
                             "`field = value`, `field in (a, b)` or `field is filled`.")
        field, op, rest = m.group(1), m.group(2).lower(), m.group(3)
        op = {"=": "is", "==": "is", "!=": "is_not", "is not": "is_not", "is filled": "filled",
              "is empty": "empty", "not in": "not_in"}.get(op, op)
        if op in ("in", "not_in"):
            rest = rest.strip()
            if rest.startswith("(") and rest.endswith(")"):
                rest = rest[1:-1]
            value = [v.strip() for v in rest.split(",") if v.strip()]
        elif op in ("filled", "empty"):
            value = None
        else:
            value = rest.strip()
        rules.append({"field": field, "op": op, "value": value})
    return {"match": match, "rules": rules} if rules else None


def format_when_text(when: dict | None) -> str:
    if not when or not when.get("rules"):
        return ""
    parts = []
    for r in when["rules"]:
        op, v = r["op"], r.get("value")
        if op in ("in", "not_in"):
            parts.append(f"{r['field']} {'not in' if op == 'not_in' else 'in'} ({', '.join(map(str, v or []))})")
        elif op == "filled":
            parts.append(f"{r['field']} is filled")
        elif op == "empty":
            parts.append(f"{r['field']} is empty")
        else:
            parts.append(f"{r['field']} {'!=' if op == 'is_not' else '='} {v}")
    text = "; ".join(parts)
    return f"any: {text}" if when.get("match") == "any" else text


# --------------------------------------------------------------------------- #
# Export documents and diffs
# --------------------------------------------------------------------------- #
def export_document(form_key: str, defn: dict, *, version: int | None = None) -> dict:
    """What `Export` downloads: self-describing, with no tenant id or anything secret in it,
    so it can move between workspaces and deployments."""
    return {"format": FORMAT, "schema_version": SCHEMA_VERSION, "key": form_key,
            "version": version, "form": copy.deepcopy(defn)}


def documents_from_json(data) -> list[dict]:
    """Every form in an uploaded JSON document → [{key, form}]. Accepts one export, a bundle,
    a bare definition, or a list of any of those."""
    if isinstance(data, list):
        out = []
        for item in data:
            out.extend(documents_from_json(item))
        return out
    if not isinstance(data, dict):
        raise ValueError("The file holds no form definition.")
    if data.get("format") == BUNDLE_FORMAT or isinstance(data.get("forms"), list):
        return documents_from_json(data.get("forms") or [])
    if isinstance(data.get("form"), dict):
        return [{"key": data.get("key"), "form": data["form"]}]
    if isinstance(data.get("fields"), list):
        return [{"key": data.get("key"), "form": data}]
    raise ValueError("The file holds no form definition (expected `fields`, or a `form` object).")


def diff(old: dict | None, new: dict) -> dict:
    """What an import would change, at the level a person reviews: fields added, removed and
    changed, and how many options moved inside the changed ones."""
    old_fields = {f["key"]: f for f in (old or {}).get("fields", [])}
    new_fields = {f["key"]: f for f in new.get("fields", [])}
    changed = []
    for k in new_fields.keys() & old_fields.keys():
        a, b = old_fields[k], new_fields[k]
        if a == b:
            continue
        ao, bo = option_map(a), option_map(b)
        changed.append({
            "key": k,
            "props": sorted(p for p in set(a) | set(b) if p != "options" and a.get(p) != b.get(p)),
            "options_added": len(bo.keys() - ao.keys()),
            "options_removed": len(ao.keys() - bo.keys()),
            "options_changed": sum(1 for x in bo.keys() & ao.keys() if ao[x] != bo[x]),
        })
    settings = sorted(k for k in ("name", "description", "language", "output_language",
                                  "instructions", "label_field")
                      if (old or {}).get(k) != new.get(k))
    return {
        "new_form": old is None,
        "settings": settings,
        "added": [k for k in new_fields if k not in old_fields],
        "removed": [k for k in old_fields if k not in new_fields],
        "changed": sorted(changed, key=lambda c: c["key"]),
    }


def summary(defn: dict) -> dict:
    """The few numbers a list row shows."""
    fields = defn.get("fields") or []
    return {"fields": len(fields),
            "options": sum(len(f.get("options") or []) for f in fields),
            "ai_fields": sum(1 for f in fields if f.get("source") == "ai")}
