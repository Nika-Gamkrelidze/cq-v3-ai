"""`services/segments.py`, the coordinate system every analyser cites.

Pure functions, so pure tests: no database, no network, no fixtures. What matters here is
the CONTRACT other services build on — the split rules that keep a segment short enough to
be a highlight, the `#` index a model is asked to cite, and the way indices come back as
seconds — because a drift in any of them shows up as a highlight in the wrong place on a
player, which nothing else in the test suite can catch.

Every fixture below is hand-made in the shape Scribe returns (`text/start/end/type/
speaker_id`), including the shapes it should NOT return (None entries, string numbers,
missing keys), since the same helpers also read rows that other code wrote to jsonb.
"""
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.services import segments as S  # noqa: E402 — follows the sys.path bootstrap


def w(text, start, end, speaker="speaker_0", type="word"):
    return {"text": text, "start": start, "end": end, "type": type, "speaker_id": speaker}


def words(n, *, start=0.0, step=0.5, speaker="speaker_0", prefix="w"):
    """`n` back-to-back words of `step` seconds each, starting at `start`."""
    return [w(f"{prefix}{k}", start + k * step, start + (k + 1) * step, speaker)
            for k in range(n)]


# ---------------------------------------------------------------------------
# build_segments
# ---------------------------------------------------------------------------
def test_groups_consecutive_words_by_speaker():
    out = S.build_segments([
        w("Hello,", 0.42, 0.8), w("how", 0.85, 1.0), w("can", 1.0, 1.2), w("I", 1.2, 1.3),
        w("help?", 1.3, 1.9),
        w("My", 2.1, 2.3, "speaker_1"), w("card", 2.3, 2.6, "speaker_1"),
        w("Sure.", 2.9, 3.9),
    ])
    assert [s["speaker"] for s in out] == ["speaker_0", "speaker_1", "speaker_0"]
    assert [s["i"] for s in out] == [0, 1, 2]
    assert out[0] == {"i": 0, "speaker": "speaker_0", "start": 0.42, "end": 1.9,
                      "text": "Hello, how can I help?"}
    assert out[1]["text"] == "My card" and (out[1]["start"], out[1]["end"]) == (2.1, 2.6)


def test_silence_gap_over_1_2s_splits_but_shorter_does_not():
    out = S.build_segments([w("a", 0.0, 0.5), w("b", 1.7, 2.0),   # 1.2 s exactly: no split
                            w("c", 3.21, 3.5)])                    # 1.21 s: split
    assert [s["text"] for s in out] == ["a b", "c"]


def test_forty_word_cap_splits_a_monologue():
    out = S.build_segments(words(85, step=0.1))    # 8.5 s: only the word cap applies
    assert [len(s["text"].split()) for s in out] == [40, 40, 5]
    assert out[1]["text"].startswith("w40 ") and out[2]["text"] == "w80 w81 w82 w83 w84"


def test_twenty_five_second_cap_splits_slow_speech():
    out = S.build_segments(words(20, step=2.0))    # 40 s over 20 words: only the time cap
    assert len(out) == 2
    assert out[0]["end"] - out[0]["start"] <= 25.0
    assert out[0]["end"] == 24.0 and out[1]["start"] == 24.0


def test_audio_events_and_spacing_are_dropped_not_split_on():
    out = S.build_segments([
        w("Hello", 0.0, 0.5), w(" ", 0.5, 0.6, type="spacing"),
        w("(laughter)", 0.6, 1.4, type="audio_event"),
        w("there", 1.4, 1.8), w("(music)", 1.8, 5.0, "speaker_1", type="audio_event"),
    ])
    assert out == [{"i": 0, "speaker": "speaker_0", "start": 0.0, "end": 1.8,
                    "text": "Hello there"}]


def test_missing_speaker_defaults_and_times_are_rounded():
    out = S.build_segments([{"text": "x", "start": 0.123456, "end": 0.98765, "type": "word"},
                            {"text": "y", "start": 1.0, "end": 1.5, "type": "word",
                             "speaker_id": None},
                            {"text": "z", "start": 1.5, "end": 2.0, "type": "word",
                             "speaker_id": ""}])
    assert out == [{"i": 0, "speaker": "speaker_0", "start": 0.12, "end": 2.0, "text": "x y z"}]


