/* Shapes this page reads off the API. Only the fields it renders — a wider type would claim
   more than the backend promises, and the rows come from jsonb columns that have held
   whatever a past import wrote into them. */

/** `GET /limits` for a registered account (`services/limits.py::user_snapshot`). */
export interface Limits {
  /** False when the operator switched this account off. Its token still works. */
  active?: boolean;
  /** Per-feature switches: absent or true means on. */
  features?: Record<string, boolean>;
  max_analyses_per_day?: number;
  max_tts_per_day?: number;
  max_conversions_per_day?: number;
  max_audio_mb?: number;
  used?: { analyses?: number; tts?: number; conversions?: number };
}

/** `GET /recordings` */
export interface RecordingRow {
  id: string;
  filename: string | null;
  source: string | null;
  language: string | null;
  duration_s: number | null;
  created_at: string;
  ran?: { factcheck?: boolean; score?: boolean; semantic?: boolean };
}

/** `GET /summaries` */
export interface SummaryRow {
  id: string;
  short_summary?: string | null;
  call_count?: number;
  language?: string | null;
  created_at: string;
}

/** `GET /tts/history` */
export interface TtsRow {
  id: string;
  text?: string | null;
  language_code?: string | null;
  created_at: string;
  has_audio?: boolean;
  audio_url?: string | null;
}

/** `GET /convert/history` */
export interface ConversionRow {
  token: string;
  format?: string | null;
  file_count?: number;
  total_bytes?: number;
  created_at: string;
  expires_at?: string | null;
  /** Null once the ZIP is gone: a row that offers a link to nothing is worse than one that
      says it expired. */
  download_path?: string | null;
}

/** The gate's message line: the boot check writes into it, and so does every failed submit.
    Lives here rather than beside the page so `Gate` does not have to import its own parent. */
export interface GateMessage {
  text: string;
  /** `.msg.err` vs a plain `.msg` — an outage and a refusal do not look the same. */
  error: boolean;
  /** An outage offers a reload rather than leaving a dead end. */
  retry?: boolean;
}
