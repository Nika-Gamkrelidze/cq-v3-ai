/* Bring-your-own AI subscription, as data — no React in this file.
   ================================================================
   Which provider, model and key each of the three capabilities runs on resolves down a chain:

     code defaults ← the deployment default ← the connection an operator assigned ← THIS
     WORKSPACE'S OWN KEY

   and each layer only states what it changes. The workspace's layer is the top one, and it is
   the only one a customer can touch: a provider, a model, and a key of their own, so the bill
   lands on their account. Never an endpoint — a tenant-set base URL could keep every transcript
   it is handed, so that stays with the operator.

   This module is the workspace side of that: reading `GET /ai/config` defensively, seeding a
   form from it, and turning the form back into the body `PUT /ai/config/{cap}` accepts. It is
   separate from `AiTab.tsx` because it is the part worth testing without a browser
   (`lib/__tests__/workspaceAiByo.test.mts`), and because Node's type stripping runs `.ts` but
   not `.tsx`. */

/** The three capabilities, in the order the card shows them: text first because it carries
    the most (analysis, fact-check, scoring, the bot), then the two voice halves. */
export const CAPABILITIES = ['llm', 'stt', 'tts'] as const;
export type Capability = (typeof CAPABILITIES)[number];

/** Which layer of the chain answered. `byo` is this workspace's own key. */
export const SOURCES = ['byo', 'assigned', 'default', 'legacy'] as const;
export type Source = (typeof SOURCES)[number];

/** What this workspace's calls actually run on right now. NEVER a key, never a hint of one:
    the connection underneath may be CommuniQ's, and its credential is not the customer's to
    see even masked. */
export interface Effective {
  source: Source | null;
  provider: string;
  model: string | null;
  /** The operator-named connection, when the answer is one. */
  connectionName: string | null;
}

/** The workspace's own layer, when it has one. `keyHint` is the server's masked tail of the
    key — the only form in which the key ever comes back. */
export interface Override {
  provider: string;
  model: string | null;
  hasKey: boolean;
  keyHint: string | null;
}

export interface CapConfig {
  effective: Effective;
  override: Override | null;
  /** Catalog ids the workspace may pick from, in the server's order. */
  providers: string[];
  /** Known model ids per provider — "known, not exhaustive". Empty for a provider the server
      sent no list for, which is what turns the model control into free text. */
  knownModels: Record<string, string[]>;
}

export interface AiConfig {
  caps: Record<Capability, CapConfig>;
  /** The server's own authority answer, or null when it did not say. */
  canEdit: boolean | null;
}

/* ---------------------------------------------------------------- reading a reply */

const obj = (v: unknown): Record<string, unknown> =>
  (v && typeof v === 'object' && !Array.isArray(v)) ? v as Record<string, unknown> : {};
const str = (v: unknown): string | null => (typeof v === 'string' && v.trim() ? v.trim() : null);
const strList = (v: unknown): string[] =>
  (Array.isArray(v) ? v.map(x => (typeof x === 'string' ? x.trim() : '')).filter(Boolean) : []);
const isSource = (v: unknown): v is Source =>
  typeof v === 'string' && (SOURCES as readonly string[]).includes(v);

export const EMPTY_CAP: CapConfig = {
  effective: { source: null, provider: '', model: null, connectionName: null },
  override: null,
  providers: [],
  knownModels: {},
};

/** Read one capability's block out of whatever the server sent.

    Forgiving on purpose, in the same way the backend's `_as_str_list` is: a field that is
    missing, null or the wrong type reads as "not stated" rather than crashing the tab. Two
    spellings are accepted where the contract left room — `providers` as a list of ids or as a
    list of `{id, known_models}` objects, and known models either inline there, in a sibling
    `known_models` map, or in a catalog handed down beside the capabilities — so the model
    control degrades to free text instead of to a blank when the list is absent. */
export function readCap(raw: unknown, catalog?: unknown): CapConfig {
  const d = obj(raw);
  const eff = obj(d.effective);
  const conn = obj(eff.connection);
  const effective: Effective = {
    source: isSource(eff.source) ? eff.source : null,
    provider: str(eff.provider) ?? '',
    model: str(eff.model),
    connectionName: str(eff.connection_name) ?? str(conn.name),
  };

  const ov = d.override && typeof d.override === 'object' ? obj(d.override) : null;
  const override: Override | null = ov
    ? {
      provider: str(ov.provider) ?? '',
      model: str(ov.model),
      hasKey: ov.has_key === true,
      keyHint: str(ov.key_hint),
    }
    : null;

  const providers: string[] = [];
  const knownModels: Record<string, string[]> = {};
  const add = (id: string | null) => { if (id && !providers.includes(id)) providers.push(id); };
  const learn = (id: string, list: unknown) => {
    const l = strList(list);
    if (l.length && !knownModels[id]) knownModels[id] = l;
  };

  if (Array.isArray(d.providers)) {
    for (const p of d.providers) {
      if (typeof p === 'string') { add(str(p)); continue; }
      const o = obj(p);
      const id = str(o.id) ?? str(o.provider) ?? str(o.key);
      if (!id) continue;
      add(id);
      learn(id, o.known_models ?? o.models);
    }
  }
  for (const map of [obj(d.known_models), obj(d.models), obj(catalog)]) {
    for (const [id, entry] of Object.entries(map)) {
      // A catalog entry is `{label, known_models, ...}`; a plain map is `id → [models]`.
      learn(id, Array.isArray(entry) ? entry : obj(entry).known_models);
    }
  }
  // A saved override names a provider the list has since dropped: keep it selectable, so a
  // disabled control still tells the truth and saving does not silently change it.
  if (override) add(override.provider);

  return { effective, override, providers, knownModels };
}

