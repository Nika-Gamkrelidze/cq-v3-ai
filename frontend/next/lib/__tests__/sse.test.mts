/* Unit tests for the SSE frame parser.
   ===================================
   Runs with the rest of the suite — see `__tests__/README.md` for the one command and for why
   these files are `.mts`.

   What is being defended here is not JSON parsing — it is the seam BETWEEN ticks. The parser
   is fed a response body that grows a few hundred bytes at a time, and a frame that is
   consumed twice applies a per-file conversion result twice. Every case below is a shape the
   real server actually emits. */

import test from 'node:test';
import assert from 'node:assert/strict';
import { newSseState, readSseBody, sseFrames, type SseData } from '../sse.ts';

test('a frame split mid-JSON across two ticks is emitted once, after the second', () => {
  const st = newSseState();
  // The split lands inside the JSON payload — the worst place for it, and the common one:
  // a progress frame is ~120 bytes and a TCP chunk boundary does not respect it.
  assert.deepEqual(sseFrames(st, 'event: progress\ndata: {"index":0,"o'), []);
  assert.deepEqual(sseFrames(st, 'k":true}\n\n'), [['progress', { index: 0, ok: true }]]);
});

test('a frame split on the blank line is not consumed until the delimiter completes', () => {
  const st = newSseState();
  assert.deepEqual(sseFrames(st, 'event: done\ndata: {"batch":"b1"}\n'), []);
  assert.deepEqual(sseFrames(st, '\n'), [['done', { batch: 'b1' }]]);
});

test('a tick with no complete frame emits nothing and does not lose the tail', () => {
  const st = newSseState();
  assert.deepEqual(sseFrames(st, 'event: st'), []);
  assert.deepEqual(sseFrames(st, 'age\ndata: {"stage":"converting"'), []);
  assert.deepEqual(sseFrames(st, ',"total":3}\n\n'),
    [['stage', { stage: 'converting', total: 3 }]]);
});

test("an interleaved ': ping' comment is skipped, and the frames around it survive", () => {
  const st = newSseState();
  // The server pings every 15s to keep an idle stream alive. The ping is its own frame.
  const out = sseFrames(st,
    'event: stage\ndata: {"stage":"extracting"}\n\n' +
    ': ping\n\n' +
    'event: progress\ndata: {"pct":40}\n\n');
  assert.deepEqual(out, [['stage', { stage: 'extracting' }], ['progress', { pct: 40 }]]);
});

test('a comment line inside a frame does not disturb its fields', () => {
  const st = newSseState();
  assert.deepEqual(sseFrames(st, 'event: progress\n: keep-alive\ndata: {"pct":7}\n\n'),
    [['progress', { pct: 7 }]]);
});

test('CRLF line endings parse the same as LF', () => {
  const st = newSseState();
  assert.deepEqual(sseFrames(st, 'event: done\r\ndata: {"ok":true}\r\n\r\n'),
    [['done', { ok: true }]]);
});

test('CHARACTERIZATION: a CRLF pair split across two ticks strands the frame', () => {
  const st = newSseState();
  // Not a wanted behaviour — a recorded one, ported verbatim from the legacy parser, and
  // asserted here so it is a decision rather than a surprise.
  //
  // Normalisation runs on the incoming tick, not on the buffer, so a trailing lone \r is
  // buffered as-is and the \n that arrives next tick never pairs with it: the delimiter
  // becomes '\n\r\n' and indexOf('\n\n') misses it. Everything after is stranded too.
  //
  // Unreachable against this backend — Starlette writes LF, and nginx does not rewrite a
  // response body — which is why the fix (normalise the whole buffer instead) has not been
  // taken unilaterally. A raw CR cannot appear inside a JSON payload either, so that fix is
  // safe whenever someone decides to make it.
  assert.deepEqual(sseFrames(st, 'event: done\r\ndata: {"ok":true}\r\n\r'), []);
  assert.deepEqual(sseFrames(st, '\n'), []);
  assert.equal(st.buf, 'event: done\ndata: {"ok":true}\n\r\n');
});

test('multi-line data: is joined with newlines before parsing', () => {
  const st = newSseState();
  assert.deepEqual(sseFrames(st, 'event: draft\ndata: {\ndata: "rubric": "a\\nb"\ndata: }\n\n'),
    [['draft', { rubric: 'a\nb' }]]);
});

test('a frame with no event: name is dropped', () => {
  const st = newSseState();
  // Nameless means we do not know which UI transition it asks for; guessing is worse.
  assert.deepEqual(sseFrames(st, 'data: {"pct":50}\n\n'), []);
});

test('a dropped nameless frame does not swallow the frame after it', () => {
  const st = newSseState();
  assert.deepEqual(sseFrames(st, 'data: {"pct":50}\n\nevent: done\ndata: {"ok":1}\n\n'),
    [['done', { ok: 1 }]]);
});

test('unparseable JSON drops the frame, keeping the stream in sync', () => {
  const st = newSseState();
  assert.deepEqual(sseFrames(st, 'event: progress\ndata: {"pct":\n\nevent: done\ndata: {}\n\n'),
    [['done', {}]]);
});

test('a frame with a name but no data: gets an empty payload', () => {
  const st = newSseState();
  assert.deepEqual(sseFrames(st, 'event: open\n\n'), [['open', {}]]);
});

