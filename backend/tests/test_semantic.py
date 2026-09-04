"""`services/semantic.py` — tone of the words, tone of the voice, and the timeline spans (§6).

Two judges that must never be averaged, so they are pinned separately: the text judge is
`llm.call_tool` (replaced on the module, overriding the conftest detonator) and the voice
judge is `sentiment.prosody_segments` (replaced the same way — the sidecar cannot run here).
What matters to the workbench is fixed below: the level maps and per-speaker verdict
thresholds, the prompt-size merge cap and its map back to original positions, the exact
§3 span shape for both lanes, and how a sloppy-but-schema-valid answer (string indices,
unknown tones, invented speakers, out-of-range citations) degrades to neutral rather than
to an exception. Voice failures must never cost the text result that was already paid for.
"""
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.services import llm  # noqa: E402 — follows the sys.path bootstrap
from app.services import semantic as SM  # noqa: E402
from app.services import sentiment  # noqa: E402

SPAN_KEYS = {"segments", "start", "end", "level", "score", "label", "detail"}
ROW_KEYS = {"i", "speaker", "start", "end", "text_tone", "text_level", "text_note",
            "voice_label", "voice_level", "voice_confidence"}
RESULT_KEYS = {"modes", "language", "speakers", "segments", "spans", "summary",
               "voice_available", "voice_status"}


def seg(i, speaker, start, end, text):
    return {"i": i, "speaker": speaker, "start": start, "end": end, "text": text}


def line(k, speaker, text=None):
    """One 0.9 s segment starting at second `k` — for building long calls."""
    return seg(k, speaker, float(k), k + 0.9, text or f"line {k}")


SEGS = [
    seg(0, "speaker_0", 0.0, 2.5, "Hello, thank you for calling, how can I help?"),
    seg(1, "speaker_1", 2.6, 5.0, "My card was blocked again."),
    seg(2, "speaker_1", 5.1, 9.0, "This is the third time, you people are useless!"),
    seg(3, "speaker_0", 9.2, 12.0, "I understand, let me check that for you."),
    seg(4, "speaker_0", 12.1, 15.0, "Give me a second."),
    seg(5, "speaker_1", 15.2, 18.0, "Fine."),
    seg(6, "speaker_0", 18.1, 22.0, "Done — the card is active again. Anything else?"),
]

# A schema-valid-but-sloppy text judgment: string/float/bool indices, odd casing, an
# unknown tone, an out-of-range line, an invented speaker, garbage entries.
TONE_ANSWER = {
    "segments": [
        {"i": 0, "tone": "polite", "note": "greets and offers help"},
        {"i": "2", "tone": "AGGRESSIVE", "note": "insults the agent"},
        {"i": 4, "tone": "curt", "note": "clipped"},
        {"i": 5, "tone": "bogus-tone", "note": "x"},
        {"i": 99, "tone": "rude", "note": "out of range"},
        {"i": 2.5, "tone": "rude", "note": "bad index"},
        {"i": True, "tone": "rude", "note": "bool index"},
        "garbage",
        None,
    ],
    "speakers": [
        {"speaker": "Speaker 0", "role": "agent", "politeness": 140, "overall": "polite",
         "flags": None, "rationale": "courteous"},
        {"speaker": "speaker_1", "role": "villain", "politeness": "37", "overall": "rude",
         "flags": ["insults", {"a": "blames"}], "rationale": "hostile"},
        {"speaker": "speaker_9", "role": "customer", "politeness": 1, "overall": "rude",
         "flags": [], "rationale": "not in the transcript"},
        "garbage",
    ],
    "summary": "Tense call handled politely.",
}

