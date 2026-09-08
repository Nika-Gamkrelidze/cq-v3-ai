/* The document-list shape, normalised once.

   The two routes answer in two shapes and BOTH are correct: `/kb/documents` is also the partner
   API `/v1/kb/documents`, whose bare list external consumers already parse, so it cannot grow an
   envelope; its operator twin was written later and returns {total, documents}. Normalise here,
   once, and take the real total when the server offers it — that is the difference between an
   honest pager and a guess. */

export interface DocRow {
  id: string;
  title: string;
  doc_type?: string;
  tags?: string[];
  chunk_count?: number;
  status?: string;
  error?: string | null;
  visibility?: string;
  source_type?: string | null;
  content_text?: string | null;
  metadata?: Record<string, unknown> | null;
}

export type DocsBody = DocRow[] | { documents?: DocRow[]; total?: number };

export function docsOf(d: DocsBody | null): DocRow[] {
  if (Array.isArray(d)) return d;
  return d && Array.isArray(d.documents) ? d.documents : [];
}

/** The server's own total, or `null` when it did not offer one. */
export function totalOf(d: DocsBody | null): number | null {
  if (!d || Array.isArray(d)) return null;
  return Number.isFinite(d.total) ? (d.total as number) : null;
}
