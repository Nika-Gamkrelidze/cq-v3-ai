/* The shapes `/admin/usage/*` returns — one file, because four tabs and two drill-downs read
   the same rows and a second copy of `CallRow` is how a column silently goes missing on one
   of them. The backend contract is `backend/app/services/usage.py`. */

/** Analyser groups, in the order every table lists them. The server maps each call's
    `feature` to one of these (`usage.GROUPS`); `other` catches a label added later. */
export const GROUPS = [
  'transcription', 'analysis', 'factcheck', 'sentiment', 'score', 'summary',
  'crm', 'bot', 'copilot', 'tts', 'kb', 'rubric', 'pricing', 'test', 'other',
] as const;
export type Group = typeof GROUPS[number];

/** The groups a RECORDING can have, i.e. the per-analyser columns of the recordings list. */
export const RECORDING_GROUPS: Group[] = ['transcription', 'analysis', 'factcheck', 'sentiment', 'score', 'summary'];

/** The chat features, i.e. the per-feature columns of the conversations list. */
export const CHAT_FEATURES = ['triage', 'autopilot', 'handoff', 'copilot'] as const;

export const CAPABILITIES = ['llm', 'stt', 'tts', 'voice_tone'] as const;

export const WINDOWS = ['24h', '7d', '30d', '90d', 'custom'] as const;
export type UsageWindow = typeof WINDOWS[number];

export interface Totals {
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_creation_tokens: number;
  total_tokens: number;
  byo_tokens: number;
  calls: number;
  failed: number;
  audio_seconds: number;
  characters: number;
  avg_latency_ms: number | null;
  first_used: string | null;
  last_used: string | null;
  /** What these calls cost us in USD, at the price in force when each ran (unpriced calls add 0). */
  cost_usd: number;
  /** cost_usd plus the markup in force for each call: what to bill. */
  billed_usd: number;
  /** Calls with no price in force — while above zero, the money figures are incomplete. */
  unpriced_calls: number;
}

/** The money every subtotal carries. */
export type Money = Pick<Totals, 'cost_usd' | 'billed_usd' | 'unpriced_calls'>;

/** One AI call. */
export interface CallRow {
  id: string;
  created_at: string;
  client_id: string | null;
  tenant_name: string | null;
  slug: string | null;
  feature: string;
  group: Group;
  capability: string;
  provider: string | null;
  model: string;
  input_tokens: number | null;
  output_tokens: number | null;
  cache_read_tokens: number | null;
  cache_creation_tokens: number | null;
  total_tokens: number;
  audio_seconds: number | null;
  characters: number | null;
  latency_ms: number | null;
  ok: boolean;
  byo: boolean;
  actor: string | null;
  job_id: string | null;
  filename: string | null;
  conversation_id: string | null;
  conversation_ref: string | null;
  channel: string | null;
  turn_id: string | null;
  turn_role: string | null;
  turn_preview: string | null;
  summary_id: string | null;
  /** USD at the price in force when the call ran; null = no price for it. */
  cost_usd: number | null;
  billed_usd: number | null;
  markup_pct: number | null;
  price_id: string | null;
}

/** A paginated, server-sorted list. */
export interface Page<T> {
  total: number;
  limit: number;
  offset: number;
  sort: string;
  dir: SortDir;
  rows: T[];
}

export type SortDir = 'asc' | 'desc';
export interface SortState { key: string; dir: SortDir }

export interface Facets {
  tenants: { client_id: string; name: string }[];
  groups: string[];
  features: string[];
  providers: string[];
  models: string[];
  capabilities: string[];
  actors: string[];
}

export interface Overview {
  window: string;
  from: string;
  to: string;
  bucket: 'hour' | 'day';
  total: Totals;
  by_tenant: (Totals & { client_id: string | null; name: string; slug: string | null })[];
  by_group: (Totals & { group: Group })[];
  by_feature: (Totals & { feature: string; group: Group })[];
  by_provider: (Totals & { provider: string })[];
  by_model: (Totals & { provider: string; model: string; capability: string })[];
  by_user: (Totals & { actor: string; client_id: string | null; name: string })[];
  series: ({ t: string; total_tokens: number; input_tokens: number; output_tokens: number; calls: number; failed: number } & Money)[];
  facets: Facets;
}

/** Per-analyser subtotal inside a recording row. */
export interface GroupCell extends Money {
  total_tokens: number;
  input_tokens: number;
  output_tokens: number;
  calls: number;
  failed: number;
  audio_seconds: number;
  characters: number;
  models: string[];
}

export interface RecordingRow {
  job_id: string;
  client_id: string | null;
  tenant_name: string | null;
  filename: string | null;
  created_at: string | null;
  duration_s: number | null;
  language: string | null;
  total: Totals;
  groups: Partial<Record<Group, GroupCell>>;
  models: string[];
}

export interface RecordingDetail {
  recording: {
    job_id: string; client_id: string | null; tenant_name: string | null; filename: string | null;
    created_at: string | null; duration_s: number | null; language: string | null;
  };
  total: Totals;
  groups: (Totals & { group: Group; models: string[] })[];
  calls: CallRow[];
  summaries: (Totals & { summary_id: string; job_count: number; created_at: string | null })[];
}