# The sidecar's answer, equally sloppy: string index and confidence, upper-case label, a
# slice it could not classify, a missing position (6), an out-of-range one, junk entries.
VOICE_ANSWER = [
    {"i": 0, "label": "neutral", "confidence": 0.8},
    {"i": 1, "label": "frustrated", "confidence": 0.6},
    {"i": "2", "label": "ANGRY", "confidence": "0.91"},
    {"i": 3, "label": "calm", "confidence": 0.7},
    {"i": 4, "label": "unknown", "confidence": 0.0},
    {"i": 5, "label": "sad", "confidence": 0.5},
    {"i": 42, "label": "angry", "confidence": 0.9},
    {"i": "x", "label": "angry"},
    "junk",
]


class Fake:
    """Both judges at once: records every model call and every sidecar call."""

    def __init__(self):
        self.tone = TONE_ANSWER
        self.voice = VOICE_ANSWER
        # What the real sidecar wrapper reports when it returns no items; a test that sets
        # `voice = None` can set this to the reason it wants to assert on.
        self.voice_status = "unreachable"
        self.fail = None
        self.calls: list[dict] = []
        self.prosody_calls: list[tuple] = []

    async def call_tool(self, **kw):
        self.calls.append(kw)
        if self.fail:
            raise self.fail
        return self.tone

    async def prosody_segments(self, audio, ranges, filename=None, content_type=None):
        # Mirrors the real (items, status) contract — see services/sentiment.py.
        self.prosody_calls.append((audio, ranges, filename, content_type))
        return self.voice, ("ok" if self.voice is not None else self.voice_status)


@pytest.fixture
def fake(monkeypatch):
    f = Fake()
    monkeypatch.setattr(llm, "call_tool", f.call_tool)
    monkeypatch.setattr(sentiment, "prosody_segments", f.prosody_segments)
    return f


async def run(fake, **overrides):
    kw = dict(segments=SEGS, transcript="", audio=b"AUDIO", filename="a.wav",
              content_type="audio/wav", modes={"text", "voice"}, api_key="k", model="m")
    kw.update(overrides)
    return await SM.analyse(**kw)


# ---------------------------------------------------------------------------
# Vocabulary, schema, the contract's numbers
# ---------------------------------------------------------------------------
def test_tone_tool_is_strict_and_matches_the_section_6_shape():
    assert SM.TONE_TOOL["name"] == "submit_tone" and SM.TONE_TOOL["strict"] is True
    schema = SM.TONE_TOOL["input_schema"]
    assert schema["required"] == ["segments", "speakers", "summary"]
    assert schema["additionalProperties"] is False
    props = schema["properties"]
    per_line = props["segments"]["items"]
    assert per_line["required"] == ["i", "tone", "note"] and per_line["additionalProperties"] is False
    assert per_line["properties"]["i"]["type"] == "integer"
    assert per_line["properties"]["tone"]["enum"] == [
        "polite", "neutral", "curt", "impolite", "rude", "aggressive"]
    per_speaker = props["speakers"]["items"]
    assert per_speaker["required"] == ["speaker", "role", "politeness", "overall", "flags", "rationale"]
    assert per_speaker["additionalProperties"] is False
    assert per_speaker["properties"]["role"]["enum"] == ["agent", "customer", "unknown"]
    assert per_speaker["properties"]["overall"]["enum"] == list(SM.TONES)
    assert per_speaker["properties"]["politeness"]["type"] == "integer"
    flags = per_speaker["properties"]["flags"]
    assert (flags["type"], flags["items"]) == ("array", {"type": "string"})
    assert props["summary"]["type"] == "string"


def test_thresholds_and_cap_are_the_contract_numbers():
    assert SM.AGGRESSIVE_SHARE == 0.30 and SM.PATIENT_SHARE == 0.80
    assert SM.PROMPT_SEGMENT_CAP == 160


# ---------------------------------------------------------------------------
# Level maps and per-speaker verdicts (pure)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("tone, level", [
    ("polite", "good"), ("neutral", "good"), ("curt", "mid"),
    ("impolite", "bad"), ("rude", "bad"), ("aggressive", "bad"),
    ("POLITE", "good"), (" Rude ", "bad"),            # case/whitespace-insensitive
    ("bogus", "good"), ("", "good"), (None, "good"),   # unknown → neutral → good
])
def test_text_level(tone, level):
    assert SM.text_level(tone) == level


