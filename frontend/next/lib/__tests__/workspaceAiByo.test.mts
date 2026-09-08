import test from 'node:test';
import assert from 'node:assert/strict';
import {
  canSave, EMPTY_CAP, isByo, isDirty, keyOnFile, modelFor, modelOptions, providerOptions,
  readAiConfig, readCap, toBody, toDraft, type CapConfig, type Override,
} from '../../app/workspace/aiByo.ts';

/* The workspace's own AI subscription. Three properties carry the feature:

     1. A KEY NEVER COMES BACK, AND A KEY IS NEVER SENT BY ACCIDENT. The reply carries only
        `has_key` and a masked hint; the body carries `api_key` only when one was typed, so an
        unrelated model change cannot wipe the stored one — and it carries no `base_url`,
        ever, because a tenant-set endpoint could keep every transcript it is handed.
     2. THE FORM OPENS ON WHAT IS IN EFFECT. A customer bringing a key usually wants the same
        model on their own account; the seed says so instead of opening blank.
     3. A KEY IS A KEY FOR A PROVIDER. Switching provider drops the model id and stops the
        stored key from counting, so a Claude key is never saved under OpenAI. */

const cap = (over: Partial<CapConfig> = {}): CapConfig => ({ ...EMPTY_CAP, ...over });
const withKey: Override = { provider: 'anthropic', model: 'claude-x', hasKey: true, keyHint: '…ab12' };

/* ------------------------------------------------------------------ reading a reply */

test('a capability block is read with its effective line, override, providers and models', () => {
  const c = readCap({
    effective: { source: 'assigned', provider: 'anthropic', model: 'claude-x', connection_name: 'Claude — prod' },
    override: { provider: 'openai', model: 'gpt-y', has_key: true, key_hint: '…zz99' },
    providers: ['anthropic', 'openai', 'gemini'],
    known_models: { anthropic: ['claude-x', 'claude-y'], openai: ['gpt-y'] },
  });
  assert.deepEqual(c.effective, { source: 'assigned', provider: 'anthropic', model: 'claude-x', connectionName: 'Claude — prod' });
  assert.deepEqual(c.override, { provider: 'openai', model: 'gpt-y', hasKey: true, keyHint: '…zz99' });
  assert.deepEqual(c.providers, ['anthropic', 'openai', 'gemini']);
  assert.deepEqual(c.knownModels.anthropic, ['claude-x', 'claude-y']);
});

test('providers may arrive as objects carrying their own known models', () => {
  const c = readCap({
    providers: [{ id: 'anthropic', label: 'Anthropic', known_models: ['claude-x'] }, { id: 'openai', known_models: [] }],
  });
  assert.deepEqual(c.providers, ['anthropic', 'openai']);
  assert.deepEqual(c.knownModels, { anthropic: ['claude-x'] });
});

test('a catalog beside the capabilities is read for known models too', () => {
  const cfg = readAiConfig({
    llm: { providers: ['gemini'] },
    catalog: { llm: { gemini: { label: 'Google', known_models: ['gemini-z'] } } },
  });
  assert.deepEqual(cfg.caps.llm.knownModels.gemini, ['gemini-z']);
});

test('the connection name is read from either spelling', () => {
  assert.equal(readCap({ effective: { connection: { id: '1', name: 'Voice A' } } }).effective.connectionName, 'Voice A');
  assert.equal(readCap({ effective: { connection_name: 'Voice B' } }).effective.connectionName, 'Voice B');
});

test('a reply that says nothing reads as nothing, never as a crash', () => {
  const cfg = readAiConfig(undefined);
  assert.deepEqual(cfg.caps.stt, EMPTY_CAP);
  assert.equal(cfg.canEdit, null);
  // A wrong-typed field is "not stated": no key is not a key hint.
  const c = readCap({ effective: { source: 'magic', provider: 7 }, override: { has_key: 'yes', key_hint: 42 } });
  assert.equal(c.effective.source, null);
  assert.equal(c.effective.provider, '');
  assert.equal(c.override?.hasKey, false);
  assert.equal(c.override?.keyHint, null);
});

test('only an explicit can_edit:false closes the form', () => {
  assert.equal(readAiConfig({ can_edit: false }).canEdit, false);
  assert.equal(readAiConfig({ can_edit: 'no' }).canEdit, null);
});

test('an override on a provider the list dropped stays selectable', () => {
  const c = readCap({ providers: ['anthropic'], override: { provider: 'legacy-x', has_key: true } });
  assert.deepEqual(c.providers, ['anthropic', 'legacy-x']);
  assert.deepEqual(providerOptions(cap({ providers: ['anthropic'] }), 'typed'), ['anthropic', 'typed']);
});

