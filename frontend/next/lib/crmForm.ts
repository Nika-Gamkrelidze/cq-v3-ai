/* The CRM form, browser side: its shapes, and the editing rules that keep it consistent.
   ===================================================================================
   The SERVER owns validation (`backend/app/services/crm_form.py` — every door a form comes in
   through goes through `normalize_definition`), so nothing here decides whether a form is
   valid. What lives here is what an editor has to get right while a person is typing:

     * RENAMING A KEY MOVES EVERY REFERENCE TO IT. A field key appears in conditions, in other
       options' `implies`, in `template_from`, `filter_by` and `label_field`; an option key in
       condition values and implied values. Renaming one without the others turns a working
       form into a page of "refers to a field that does not exist".
     * REMOVING AN OPTION KEEPS ITS CHILDREN, re-parented one level up, rather than leaving
       them pointing at a parent that is gone.
     * THE OPTION TREE IS SHOWN IN TREE ORDER (each parent followed by its branch) whatever
       order the options were added in, which is the only order a 60-option list can be read.

   Dependency-free on purpose: `lib/__tests__/crmForm.test.mts` runs it under plain Node. */

export type FieldType =
  | 'text' | 'textarea' | 'number' | 'money' | 'percent' | 'date' | 'datetime' | 'time'
  | 'boolean' | 'phone' | 'email' | 'choice' | 'multichoice' | 'template';
export type Source = 'ai' | 'derived' | 'external' | 'fixed';
export type Op = 'is' | 'is_not' | 'in' | 'not_in' | 'filled' | 'empty';

export const FIELD_TYPES: FieldType[] = [
  'text', 'textarea', 'choice', 'multichoice', 'template', 'number', 'money', 'percent',
  'date', 'datetime', 'time', 'boolean', 'phone', 'email',
];
export const SOURCES: Source[] = ['ai', 'derived', 'external', 'fixed'];
export const OPS: Op[] = ['is', 'is_not', 'in', 'not_in', 'filled', 'empty'];
export const CHOICE_TYPES: FieldType[] = ['choice', 'multichoice'];
export const KEY_RE = /^[A-Za-z][A-Za-z0-9_.-]{0,63}$/;

export interface CrmOption {
  key: string;
  label: string;
  parent: string | null;
  description: string;
  keywords: string[];
  implies: Record<string, unknown>;
  template: string;
}

export interface Rule { field: string; op: Op; value: string | string[] | boolean | null }
export interface When { match: 'all' | 'any'; rules: Rule[] }
export interface Slot { name: string; instruction: string }

export interface CrmField {
  key: string;
  label: string;
  type: FieldType;
  source: Source;
  required: boolean;
  instruction: string;
  when: When | null;
  default: unknown;
  options: CrmOption[];
  template: string;
  template_from: string | null;
  filter_by: string | null;
  slots: Slot[];
  unit: string;
  max_length: number | null;
}

export interface CrmDefinition {
  name: string;
  description: string;
  language: 'ka' | 'en' | 'ru';
  output_language: 'call' | 'form';
  instructions: string;
  label_field: string | null;
  fields: CrmField[];
}

export interface Problem { path: string; message: string }

export interface FormSummary { fields: number; options: number; ai_fields: number }

/** A row of `GET /crm/forms` (no definition) or a full `GET /crm/forms/{key}`. */
export interface FormRow {
  form_key: string;
  version: number;
  name: string;
  is_primary: boolean;
  is_default: boolean;
  updated_at: string | null;
  updated_by: string | null;
  description?: string;
  summary?: FormSummary;
  definition?: CrmDefinition;
}

export interface Evidence { quote: string; segments: number[]; start: number | null; end: number | null }

export type FieldStatus = 'filled' | 'empty' | 'invalid' | 'external' | 'not_applicable';

export interface ResultField {
  key: string;
  label: string;
  type: FieldType;
  source: Source;
  required: boolean;
  status: FieldStatus;
  missing: boolean;
  value: unknown;
  display: string;
  confidence: 'high' | 'medium' | 'low' | null;
  evidence: Evidence[];
  slots: Record<string, string> | null;
  template: string | null;
  raw: string | null;
  unit: string;
  max_length: number | null;
  options: { key: string; label: string; parent: string | null }[] | null;
}

export interface CrmResult {
  form_key: string;
  form_version: number;
  form_name: string;
  language: string;
  label_field: string | null;
  label: { field: string; key: string; text: string } | null;
  fields: ResultField[];
  missing_required: string[];
  model_calls: number;
}