def test_text_level_map_is_the_contract():
    assert SM.TEXT_LEVEL == {"polite": "good", "neutral": "good", "curt": "mid",
                             "impolite": "bad", "rude": "bad", "aggressive": "bad"}


@pytest.mark.parametrize("label, level", [
    ("angry", "bad"), ("frustrated", "bad"), ("disgusted", "bad"), ("fearful", "bad"),
    ("sad", "mid"),
    ("neutral", "good"), ("calm", "good"), ("happy", "good"), ("excited", "good"),
    ("surprised", "good"), ("other", "good"),
    ("bored", "good"), ("ANGRY", "bad"),               # unlisted → the "other" bucket; case-insensitive
    ("unknown", "none"),                               # too short to classify: not drawn
])
def test_voice_level(label, level):
    assert SM.voice_level(label) == level


@pytest.mark.parametrize("share_bad, share_good, classified, verdict", [
    (0.0, 0.0, 0, "unknown"),
    (0.9, 1.0, 0, "unknown"),          # nothing classified → the shares mean nothing
    (0.30, 0.5, 10, "aggressive"),
    (0.29, 0.5, 10, "tense"),
    (0.01, 0.99, 100, "tense"),        # any hostility at all is "tense"
    (0.0, 0.80, 10, "patient"),
    (0.0, 0.79, 10, "calm"),
    (0.0, 1.0, 1, "patient"),
    (0.0, 0.0, 5, "calm"),             # all "mid" (sad): no hostility, not patient either
])
def test_voice_verdict_thresholds(share_bad, share_good, classified, verdict):
    assert SM.voice_verdict(share_bad, share_good, classified) == verdict


def test_speaker_voice_counts_only_classified_segments():
    assert SM.speaker_voice(["good"] * 4 + ["mid"]) == {
        "voice": "patient", "share_bad": 0.0, "share_good": 0.8}
    assert SM.speaker_voice(["good", "bad", "good", "good"]) == {
        "voice": "tense", "share_bad": 0.25, "share_good": 0.75}
    assert SM.speaker_voice(["bad", "bad", "mid", "none", "none"]) == {
        "voice": "aggressive", "share_bad": 0.667, "share_good": 0.0}
    assert SM.speaker_voice(["good", "good", "none"]) == {
        "voice": "patient", "share_bad": 0.0, "share_good": 1.0}
    assert SM.speaker_voice(["none", "none"]) == {"voice": "unknown", "share_bad": 0.0, "share_good": 0.0}
    assert SM.speaker_voice([]) == {"voice": "unknown", "share_bad": 0.0, "share_good": 0.0}


# ---------------------------------------------------------------------------
# The prompt-size cap (pure)
# ---------------------------------------------------------------------------
def test_merge_under_or_at_the_cap_is_the_identity():
    merged, groups = SM.merge_for_prompt(SEGS)
    assert merged == SEGS and merged is not SEGS
    assert groups == [[i] for i in range(7)]
    exactly = [line(k, "speaker_0") for k in range(SM.PROMPT_SEGMENT_CAP)]
    assert SM.merge_for_prompt(exactly)[1] == [[k] for k in range(SM.PROMPT_SEGMENT_CAP)]


def test_merge_uses_the_smallest_run_that_fits():
    one_speaker = [line(k, "speaker_0") for k in range(161)]
    merged, groups = SM.merge_for_prompt(one_speaker)
    assert len(merged) == 81 and max(map(len, groups)) == 2      # pairs, not everything in a row
    assert groups[0] == [0, 1] and groups[-1] == [160]
    assert merged[0] == {"i": 0, "speaker": "speaker_0", "start": 0.0, "end": 1.9,
                         "text": "line 0 line 1"}
    assert [m["i"] for m in merged] == list(range(81))


