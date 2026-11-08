import test from 'node:test';
import assert from 'node:assert/strict';
import {
  blankField, blankOption, displayValue, draftEdits, keywordLines, keywordList, optionPath,
  optionsFromLines, problemsByField, recordText, removeField, removeOption, renameField,
  renameOption, reviewValues, slotNames, slugKey, treeOrder, visibleOptions,
  type CrmDefinition, type CrmField, type CrmOption, type ResultField,
} from '../crmForm.ts';

/* The editor's consistency rules. The server validates every save, so a mistake here does not
   corrupt a form — it produces a form the server refuses with "refers to a field that does not
   exist", on a page the person cannot see the cause of. These pin the renames and removals
   that would otherwise produce exactly that. */

const opt = (key: string, parent: string | null = null, extra: Partial<CrmOption> = {}): CrmOption =>
  ({ key, label: key.toUpperCase(), parent, description: '', keywords: [], implies: {}, template: '', ...extra });

function field(key: string, extra: Partial<CrmField> = {}): CrmField {
  return {
    key, label: key, type: 'text', source: 'ai', required: false, instruction: '', when: null,
    default: null, options: [], template: '', template_from: null, filter_by: null, slots: [],
    unit: '', max_length: null, ...extra,
  };
}

const DEFN: CrmDefinition = {
  name: 'T', description: '', language: 'en', output_language: 'call', instructions: '',
  label_field: 'req',
  fields: [
    field('req', { type: 'choice', options: [opt('med', null, { implies: { cat: 'm' } }), opt('out', 'med')] }),
    field('cat', { type: 'choice', source: 'derived', options: [opt('m')] }),
    field('clinic', { when: { match: 'all', rules: [{ field: 'req', op: 'in', value: ['med', 'out'] }] } }),
    field('scenario', { type: 'choice', filter_by: 'req', options: [opt('med'), opt('m1', 'med')] }),
    field('text', { type: 'template', template: '{a}', template_from: 'req' }),
    field('other', { type: 'choice', options: [opt('med')] }),
  ],
};

test('slugKey makes ASCII keys, falls back for Georgian, and never collides', () => {
  assert.equal(slugKey('Customer name', 'f'), 'customer_name');
  assert.equal(slugKey('მოთხოვნა', 'field_3'), 'field_3');
  assert.equal(slugKey('A', 'f', ['a', 'a_2']), 'a_3');
  assert.equal(slugKey('9 lives', 'f'), 'f');
});

test('renaming a field moves every reference', () => {
  const d = renameField(DEFN, 'req', 'request');
  assert.equal(d.label_field, 'request');
  assert.equal(d.fields[0].key, 'request');
  assert.equal(d.fields[2].when!.rules[0].field, 'request');
  assert.equal(d.fields[3].filter_by, 'request');
  assert.equal(d.fields[4].template_from, 'request');
  const d2 = renameField(DEFN, 'cat', 'category');
  assert.deepEqual(d2.fields[0].options[0].implies, { category: 'm' });
});

test('renaming an option moves its children, conditions and implied values', () => {
  const d = renameOption(DEFN, 'req', 'med', 'medical');
  assert.equal(d.fields[0].options[0].key, 'medical');
  assert.equal(d.fields[0].options[1].parent, 'medical');
  assert.deepEqual(d.fields[2].when!.rules[0].value, ['medical', 'out']);
  // another field's option with the same key is a different option
  assert.equal(d.fields[5].options[0].key, 'med');
  const d2 = renameOption(DEFN, 'cat', 'm', 'medical');
  assert.deepEqual(d2.fields[0].options[0].implies, { cat: 'medical' });
});

test('renaming an option moves the root a dependent dropdown mirrors it with', () => {
  // `scenario` is filtered by `req`, so its TOP-LEVEL keys are `req`'s option keys. Left
  // behind, the branch matches nothing and every sub-scenario of every request is offered.
  const d = renameOption(DEFN, 'req', 'med', 'medical');
  assert.deepEqual(d.fields[3].options.map(o => [o.key, o.parent]), [['medical', null], ['m1', 'medical']]);
  // A root keyed like the new name already exists: renaming would mint a duplicate key, so
  // the mirror is left for the author rather than quietly broken in a second way.
  const clash = renameOption({
    ...DEFN,
    fields: DEFN.fields.map(f => (f.key === 'scenario' ? { ...f, options: [opt('med'), opt('medical')] } : f)),
  }, 'req', 'med', 'medical');
  assert.deepEqual(clash.fields[3].options.map(o => o.key), ['med', 'medical']);
});

test('removing a field drops the references that would dangle', () => {
  const d = removeField(DEFN, 'req');
  assert.equal(d.label_field, null);
  assert.equal(d.fields[1].when, null);
  assert.equal(d.fields[2].filter_by, null);
  assert.equal(d.fields[3].template_from, null);
  const d2 = removeField(DEFN, 'cat');
  assert.deepEqual(d2.fields[0].options[0].implies, {});
});

test('removing an option re-parents its children', () => {
  const out = removeOption([opt('a'), opt('b', 'a'), opt('c', 'b')], 'b');
  assert.deepEqual(out.map(o => [o.key, o.parent]), [['a', null], ['c', 'a']]);
});