export interface CrmResultRow {
  id: string;
  job_id: string | null;
  form_key: string;
  form_version: number;
  form_name: string | null;
  label: string | null;
  result: CrmResult;
  final: Record<string, unknown> | null;
  status: 'draft' | 'approved';
  created_at: string;
  created_by: string | null;
  reviewed_at: string | null;
  reviewed_by: string | null;
}

/* ------------------------------------------------------------------ keys */

/** An ASCII key from a label, or `fallback` when the label has none (a Georgian label slugs to
    nothing — the server does the same, and an honest `field_3` beats a transliteration nobody
    chose). Never one of `taken`. */
export function slugKey(label: string, fallback: string, taken: Iterable<string> = []): string {
  const used = new Set(taken);
  let base = (label || '').replace(/[^A-Za-z0-9]+/g, '_').replace(/^_+|_+$/g, '').toLowerCase().slice(0, 40);
  if (!base || !/^[a-z]/.test(base)) base = fallback;
  if (!used.has(base)) return base;
  let n = 2;
  while (used.has(`${base}_${n}`)) n += 1;
  return `${base}_${n}`;
}

export function blankField(defn: CrmDefinition, type: FieldType = 'text'): CrmField {
  const key = slugKey('', `field_${defn.fields.length + 1}`, defn.fields.map(f => f.key));
  return {
    key, label: '', type, source: 'ai', required: false, instruction: '', when: null,
    default: null, options: [], template: '', template_from: null, filter_by: null, slots: [],
    unit: '', max_length: null,
  };
}

export function blankOption(field: CrmField, label = '', parent: string | null = null): CrmOption {
  const key = slugKey(label, `opt_${field.options.length + 1}`, field.options.map(o => o.key));
  return { key, label, parent, description: '', keywords: [], implies: {}, template: '' };
}

/** Rename a field key and every reference to it. Returns a new definition. */
export function renameField(defn: CrmDefinition, from: string, to: string): CrmDefinition {
  if (from === to) return defn;
  const swap = (k: string | null) => (k === from ? to : k);
  return {
    ...defn,
    label_field: swap(defn.label_field),
    fields: defn.fields.map(f => ({
      ...f,
      key: f.key === from ? to : f.key,
      template_from: swap(f.template_from),
      filter_by: swap(f.filter_by),
      when: f.when ? { ...f.when, rules: f.when.rules.map(r => ({ ...r, field: r.field === from ? to : r.field })) } : null,
      options: f.options.map(o => ({
        ...o,
        implies: Object.fromEntries(Object.entries(o.implies || {}).map(([k, v]) => [k === from ? to : k, v])),
      })),
    })),
  };
}

/** Rename one option of `fieldKey` and every reference to it: its children's `parent`,
    condition values that name it, values other options imply for that field, and the TOP-LEVEL
    option a dependent dropdown mirrors it with (`filter_by`). */
export function renameOption(defn: CrmDefinition, fieldKey: string, from: string, to: string): CrmDefinition {
  if (from === to) return defn;
  const swapVal = (v: Rule['value']): Rule['value'] =>
    Array.isArray(v) ? v.map(x => (x === from ? to : x)) : v === from ? to : v;
  return {
    ...defn,
    fields: defn.fields.map(f => {
      /* A `filter_by` dropdown scopes itself by keying its ROOT options like the options of the
         source field, so a rename here has to move that root too. Leaving it behind breaks the
         scoping silently: the server's only guard warns when NO root matches any more, and one
         surviving sibling keeps it quiet, after which `offered_options` falls back to offering
         every leaf of every branch — the exact mix-up the dependent dropdown exists to prevent.
         Skipped when the mirror already has a root keyed `to`, because inventing a duplicate key
         is worse than the mismatch: that branch exists and the author can re-point it. */
      const mirrors = f.filter_by === fieldKey
        && f.options.some(o => o.key === from && !o.parent)
        && !f.options.some(o => o.key === to);
      const own = f.key === fieldKey;
      return {
        ...f,
        options: f.options.map(o => ({
          ...o,
          key: (own || mirrors) && o.key === from ? to : o.key,
          parent: (own || mirrors) && o.parent === from ? to : o.parent,
          implies: Object.fromEntries(Object.entries(o.implies || {}).map(([k, v]) =>
            [k, k === fieldKey && v === from ? to : v])),
        })),
        when: f.when ? {
          ...f.when,
          rules: f.when.rules.map(r => (r.field === fieldKey ? { ...r, value: swapVal(r.value) } : r)),
        } : null,
      };
    }),
  };
}

/** Remove a field and the references that would otherwise dangle. Conditions that named it
    lose that rule; a condition left with no rules becomes "always". */