def test_merge_never_straddles_speakers_and_maps_back_to_every_position():
    big = [line(k, f"speaker_{(k // 4) % 2}") for k in range(400)]   # runs of 4, alternating
    merged, groups = SM.merge_for_prompt(big)
    assert len(merged) == len(groups) == 100                       # k=4 is the first that fits
    assert [p for g in groups for p in g] == list(range(400))      # every position once, in order
    assert all(len({big[p]["speaker"] for p in g}) == 1 for g in groups)
    assert merged[0] == {"i": 0, "speaker": "speaker_0", "start": 0.0, "end": 3.9,
                         "text": "line 0 line 1 line 2 line 3"}
    assert merged[1]["speaker"] == "speaker_1"


def test_alternating_speakers_cannot_merge_and_are_prompted_as_is():
    alternating = [line(k, f"speaker_{k % 2}") for k in range(200)]
    merged, groups = SM.merge_for_prompt(alternating)
    assert len(merged) == 200 and all(len(g) == 1 for g in groups)


def test_merge_stops_at_the_longest_same_speaker_run_when_the_cap_cannot_be_met():
    merged, groups = SM.merge_for_prompt(SEGS, cap=3)
    assert groups == [[0], [1, 2], [3, 4], [5], [6]]
    assert merged[1]["text"] == "My card was blocked again. This is the third time, you people are useless!"
    assert (merged[1]["start"], merged[1]["end"]) == (2.6, 9.0)


def test_merge_in_text_mode_keeps_none_times():
    rows = [seg(k, "speaker_0", None, None, f"t{k}") for k in range(161)]
    merged, _ = SM.merge_for_prompt(rows)
    assert len(merged) == 81
    assert merged[0] == {"i": 0, "speaker": "speaker_0", "start": None, "end": None, "text": "t0 t1"}


# ---------------------------------------------------------------------------
# analyse(): transport and prompt
# ---------------------------------------------------------------------------
async def test_text_judge_transport_and_prompt(fake):
    await run(fake, guidance="Be strict.", client_id="tenant-a", user_id="u1")
    (call,) = fake.calls
    assert call["feature"] == "semantic_text" and call["client_id"] == "tenant-a"
    assert call["tool"] is SM.TONE_TOOL and call["opts"] is llm.ANALYSIS
    assert call["stream"] is True and call["max_tokens"] == SM.TONE_MAX_TOKENS
    assert call["api_key"] == "k" and call["model"] == "m"
    assert call["system"].startswith(SM._TONE_SYSTEM) and call["system"].endswith("Be strict.")
    assert "WORDS ONLY" in call["system"]
    assert call["user"].startswith("Speaker ids in this transcript: speaker_0, speaker_1\n")
    assert "[#2 00:05.1-00:09.0 speaker_1] This is the third time, you people are useless!" in call["user"]
    assert call["user"].count("[#") == 7


async def test_blank_guidance_leaves_the_system_prompt_bare(fake):
    await run(fake, guidance="   ")
    assert fake.calls[0]["system"] == SM._TONE_SYSTEM


async def test_voice_judge_receives_the_audio_and_only_the_timed_ranges(fake):
    mixed = SEGS[:3] + [seg(3, "speaker_0", None, None, "untimed"),
                        seg(4, "speaker_0", 9.2, None, "half-timed")]
    await run(fake, segments=mixed)
    ((audio, ranges, filename, content_type),) = fake.prosody_calls
    assert (audio, filename, content_type) == (b"AUDIO", "a.wav", "audio/wav")
    assert ranges == [{"i": 0, "start": 0.0, "end": 2.5},
                      {"i": 1, "start": 2.6, "end": 5.0},
                      {"i": 2, "start": 5.1, "end": 9.0}]