test('a non-object payload is normalised to {} rather than reaching a caller', () => {
  const st = newSseState();
  // Callers read named properties off the payload; `5` and `{}` answer identically, and this
  // is what keeps the payload type honest without an `unknown` cast at every call site.
  assert.deepEqual(sseFrames(st, 'event: progress\ndata: 5\n\n'), [['progress', {}]]);
  assert.deepEqual(sseFrames(st, 'event: progress\ndata: null\n\n'), [['progress', {}]]);
});

test('several frames arriving in one tick come back in order', () => {
  const st = newSseState();
  const out = sseFrames(st,
    'event: progress\ndata: {"index":0}\n\nevent: progress\ndata: {"index":1}\n\nevent: done\ndata: {"n":2}\n\n');
  assert.deepEqual(out, [['progress', { index: 0 }], ['progress', { index: 1 }], ['done', { n: 2 }]]);
});

test('byte-by-byte delivery emits each frame exactly once', () => {
  // The property that matters most, checked the brute way: whatever the chunk boundaries are,
  // every frame is applied once and only once.
  const wire =
    'event: stage\ndata: {"stage":"converting","total":2}\n\n' +
    ': ping\n\n' +
    'event: progress\ndata: {"index":0,"ok":true}\n\n' +
    'event: progress\ndata: {"index":1,"ok":false}\n\n' +
    'event: done\ndata: {"batch":"b1"}\n\n';
  const st = newSseState();
  const out: [string, SseData][] = [];
  for (const ch of wire) out.push(...sseFrames(st, ch));
  assert.deepEqual(out, [
    ['stage', { stage: 'converting', total: 2 }],
    ['progress', { index: 0, ok: true }],
    ['progress', { index: 1, ok: false }],
    ['done', { batch: 'b1' }],
  ]);
  assert.equal(st.buf, '');
});

test('two states parsing at once do not see each other', () => {
  const a = newSseState();
  const b = newSseState();
  sseFrames(a, 'event: progress\ndata: {"index":0}');
  assert.deepEqual(sseFrames(b, 'event: done\ndata: {"batch":"other"}\n\n'),
    [['done', { batch: 'other' }]]);
  assert.deepEqual(sseFrames(a, '\n\n'), [['progress', { index: 0 }]]);
});

/* readSseBody — the fetch-body reader, for the one streaming route with no upload.
   Its conventions differ from sseFrames on purpose (see the comment on the function), so the
   differences are the thing worth asserting. */

// Deliver the body in the chunks a caller would really get, to prove the reader re-joins them.
function bodyOf(chunks: string[]): Response {
  const enc = new TextEncoder();
  return new Response(new ReadableStream({
    start(c) { for (const s of chunks) c.enqueue(enc.encode(s)); c.close(); },
  }));
}

test('readSseBody re-joins a frame split across two chunks', async () => {
  const seen: [string, SseData | null][] = [];
  await readSseBody(bodyOf(['event: delta\ndata: {"te', 'xt":"hi"}\n\n']),
    (n, d) => seen.push([n, d]));
  assert.deepEqual(seen, [['delta', { text: 'hi' }]]);
});

test('readSseBody skips pings and defaults a nameless frame to "message"', async () => {
  const seen: [string, SseData | null][] = [];
  await readSseBody(bodyOf([': ping\n\ndata: {"x":1}\n\n']), (n, d) => seen.push([n, d]));
  // Unlike sseFrames, a nameless frame is kept here: this server relies on the SSE default.
  assert.deepEqual(seen, [['message', { x: 1 }]]);
});

test('readSseBody reports unparseable JSON as null instead of dropping the frame', async () => {
  const seen: [string, SseData | null][] = [];
  await readSseBody(bodyOf(['event: delta\ndata: {"text":\n\n']), (n, d) => seen.push([n, d]));
  // The caller ignores a null payload but still learns the frame arrived.
  assert.deepEqual(seen, [['delta', null]]);
});

test('readSseBody emits nothing for a frame with no data: line', async () => {
  const seen: [string, SseData | null][] = [];
  await readSseBody(bodyOf(['event: open\n\nevent: done\ndata: {"ok":1}\n\n']),
    (n, d) => seen.push([n, d]));
  assert.deepEqual(seen, [['done', { ok: 1 }]]);
});

test('readSseBody strips exactly one leading space from data:, keeping the rest', async () => {
  const seen: [string, SseData | null][] = [];
  // The SSE spec strips one space after the colon; a payload that is meaningfully indented
  // keeps the remainder, which is why this is not a trim().
  await readSseBody(bodyOf(['event: delta\ndata:  {"text":" hi "}\n\n']),
    (n, d) => seen.push([n, d]));
  assert.deepEqual(seen, [['delta', { text: ' hi ' }]]);
});

test('readSseBody drops a trailing frame that never got its blank line', async () => {
  const seen: [string, SseData | null][] = [];
  await readSseBody(bodyOf(['event: delta\ndata: {"text":"a"}\n\nevent: delta\ndata: {"text":"b"}']),
    (n, d) => seen.push([n, d]));
  assert.deepEqual(seen, [['delta', { text: 'a' }]]);
});