def test_tolerates_garbage_entries_and_string_numbers():
    out = S.build_segments([
        None, "not a dict", 42, {"type": "word"}, {"text": "   ", "start": 0, "end": 1},
        {"text": "one", "start": "0.5", "end": "1.0", "type": "word", "speaker_id": "s"},
        {"text": "two", "start": "nope", "end": None, "type": "word", "speaker_id": "s"},
        {"text": "three", "start": 1.6, "end": 2.0, "speaker_id": "s"},   # no `type` at all
    ])
    assert out == [{"i": 0, "speaker": "s", "start": 0.5, "end": 2.0, "text": "one two three"}]


@pytest.mark.parametrize("bad", [None, [], {}, "", 0, "words"])
def test_empty_or_missing_words_give_empty_list(bad):
    assert S.build_segments(bad) == []


def test_text_is_whitespace_normalised():
    out = S.build_segments([w("  Hello\n", 0, 0.5), w("\tworld  ", 0.5, 1)])
    assert out[0]["text"] == "Hello world"


# ---------------------------------------------------------------------------
# segments_from_text
# ---------------------------------------------------------------------------
def test_speaker_labels_are_parsed_lowercased_and_stripped_of_the_colon():
    out = S.segments_from_text(
        "Agent: Good morning, how can I help?\n"
        "Customer:  My card was blocked.\n"
        "\n"
        "ოპერატორი: ერთი წუთით.\n"
        "speaker_1: thanks\n"
        "no label on this line\n"
    )
    assert [(s["speaker"], s["text"]) for s in out] == [
        ("agent", "Good morning, how can I help?"),
        ("customer", "My card was blocked."),
        ("ოპერატორი", "ერთი წუთით."),
        ("speaker_1", "thanks"),
        ("speaker_0", "no label on this line"),
    ]
    assert [s["i"] for s in out] == [0, 1, 2, 3, 4]
    assert all(s["start"] is None and s["end"] is None for s in out)


def test_things_that_are_not_speaker_labels():
    out = S.segments_from_text("12:30 we agreed to call back\nhttps://example.com/help\n")
    assert [(s["speaker"], s["text"]) for s in out] == [
        ("speaker_0", "12:30 we agreed to call back"),
        ("speaker_0", "https://example.com/help"),
    ]


def test_bare_label_line_names_the_next_line():
    out = S.segments_from_text("Agent:\nHello there.\nAnd this one is unlabelled.\n")
    assert [(s["speaker"], s["text"]) for s in out] == [
        ("agent", "Hello there."), ("speaker_0", "And this one is unlabelled.")]


def test_long_line_is_split_at_sentence_punctuation():
    sentences = [f"Sentence number {k} has exactly seven words." for k in range(12)]  # 84 words
    out = S.segments_from_text("Agent: " + " ".join(sentences))
    assert len(out) == 2
    assert all(len(s["text"].split()) <= 60 for s in out)
    assert out[0]["text"].endswith("words.") and out[1]["text"].startswith("Sentence number")
    assert {s["speaker"] for s in out} == {"agent"}
    assert " ".join(s["text"] for s in out) == " ".join(sentences)


def test_long_line_without_punctuation_is_chopped_at_the_cap():
    out = S.segments_from_text(" ".join(f"w{k}" for k in range(130)))
    assert [len(s["text"].split()) for s in out] == [60, 60, 10]


def test_georgian_sentence_ends_split_too():
    line = " ".join(["სიტყვა"] * 40) + "჻ " + " ".join(["სიტყვა"] * 40) + "։ " + "ბოლო."
    out = S.segments_from_text(line)
    assert len(out) == 2 and out[0]["text"].endswith("჻")


@pytest.mark.parametrize("bad", [None, "", "\n\n  \n", 0])
def test_empty_text_gives_empty_list(bad):
    assert S.segments_from_text(bad) == []


# ---------------------------------------------------------------------------
# fmt_time / render_timeline
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("seconds,expected", [
    (0, "00:00.0"), (34.2, "00:34.2"), (41.84, "00:41.8"), (59.97, "01:00.0"),
    (600.0, "10:00.0"), (3725.55, "62:05.6"), ("7.5", "00:07.5"), (-3, "00:00.0"),
    (None, "00:00.0"), ("x", "00:00.0"),
])
def test_fmt_time(seconds, expected):
    assert S.fmt_time(seconds) == expected