# ---------------------------------------------------------------------------
# analyse(): the §6 record, field by field
# ---------------------------------------------------------------------------
async def test_voice_status_says_why_there_is_no_voice_half(fake):
    """A missing voice half must name its cause. Reporting every one of them as a bare
    "unavailable" is what made a sidecar still fetching its model indistinguishable from a
    recording that never had timestamps."""
    fake.voice, fake.voice_status = None, "timeout"
    out = await run(fake)
    assert out["voice_available"] is False and out["voice_status"] == "timeout"

    fake.voice, fake.voice_status = None, "unreachable"
    assert (await run(fake))["voice_status"] == "unreachable"

    # Not asked for at all is its own answer, and never reaches the sidecar.
    fake.prosody_calls.clear()
    out = await run(fake, modes={"text"})
    assert out["voice_status"] == "not_requested" and not fake.prosody_calls


async def test_voice_status_no_timestamps_when_segments_carry_no_times(fake):
    """A pasted transcript (or a recording made before transcripts carried timings) has
    nothing to slice, so the sidecar is never called."""
    untimed = [seg(i, "speaker_0", None, None, txt) for i, txt in enumerate(["one", "two"])]
    out = await run(fake, segments=untimed)
    assert out["voice_available"] is False and out["voice_status"] == "no_timestamps"
    assert not fake.prosody_calls



async def test_result_shape_and_mode_bookkeeping(fake):
    out = await run(fake, language="en")
    assert set(out) == RESULT_KEYS
    assert out["modes"] == ["text", "voice"] and out["voice_available"] is True
    assert out["voice_status"] == "ok"
    assert out["language"] == "en" and out["summary"] == "Tense call handled politely."
    assert set(out["spans"]) == {"text", "voice"}
    assert [set(r) for r in out["segments"]] == [ROW_KEYS] * 7
    assert [(r["i"], r["speaker"], r["start"], r["end"]) for r in out["segments"]][:2] == [
        (0, "speaker_0", 0.0, 2.5), (1, "speaker_1", 2.6, 5.0)]


async def test_language_defaults_to_none(fake):
    assert (await run(fake))["language"] is None


async def test_text_tones_are_normalised_per_line(fake):
    out = await run(fake)
    assert [(r["text_tone"], r["text_level"], r["text_note"]) for r in out["segments"]] == [
        ("polite", "good", "greets and offers help"),
        ("neutral", "good", ""),                       # not listed → neutral
        ("aggressive", "bad", "insults the agent"),    # "2" / "AGGRESSIVE"
        ("neutral", "good", ""),
        ("curt", "mid", "clipped"),
        ("neutral", "good", "x"),                      # unknown tone → neutral, note kept
        ("neutral", "good", ""),
    ]


async def test_voice_labels_are_normalised_per_line(fake):
    out = await run(fake)
    assert [(r["voice_label"], r["voice_level"], r["voice_confidence"]) for r in out["segments"]] == [
        ("neutral", "good", 0.8),
        ("frustrated", "bad", 0.6),
        ("angry", "bad", 0.91),                        # "ANGRY" / "0.91"
        ("calm", "good", 0.7),
        ("unknown", "none", 0.0),                      # the sidecar's own "too short"
        ("sad", "mid", 0.5),
        ("unknown", "none", None),                     # position the sidecar never answered
    ]


async def test_speakers_combine_the_text_and_voice_verdicts(fake):
    out = await run(fake)
    assert [s["speaker"] for s in out["speakers"]] == ["speaker_0", "speaker_1"]   # speaker_9 ignored
    s0, s1 = out["speakers"]
    assert set(s0) == {"speaker", "role", "text", "voice"}
    assert s0["role"] == "agent"
    assert s0["text"] == {"politeness": 100, "overall": "polite", "flags": [], "rationale": "courteous"}
    # speaker_0 voice: 0 neutral + 3 calm = good; 4 and 6 unknown are left out → 2/2 good
    assert s0["voice"] == {"voice": "patient", "share_bad": 0.0, "share_good": 1.0}
    assert s1["role"] == "unknown"                                                # "villain"
    assert s1["text"] == {"politeness": 37, "overall": "rude", "flags": ["insults", "blames"],
                          "rationale": "hostile"}
    # speaker_1 voice: 1 frustrated + 2 angry = bad, 5 sad = mid → 2/3 bad
    assert s1["voice"] == {"voice": "aggressive", "share_bad": 0.667, "share_good": 0.0}