test('treeOrder shows each parent followed by its branch, and keeps orphans and cycles', () => {
  const rows = treeOrder([opt('child', 'p'), opt('p'), opt('q'), opt('grand', 'child'), opt('x', 'y'), opt('y', 'x')]);
  assert.deepEqual(rows.map(r => `${r.option.key}:${r.depth}`), ['p:0', 'child:1', 'grand:2', 'q:0', 'x:0', 'y:0']);
});

test('the filtered option list still shows the one just added', () => {
  const f = field('service', { type: 'choice', options: [opt('dermatology'), opt('cardiology')] });
  assert.deepEqual(visibleOptions(f.options, 'derm').map(r => r.option.key), ['dermatology']);
  // "+ Add option" under an active filter: blank label, key `opt_3`, nothing matching "derm".
  const added = [...f.options, blankOption(f, '', null)];
  assert.deepEqual(visibleOptions(added, 'derm', 'opt_3').map(r => r.option.key), ['dermatology', 'opt_3']);
  assert.equal(visibleOptions(added, '   ').length, 3);
});

test('a keyword line typed in the middle survives, and blanks go on the way out', () => {
  // The textarea is bound to `keywords.join("\n")`, so typing has to round-trip exactly or the
  // keystroke is undone: Enter between two keywords used to vanish as it was pressed.
  assert.equal(keywordLines('ambulatoria\n\noutpatient').join('\n'), 'ambulatoria\n\noutpatient');
  assert.equal(keywordLines(' lead').join('\n'), ' lead');
  assert.deepEqual(keywordList('ambulatoria\n \n outpatient \n'), ['ambulatoria', 'outpatient']);
});

test('optionPath reads the branch', () => {
  assert.equal(optionPath([opt('a'), opt('b', 'a')], 'b'), 'A › B');
  assert.equal(optionPath([], 'zz'), 'zz');
});

test('pasted lines become options, indentation nests', () => {
  const f = field('f', { type: 'choice' });
  const out = optionsFromLines(f, 'Medical\n  Outpatient\n  - Dental\nTravel\n\n');
  assert.deepEqual(out.map(o => [o.key, o.label, o.parent]), [
    ['medical', 'Medical', null], ['outpatient', 'Outpatient', 'medical'],
    ['dental', 'Dental', 'medical'], ['travel', 'Travel', null]]);
});

test('slot names, including Georgian ones', () => {
  assert.deepEqual(slotNames('ეცნო {მომსახურება}: {X}%[[ {X}]]'), ['მომსახურება', 'X']);
});

test('problems are grouped by field index', () => {
  const m = problemsByField([{ path: 'fields[2].when', message: 'a' }, { path: 'name', message: 'b' },
    { path: 'fields[2].key', message: 'c' }]);
  assert.equal(m.get(2)!.length, 2);
  assert.equal(m.get(-1)!.length, 1);
});

test('blankField picks an unused key', () => {
  assert.equal(blankField({ ...DEFN, fields: [field('field_2')] }).key, 'field_2_2');
});

test('display values and the copied record', () => {
  const rf = (extra: Partial<ResultField>): ResultField => ({
    key: 'k', label: 'K', type: 'text', source: 'ai', required: false, status: 'filled', missing: false,
    value: null, display: '', confidence: null, evidence: [], slots: null, template: null, raw: null,
    unit: '', max_length: null, options: null, ...extra,
  });
  const choice = rf({ type: 'choice', options: [{ key: 'a', label: 'A', parent: null }, { key: 'b', label: 'B', parent: 'a' }] });
  assert.equal(displayValue(choice, 'b'), 'A › B');
  assert.equal(displayValue(rf({ type: 'boolean' }), false, ['კი', 'არა']), 'არა');
  assert.equal(displayValue(rf({ type: 'money', unit: '₾' }), 50), '50 ₾');
  assert.equal(displayValue(rf({}), ''), '');
  const row = {
    final: { k: 'edited' },
    result: { fields: [rf({ value: 'model', label: 'Name' }), rf({ key: 'z', label: 'Gone', status: 'not_applicable' })] },
  } as never;
  assert.equal(recordText(row, ['y', 'n']), 'Name: edited');
});

test('a review draft is dirty only where the reviewer changed something', () => {
  const rf = (key: string, value: unknown): ResultField => ({
    key, label: key.toUpperCase(), type: 'text', source: 'ai', required: false, status: 'filled',
    missing: false, value, display: '', confidence: null, evidence: [], slots: null, template: null,
    raw: null, unit: '', max_length: null, options: null,
  });
  const row = { final: { b: 'reviewed' }, result: { fields: [rf('a', 'model'), rf('b', 'model')] } } as never;
  assert.deepEqual(draftEdits(row, {}), []);
  // Visited and typed back to what it says: nothing to warn about, nothing to lose.
  assert.deepEqual(draftEdits(row, { a: 'model', b: 'reviewed' }), []);
  // Corrections nobody has sent — what a "fill again" or another form would throw away.
  assert.deepEqual(draftEdits(row, { a: 'corrected', b: 'reviewed again' }), ['a', 'b']);
  // Against the model's value when there is no reviewed one, and `null` ≡ empty.
  assert.deepEqual(draftEdits({ final: null, result: { fields: [rf('a', null)] } } as never, { a: '' }), []);
  // A review sends the correction alone: `b` here is this tab's copy, maybe an old one.
  assert.deepEqual(reviewValues(row, { a: 'corrected', b: 'reviewed' }), { a: 'corrected' });
  assert.deepEqual(reviewValues(row, {}), {});
});