export function removeField(defn: CrmDefinition, key: string): CrmDefinition {
  const drop = (k: string | null) => (k === key ? null : k);
  return {
    ...defn,
    label_field: drop(defn.label_field),
    fields: defn.fields.filter(f => f.key !== key).map(f => {
      const rules = (f.when?.rules || []).filter(r => r.field !== key);
      return {
        ...f,
        template_from: drop(f.template_from),
        filter_by: drop(f.filter_by),
        when: rules.length ? { match: f.when!.match, rules } : null,
        options: f.options.map(o => {
          const implies = { ...(o.implies || {}) };
          delete implies[key];
          return { ...o, implies };
        }),
      };
    }),
  };
}

/** Remove an option; its children move up to its parent. */
export function removeOption(options: CrmOption[], key: string): CrmOption[] {
  const gone = options.find(o => o.key === key);
  if (!gone) return options;
  return options.filter(o => o.key !== key).map(o => (o.parent === key ? { ...o, parent: gone.parent } : o));
}

/* ------------------------------------------------------------------ the option tree */

export interface TreeRow { option: CrmOption; depth: number; index: number }

/** Options in tree order with their depth. An option whose parent is missing, or that sits in
    a cycle, is shown at the top level rather than lost. */
export function treeOrder(options: CrmOption[]): TreeRow[] {
  const keys = new Set(options.map(o => o.key));
  const children = new Map<string | null, number[]>();
  options.forEach((o, i) => {
    const p = o.parent && keys.has(o.parent) && o.parent !== o.key ? o.parent : null;
    const list = children.get(p) || [];
    list.push(i);
    children.set(p, list);
  });
  const out: TreeRow[] = [];
  const seen = new Set<number>();
  const walk = (parent: string | null, depth: number) => {
    for (const i of children.get(parent) || []) {
      if (seen.has(i)) continue;
      seen.add(i);
      out.push({ option: options[i], depth, index: i });
      walk(options[i].key, depth + 1);
    }
  };
  walk(null, 0);
  // Anything a cycle kept unreachable.
  options.forEach((o, i) => { if (!seen.has(i)) out.push({ option: o, depth: 0, index: i }); });
  return out;
}

/** The rows the option list shows under a filter: tree order, matched on label or key, plus
    `keep` whatever the filter says. `keep` is the option whose detail panel is open, which is
    the one just added — a fresh option has an empty label and a key like `opt_41`, so without
    this a filtered list swallows it and the "+ Add option" click looks like it did nothing,
    while the blank option is really there and saves as an option named after its own key. */
export function visibleOptions(options: CrmOption[], filter: string, keep?: string | null): TreeRow[] {
  const q = filter.trim().toLowerCase();
  const rows = treeOrder(options);
  if (!q) return rows;
  return rows.filter(r => r.option.key === keep
    || r.option.label.toLowerCase().includes(q) || r.option.key.toLowerCase().includes(q));
}

/** "Medical › Outpatient" — an option as it reads in a list. */
export function optionPath(options: { key: string; label: string; parent: string | null }[], key: string): string {
  const by = new Map(options.map(o => [o.key, o]));
  const parts: string[] = [];
  let cur = by.get(key);
  let guard = 0;
  while (cur && guard < 8) {
    parts.unshift(cur.label || cur.key);
    cur = cur.parent ? by.get(cur.parent) : undefined;
    guard += 1;
  }
  return parts.join(' › ') || key;
}

/** Labels pasted one per line become options; a line indented with spaces or a tab nests
    under the nearest less-indented line above it, so a tree can be pasted from a document. */
export function optionsFromLines(field: CrmField, text: string): CrmOption[] {
  const out = [...field.options];
  const stack: { indent: number; key: string }[] = [];
  for (const raw of text.split(/\r?\n/)) {
    if (!raw.trim()) continue;
    const indent = raw.length - raw.replace(/^[\t ]+/, '').length;
    while (stack.length && stack[stack.length - 1].indent >= indent) stack.pop();
    const label = raw.trim().replace(/^[-•*]\s*/, '');
    const opt = blankOption({ ...field, options: out }, label, stack.length ? stack[stack.length - 1].key : null);
    out.push(opt);
    stack.push({ indent, key: opt.key });
  }
  return out;
}

/** The keyword box WHILE IT IS BEING TYPED: every line, blank ones included. The box is a
    controlled textarea bound to `keywords.join('\n')`, so anything dropped here is a keystroke
    undone — React writes the old string back and the caret jumps to the end. Dropping blank
    lines on the way in therefore made Enter work only at the bottom of the list: a newline
    typed between two keywords vanished as it was pressed. `keywordList` does the tidying on
    blur instead, where the person has finished. */
