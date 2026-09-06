/* Server-Sent Events, parsed by hand.
   ==================================
   Two readers, because the app streams over two different transports and neither of them is
   `EventSource`:

   * `sseFrames` reads frames out of a string that GROWS between calls — an
     `XMLHttpRequest.responseText`. XHR is used wherever a request uploads a file AND then
     streams progress back, because `fetch()` cannot report upload progress and `EventSource`
     is GET-only so it cannot carry the file at all. Three legacy pages carry an identical
     copy of this parser (`index.html`, `account.html`, `tenant.html`'s rubric import); this
     is that parser, moved rather than rewritten.
   * `readSseBody` reads a `fetch` response body incrementally, for the one route that has no
     upload (copilot's `POST /v1/chat/answer?stream=1`).

   Neither is a general SSE implementation: no `id:`/`retry:`/last-event-id, no reconnection.
   Both requests are POSTs the server can neither replay nor resume, so a reconnecting client
   would re-run the model on someone's money. */

/** A frame's decoded payload. The server sends an object on every event; a non-object is
    normalised to `{}` below rather than typed as `unknown`, which is what the legacy callers
    already saw through their `ev[1] || {}` guard. */
export type SseData = Record<string, unknown>;

/** `[event name, payload]` — a tuple, so a caller can destructure it in a loop. */
export type SseFrame = [name: string, data: SseData];

/** The parser's memory between ticks: the bytes of an unfinished frame. One per request —
    sharing it across two requests would splice one response into the other. */
export interface SseState {
  buf: string;
}

export function newSseState(): SseState {
  return { buf: '' };
}

/** Pull whole `event: x\ndata: {…}\n\n` frames out of a buffer that grows between ticks.

    The partial tail is KEPT for next time and only complete `\n\n`-terminated frames are
    consumed. That is the whole point of the function: a frame routinely arrives split across
    two `progress` events, and a parser that consumed the incomplete half — or re-read the
    prefix on the next tick — would apply a per-file result TWICE and corrupt the queue it is
    driving.

    Everything else it does is equally load-bearing:
      * `\r\n` is normalised, because a proxy in front of the API may rewrite line endings and
        the frame delimiter is spelled `\n\n`. Note that this runs on the arriving tick, not on
        the buffer, so a `\r\n` split across two ticks strands the frame — unreachable against
        this backend, ported unchanged rather than fixed on a porter's judgement, and pinned by
        a characterization test in `__tests__/sse.test.mts`.
      * `:` lines are comments — the server pings every 15s to keep an idle stream alive — and
        must not be mistaken for a field.
      * multiple `data:` lines join with `\n`, per the SSE spec; a JSON payload wide enough to
        be wrapped still parses.
      * a frame with no `event:` name, or with a payload that does not parse, is DROPPED. Both
        mean we do not know what the server is asking for, and guessing at a UI transition
        from a half-read frame is worse than missing one. */
export function sseFrames(state: SseState, text: string): SseFrame[] {
  state.buf += text.replace(/\r\n/g, '\n');
  const out: SseFrame[] = [];
  let i: number;
  while ((i = state.buf.indexOf('\n\n')) >= 0) {
    const raw = state.buf.slice(0, i);
    state.buf = state.buf.slice(i + 2);
    let name = '';
    let data = '';
    raw.split('\n').forEach(line => {
      if (!line || line[0] === ':') return;                 // keep-alive comment, not an event
      if (line.indexOf('event:') === 0) name = line.slice(6).trim();
      else if (line.indexOf('data:') === 0) data += (data ? '\n' : '') + line.slice(5).trim();
    });
    if (!name) continue;
    let payload: unknown;
    try { payload = data ? JSON.parse(data) : {}; } catch { continue; }
    out.push([name, asData(payload)]);
  }
  return out;
}

/** Read an SSE `fetch` response as it arrives.

    Separate from `sseFrames` because it is a different problem: there is no growing
    `responseText` to re-slice, so the tail is a local and the state is the loop itself. And
    separate from `session.ts`'s `apiGet`, whose `readBody` goes through `readJsonBody` and so
    `await r.text()`s the WHOLE body — correct for a JSON route, fatal here: it would hand back
    every frame at once, after the last one, which is exactly the streaming this route exists
    to provide.

    The differences from `sseFrames` are the copilot server's own conventions, not drift:
    a nameless frame defaults to `message` (the SSE default) instead of being dropped, `data:`
    keeps its payload verbatim minus one leading space, and an unparseable payload arrives as
    `null` so the caller can ignore that frame while still seeing its name. */
export async function readSseBody(
  response: Response,
  onFrame: (name: string, data: SseData | null) => void,
): Promise<void> {
  if (!response.body) throw new Error('response has no body to stream');
  const reader = response.body.getReader();
  const dec = new TextDecoder();
  let buf = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i: number;
    while ((i = buf.indexOf('\n\n')) >= 0) {
      const frame = buf.slice(0, i);
      buf = buf.slice(i + 2);
      let name = 'message';
      const data: string[] = [];
      frame.split('\n').forEach(line => {
        if (!line || line[0] === ':') return;
        if (line.startsWith('event:')) name = line.slice(6).trim();
        else if (line.startsWith('data:')) data.push(line.slice(5).replace(/^ /, ''));
      });
      // A frame with no `data:` at all carries nothing to act on. Silence beats a callback
      // that fires with an empty payload every keep-alive block.
      if (data.length) onFrame(name, safeJson(data.join('\n')));
    }
  }
}

/* `null` means "nothing usable here" — an unparseable payload and a literal `null` one are the
   same thing to a caller that has to decide whether to act on the frame. */
function safeJson(s: string): SseData | null {
  let parsed: unknown;
  try { parsed = JSON.parse(s); } catch { return null; }
  return parsed ? asData(parsed) : null;
}

/* A payload that is not an object cannot answer `d.stage` or `d.files` anyway, so it becomes
   the empty object every caller already substituted for it. Keeping the raw value would only
   push an `unknown` cast into every call site to say the same thing. */
function asData(parsed: unknown): SseData {
  return parsed !== null && typeof parsed === 'object' ? parsed as SseData : {};
}
