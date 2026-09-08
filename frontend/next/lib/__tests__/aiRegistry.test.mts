import test from 'node:test';
import assert from 'node:assert/strict';
import {
  CAPABILITIES, MODEL_OTHER, asCapability, assignmentOptions, assignmentsPayload,
  connectionPayload, defaultOf, effectiveLabel, formFromConnection, groupConnections,
  modelFromPick, modelOptions, modelPickFor, picksFromAssignments, settingsFields, sourceKey,
  testBadge,
} from '../../app/console/logic.ts';
import type { AiConnection } from '../../app/console/api.ts';

/* The AI provider registry's decisions, shared by the console's registry tab and the per-
   workspace AI setup page. Every rule here is a safe-direction default about credentials or
   about what a picker shows — the kind of thing a tidy-up flips without noticing. */

function conn(over: Partial<AiConnection> & { id: string }): AiConnection {
  return {
    name: over.id, capability: 'llm', provider: 'anthropic', model: null, base_url: null,
    has_key: true, key_hint: '…abcd', settings: {}, is_active: true, is_default: false,
    last_test: null, updated_at: null, updated_by: null,
    ...over,
  };
}

const ENTRY = { label: 'Anthropic', known_models: ['claude-a', 'claude-b'], allows_base_url: true, fields: [] };
const NO_URL = { ...ENTRY, allows_base_url: false };

test('asCapability narrows to the three known capabilities and nothing else', () => {
  assert.deepEqual(CAPABILITIES, ['llm', 'stt', 'tts']);
  assert.equal(asCapability('llm'), 'llm');
  assert.equal(asCapability('embeddings'), null);
  assert.equal(asCapability(undefined), null);
});

test('groupConnections: default first, active by name, inactive last; unknown capability dropped', () => {
  const rows = [
    conn({ id: 'z', name: 'Zed' }),
    conn({ id: 'off', name: 'Aardvark', is_active: false }),
    conn({ id: 'd', name: 'Mid', is_default: true }),
    conn({ id: 'a', name: 'Alpha' }),
    conn({ id: 'v', name: 'Voice', capability: 'tts' }),
    { ...conn({ id: 'x', name: 'X' }), capability: 'video' as never },
  ];
  const g = groupConnections(rows);
  assert.deepEqual(g.llm.map(c => c.id), ['d', 'a', 'z', 'off']);
  assert.deepEqual(g.tts.map(c => c.id), ['v']);
  assert.deepEqual(g.stt, []);
});

test('defaultOf: an inactive default is not a default', () => {
  assert.equal(defaultOf([conn({ id: 'a', is_default: true })])?.id, 'a');
  assert.equal(defaultOf([conn({ id: 'a', is_default: true, is_active: false })]), null);
  assert.equal(defaultOf([]), null);
});

test('testBadge: never / works / failed', () => {
  assert.deepEqual(testBadge(null), { cls: 'notinkb', key: 'ai.test.never' });
  assert.deepEqual(testBadge({ ok: true, at: 'x' }), { cls: 'ready', key: 'ai.test.ok' });
  assert.deepEqual(testBadge({ ok: false, detail: 'nope' }), { cls: 'error', key: 'ai.test.fail' });
  // A malformed blob from an older server reads as untested rather than as a pass.
  assert.equal(testBadge({} as never).key, 'ai.test.never');
});

test('model picker: known id selects itself, unknown selects Other with the id typed, empty is empty', () => {
  const known = ['claude-a', 'claude-b'];
  assert.deepEqual(modelPickFor(known, 'claude-b'), { pick: 'claude-b', text: '' });
  assert.deepEqual(modelPickFor(known, 'claude-zeta'), { pick: MODEL_OTHER, text: 'claude-zeta' });
  assert.deepEqual(modelPickFor(known, null), { pick: '', text: '' });
  assert.deepEqual(modelPickFor(known, '  '), { pick: '', text: '' });
});