/** The whole `GET /ai/config` reply. A capability the server left out reads as EMPTY_CAP —
    nothing to pick from, nothing in effect — rather than as a crash; the card for it then
    shows the "in effect" line blank, which is the truth about a reply that said nothing. */
export function readAiConfig(raw: unknown): AiConfig {
  const d = obj(raw);
  const caps = obj(d.capabilities);
  const catalog = obj(d.catalog);
  const read = (cap: Capability) => readCap(d[cap] ?? caps[cap], catalog[cap]);
  return {
    caps: { llm: read('llm'), stt: read('stt'), tts: read('tts') },
    // Only an explicit false closes the form. A reply without the field is an older server,
    // not a refusal, and the page's own owner predicate still applies either way.
    canEdit: typeof d.can_edit === 'boolean' ? d.can_edit : null,
  };
}

/** Is this workspace's own key what the capability is running on? The server's `source` is
    the authority; a reply that omitted it is read from whether an override exists at all. */
export function isByo(c: CapConfig): boolean {
  return c.effective.source ? c.effective.source === 'byo' : !!c.override;
}

/* ---------------------------------------------------------------- the form

   The key is held as the RAW TEXT typed this session and nothing else: the server never sends
   a key back, so an empty box means "keep what is stored", never "clear it" — the same rule
   the operator's editor keeps. Removing the key is removing the whole override. */

export interface Draft {
  provider: string;
  /** '' = not stated; the resolver falls back to the layer underneath. */
  model: string;
  /** Write-only. '' = nothing typed. */
  apiKey: string;
}

/** The model to show after `provider` is chosen: the current id when the provider's known
    list contains it, the first known id otherwise, and nothing at all when there is no list —
    a model id typed for one provider is not a model id for another. */
export function modelFor(c: CapConfig, provider: string, current: string): string {
  const known = c.knownModels[provider] ?? [];
  if (known.includes(current)) return current;
  return known[0] ?? '';
}

/** Seed the form: from the override when there is one, otherwise from what is in effect —
    a customer bringing a key usually wants to keep running the same model on their own
    account, and the form should say so rather than open blank. */
export function toDraft(c: CapConfig): Draft {
  if (c.override) {
    return { provider: c.override.provider, model: c.override.model ?? '', apiKey: '' };
  }
  const eff = c.effective.provider;
  const provider = c.providers.includes(eff) ? eff : (c.providers[0] ?? eff);
  const model = provider === eff && c.effective.model ? c.effective.model : modelFor(c, provider, '');
  return { provider, model, apiKey: '' };
}

/** The ids the model control offers: the known list, plus the current value when it is not
    on it, so a saved custom id stays selectable instead of snapping to the first option. */
export function modelOptions(c: CapConfig, provider: string, current: string): string[] {
  const known = c.knownModels[provider] ?? [];
  const cur = current.trim();
  return cur && !known.includes(cur) ? [...known, cur] : [...known];
}

/** The ids the provider control offers — the server's list, plus whatever the form currently
    says when the list has since dropped it (or arrived empty from an older server). */
export function providerOptions(c: CapConfig, current: string): string[] {
  const cur = current.trim();
  return cur && !c.providers.includes(cur) ? [...c.providers, cur] : [...c.providers];
}

/** The body `PUT /ai/config/{cap}` gets. NO `base_url`, ever — a tenant cannot set one and the
    server refuses the field — and `api_key` only when something was typed, because the box is
    empty on every load and sending it unconditionally would wipe the stored key on a model
    change. */
export function toBody(d: Draft): { provider: string; model: string | null; api_key?: string } {
  const key = d.apiKey.trim();
  return {
    provider: d.provider.trim(),
    model: d.model.trim() || null,
    ...(key ? { api_key: key } : {}),
  };
}

/** Whether the stored key still applies to what the form says: a key is a key FOR a provider,
    and one saved for Anthropic is not one for OpenAI. */
export function keyOnFile(d: Draft, override: Override | null): boolean {
  return !!override && override.hasKey && override.provider === d.provider.trim();
}

/** May this be sent? A provider, and a key — typed now, or already stored for that same
    provider. An override without a key is not "your own subscription", it is a model change
    on CommuniQ's account, which is the operator's call to make. */
export function canSave(d: Draft, override: Override | null): boolean {
  return !!d.provider.trim() && (!!d.apiKey.trim() || keyOnFile(d, override));
}

/** Does the form differ from what is saved? While it does, Test would probe the SAVED
    override and report on something other than what is on screen. */
export function isDirty(d: Draft, override: Override | null): boolean {
  if (!override) return true;
  return d.provider.trim() !== override.provider
    || (d.model.trim() || null) !== (override.model ?? null)
    || !!d.apiKey.trim();
}