async def test_text_spans_have_the_exact_section_3_shape_and_merge_same_tone_runs(fake):
    spans = (await run(fake))["spans"]["text"]
    for span in spans:
        assert set(span) == SPAN_KEYS
        assert isinstance(span["segments"], list) and span["segments"]
        assert isinstance(span["start"], float) and isinstance(span["end"], float)
        assert span["level"] in ("good", "mid", "bad", "none")
        assert span["score"] is None
        assert span["label"] in SM.TONES and isinstance(span["detail"], str)
    assert [(s["segments"], s["level"], s["label"], s["detail"]) for s in spans] == [
        ([0], "good", "polite", "greets and offers help"),
        ([1], "good", "neutral", ""),
        ([2], "bad", "aggressive", "insults the agent"),
        ([3], "good", "neutral", ""),
        ([4], "mid", "curt", "clipped"),
        ([5, 6], "good", "neutral", "x"),               # adjacent neutrals → one block
    ]
    assert spans[0] == {"segments": [0], "start": 0.0, "end": 2.5, "level": "good", "score": None,
                        "label": "polite", "detail": "greets and offers help"}
    assert (spans[-1]["start"], spans[-1]["end"]) == (15.2, 22.0)


async def test_voice_spans_skip_unknown_and_split_on_label_not_level(fake):
    spans = (await run(fake))["spans"]["voice"]
    for span in spans:
        assert set(span) == SPAN_KEYS and span["score"] is None
        assert isinstance(span["start"], float) and isinstance(span["end"], float)
    assert [(s["segments"], s["level"], s["label"], s["detail"]) for s in spans] == [
        ([0], "good", "neutral", "neutral · 80%"),
        ([1], "bad", "frustrated", "frustrated · 60%"),   # two "bad" labels stay two spans
        ([2], "bad", "angry", "angry · 91%"),
        ([3], "good", "calm", "calm · 70%"),
        ([5], "mid", "sad", "sad · 50%"),                  # 4 and 6 (unknown) are not drawn
    ]


# ---------------------------------------------------------------------------
# analyse(): modes, sources, degraded judges
# ---------------------------------------------------------------------------
async def test_text_source_drops_voice_silently_and_highlights_the_transcript(fake):
    fake.tone = {"segments": [{"i": 1, "tone": "rude", "note": "insult"}], "speakers": [],
                 "summary": "s"}
    out = await run(fake, segments=None, audio=None,
                    transcript="Agent: Hello there\nCustomer: You are useless!")
    assert out["modes"] == ["text"] and out["voice_available"] is False
    assert fake.prosody_calls == [] and "[#0 agent] Hello there" in fake.calls[0]["user"]
    assert [(r["i"], r["speaker"], r["start"], r["end"]) for r in out["segments"]] == [
        (0, "agent", None, None), (1, "customer", None, None)]
    assert all(r["voice_label"] is None and r["voice_level"] is None for r in out["segments"])
    assert out["spans"]["voice"] == []
    # §3: in text mode the span still exists, with start/end = None
    assert [(s["segments"], s["start"], s["end"], s["level"], s["label"])
            for s in out["spans"]["text"]] == [([0], None, None, "good", "neutral"),
                                               ([1], None, None, "bad", "rude")]
    assert [(s["speaker"], s["role"], s["text"], s["voice"]) for s in out["speakers"]] == [
        ("agent", "unknown", None, None), ("customer", "unknown", None, None)]


