import { Tip } from '@/components/ui/Tip';

/* A MEASURED rubric dimension, rendered the same way wherever a rubric is edited.
   ============================================================================
   Two dimensions are scored by code from an analyser rather than by the model reading the
   transcript: `factcheck` from the knowledge-base fact check, `sentiment` from the tone
   analyser's politeness for the agent. An editor owns their WEIGHT and nothing else — the name
   and the meaning are the product's, because knowing where the number came from is its value.

   WHY THIS IS ITS OWN FILE. The rubric editor exists three times — the workspace Rubric tab,
   the console's default rubric, and the account's personal rubric — and the rule was first
   applied to only one of them. A superadmin editing the default saw both rows as ordinary:
   delete one and it silently came back at weight 0 (the store re-adds it), rename one and the
   name silently reverted (the store restores it). One definition, used by all three, is what
   stops the next copy drifting the same way.

   `isMeasured` accepts only the known sources, matching `scoring.normalize_dimensions` on the
   server, which drops any other marker — so a row the server treats as ordinary is never
   locked here, and the other way round. */

type T = (key: string, vars?: Record<string, string | number>) => string;

export const MEASURED_SOURCES = ['factcheck', 'sentiment'] as const;
export type MeasuredSource = typeof MEASURED_SOURCES[number];

export function isMeasured(source: string | null | undefined): source is MeasuredSource {
  return !!source && (MEASURED_SOURCES as readonly string[]).includes(source);
}

/** The "Measured" pill and its explanation, where an ordinary row has its delete button. */
export function MeasuredBadge({ source, t }: { source: MeasuredSource; t: T }) {
  return (
    <span className="inline" style={{ gap: 6 }}>
      <span className="pill ready">{t(`sc.measured.${source}`)}</span>
      <Tip text={t(`sc.measured.${source}.hint`)} />
    </span>
  );
}

/** Where the number comes from, in place of the description and guidance boxes. Those two
    exist to steer a model, and no model reads a measured row — leaving them editable would
    invite someone to write scoring instructions that nothing obeys. */
export function MeasuredNote({ source, t }: { source: MeasuredSource; t: T }) {
  return <p className="hint" style={{ margin: '4px 0 0' }}>{t(`sc.measured.${source}.desc`)}</p>;
}
