import test from 'node:test';
import assert from 'node:assert/strict';
import {
  callFromRow, featureOrder, firstResultTab, lanesFor, levelOf, LIMITS, noVoiceKey, numOrNull,
  overTotalSize, pct, queueFiles, rolesWithSpeakers, sortLanes, toneLevel, VERDICT_LABEL_KEY,
  voiceVerdictLevel, worstLevel,
  type Call, type Lane, type Level,
} from '../../components/Workbench/logic.ts';

/* These are the parts of `workbench.js` that were wrong at least once, or that a reader would
   reasonably "clean up" into being wrong. The rest of the panel is markup and is checked by
   looking at it; this is the part where being off by one word costs a compliance review. */

/* ------------------------------------------------------------------ bands, injected */

// Stands in for `scoreBand` from lib/aiShapes bound to a workspace's fetched thresholds.
const scoreLevel = (amber: number, green: number) => (v: number | null): Level =>
  v === null || !Number.isFinite(v) ? 'none' : v >= green ? 'good' : v >= amber ? 'mid' : 'bad';

const NAMES = { factcheck: 'Fact-check', words: 'Words', voice: 'Voice' };
const opts = (amber = 50, green = 80) => ({ names: NAMES, scoreLevel: scoreLevel(amber, green) });

/* ------------------------------------------------------------------ verdicts */

test('PARTIALLY_SUPPORTED is not NOT_IN_KB', () => {
  // The bug this file exists for: collapsing the two told a reviewer the knowledge base had
  // nothing to say about a claim it in fact partly contradicted.
  assert.notEqual(VERDICT_LABEL_KEY.partial, VERDICT_LABEL_KEY.notinkb);
  assert.equal(VERDICT_LABEL_KEY.partial, 'wb.fc.partial');
  assert.equal(VERDICT_LABEL_KEY.notinkb, 'fc.notinkb');
  // All four verdict classes have a label; a missing one would render a raw key on the pill.
  for (const cls of ['supported', 'partial', 'contradicted', 'notinkb']) {
    assert.equal(typeof VERDICT_LABEL_KEY[cls], 'string');
    assert.ok(VERDICT_LABEL_KEY[cls].length > 0);
  }
});

/* ------------------------------------------------------------------ levels */

test('levelOf keeps the four levels and rejects anything else', () => {
  assert.equal(levelOf('good'), 'good');
  assert.equal(levelOf('bad'), 'bad');
  assert.equal(levelOf('excellent'), 'none');
  assert.equal(levelOf(undefined), 'none');
  assert.equal(levelOf(null), 'none');
});

test('worstLevel prefers the level a reviewer must look at first', () => {
  assert.equal(worstLevel('good', 'bad'), 'bad');
  assert.equal(worstLevel('bad', 'good'), 'bad');
  assert.equal(worstLevel('mid', 'bad'), 'bad');
  assert.equal(worstLevel('good', 'mid'), 'mid');
  // `none` is UNJUDGED, not "fine": a judged half always wins over it.
  assert.equal(worstLevel('none', 'good'), 'good');
  assert.equal(worstLevel('good', 'none'), 'good');
  assert.equal(worstLevel('none', 'none'), 'none');
});

test('a neutral tone is good, not middling', () => {
  // Painting every ordinary turn amber would flag every ordinary call.
  assert.equal(toneLevel('neutral'), 'good');
  assert.equal(toneLevel('polite'), 'good');
  assert.equal(toneLevel('curt'), 'mid');
  assert.equal(toneLevel('rude'), 'bad');
  assert.equal(toneLevel('Aggressive'), 'bad');       // the model's casing is not load-bearing
  assert.equal(toneLevel('sarcastic'), 'none');       // a word this build has no colour for
});

test('voice verdicts map to their own scale', () => {
  assert.equal(voiceVerdictLevel('patient'), 'good');
  assert.equal(voiceVerdictLevel('calm'), 'good');
  assert.equal(voiceVerdictLevel('tense'), 'mid');
  assert.equal(voiceVerdictLevel('aggressive'), 'bad');
  assert.equal(voiceVerdictLevel('unknown'), 'none');
});

/* ------------------------------------------------------------------ voice status */