test('modelFromPick: Other with nothing typed is null, never an empty-string model id', () => {
  assert.equal(modelFromPick('claude-a', 'ignored'), 'claude-a');
  assert.equal(modelFromPick(MODEL_OTHER, ' my-model '), 'my-model');
  assert.equal(modelFromPick(MODEL_OTHER, ''), null);
  assert.equal(modelFromPick('', ''), null);
});

test('modelOptions: empty, the known ids, then Other — in that order', () => {
  const o = modelOptions(['a', 'b'], { none: 'Default', other: 'Other…' });
  assert.deepEqual(o.map(x => x.value), ['', 'a', 'b', MODEL_OTHER]);
  assert.equal(o[0].label, 'Default');
  assert.equal(o[3].label, 'Other…');
});

test('settingsFields: a TTS connection always has somewhere to put a voice id', () => {
  assert.deepEqual(settingsFields('tts', { ...ENTRY, fields: [] }), ['voice_id']);
  assert.deepEqual(settingsFields('tts', { ...ENTRY, fields: ['voice_id', 'stability'] }), ['voice_id', 'stability']);
  assert.deepEqual(settingsFields('llm', { ...ENTRY, fields: [] }), []);
  assert.deepEqual(settingsFields('stt', undefined), []);
});

const FORM = {
  name: ' Prod ', provider: 'anthropic', model: 'claude-a', baseUrl: ' https://gw.example ',
  apiKey: '', clearKey: false, settings: { voice_id: ' v1 ', stability: '' },
};

test('connectionPayload: the key is sent only when typed; an empty box keeps the stored key', () => {
  const body = connectionPayload('llm', FORM, ENTRY);
  assert.equal('api_key' in body, false);
  assert.equal('clear_key' in body, false);
  const typed = connectionPayload('llm', { ...FORM, apiKey: ' sk-new ' }, ENTRY);
  assert.equal(typed.api_key, 'sk-new');
  assert.equal('clear_key' in typed, false);
});

test('connectionPayload: clear_key only when asked AND nothing was typed', () => {
  const cleared = connectionPayload('llm', { ...FORM, clearKey: true }, ENTRY);
  assert.equal(cleared.clear_key, true);
  assert.equal('api_key' in cleared, false);
  // A typed key is a replacement — the clear flag is dropped, not sent alongside it.
  const both = connectionPayload('llm', { ...FORM, clearKey: true, apiKey: 'sk-new' }, ENTRY);
  assert.equal('clear_key' in both, false);
  assert.equal(both.api_key, 'sk-new');
});

test('connectionPayload: base_url only for a provider that allows one', () => {
  assert.equal(connectionPayload('llm', FORM, ENTRY).base_url, 'https://gw.example');
  assert.equal(connectionPayload('llm', { ...FORM, baseUrl: '  ' }, ENTRY).base_url, null);
  assert.equal('base_url' in connectionPayload('llm', FORM, NO_URL), false);
  assert.equal('base_url' in connectionPayload('llm', FORM, undefined), false);
});

test('connectionPayload: the rest — trimmed name, capability, null model, non-empty settings', () => {
  const body = connectionPayload('tts', { ...FORM, model: null }, ENTRY);
  assert.equal(body.name, 'Prod');
  assert.equal(body.capability, 'tts');
  assert.equal(body.provider, 'anthropic');
  assert.equal(body.model, null);
  assert.deepEqual(body.settings, { voice_id: 'v1' });
});

