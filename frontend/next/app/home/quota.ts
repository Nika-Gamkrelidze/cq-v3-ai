/* The anonymous allowance banner: `GET /limits` → what the line should say.
   =========================================================================
   Pure, so the one thing that is easy to get wrong here is testable: which of the four states
   a snapshot is in. Three of them look alike from the outside (a banner with a sentence in it)
   and one of them is new.

   WHAT THE BACKEND ACTUALLY RETURNS (`services/limits.py::snapshot`):

     * a tenant, an operator or an integration → `{anonymous:false, unlimited:true}`
     * a REGISTERED user → `{anonymous:false, registered:true, remaining:{…}}`
     * an anonymous visitor → `{anonymous:true, enabled, remaining:{analyses,tts,conversions}}`
     * an anonymous visitor the server CANNOT TELL APART from any other → the same shape with
       `enabled:false` AND `visitor_identified:false`, every remaining count 0.

   That last one is the state this port has to render differently from the legacy page, and
   the reason is a lie the legacy wording would now tell. `_unidentified_snapshot` sets
   `remaining` to zeroes because there is no per-visitor counter to read — not because this
   visitor has spent anything. It happens when `auth.visitor_key` withholds the address (the
   app is behind a NAT whose address is the deployment's own), and the anonymous tier then
   refuses every request with a 503 that says "temporarily unavailable on this server". So the
   banner must say the feature is off RIGHT NOW and not that an allowance ran out: one is a
   server condition an operator fixes, the other is a sentence that tells a first-time visitor
   they have already used something they have never used.

   The legacy page collapses both into `quota.disabled` ("Anonymous access is disabled"), which
   is at least not the allowance lie — but it points at the wrong remedy. `visitor_identified`
   is exactly the flag the backend added so this state is distinguishable from one unauthenticated
   GET, and it is additive: an older server that does not send it lands on `disabled`, as before. */

export interface LimitsSnapshot {
  anonymous?: boolean;
  enabled?: boolean;
  /** Present only on the anonymous snapshot, and only since the metering fix. Absent means
      "this server does not report it" — NOT `false`, which is why the test below is `=== false`. */
  visitor_identified?: boolean;
  max_conversions_per_day?: number;
  remaining?: {
    analyses?: number | null;
    tts?: number | null;
    conversions?: number | null;
  };
}

/** One clause of the sentence: `<b>7</b> speech clips`. `left: null` is the API's "uncapped",
    rendered as ∞ — never as zero. */
export interface QuotaPart {
  left: number | null;
  labelKey: string;
}

export type QuotaView =
  /** Not an anonymous caller: no allowance to count down, so no banner at all. */
  | { kind: 'none' }
  /** The operator switched the anonymous tier off. */
  | { kind: 'disabled' }
  /** The server cannot meter anonymous visitors right now. Not the visitor's fault, and not
      an exhausted allowance — see the header of this file. */
  | { kind: 'unavailable' }
  | { kind: 'counts'; parts: QuotaPart[]; warn: boolean };

/** `signedIn` is "a token of ANY kind is in this tab", which is what decides whether the
    transcription tabs exist — and therefore whether quoting a transcription allowance is
    describing a door that is in the wall. Signed out those tabs are not rendered, so the
    banner does not mention them. */
export function quotaView(d: LimitsSnapshot | null, signedIn: boolean): QuotaView {
  if (!d || !d.anonymous) return { kind: 'none' };
  if (!d.enabled) return d.visitor_identified === false ? { kind: 'unavailable' } : { kind: 'disabled' };

  const rem = d.remaining || {};
  const parts: QuotaPart[] = [];
  if (signedIn) parts.push({ left: rem.analyses ?? null, labelKey: 'quota.analyses' });
  parts.push({ left: rem.tts ?? null, labelKey: 'quota.clips' });
  // `in`, not a truthiness test: `conversions` is ABSENT on a server that predates the
  // converter, and an absent key means "claim nothing", not "zero".
  if ('conversions' in rem) parts.push({ left: rem.conversions ?? null, labelKey: 'quota.conversions' });

  return { kind: 'counts', parts, warn: parts.some(p => p.left != null && p.left <= 0) };
}

/** The converter's own line, which counts FILES per day rather than batches — otherwise a
    visitor learns that detail from a refusal halfway through thirty files.
    `null` when the server names no cap: an absent number is neither zero nor infinity. */
export function conversionAllowance(d: LimitsSnapshot | null): { max: number; left: number } | null {
  const max = d && d.anonymous ? Number(d.max_conversions_per_day) : 0;
  if (!max || max <= 0) return null;
  const left = d?.remaining?.conversions ?? max;
  return { max, left };
}
