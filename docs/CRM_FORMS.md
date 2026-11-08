# CRM forms — the configuration format

The CRM analyser fills a workspace's **CRM form** from a call: it labels the call with a
category and fills every field the conversation answers. A person reviews the result
(draft → approved) before it counts. This document is the reference for the form definition —
what the editor writes, what an import file holds, and what to generate when onboarding a
customer from their own CRM's field list.

Code: `backend/app/services/crm_form.py` (the definition and every rule below),
`crm_extract.py` (filling), `crm_store.py` (persistence), `crm_xlsx.py` (spreadsheets),
`crm_presets.py` (the built-in default + presets), `routers/crm.py` (HTTP). UI:
`frontend/next/components/crm/*`, the workspace's **CRM forms** tab, the console's **Default
CRM form** tab, and the **CRM form** section of *Analyse a call*.

## The definition

```json
{
  "name": "Inbound support",
  "description": "",
  "language": "ka",               // ka | en | ru — the language the labels are written in
  "output_language": "form",      // call = values in the call's language; form = in `language`
  "instructions": "",             // company context for the AI
  "label_field": "request",       // the single-choice field that labels the call
  "fields": [ FIELD, ... ]        // 1..80
}
```

### FIELD

| key | meaning |
|---|---|
| `key` | Stable code; what an integration maps on. `^[A-Za-z][A-Za-z0-9_.-]{0,63}$` — CRM-style codes such as `REQ_TYPE` or `custom.field-12` are fine. Missing → generated from the label (a warning). |
| `label` | As it reads in the customer's CRM. |
| `type` | `text`, `textarea`, `number`, `money`, `percent`, `date`, `datetime`, `time`, `boolean`, `phone`, `email`, `choice`, `multichoice`, `template`. Aliases accepted on import: `select`/`dropdown` → choice, `multiselect`, `string`, `longtext`, `bool`/`yesno`, `integer`/`float`, `currency`. |
| `source` | `ai` (filled from the call), `derived` (set only by another field's option via `implies`; never asked of the AI), `external` (the CRM fills it — left blank, never guessed), `fixed` (always `default`). |
| `required` | A required field the call does not answer is flagged for the reviewer. |
| `instruction` | How the AI should fill it. |
| `when` | When the field applies: `{"match": "all"\|"any", "rules": [{"field", "op", "value"}]}`; ops `is`, `is_not`, `in`, `not_in`, `filled`, `empty`. On a choice field with nested options, `is`/`in` on an option also match every option **under** it. A field whose condition is false is not asked and shows as "does not apply". |
| `options` | Choice fields: `[{key, label, parent, description, keywords[], implies{}, template}]` — see below. |
| `filter_by` | Choice fields: a **dependent dropdown**. Its top-level option keys equal option keys of the named single-choice field, and only the branch under that field's answer is offered. |
| `template`, `template_from`, `slots` | Template fields — see below. |
| `default` | The value of a `fixed` field. |
| `unit` | `money`: the currency shown with the amount. |
| `max_length` | `text` / `textarea`. |

### OPTION

| key | meaning |
|---|---|
| `key`, `label` | Unique within the field. |
| `parent` | Another option's key → a tree (≤ 5 levels). Category › subcategory › request is one field, not three. |
| `description` | How it sounds in a real call. |
| `keywords` | Signal words. **Hints for the model, never string matches** — Georgian inflects, operators abbreviate. |
| `implies` | `{field_key: value}` set when this option is chosen, root-first along the branch (a leaf overrides its parent). A `derived` field always takes it; any other field only when the call left it empty. |
| `template` | The text template used by a template field whose `template_from` is this field. |

### Templates

`{slot}` is filled from the call; `[[ … ]]` is dropped when any slot inside it is empty; a slot
left empty outside `[[ ]]` stays visible as `[slot]` so the reviewer sees the gap. **Code renders
the sentence, never the model** — the model only supplies slot values, so the sentence in the
CRM is always the one the customer wrote. `template_from` names a single-choice field whose
chosen option's template is used (the field's own `template` is the fallback). `slots:
[{name, instruction}]` are optional hints per slot name. Slot names may be Georgian.

### Limits

80 fields, 600 options per field, 2 000 options per form, 5 tree levels, 40 keywords per option,
12 rules per condition. Every option and keyword is prompt text on every call, so these bound
the cost of one form.

## How a call is filled

The AI fields are asked in **passes** ordered by dependency: a field waits for the fields its
condition or dependent dropdown reads; a template field may share a pass with the choice its
template comes from (the prompt lists the candidate templates). No dependencies → one model
call; most real forms → two; never more than three. The transcript sits in the cached system
block, so a second pass re-reads it from the prompt cache. Afterwards code applies `implies`,
re-checks every condition on the final values, coerces each value to its type, renders
templates and flags required fields left empty. Usage is recorded as feature `crm_fill`
(group **CRM form** on `/usage`), attributed to the recording.

## Review

`PATCH /crm/results/{id}` takes `{values, status}`. `values` carries only the fields this
reviewer changed: a field it omits keeps the stored review, and shows the model's value only if
nobody has reviewed it. `{"status": "approved"}` alone approves a draft as it stands. Two people
editing the same field is last write wins.

## Files

* **JSON** — one form: `{"format": "cq.crm-form", "schema_version": 1, "key": "...", "form": {DEFINITION}}`;
  several: `{"format": "cq.crm-forms", "forms": [ ... ]}`. A bare definition is accepted too.
* **Excel** — one form per workbook: sheets `Form` (setting | value), `Fields` (one row per
  field, the columns of the table above), `Options` (`field`, `key`, `label`, `parent`,
  `description`, `keywords`, `implies`, `template`) and `Help`. In cells: keywords one per line
  (or `;`), `implies` as `field=value` per line, slot hints as `name: hint` per line, and a
  condition as text — `request in (a, b); outcome = follow_up`, `any: x = 1; y is filled`,
  `clinic is filled`. Export any form to get a filled-in example.

Import always previews first (`POST /crm/import/preview`): every problem with its path, and for a
key the workspace already has, which fields would be added, removed or changed. Applying
(`POST /crm/import`) re-validates and is all-or-nothing.

An upload is at most **5 MB** and **20 forms**, preview and apply alike, so split a larger
onboarding bundle. A workbook is refused while it is being read once a sheet passes 280 `Fields`
rows or 2 200 `Options` rows (the form limits plus room for stray rows), and a cell is cut at
Excel's own 32 767 characters. Export writes text that starts with `=` as text, never as a
formula, and drops the control characters a workbook cannot hold.

## Onboarding a customer from their CRM's field list

1. One FIELD per CRM field, `key` = the CRM's own field code, `label` = its display name.
2. The call category → a `choice` with an option tree; put the category in `label_field`.
   Fields that follow from the category (department, subcategory) → `derived` + `implies` on
   the options, not separate questions.
3. Anything the CRM knows and the call does not (policy type, customer tier, IDs from the
   customer record) → `external`.
4. Per-type follow-up fields → `when`; per-type sub-reasons → a second choice with `filter_by`.
5. Free-text the operators type from a fixed pattern → `template`, the pattern as written.
6. Words operators actually type or say for each option → `keywords`.
7. Export, import into a test workspace, paste a real transcript into **Try it on a call**.

The medical-insurance preset (`crm_presets.py`, `insurance_ka`) is a complete worked example of
every rule above, modelled on a real call centre's spreadsheet with its clinic and team names
replaced by role labels.