test('formFromConnection: a stored connection opens with an EMPTY key box and its settings', () => {
  const c = conn({ id: 'a', name: 'A', model: 'claude-a', base_url: 'https://x', settings: { voice_id: 7 } });
  const f = formFromConnection('tts', c, ENTRY, 'elevenlabs');
  assert.equal(f.apiKey, '');
  assert.equal(f.clearKey, false);
  assert.equal(f.name, 'A');
  assert.equal(f.provider, 'anthropic');
  assert.equal(f.model, 'claude-a');
  assert.equal(f.baseUrl, 'https://x');
  assert.deepEqual(f.settings, { voice_id: '7' });
  // Blank: the fallback provider, nothing else.
  const blank = formFromConnection('llm', null, ENTRY, 'anthropic');
  assert.equal(blank.provider, 'anthropic');
  assert.equal(blank.model, null);
  assert.deepEqual(blank.settings, {});
});

const LABELS = {
  default: (name: string | null) => (name ? `Default (${name})` : 'Default'),
  connection: (c: AiConnection) => c.name,
};

test('assignmentOptions: default first (named when there is one), active connections only', () => {
  const group = [
    conn({ id: 'd', name: 'Main', is_default: true }),
    conn({ id: 'b', name: 'Backup' }),
    conn({ id: 'x', name: 'Old', is_active: false }),
  ];
  const o = assignmentOptions(group, null, LABELS);
  assert.deepEqual(o.map(x => [x.value, x.label]), [['', 'Default (Main)'], ['d', 'Main'], ['b', 'Backup']]);
  assert.equal(assignmentOptions([conn({ id: 'b', name: 'Backup' })], null, LABELS)[0].label, 'Default');
});

test('assignmentOptions: a deactivated connection that is still assigned stays in the list, disabled', () => {
  const group = [conn({ id: 'x', name: 'Old', is_active: false }), conn({ id: 'b', name: 'Backup' })];
  const o = assignmentOptions(group, 'x', LABELS);
  const stale = o.find(x => x.value === 'x');
  assert.ok(stale);
  assert.equal(stale.disabled, true);
  assert.equal(stale.label, 'Old');
  // An assignment to an id the list has never heard of is shown as the id, still disabled.
  const gone = assignmentOptions(group, 'ghost', LABELS).find(x => x.value === 'ghost');
  assert.equal(gone?.disabled, true);
  assert.equal(gone?.label, 'ghost');
});

test('assignmentsPayload sends every capability, null for default', () => {
  assert.deepEqual(assignmentsPayload({ llm: 'a' }), { llm: 'a', stt: null, tts: null });
  assert.deepEqual(assignmentsPayload({ llm: ' ', stt: 'b', tts: '' }), { llm: null, stt: 'b', tts: null });
});

test('picksFromAssignments reads the assigned id, empty for default or missing', () => {
  const eff = { source: 'default', provider: 'anthropic', model: null, connection: null };
  assert.deepEqual(
    picksFromAssignments({ llm: { connection_id: 'a', effective: eff }, stt: { connection_id: null, effective: eff } }),
    { llm: 'a', stt: '', tts: '' },
  );
});

test('sourceKey: the four sources, and an unknown one reads as legacy', () => {
  assert.equal(sourceKey('byo'), 'ai.source.byo');
  assert.equal(sourceKey('assigned'), 'ai.source.assigned');
  assert.equal(sourceKey('default'), 'ai.source.default');
  assert.equal(sourceKey('legacy'), 'ai.source.legacy');
  assert.equal(sourceKey('galactic'), 'ai.source.legacy');
  assert.equal(sourceKey(undefined), 'ai.source.legacy');
});

test('effectiveLabel: connection name over provider, model beside it, null when nothing is known', () => {
  assert.deepEqual(
    effectiveLabel({ source: 'assigned', provider: 'openai', model: 'gpt-x', connection: { id: '1', name: 'GPT prod' } }),
    { head: 'GPT prod', model: 'gpt-x' },
  );
  assert.deepEqual(
    effectiveLabel({ source: 'legacy', provider: 'anthropic', model: null, connection: null }),
    { head: 'anthropic', model: '' },
  );
  assert.equal(effectiveLabel({ source: 'legacy', provider: '', model: null, connection: null }), null);
  assert.equal(effectiveLabel(null), null);
});