test('noVoiceKey names the cause, and only falls back when there is none', () => {
  // "Still warming up" is worth retrying and "no per-turn timings" never is. Both used to
  // render the same sentence.
  assert.equal(noVoiceKey('timeout'), 'wb.novoice.timeout');
  assert.equal(noVoiceKey('no_timestamps'), 'wb.novoice.no_timestamps');
  assert.equal(noVoiceKey('no_audio'), 'wb.novoice.no_audio');
  assert.equal(noVoiceKey('disabled'), 'wb.novoice.disabled');
  assert.notEqual(noVoiceKey('timeout'), noVoiceKey('no_timestamps'));
  // A result stored before `voice_status` existed carries none.
  assert.equal(noVoiceKey(undefined), 'wb.sem.novoice');
  assert.equal(noVoiceKey(''), 'wb.sem.novoice');
  assert.equal(noVoiceKey('ok'), 'wb.sem.novoice');
});

/* ------------------------------------------------------------------ lanes */

test('the fact-check lane carries the result spans under a translated name', () => {
  const lanes = lanesFor('factcheck', { spans: [{ segments: [1], level: 'bad' }] }, opts());
  assert.equal(lanes.length, 1);
  assert.equal(lanes[0].id, 'factcheck');
  assert.equal(lanes[0].name, 'Fact-check');
  assert.equal(lanes[0].spans.length, 1);
  assert.deepEqual(lanesFor('factcheck', null, opts()), []);
});

test('score spans are re-levelled against the WORKSPACE bands and lose their number', () => {
  const data = { lanes: [{ key: 'tone', name: 'Tone', spans: [{ segments: [0], score: 62, level: 'good' }] }] };

  // 62 is amber on the default 50/80 …
  const dflt = lanesFor('score', data, opts());
  assert.equal(dflt[0].id, 'score:tone');
  assert.equal(dflt[0].spans[0].level, 'mid');
  // … and green on a workspace that set 40/60. The card, the bar and the lane must agree,
  // which is why the lane cannot keep the server's own `level`.
  const lenient = lanesFor('score', data, opts(40, 60));
  assert.equal(lenient[0].spans[0].level, 'good');

  // `score: null` on purpose: it makes the timeline colour by `level` — by the workspace's
  // three bands — instead of by a continuous ramp of its own.
  assert.equal(dflt[0].spans[0].score, null);
  // A span with no score of its own keeps the server's level.
  const unscored = lanesFor('score', { lanes: [{ key: 'k', spans: [{ segments: [2], level: 'bad' }] }] }, opts());
  assert.equal(unscored[0].spans[0].level, 'bad');
});

test('score lanes without a key fall back to their index, so two lanes cannot collide', () => {
  const lanes = lanesFor('score', { lanes: [{ spans: [] }, { spans: [] }] }, opts());
  assert.deepEqual(lanes.map(l => l.id), ['score:0', 'score:1']);
});

test('the semantic lanes follow the modes that were actually run', () => {
  const both = lanesFor('semantic', {
    modes: ['text', 'voice'], voice_available: true,
    spans: { text: [{ segments: [0] }], voice: [{ segments: [1] }] },
  }, opts());
  assert.deepEqual(both.map(l => l.id), ['semantic:text', 'semantic:voice']);

  // Voice was asked for and the sidecar could not answer: no empty lane, no legend entry —
  // the pane explains why instead.
  const wordsOnly = lanesFor('semantic', {
    modes: ['text', 'voice'], voice_available: false, spans: { text: [{ segments: [0] }], voice: [] },
  }, opts());
  assert.deepEqual(wordsOnly.map(l => l.id), ['semantic:text']);

  // A stored result with spans but no `modes` still draws them.
  const legacy = lanesFor('semantic', { spans: { text: [{ segments: [0] }], voice: [{ segments: [0] }] } }, opts());
  assert.deepEqual(legacy.map(l => l.id), ['semantic:text', 'semantic:voice']);
});

test('lanes are ordered fact-check, score, sentiment whatever order they were run in', () => {
  const lanes: Lane[] = [
    { id: 'semantic:text', name: 'w', spans: [] },
    { id: 'score:tone', name: 's', spans: [] },
    { id: 'factcheck', name: 'f', spans: [] },
  ];
  assert.deepEqual(sortLanes(lanes).map(l => l.id), ['factcheck', 'score:tone', 'semantic:text']);
  // Pure: the caller's array is not reordered underneath it.
  assert.equal(lanes[0].id, 'semantic:text');
});

/* ------------------------------------------------------------------ adopting a row */

