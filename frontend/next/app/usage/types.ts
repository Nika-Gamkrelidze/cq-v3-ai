/* The shapes `/admin/usage/*` returns — one file, because four tabs and two drill-downs read
   the same rows and a second copy of `CallRow` is how a column silently goes missing on one
   of them. The backend contract is `backend/app/services/usage.py`. */

/** Analyser groups, in the order every table lists them. The server maps each call's
    `feature` to one of these (`usage.GROUPS`); `other` catches a label added later. */
export const GROUPS = [
  'transcription', 'analysis', 'factcheck', 'sentiment', 'score', 'summary',
  'bot', 'copilot', 'tts', 'kb', 'rubric', 'test', 'other',
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
}

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
  series: { t: string; total_tokens: number; input_tokens: number; output_tokens: number; calls: number; failed: number }[];
  facets: Facets;
}

/** Per-analyser subtotal inside a recording row. */
export interface GroupCell {
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

export interface FeatureCell {
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
  t: T;
}