export interface FeatureCell extends Money {
  total_tokens: number;
  input_tokens: number;
  output_tokens: number;
  calls: number;
  failed: number;
}

export interface ConversationRow {
  conversation_id: string;
  client_id: string | null;
  tenant_name: string | null;
  external_ref: string | null;
  channel: string | null;
  locale: string | null;
  state: string | null;
  subject: string | null;
  created_at: string | null;
  last_message_at: string | null;
  turns: number;
  questions: number;
  total: Totals;
  features: Partial<Record<string, FeatureCell>>;
  models: string[];
}

export interface ConversationDetail {
  conversation: {
    conversation_id: string; client_id: string | null; tenant_name: string | null;
    external_ref: string | null; channel: string | null; locale: string | null; state: string | null;
    subject: string | null; created_at: string | null; last_message_at: string | null;
  };
  total: Totals;
  turns: {
    turn_id: string; turn_ref: string | null; role: string; content: string | null;
    created_at: string | null; grounded: boolean | null; total: Totals; calls: CallRow[];
  }[];
  unattached: CallRow[];
}

/** The filter bar, shared by every tab. Empty string = not filtered. */
export interface Filters {
  window: UsageWindow;
  from: string;          // YYYY-MM-DD, only with window 'custom'
  to: string;            // YYYY-MM-DD, only with window 'custom'
  client_id: string;
  group: string;
  provider: string;
  model: string;
  capability: string;
  status: '' | 'ok' | 'failed';
}

export const EMPTY_FILTERS: Filters = {
  window: '30d', from: '', to: '', client_id: '', group: '', provider: '', model: '',
  capability: '', status: '',
};

export type T = (key: string, vars?: Record<string, string | number>) => string;

/** What every tab component receives from the page. */
export interface TabProps {
  filters: Filters;
  /** Narrow the shared filters from inside a tab (e.g. clicking a workspace name). */
  onFilter: (patch: Partial<Filters>) => void;
  /** Open the Prices tab (the overview's "set the missing prices" link). */
  onPrices?: () => void;
  t: T;
}

/* ---- Prices and markup (`GET /admin/usage/pricing`) ---------------------------------------- */

/** USD per unit, as the providers' price pages state them. */
export const PRICE_FIELDS = [
  'input_per_mtok', 'output_per_mtok', 'cache_write_per_mtok', 'cache_read_per_mtok',
  'audio_per_hour', 'chars_per_k',
] as const;
export type PriceField = typeof PRICE_FIELDS[number];

export type PriceRow = {
  id: string;
  provider: string;
  /** An exact model id, or '*' for every model of the provider without its own row. */
  model: string;
  capability: string | null;
  effective_from: string;
  note: string | null;
  created_at: string;
  created_by: string | null;
  updated_at: string | null;
} & Record<PriceField, number | null>;

export interface MarkupRow {
  id: string;
  /** null = the default for every workspace. */
  client_id: string | null;
  tenant_name: string | null;
  /** null only on a workspace row: "back to the default from this date". */
  pct: number | null;
  effective_from: string;
  note: string | null;
  created_at: string;
}

export interface UnpricedRow {
  provider: string;
  model: string;
  capability: string;
  calls: number;
  input_tokens: number;
  output_tokens: number;
  audio_seconds: number;
  characters: number;
  first_used: string | null;
  last_used: string | null;
}

export interface Pricing {
  prices: PriceRow[];
  markups: MarkupRow[];
  unpriced: UnpricedRow[];
  models: { provider: string; model: string; capability: string }[];
  tenants: { client_id: string; name: string }[];
}

/* ---- Official price lookup (`POST /admin/usage/prices/lookup`) ----------------------------- */

export interface LookupPrice {
  /** In our column's unit (per 1M tokens, per hour, per 1K characters). */
  value: number;
  /** The line of the provider's page the price was read from (verified to be on the page). */
  quote: string;
  /** The price as the page states it, e.g. "0.006 per minute". */
  stated: string;
}

export interface LookupResult {
  provider: string;
  model: string;
  capability: string;
  status: 'found' | 'unstable' | 'unverified' | 'not_listed' | 'unsupported' | 'error';
  source_url: string | null;
  fetched_at: string;
  page_model_name?: string;
  notes?: string;
  error?: string;
  prices: Partial<Record<PriceField, LookupPrice>>;
  /** True when this is an earlier reading of a page that has not changed (`fetched_at` is when
      it was read). */
  cached?: boolean;
  /** Figures the AI's independent readings of the page did NOT agree on: not proposed. */
  unstable?: { field: PriceField; when: string; values: (number | null)[]; quote: string }[];
  /** Prices the AI named that the page does not back, with why. */
  rejected?: { field: string | null; value: unknown; unit: string | null; reason: string }[];
  /** Changes the page announces for a later date ("$1.50 starting January 1, 2027"). */
  scheduled?: { effective_from: string; prices: Partial<Record<PriceField, LookupPrice>> }[];
}

export interface LookupResponse {
  results: LookupResult[];
  sources: Record<string, string>;
}