test('callFromRow keeps a timestamped transcript as it came', () => {
  const call = callFromRow({
    id: 'abc', filename: 'call.mp3', language: 'ka', duration_s: 91.4,
    audio_url: '/recordings/abc/audio', has_audio: true,
    transcript: 'hello', segments: [{ i: 0, speaker: 'speaker_0', start: 0, end: 2, text: 'hello' }],
    scoring: { weighted_total: 71, dimensions: [] },
  });
  assert.equal(call.id, 'abc');
  assert.equal(call.source, 'audio');
  assert.equal(call.hasAudio, true);
  assert.equal(call.duration, 91.4);
  assert.equal(call.segments.length, 1);
  assert.equal(call.results.score?.weighted_total, 71);
  assert.equal(call.results.factcheck, null);
  assert.equal(call.noteKey, '');
});

test('callFromRow splits a pasted transcript into turns and keeps its own labels as roles', () => {
  const call = callFromRow({ id: 'x', transcript: 'Agent: hi\n\nოპერატორი: გამარჯობა\n' }, { source: 'text' });
  assert.equal(call.source, 'text');
  assert.equal(call.hasAudio, false);
  assert.deepEqual(call.segments.map(s => s.text), ['Agent: hi', 'ოპერატორი: გამარჯობა']);
  // Every line is one anonymous speaker until a sentiment run says otherwise.
  assert.deepEqual(call.roles, {});

  // A transcript the server HAS diarised with its own labels keeps them as roles, so the
  // chips read "Agent" rather than "Speaker 1".
  const labelled = callFromRow({
    id: 'y',
    segments: [
      { i: 0, speaker: 'Agent', start: null, end: null, text: 'hi' },
      { i: 1, speaker: 'speaker_1', start: null, end: null, text: 'hello' },
    ],
  });
  assert.deepEqual(labelled.roles, { Agent: 'Agent' });
});

test('callFromRow trusts has_audio over the presence of a url', () => {
  // A purged recording still has an `audio_url` shaped hole in some stored rows.
  const purged = callFromRow({ id: 'a', audio_url: '/recordings/a/audio', has_audio: false });
  assert.equal(purged.hasAudio, false);
  assert.equal(purged.audioUrl, '/recordings/a/audio');
  const kept = callFromRow({ id: 'b', audio_url: '/recordings/b/audio' });
  assert.equal(kept.hasAudio, true);
});

test('rolesWithSpeakers takes only the roles that mean something', () => {
  const roles = { Agent: 'Agent' };
  const next = rolesWithSpeakers(roles, {
    speakers: [
      { speaker: 'speaker_0', role: 'agent' },
      { speaker: 'speaker_1', role: 'customer' },
      { speaker: 'speaker_2', role: 'unknown' },
      { speaker: 'Agent', role: 'other' },
    ],
  });
  assert.deepEqual(next, { Agent: 'Agent', speaker_0: 'agent', speaker_1: 'customer' });
  // Pure, and a missing result changes nothing.
  assert.deepEqual(roles, { Agent: 'Agent' });
  assert.equal(rolesWithSpeakers(roles, null), roles);
});

/* ------------------------------------------------------------------ the file queue */

const file = (name: string, mb: number) => ({ name, size: Math.round(mb * 1048576) });

test('outside Summarise the queue holds exactly one recording, and says so', () => {
  const first = queueFiles([], [file('a.mp3', 2)], false);
  assert.deepEqual(first.files.map(f => f.name), ['a.mp3']);
  assert.deepEqual(first.notices, []);

  // Replacing is not silent: a drop target that swallows the extra files reads as broken.
  const replaced = queueFiles([file('a.mp3', 2)], [file('b.mp3', 2)], false);
  assert.deepEqual(replaced.files.map(f => f.name), ['b.mp3']);
  assert.deepEqual(replaced.notices, [{ key: 'wb.onefile', kind: 'info' }]);

  const dropped = queueFiles([], [file('a.mp3', 2), file('b.mp3', 2)], false);
  assert.deepEqual(dropped.files.map(f => f.name), ['a.mp3']);
  assert.equal(dropped.notices[0].key, 'wb.onefile');
});