async def test_voice_with_audio_but_untimed_segments_never_calls_the_sidecar(fake):
    rows = [seg(0, "agent", None, None, "Hello"), seg(1, "customer", None, None, "hi")]
    out = await run(fake, segments=rows, modes={"voice"})
    assert out["modes"] == ["voice"] and out["voice_available"] is False
    assert fake.prosody_calls == [] and fake.calls == []


async def test_voice_only_with_the_sidecar_down_makes_no_model_call(fake):
    fake.voice = None
    out = await run(fake, modes={"voice"})
    assert fake.calls == [] and len(fake.prosody_calls) == 1
    assert out["modes"] == ["voice"] and out["voice_available"] is False
    assert out["summary"] == "" and out["spans"] == {"text": [], "voice": []}
    assert all(r["text_tone"] is None and r["voice_label"] is None for r in out["segments"])
    assert all(s["text"] is None and s["voice"] is None and s["role"] == "unknown"
               for s in out["speakers"])


async def test_voice_only_with_the_sidecar_up(fake):
    out = await run(fake, modes={"voice"})
    assert fake.calls == [] and out["modes"] == ["voice"] and out["voice_available"] is True
    assert out["spans"]["text"] == [] and len(out["spans"]["voice"]) == 5
    assert all(r["text_tone"] is None and r["text_level"] is None for r in out["segments"])
    assert out["speakers"][1]["text"] is None
    assert out["speakers"][1]["voice"]["voice"] == "aggressive"


async def test_sidecar_down_only_clears_voice_when_both_were_asked(fake):
    fake.voice = None
    out = await run(fake)
    # "voice" stays in modes (it was attempted — audio existed); voice_available says it failed
    assert out["modes"] == ["text", "voice"] and out["voice_available"] is False
    assert out["spans"]["voice"] == [] and len(out["spans"]["text"]) == 6
    assert out["speakers"][0]["voice"] is None
    assert out["speakers"][0]["text"]["politeness"] == 100


@pytest.mark.parametrize("modes", [set(), {"xyz"}, None])
async def test_empty_or_unknown_modes_fall_back_to_text(fake, modes):
    out = await run(fake, modes=modes, audio=None)
    assert out["modes"] == ["text"] and len(fake.calls) == 1 and fake.prosody_calls == []


async def test_nothing_to_analyse_is_an_empty_record_without_a_model_call(fake):
    out = await run(fake, segments=None, transcript="   ", audio=None, modes={"text"})
    assert fake.calls == [] and out["segments"] == [] and out["speakers"] == []
    assert out["spans"] == {"text": [], "voice": []}
    assert out["modes"] == ["text"] and out["voice_available"] is False


# ---------------------------------------------------------------------------
# analyse(): failures and garbage
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("exc", [llm.LLMError("upstream down"), llm.LLMBusyError("at capacity"),
                                 llm.LLMTruncatedError("cut off")])
async def test_model_failure_is_a_semanticerror_with_its_cause(fake, exc):
    fake.fail = exc
    with pytest.raises(SM.SemanticError) as info:
        await run(fake, modes={"text"})
    assert info.value.__cause__ is exc


async def test_model_answer_that_is_not_a_dict_is_a_semanticerror(fake):
    fake.tone = None
    with pytest.raises(SM.SemanticError):
        await run(fake, modes={"text"})


async def test_missing_api_key_is_a_semanticerror_before_any_call(fake):
    with pytest.raises(SM.SemanticError):
        await run(fake, modes={"text"}, api_key="")
    assert fake.calls == []


async def test_garbage_model_answer_degrades_to_all_neutral(fake):
    fake.tone = {"segments": "nope", "speakers": None, "summary": None}
    out = await run(fake, modes={"text"})
    assert all(r["text_tone"] == "neutral" and r["text_level"] == "good" and r["text_note"] == ""
               for r in out["segments"])
    assert all(s["text"] is None and s["role"] == "unknown" for s in out["speakers"])
    assert out["summary"] == ""
    assert [s["segments"] for s in out["spans"]["text"]] == [[0, 1, 2, 3, 4, 5, 6]]   # one green block


