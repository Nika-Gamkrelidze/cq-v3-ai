"""Fill a tenant's CRM form from one call — the CRM analyser.

The form (`crm_form.py`) says what to fill; this module asks the model for the parts only a
reader of the call can supply, and CODE does everything else: implied values, conditions,
option matching, type coercion, template rendering, and the required-field check. The model
is never trusted with a choice it was not offered or a sentence the tenant did not write.

PASSES. A field whose condition, dependent dropdown or template depends on another field's
answer is asked AFTER that answer is known, so the second call is told "the request was
X — which of X's sub-scenarios happened?" instead of being shown every sub-scenario of every
request and asked to keep them apart. Fields are grouped by how many answers they wait on;
a form with no dependencies is ONE call, most real forms are two, and the count is capped at
`MAX_PASSES` (whatever is still waiting then is asked in the last pass with its condition
spelled out, and code re-checks the condition afterwards). A template field may share a pass
with the choice its template comes from: the prompt lists the candidate templates, which
saves a whole call for the commonest shape of form ("pick the scenario, write its sentence").

The transcript sits in the SYSTEM block and the per-pass field list in the USER block. That
is the opposite of the scoring prompt and it is deliberate: the system block is the cached
prefix, so the second pass reads a long transcript from the prompt cache instead of paying
for it again.

Forced tool use with a strict schema, as everywhere in this codebase. The schema is built per
call — `key` is an enum of exactly the fields asked in this pass — but its SHAPE never
changes, so every provider adapter translates it the same way.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from . import llm
from .crm_form import (CHOICE_TYPES, apply_implies, applies, coerce, depends_on,
                       display, format_when_text, offered_options, option_path, render_template,
                       slot_names, template_for)
from .scoring import _evidence, _timeline_for

log = logging.getLogger("cq")

FEATURE = "crm_fill"
MAX_PASSES = 3
BASE_OUTPUT_TOKENS = 700
PER_FIELD_TOKENS = 260
PER_SLOT_TOKENS = 60
MAX_OUTPUT_TOKENS = 8000
# Options listed per field in one prompt. A filtered dependent dropdown is usually a handful;
# this only bites on a flat list of hundreds (a clinic directory), where it keeps one field
# from swallowing the prompt. The rest are still valid answers — matched by key or label.
MAX_PROMPT_OPTIONS = 250

LANG_NAMES = {"ka": "Georgian", "en": "English", "ru": "Russian"}


class CrmError(RuntimeError):
    pass


class ReviewInvalid(ValueError):
    """Reviewed values that do not fit their fields. `fields` holds the keys, so the editor
    can mark the inputs; the message names them by label, for the person."""

    def __init__(self, message: str, fields: list[str]):
        super().__init__(message)
        self.fields = fields


def _tool(keys: list[str]) -> dict:
    return {
        "name": "fill_form",
        "description": "Return what the call says for each listed form field.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "fields": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "key": {"type": "string", "enum": keys,
                                    "description": "The field key, exactly as listed."},
                            "values": {
                                "type": "array", "items": {"type": "string"},
                                "description": "The answer. One item for a single value; several for a "
                                               "multi-choice field; EMPTY when the call does not say it. "
                                               "For choice fields: option KEYS. For template fields: empty "
                                               "(fill `slots` instead).",
                            },
                            "slots": {
                                "type": "array",
                                "description": "Template fields only: one entry per slot of the template. "
                                               "Empty for every other field.",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "value": {"type": "string",
                                                  "description": "Short value from the call, or empty."},
                                    },
                                    "required": ["name", "value"],
                                    "additionalProperties": False,
                                },
                            },
                            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                            "evidence": {
                                "type": "array",
                                "description": "One or two short verbatim quotes that justify the answer, "
                                               "with the # indices of their transcript lines. Empty when "
                                               "the answer is empty.",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "quote": {"type": "string"},
                                        "segments": {"type": "array", "items": {"type": "integer"}},
                                    },
                                    "required": ["quote", "segments"],
                                    "additionalProperties": False,
                                },
                            },
                        },
                        "required": ["key", "values", "slots", "confidence", "evidence"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["fields"],
            "additionalProperties": False,
        },
    }


# --------------------------------------------------------------------------- #
# Planning
# --------------------------------------------------------------------------- #
def _when_deps(f: dict) -> set[str]:
    return {r["field"] for r in (f.get("when") or {}).get("rules", [])}


def _hard_deps(f: dict) -> set[str]:
    """What must be answered in an EARLIER pass: a condition decides whether to ask at all,
    and a dependent dropdown decides which options to offer."""
    out = _when_deps(f)
    if f.get("filter_by"):
        out.add(f["filter_by"])
    return out


def plan_passes(defn: dict) -> list[list[str]]:
    """The AI fields, grouped into the passes they will be asked in.

    A field is ready once every field it depends on is KNOWN: fixed and external fields are
    known from the start, an AI field once its pass has run, and a derived field once every
    field whose options imply it has run. A `template_from` source may instead be asked in
    the same pass (see the module docstring). Anything still waiting at the cap goes last."""
    fields = defn["fields"]
    by_key = {f["key"]: f for f in fields}
    implied_by: dict[str, set[str]] = {}
    for f in fields:
        for o in f.get("options") or []:
            for tk in o.get("implies") or {}:
                implied_by.setdefault(tk, set()).add(f["key"])
    known = {f["key"] for f in fields if f["source"] in ("fixed", "external")}
    pending = [f["key"] for f in fields if f["source"] == "ai"]

    def refresh() -> None:
        for f in fields:
            if f["source"] == "derived" and implied_by.get(f["key"], set()) <= known:
                known.add(f["key"])

    passes: list[list[str]] = []
    refresh()
    while pending:
        if len(passes) == MAX_PASSES - 1:
            passes.append(pending)
            break
        ready = [k for k in pending if _hard_deps(by_key[k]) <= known]
        ready = [k for k in ready
                 if not by_key[k].get("template_from")
                 or by_key[k]["template_from"] in known or by_key[k]["template_from"] in ready]
        if not ready:                    # only reachable through a cycle validation refused
            passes.append(pending)
            break
        passes.append(ready)
        known.update(ready)
        pending = [k for k in pending if k not in ready]
        refresh()
    return passes


# --------------------------------------------------------------------------- #
# Prompts
# --------------------------------------------------------------------------- #
def _system(defn: dict, timeline: str, call_date: date | None) -> str:
    out_lang = (f"in {LANG_NAMES.get(defn['language'], defn['language'])}"
                if defn.get("output_language") == "form" else "in the language the call was held in")
    today = (f"The call took place on {call_date.isoformat()} ({call_date.strftime('%A')}). "
             "Resolve relative dates (tomorrow, on Monday) against that day. ") if call_date else ""
    lines = [
        "You fill in a company's CRM form from one recorded customer call. The transcript is below; "
        "its lines are numbered with #.",
        "",
        "Rules:",
        "- Use only what the call says. When the call does not state something, return an EMPTY "
        "`values` list for that field — never guess, never fill a field from general knowledge.",
        "- Choice fields: answer with option KEYS exactly as listed, choosing the most specific "
        "option that fits. The descriptions and signal words show how each option sounds in a "
        "real call; they are hints, not exact phrases (callers inflect, abbreviate, mix languages).",
        f"- Write free-text values {out_lang}, short and factual, as an agent would type them.",
        "- Dates as YYYY-MM-DD, date-times as YYYY-MM-DDTHH:MM, times as HH:MM, numbers as plain "
        "digits without units. Yes/no fields: `yes` or `no`.",
        "- Template fields: do NOT write the sentence. Fill each slot with a short value from the "
        "call, or an empty string when the call does not give it.",
        f"- {today}Give one or two short verbatim quotes as evidence for every answer you fill.",
    ]
    if defn.get("instructions"):
        lines += ["", "About this company and form:", defn["instructions"]]
    lines += ["", f"Form: {defn['name']}"]
    if defn.get("description"):
        lines.append(defn["description"])
    lines += ["", "<transcript>", timeline, "</transcript>"]
    return "\n".join(lines)


def _field_spec(f: dict, by_key: dict, values: dict, asked: set[str]) -> str:
    kind = {
        "choice": "choice — exactly ONE option key",
        "multichoice": "multi-choice — every option key that applies",
        "template": "template — fill the slots",
        "textarea": "text (a few sentences)",
        "boolean": "yes/no",
        "money": f"amount{' in ' + f['unit'] if f.get('unit') else ''}",
        "percent": "percentage (number only)",
    }.get(f["type"], f["type"])
    out = [f"### {f['key']} — {f['label']}", f"type: {kind}"]
    if f.get("required"):
        out.append("required: yes — but still empty if the call truly does not say it")
    if f.get("instruction"):
        out.append(f"instruction: {f['instruction']}")
    pending = depends_on(f) & asked
    if f.get("when") and pending:
        out.append(f"applies only when: {format_when_text(f['when'])} (answer it only if that holds)")
    if f["type"] in CHOICE_TYPES:
        opts = offered_options(f, by_key, values)
        if f.get("filter_by") and f["filter_by"] in pending:
            out.append(f"options are grouped by `{f['filter_by']}`: choose under the group matching "
                       f"your answer for `{f['filter_by']}`")
        out.append("options:")
        for o in opts[:MAX_PROMPT_OPTIONS]:
            label = option_path(f, o["key"]) if o.get("parent") else o["label"]
            out.append(f"  - {o['key']}: {label}")
            if o.get("description"):
                out.append(f"      recognise: {o['description']}")
            if o.get("keywords"):
                out.append("      signals: " + ", ".join(f'"{k}"' for k in o["keywords"]))
        if len(opts) > MAX_PROMPT_OPTIONS:
            out.append(f"  … {len(opts) - MAX_PROMPT_OPTIONS} more; answer with the option's exact label if it is not listed")
    if f["type"] == "template":
        src = f.get("template_from")
        if src and src in pending:
            src_f = by_key[src]
            out.append(f"the template depends on your answer for `{src}`:")
            for o in offered_options(src_f, by_key, values):
                if o.get("template"):
                    out.append(f"  - if {o['key']}: \"{o['template']}\"")
            if f.get("template"):
                out.append(f"  - otherwise: \"{f['template']}\"")
        else:
            out.append(f"template: \"{template_for(f, by_key, values)}\"")
        if f.get("slots"):
            out.append("slot hints: " + "; ".join(f"{{{s['name']}}} = {s['instruction']}"
                                                  for s in f["slots"] if s.get("instruction")))
    return "\n".join(out)


def _user(fields: list[dict], by_key: dict, values: dict, asked: set[str]) -> str:
    known = []
    for k, v in values.items():
        f = by_key.get(k)
        if f is not None and v not in (None, "", []):
            known.append(f"- {k} ({f['label']}): {display(f, v)}")
    head = []
    if known:
        head = ["Already established for this call:", *known, ""]
    body = "\n\n".join(_field_spec(f, by_key, values, asked) for f in fields)
    return "\n".join(head) + "Fill these fields:\n\n" + body


def output_budget(fields: list[dict], by_key: dict, values: dict) -> int:
    slots = sum(len(slot_names(template_for(f, by_key, values) or "")) + len(f.get("slots") or [])
                for f in fields if f["type"] == "template")
    return min(MAX_OUTPUT_TOKENS,
               BASE_OUTPUT_TOKENS + PER_FIELD_TOKENS * len(fields) + PER_SLOT_TOKENS * slots)


# --------------------------------------------------------------------------- #
# The run
# --------------------------------------------------------------------------- #
async def fill(defn: dict, *, form_key: str, form_version: int, transcript: str,
               segments: list[dict] | None, api_key: str, model: str,
               client_id: str | None, call_date: date | None = None) -> dict:
    """Fill `defn` (a validated definition) from the call. Raises CrmError on a model failure.

    Returns the result document stored in `crm_results.result` — see `assemble`."""
    if not (transcript or "").strip():
        raise CrmError("This recording has no transcript to read.")
    by_key = {f["key"]: f for f in defn["fields"]}
    values: dict = {}
    sources: dict = {}
    meta: dict = {}
    for f in defn["fields"]:
        if f["source"] == "fixed":
            values[f["key"]] = f["default"]
            sources[f["key"]] = "fixed"

    segs, timeline = _timeline_for(transcript, segments)
    system = _system(defn, timeline, call_date)
    passes = plan_passes(defn)
    calls = 0
    for i, keys in enumerate(passes):
        # A field whose condition is already decidable, and false, is not worth a token. One
        # that waits on a field still unanswered (only possible in the capped last pass) is
        # asked with its condition spelled out, and `assemble` re-checks it.
        unanswered = {k for p in passes[i:] for k in p}
        ask = [f for f in (by_key[k] for k in keys)
               if (_when_deps(f) & unanswered) or applies(f, by_key, values)]
        if not ask:
            continue
        asked = {f["key"] for f in ask}
        try:
            raw = await llm.call_tool(
                feature=FEATURE, client_id=client_id, api_key=api_key, model=model,
                system=system, user=_user(ask, by_key, values, asked),
                tool=_tool([f["key"] for f in ask]), opts=llm.ANALYSIS,
                max_tokens=output_budget(ask, by_key, values), cache_system=True, stream=True)
        except llm.LLMError as exc:
            raise CrmError(f"Filling the CRM form failed: {exc}") from exc
        calls += 1
        _take(raw, ask, by_key, values, sources, meta, segs)
        apply_implies(defn, values, sources)
    log.info("crm fill client=%s form=%s v%s fields=%d passes=%d", client_id, form_key,
             form_version, len(defn["fields"]), calls)
    return assemble(defn, form_key=form_key, form_version=form_version, values=values,
                    sources=sources, meta=meta, calls=calls)


def _as_str_list(value) -> list[str]:
    """House array normalisation (see services/claude.py) — strict schemas still slip.

    A bare string matters more here than anywhere else: iterating it yields CHARACTERS, so
    `"1500"` would coerce to the number 1 and store as a confidently filled field."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, dict):
        value = list(value.values())
    if not isinstance(value, (list, tuple)):
        return [str(value).strip()]
    out = []
    for item in value:
        if item is None:
            continue
        s = " — ".join(str(v).strip() for v in item.values() if v not in (None, "")) \
            if isinstance(item, dict) else str(item).strip()
        if s:
            out.append(s)
    return out