test('Summarise appends, and refuses each limit with its own words', () => {
  const ok = queueFiles([file('a.mp3', 1)], [file('b.mp3', 1), file('c.mp3', 1)], true);
  assert.deepEqual(ok.files.map(f => f.name), ['a.mp3', 'b.mp3', 'c.mp3']);
  assert.deepEqual(ok.notices, []);

  // One oversized file is refused; the others in the same drop still go in.
  const big = queueFiles([], [file('huge.wav', LIMITS.maxMb + 1), file('fine.mp3', 1)], true);
  assert.deepEqual(big.files.map(f => f.name), ['fine.mp3']);
  assert.equal(big.notices[0].key, 'cv.toobig');
  assert.equal(big.notices[0].vars?.name, 'huge.wav');

  // The count ceiling stops the loop: nothing after it can fit either.
  const many = Array.from({ length: LIMITS.maxFiles + 2 }, (_, i) => file(`f${i}.mp3`, 1));
  const capped = queueFiles([], many, true);
  assert.equal(capped.files.length, LIMITS.maxFiles);
  assert.equal(capped.notices.filter(n => n.key === 'wb.toomany').length, 1);

  // The TOTAL ceiling skips the file that would break it and keeps looking: the server
  // refuses the whole upload over the limit, so it is worth saying before the bytes go up.
  const total = queueFiles([file('big.wav', 90)], [file('bigger.wav', 90), file('small.mp3', 1)], true, {
    maxFiles: 10, maxMb: 100, maxTotalMb: 100,
  });
  assert.deepEqual(total.files.map(f => f.name), ['big.wav', 'small.mp3']);
  assert.equal(total.notices[0].key, 'wb.toobig.total');
});

test('queueFiles is pure and ignores an empty drop', () => {
  const current = [file('a.mp3', 1)];
  const same = queueFiles(current, [], true);
  assert.deepEqual(same.files.map(f => f.name), ['a.mp3']);
  assert.notEqual(same.files, current);          // a copy, not the caller's array
  assert.equal(current.length, 1);
});

test('overTotalSize guards the re-run path, which never passed through the queue', () => {
  assert.equal(overTotalSize([file('a', 100), file('b', 100)]), false);
  assert.equal(overTotalSize([file('a', 200), file('b', 101)]), true);
  assert.equal(overTotalSize([]), false);
});

/* ------------------------------------------------------------------ tabs */

test('featureOrder honours the caller, drops the unknown and collapses duplicates', () => {
  assert.deepEqual(featureOrder(['score', 'factcheck']), ['score', 'factcheck']);
  assert.deepEqual(featureOrder(['score', 'score']), ['score']);
  assert.deepEqual(featureOrder(['score', 'nonsense']), ['score']);
  // An empty or wholly unknown list is a caller mistake, not a reason to render no tabs.
  assert.deepEqual(featureOrder([]), ['factcheck', 'score', 'semantic', 'summarise']);
  assert.deepEqual(featureOrder(['nope']), ['factcheck', 'score', 'semantic', 'summarise']);
  assert.deepEqual(featureOrder(undefined), ['factcheck', 'score', 'semantic', 'summarise']);
});

test('firstResultTab opens on what has already been run', () => {
  const call = callFromRow({ id: 'a', transcript: 'hi', semantic: { modes: ['text'] } });
  assert.equal(firstResultTab(['factcheck', 'score', 'semantic'], call, false), 'semantic');
  // The caller's order decides, not the enum's.
  assert.equal(firstResultTab(['semantic', 'score'], call, false), 'semantic');
  // Nothing run yet: leave the tab where it is rather than jumping to the first one.
  const blank = callFromRow({ id: 'b', transcript: 'hi' });
  assert.equal(firstResultTab(['factcheck', 'score'], blank, false), null);
  assert.equal(firstResultTab(['summarise'], blank, true), 'summarise');
  assert.equal(firstResultTab(['score'], null as Call | null, false), null);
});

/* ------------------------------------------------------------------ narrowing */

test('numOrNull refuses everything that is not a number', () => {
  assert.equal(numOrNull(3), 3);
  assert.equal(numOrNull('3.5'), 3.5);
  assert.equal(numOrNull(0), 0);                 // a real zero survives
  assert.equal(numOrNull(''), null);
  assert.equal(numOrNull(null), null);
  assert.equal(numOrNull(undefined), null);
  assert.equal(numOrNull(NaN), null);
  assert.equal(numOrNull('abc'), null);
});

test('pct clamps a bar to something drawable', () => {
  assert.equal(pct(42.4), 42);
  assert.equal(pct(-5), 0);
  assert.equal(pct(140), 100);
  assert.equal(pct(null), 0);
  assert.equal(pct('not a number'), 0);
});