test('the pill follows the server\'s source, and the override only when there is no source', () => {
  assert.equal(isByo(cap({ effective: { ...EMPTY_CAP.effective, source: 'byo' } })), true);
  assert.equal(isByo(cap({ effective: { ...EMPTY_CAP.effective, source: 'assigned' }, override: withKey })), false);
  assert.equal(isByo(cap({ override: withKey })), true);
  assert.equal(isByo(cap()), false);
});

/* ------------------------------------------------------------------ seeding the form */

test('the form opens on what is in effect', () => {
  const d = toDraft(cap({
    effective: { source: 'default', provider: 'anthropic', model: 'claude-x', connectionName: null },
    providers: ['anthropic', 'openai'],
  }));
  assert.deepEqual(d, { provider: 'anthropic', model: 'claude-x', apiKey: '' });
});

test('the form opens on the saved override when there is one, with an empty key box', () => {
  const d = toDraft(cap({ override: withKey, providers: ['anthropic'] }));
  assert.deepEqual(d, { provider: 'anthropic', model: 'claude-x', apiKey: '' });
});

test('an effective provider the tenant may not pick falls to the first offered one', () => {
  const d = toDraft(cap({
    effective: { source: 'legacy', provider: 'secret-gateway', model: 'm', connectionName: null },
    providers: ['openai'],
    knownModels: { openai: ['gpt-y', 'gpt-z'] },
  }));
  assert.equal(d.provider, 'openai');
  assert.equal(d.model, 'gpt-y');            // not the other provider's model
});

test('switching provider keeps the model only if the new provider knows it', () => {
  const c = cap({ knownModels: { openai: ['gpt-y', 'gpt-z'] } });
  assert.equal(modelFor(c, 'openai', 'gpt-z'), 'gpt-z');
  assert.equal(modelFor(c, 'openai', 'claude-x'), 'gpt-y');
  assert.equal(modelFor(c, 'gemini', 'claude-x'), '');   // no list: free text, starts empty
});

test('a saved custom model id stays on the dropdown', () => {
  const c = cap({ knownModels: { anthropic: ['claude-x'] } });
  assert.deepEqual(modelOptions(c, 'anthropic', 'claude-custom'), ['claude-x', 'claude-custom']);
  assert.deepEqual(modelOptions(c, 'anthropic', 'claude-x'), ['claude-x']);
  assert.deepEqual(modelOptions(c, 'anthropic', '  '), ['claude-x']);
});

/* ------------------------------------------------------------------ what is sent */

test('the body never carries base_url, and api_key only when typed', () => {
  const quiet = toBody({ provider: 'anthropic', model: ' claude-x ', apiKey: '  ' });
  assert.deepEqual(quiet, { provider: 'anthropic', model: 'claude-x' });
  assert.equal('api_key' in quiet, false);
  assert.equal('base_url' in quiet, false);
  const typed = toBody({ provider: 'openai', model: '', apiKey: ' sk-1 ' });
  assert.deepEqual(typed, { provider: 'openai', model: null, api_key: 'sk-1' });
});

test('a stored key counts only for the provider it was saved for', () => {
  assert.equal(keyOnFile({ provider: 'anthropic', model: '', apiKey: '' }, withKey), true);
  assert.equal(keyOnFile({ provider: 'openai', model: '', apiKey: '' }, withKey), false);
  assert.equal(keyOnFile({ provider: 'anthropic', model: '', apiKey: '' }, { ...withKey, hasKey: false }), false);
  assert.equal(keyOnFile({ provider: 'anthropic', model: '', apiKey: '' }, null), false);
});

test('save needs a provider and a key — typed now, or stored for that same provider', () => {
  assert.equal(canSave({ provider: 'anthropic', model: '', apiKey: '' }, null), false);
  assert.equal(canSave({ provider: 'anthropic', model: '', apiKey: 'sk-1' }, null), true);
  assert.equal(canSave({ provider: '', model: '', apiKey: 'sk-1' }, null), false);
  assert.equal(canSave({ provider: 'anthropic', model: 'claude-y', apiKey: '' }, withKey), true);
  assert.equal(canSave({ provider: 'openai', model: '', apiKey: '' }, withKey), false);
});

test('dirty means Test would report on something other than what is on screen', () => {
  assert.equal(isDirty({ provider: 'anthropic', model: 'claude-x', apiKey: '' }, withKey), false);
  assert.equal(isDirty({ provider: 'anthropic', model: 'claude-x', apiKey: 'sk-2' }, withKey), true);
  assert.equal(isDirty({ provider: 'anthropic', model: 'claude-y', apiKey: '' }, withKey), true);
  assert.equal(isDirty({ provider: 'openai', model: 'claude-x', apiKey: '' }, withKey), true);
  assert.equal(isDirty({ provider: 'anthropic', model: '', apiKey: '' }, { ...withKey, model: null }), false);
  assert.equal(isDirty({ provider: 'anthropic', model: '', apiKey: '' }, null), true);
});