export function keywordLines(text: string): string[] {
  return text.split('\n');
}

/** The keywords as they are kept: trimmed, with the blank lines gone. */
export function keywordList(text: string): string[] {
  return text.split('\n').map(s => s.trim()).filter(Boolean);
}

/* ------------------------------------------------------------------ templates + problems */

export function slotNames(template: string): string[] {
  const out: string[] = [];
  for (const m of (template || '').matchAll(/\{([^{}\n]{1,60})\}/g)) {
    const name = m[1].trim();
    if (name && !out.includes(name)) out.push(name);
  }
  return out;
}

/** Server problems grouped by the field they point at (`fields[3].options[2].key` → 3);
    form-level problems under -1. */
export function problemsByField(problems: Problem[]): Map<number, Problem[]> {
  const out = new Map<number, Problem[]>();
  for (const p of problems) {
    const m = /^fields\[(\d+)\]/.exec(p.path || '');
    const i = m ? Number(m[1]) : -1;
    out.set(i, [...(out.get(i) || []), p]);
  }
  return out;
}

export function move<T>(list: T[], i: number, dir: -1 | 1): T[] {
  const j = i + dir;
  if (j < 0 || j >= list.length) return list;
  const out = [...list];
  [out[i], out[j]] = [out[j], out[i]];
  return out;
}

/* ------------------------------------------------------------------ filled forms */

/** A value as it reads to a person. Yes/no words come from the caller, in its language. */
export function displayValue(
  field: Pick<ResultField, 'type' | 'options' | 'unit'>,
  value: unknown,
  yesNo: [string, string] = ['yes', 'no'],
): string {
  if (value === null || value === undefined || value === '' || (Array.isArray(value) && !value.length)) return '';
  const opts = field.options || [];
  switch (field.type) {
    case 'choice': return optionPath(opts, String(value));
    case 'multichoice': return (Array.isArray(value) ? value : [value]).map(v => optionPath(opts, String(v))).join(', ');
    case 'boolean': return value ? yesNo[0] : yesNo[1];
    case 'percent': return `${value}%`;
    case 'money': return field.unit ? `${value} ${field.unit}` : String(value);
    case 'datetime': return String(value).replace('T', ' ');
    default: return String(value);
  }
}

/** The value a filled form shows for a field: the reviewer's when there is one. */
export function finalValue(row: Pick<CrmResultRow, 'final'>, field: ResultField): unknown {
  return row.final && field.key in row.final ? row.final[field.key] : field.value;
}

/** The fields a reviewer has really changed: a draft value that differs from the one the row
    shows today. The draft is keyed per keystroke, so a value typed and typed back is not an
    edit — and nothing may be thrown away, or warned about, over a field somebody only visited.
    What hangs on it: the review draft lives in memory until it is saved, and every control
    that swaps the shown row (fill again, another form, another recording) unmounts it. */
export function draftEdits(
  row: Pick<CrmResultRow, 'final' | 'result'>,
  draft: Record<string, unknown>,
): string[] {
  return row.result.fields
    .filter(f => f.key in draft && !sameJson(draft[f.key], finalValue(row, f)))
    .map(f => f.key);
}

/** What a review sends: the changed fields and nothing else. Sending the rest as well would
    send this tab's copy of them, and a copy loaded before a colleague's correction would put
    the old value back; the server keeps whatever a PATCH leaves out. */
export function reviewValues(
  row: Pick<CrmResultRow, 'final' | 'result'>,
  draft: Record<string, unknown>,
): Record<string, unknown> {
  return Object.fromEntries(draftEdits(row, draft).map(k => [k, draft[k]]));
}

/** Equal for this purpose. Every shape of empty counts as the same one: a reviewer who clicked
    into a field the model left empty and cleared it again has changed nothing a person would
    recognise as a change, and `""` against `null` is not worth a dialog. */
function sameJson(a: unknown, b: unknown): boolean {
  const blank = (v: unknown) => v === null || v === undefined || v === '' || (Array.isArray(v) && !v.length);
  if (blank(a) || blank(b)) return blank(a) && blank(b);
  return JSON.stringify(a) === JSON.stringify(b);
}

/** "Label: value" lines — what Copy puts on the clipboard for pasting into a CRM by hand. */
export function recordText(row: Pick<CrmResultRow, 'final' | 'result'>, yesNo: [string, string]): string {
  return row.result.fields
    .filter(f => f.status !== 'not_applicable')
    .map(f => `${f.label}: ${displayValue(f, finalValue(row, f), yesNo)}`)
    .join('\n');
}