@pytest.mark.parametrize("politeness, expected", [
    (140, 100), (-5, 0), ("37", 37), (49.6, 50), (None, None), (True, None), ("abc", None),
])
async def test_politeness_is_clamped_to_0_100(fake, politeness, expected):
    fake.tone = {"segments": [], "summary": "",
                 "speakers": [{"speaker": "speaker_0", "role": "agent", "politeness": politeness,
                               "overall": "polite", "flags": [], "rationale": ""}]}
    out = await run(fake, modes={"text"})
    assert out["speakers"][0]["text"]["politeness"] == expected


@pytest.mark.parametrize("flags, expected", [
    (None, []), ("single", ["single"]), ({"x": "c"}, ["c"]), (7, ["7"]),
    (["a", "", None, {"k": "b", "z": ""}], ["a", "b"]),
])
async def test_flags_are_coerced_to_a_clean_string_list(fake, flags, expected):
    fake.tone = {"segments": [], "summary": "",
                 "speakers": [{"speaker": "speaker_0", "role": "agent", "politeness": 50,
                               "overall": "neutral", "flags": flags, "rationale": ""}]}
    out = await run(fake, modes={"text"})
    assert out["speakers"][0]["text"]["flags"] == expected


@pytest.mark.parametrize("spelled", ["speaker_0", "Speaker 0", "SPEAKER-0", " speaker_0 "])
async def test_speaker_ids_are_matched_loosely(fake, spelled):
    fake.tone = {"segments": [], "summary": "",
                 "speakers": [{"speaker": spelled, "role": "CUSTOMER", "politeness": 50,
                               "overall": "curt", "flags": [], "rationale": "r"}]}
    out = await run(fake, modes={"text"})
    assert out["speakers"][0]["role"] == "customer"
    assert out["speakers"][0]["text"]["overall"] == "curt"
    assert out["speakers"][1]["text"] is None                 # speaker_1 was not judged


async def test_positions_are_renumbered_densely_regardless_of_the_stored_i(fake):
    stale = [dict(s, i=s["i"] + 10) for s in SEGS]
    out = await run(fake, segments=stale, modes={"text"})
    assert [r["i"] for r in out["segments"]] == list(range(7))
    assert "[#0 00:00.0-00:02.5 speaker_0]" in fake.calls[0]["user"]
    assert out["spans"]["text"][2]["segments"] == [2]        # cited "2" lands on position 2


async def test_non_dict_segment_rows_are_dropped(fake):
    out = await run(fake, segments=[SEGS[0], "junk", None, SEGS[1]], modes={"text"})
    assert [(r["i"], r["speaker"]) for r in out["segments"]] == [(0, "speaker_0"), (1, "speaker_1")]


async def test_over_the_cap_the_prompt_is_merged_and_a_cited_line_fans_out(fake):
    big = [line(k, f"speaker_{(k // 4) % 2}") for k in range(400)]
    fake.tone = {"segments": [{"i": 0, "tone": "rude", "note": "n"},
                              {"i": 1, "tone": "curt", "note": "c"}],
                 "speakers": [], "summary": "s"}
    out = await run(fake, segments=big, modes={"text"})
    user = fake.calls[0]["user"]
    assert user.count("[#") == 100
    assert "[#0 00:00.0-00:03.9 speaker_0] line 0 line 1 line 2 line 3" in user
    assert len(out["segments"]) == 400                        # the record is per ORIGINAL segment
    assert [r["i"] for r in out["segments"] if r["text_tone"] == "rude"] == [0, 1, 2, 3]
    assert [r["i"] for r in out["segments"] if r["text_tone"] == "curt"] == [4, 5, 6, 7]
    first = out["spans"]["text"][0]
    assert (first["segments"], first["start"], first["end"], first["level"], first["label"]) == (
        [0, 1, 2, 3], 0.0, 3.9, "bad", "rude")
    assert set(first["detail"].split(" · ")) == {"n"}