def _slot_map(raw) -> dict[str, str]:
    """`slots` → {name: value}, the same defensive posture as `_as_str_list`.

    The schema asks for a list of `{name, value}` objects; a model that slips the shape sends
    one object instead of a list, or a plain `{name: value}` mapping. Both used to iterate to
    strings, fail every `isinstance` check and leave the template silently unfilled."""
    items = raw
    if isinstance(raw, dict):
        items = [raw] if "name" in raw else [{"name": k, "value": v} for k, v in raw.items()]
    if not isinstance(items, (list, tuple)):
        return {}
    out: dict[str, str] = {}
    for s in items:
        if not isinstance(s, dict):
            continue
        name = str(s.get("name") or "").strip().strip("{}")
        if name:
            out[name] = str(s.get("value") or "").strip()
    return out


def _take(raw: dict, asked: list[dict], by_key: dict, values: dict, sources: dict,
          meta: dict, segs: list[dict]) -> None:
    """The model's answers → canonical values. Template fields are read LAST, so one whose
    `template_from` choice was asked in the same pass sees that choice before it renders."""
    answers: dict = {}
    for item in (raw or {}).get("fields") or []:
        if isinstance(item, dict) and item.get("key") in by_key and item["key"] not in answers:
            answers[item["key"]] = item
    for f in sorted(asked, key=lambda f: f["type"] == "template"):
        item = answers.get(f["key"])
        if item is None:
            continue
        got = _as_str_list(item.get("values"))
        entry = {"confidence": item.get("confidence") if item.get("confidence") in ("high", "medium", "low") else None,
                 "evidence": _evidence(item.get("evidence") or [], segs)[:3]}
        if f["type"] == "template":
            slots = _slot_map(item.get("slots"))
            entry["slots"] = slots
            tpl = template_for(f, by_key, values)
            entry["template"] = tpl
            names = slot_names(tpl)
            if any(slots.get(n) for n in names) or not names:
                # A template with no slots at all is a whole sentence the tenant wrote for this
                # scenario — there is nothing for the model to fill, and code writing the
                # sentence is the point of the feature. Gating on a filled slot left those
                # fields empty and flagged required ("forwarded to the tech team" is one of the
                # commonest outcomes of the shipped insurance form).
                values[f["key"]] = render_template(tpl, slots)
                sources[f["key"]] = "ai"
            elif got:
                # The model wrote the sentence instead of filling slots. Kept as written rather
                # than lost — the reviewer sees it, and the template is still on the field.
                values[f["key"]] = got[0][:20000]
                sources[f["key"]] = "ai"
            elif any(slots.values()):
                # Slots that belong to no slot of THIS template: the model answered for the
                # template it had in mind, but the `template_from` choice came back empty,
                # invalid or as a non-leaf group, so the field fell back to its own. Rendering
                # anyway would store a sentence of nothing but [placeholders], count as filled,
                # pass the required check and drop out of the reviewer's "needs attention" —
                # while what the model did extract survived only where no screen reads it.
                entry["raw"] = ", ".join(f"{n}: {v}" for n, v in slots.items() if v)[:500]
                entry["invalid"] = True
        elif got:
            value, ok = coerce(f, got if f["type"] == "multichoice" else got[0])
            if ok and value is not None:
                values[f["key"]] = value
                sources[f["key"]] = "ai"
            else:
                entry["raw"] = ", ".join(got)[:500]
                entry["invalid"] = True
        meta[f["key"]] = entry