def test_render_timeline_with_times():
    segs = [{"i": 0, "speaker": "speaker_0", "start": 0.42, "end": 3.9, "text": "Hello"},
            {"i": 1, "speaker": "speaker_1", "start": 34.2, "end": 41.8, "text": "Hi\nthere"}]
    assert S.render_timeline(segs) == (
        "[#0 00:00.4-00:03.9 speaker_0] Hello\n"
        "[#1 00:34.2-00:41.8 speaker_1] Hi there")


def test_render_timeline_without_times_and_with_garbage():
    segs = S.segments_from_text("Agent: yes\nCustomer: no")
    assert S.render_timeline(segs) == "[#0 agent] yes\n[#1 customer] no"
    mixed = [{"speaker": "a", "start": 1.0, "end": None, "text": "half"}, None,
             {"text": "bare"}]
    # A missing end (or a None entry) never produces a half-formed time range.
    assert S.render_timeline(mixed) == "[#0 a] half\n[#2 speaker_0] bare"
    assert S.render_timeline([]) == "" and S.render_timeline(None) == ""


def test_render_timeline_numbers_by_position_so_spans_map_back():
    """The `#` a model cites is the position spans_from_indices looks up — even when a row's
    own `i` disagrees (a subset, or a hand-edited jsonb)."""
    segs = [{"i": 7, "speaker": "s", "start": 0.0, "end": 1.0, "text": "a"},
            {"i": 9, "speaker": "s", "start": 1.0, "end": 2.0, "text": "b"}]
    assert S.render_timeline(segs).splitlines()[1].startswith("[#1 ")
    assert S.spans_from_indices(segs, [1])[0]["start"] == 1.0


# ---------------------------------------------------------------------------
# spans_from_indices / duration_of
# ---------------------------------------------------------------------------
SEGS = [{"i": k, "speaker": "s", "start": float(k), "end": float(k + 1), "text": f"seg{k}"}
        for k in range(6)]   # #0 0-1, #1 1-2, ... #5 5-6


def test_spans_merge_adjacent_indices_and_keep_gaps_apart():
    spans = S.spans_from_indices(SEGS, [4, 3, 0, 3, 5])
    assert spans == [{"segments": [0], "start": 0.0, "end": 1.0},
                     {"segments": [3, 4, 5], "start": 3.0, "end": 6.0}]


def test_spans_drop_out_of_range_and_garbage_indices():
    spans = S.spans_from_indices(SEGS, [-1, 6, 99, None, "x", 2.5, True, "1", 2.0])
    assert spans == [{"segments": [1, 2], "start": 1.0, "end": 3.0}]
    assert S.spans_from_indices(SEGS, []) == []
    assert S.spans_from_indices(SEGS, None) == []
    assert S.spans_from_indices([], [0, 1]) == []
    assert S.spans_from_indices(SEGS, 2) == [{"segments": [2], "start": 2.0, "end": 3.0}]


def test_spans_carry_extra_fields():
    spans = S.spans_from_indices(SEGS, [1], level="good", label="claim", score=None)
    assert spans == [{"segments": [1], "start": 1.0, "end": 2.0, "level": "good",
                      "label": "claim", "score": None}]


def test_spans_in_text_mode_exist_with_none_times():
    segs = S.segments_from_text("Agent: one\nCustomer: two\nAgent: three")
    spans = S.spans_from_indices(segs, [0, 1], level="bad")
    assert spans == [{"segments": [0, 1], "start": None, "end": None, "level": "bad"}]


def test_span_uses_first_known_start_and_last_known_end():
    segs = [{"start": None, "end": None, "text": "a"}, {"start": 2.0, "end": 3.0, "text": "b"},
            {"start": 3.0, "end": None, "text": "c"}]
    assert S.spans_from_indices(segs, [0, 1, 2]) == [
        {"segments": [0, 1, 2], "start": 2.0, "end": 3.0}]


def test_duration_of():
    assert S.duration_of(SEGS) == 6.0
    assert S.duration_of(S.build_segments([w("a", 0, 1), w("b", 5, 12.345)])) == 12.35
    assert S.duration_of(S.segments_from_text("Agent: hi")) is None
    assert S.duration_of([]) is None and S.duration_of(None) is None
    assert S.duration_of([{"start": "4", "end": None}]) == 4.0