def assemble(defn: dict, *, form_key: str, form_version: int, values: dict, sources: dict,
             meta: dict, calls: int) -> dict:
    """The stored result: every field of the form, in form order, with its status.

    Conditions are re-checked here on the FINAL values, repeatedly until nothing changes:
    clearing a field that turned out not to apply can switch off a field that depended on it.
    Each field carries what a reviewer needs to edit it without loading the form again (type,
    options, unit), which is also what lets an old result stay readable after the form
    changed."""
    by_key = {f["key"]: f for f in defn["fields"]}
    final = dict(values)
    inactive: set[str] = set()
    for _ in range(len(defn["fields"]) + 1):
        changed = False
        for f in defn["fields"]:
            if f["key"] not in inactive and not applies(f, by_key, final):
                inactive.add(f["key"])
                final.pop(f["key"], None)
                changed = True
        if not changed:
            break
    out_fields = []
    missing = []
    for f in defn["fields"]:
        k = f["key"]
        m = meta.get(k, {})
        value = final.get(k)
        if k in inactive:
            status = "not_applicable"
        elif f["source"] == "external":
            status = "external"
        elif value not in (None, "", []):
            status = "filled"
        elif m.get("invalid"):
            status = "invalid"
        else:
            status = "empty"
        is_missing = bool(f.get("required")) and status in ("empty", "invalid")
        if is_missing:
            missing.append(k)
        out_fields.append({
            "key": k, "label": f["label"], "type": f["type"],
            "source": sources.get(k, f["source"]),
            "required": bool(f.get("required")),
            "status": status, "missing": is_missing,
            "value": value, "display": display(f, value),
            "confidence": m.get("confidence"), "evidence": m.get("evidence") or [],
            "slots": m.get("slots"), "template": m.get("template"), "raw": m.get("raw"),
            "unit": f.get("unit") or "", "max_length": f.get("max_length"),
            "options": [{"key": o["key"], "label": o["label"], "parent": o["parent"]}
                        for o in f.get("options") or []] if f["type"] in CHOICE_TYPES else None,
        })
    label = None
    lf = defn.get("label_field")
    if lf and final.get(lf):
        label = {"field": lf, "key": final[lf], "text": option_path(by_key[lf], str(final[lf]))}
    return {
        "form_key": form_key, "form_version": form_version, "form_name": defn["name"],
        "language": defn.get("language"), "label_field": lf, "label": label,
        "fields": out_fields, "missing_required": missing, "model_calls": calls,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


# --------------------------------------------------------------------------- #
# Review
# --------------------------------------------------------------------------- #
def _field_of(item: dict) -> dict:
    """The stored field entry as enough of a definition for `coerce` and `display`."""
    return {"key": item["key"], "type": item["type"], "unit": item.get("unit") or "",
            "max_length": item.get("max_length"),
            "options": [{**o, "description": "", "keywords": [], "implies": {}, "template": ""}
                        for o in (item.get("options") or [])]}


def apply_review(result: dict, edits: dict) -> tuple[dict, dict]:
    """A reviewer's values over the model's: ({key: final value}, {key: final display}).

    Every edit is coerced by the field's own type and option list; an edit that does not fit
    raises ReviewInvalid naming the field, so the editor can point at it rather than store a
    "2026-13-45" in somebody's CRM. Fields the reviewer did not touch keep the model's value."""
    finals, shown, bad, bad_keys = {}, {}, [], []
    by_key = {item["key"]: item for item in result.get("fields") or []}
    for k in edits:
        if k not in by_key:
            bad.append(f"`{k}` is not a field of this form")
            bad_keys.append(k)
    for k, item in by_key.items():
        f = _field_of(item)
        if k in edits:
            value, ok = coerce(f, edits[k])
            if not ok:
                bad.append(f"`{item['label']}`: `{edits[k]}` is not a valid {item['type']}")
                bad_keys.append(k)
                continue
        else:
            value = item.get("value")
        finals[k] = value
        shown[k] = display(f, value)
    if bad:
        raise ReviewInvalid("; ".join(bad), bad_keys)
    return finals, shown


def as_record(result: dict, final: dict | None) -> dict:
    """The flat record a CRM import wants: field key → value, reviewed values winning."""
    final = final or {}
    values = {item["key"]: final.get(item["key"], item.get("value"))
              for item in result.get("fields") or []}
    # The label is read from the FINAL values: a reviewer who corrects the category has
    # corrected the label too.
    return {"form": result.get("form_key"), "form_version": result.get("form_version"),
            "label": values.get(result.get("label_field") or ""), "values": values}


def call_date_of(created_at) -> date | None:
    if isinstance(created_at, datetime):
        return created_at.date()
    return None
