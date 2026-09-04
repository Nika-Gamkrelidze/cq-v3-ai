"""`services/scoring.py` v2 — evidence placed on the timeline, one lane per dimension (§5).

Pure where it can be (`build_result`, `evidence_text`, the level thresholds) and mocked
where it must be (`run_scoring` with `llm.call_tool` replaced on the module). The contract
under test: evidence items are `{quote, segments, start, end}` whether the model returned
objects or legacy plain strings; every cited `#` index is validated against the caller's
segments; each dimension gets §3 spans coloured by its score; `lanes` mirrors those spans
in rubric order; and the weighted arithmetic — code's, not the model's — is unchanged.
"""
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.services import llm  # noqa: E402 — follows the sys.path bootstrap
from app.services import scoring as SC  # noqa: E402

SPAN_KEYS = {"segments", "start", "end", "level", "score", "label", "detail"}

SEGS = [
    {"i": 0, "speaker": "speaker_0", "start": 0.0, "end": 2.5, "text": "Hello, thanks for calling."},
    {"i": 1, "speaker": "speaker_1", "start": 2.6, "end": 5.0, "text": "What is the wire fee?"},
    {"i": 2, "speaker": "speaker_0", "start": 5.2, "end": 9.9, "text": "The wire fee is 25 lari."},
    {"i": 3, "speaker": "speaker_0", "start": 10.1, "end": 14.0,
     "text": "Transfers arrive within two business days."},
    {"i": 4, "speaker": "speaker_1", "start": 14.2, "end": 16.0, "text": "And on weekends?"},
    {"i": 5, "speaker": "speaker_0", "start": 16.3, "end": 20.7,
     "text": "Weekend transfers are processed too."},
]
TRANSCRIPT = "\n".join(s["text"] for s in SEGS)

DIMS = SC.normalize_dimensions([
    {"key": "greeting", "name": "Greeting", "weight": 30, "guidance": "Warm opening."},
    {"key": "accuracy", "name": "Accuracy", "weight": 70, "description": "Facts right."},
])
CONFIG = {"version": 7, "rubric": "Be strict.", "dimensions": DIMS}

# One dimension answered with every evidence shape a model has been seen to produce.
BY_KEY = {
    "greeting": {
        "key": "greeting", "score": 85, "rationale": " Warm. ",
        "evidence": [
            {"quote": "Hello,  thanks for calling.", "segments": [0]},   # object, placed
            "legacy string quote",                                        # pre-v2 shape
            {"quote": "", "segments": []},                                # nothing → dropped
            None,                                                         # dropped
            {"quote": "far", "segments": [99, -1, "x"]},                  # unplaceable → kept, unplaced
            {"quote": "", "segments": [1]},                               # placed, no quote
        ],
    },
    "accuracy": {
        "key": "accuracy", "score": 30, "rationale": "Weekend claim is wrong.",
        "evidence": [
            {"quote": "fee ... weekend", "segments": [5, 2, 3, 3]},       # non-adjacent, unsorted, dup
            {"quote": "five", "segments": "5"},                           # string index
        ],
    },
    "unknown_key": {"key": "unknown_key", "score": 99, "rationale": "", "evidence": []},
}


def result(by_key=BY_KEY, dims=DIMS, segments=SEGS, version=7, operator="speaker_0"):
    return SC.build_result(dims, by_key, version, operator, segments=segments)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
def test_score_tool_evidence_items_are_strict_objects():
    assert SC.SCORE_TOOL["strict"] is True
    item = SC.SCORE_TOOL["input_schema"]["properties"]["scores"]["items"]
    ev = item["properties"]["evidence"]["items"]
    assert ev["type"] == "object" and ev["additionalProperties"] is False
    assert ev["required"] == ["quote", "segments"]
    assert ev["properties"]["quote"]["type"] == "string"
    assert ev["properties"]["segments"] == {
        "type": "array", "items": {"type": "integer"},
        "description": "The # indices of the transcript lines the quote comes from."}


# ---------------------------------------------------------------------------
# Level thresholds
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("score, level", [
    (100, "good"), (70, "good"), (69, "mid"), (40, "mid"), (39, "bad"), (0, "bad"), (None, "none"),
])
def test_level_thresholds(score, level):
    assert SC._level(score) == level
    assert (SC.GOOD_MIN, SC.MID_MIN) == (70, 40)


# ---------------------------------------------------------------------------
# build_result: evidence normalisation
# ---------------------------------------------------------------------------
def test_object_evidence_is_placed_and_legacy_strings_are_kept_unplaced():
    greeting = result()["dimensions"][0]
    assert greeting["evidence"] == [
        {"quote": "Hello, thanks for calling.", "segments": [0], "start": 0.0, "end": 2.5},
        {"quote": "legacy string quote", "segments": [], "start": None, "end": None},
        {"quote": "far", "segments": [], "start": None, "end": None},
        {"quote": "", "segments": [1], "start": 2.6, "end": 5.0},
    ]
    assert greeting["rationale"] == "Warm."


def test_non_adjacent_citations_keep_every_index_but_start_at_the_first_run():
    accuracy = result()["dimensions"][1]
    first, second = accuracy["evidence"]
    assert first == {"quote": "fee ... weekend", "segments": [2, 3, 5], "start": 5.2, "end": 14.0}
    assert second == {"quote": "five", "segments": [5], "start": 16.3, "end": 20.7}


def test_evidence_that_is_not_a_list_is_treated_as_one_entry():
    by_key = {"greeting": {"key": "greeting", "score": 50,
                           "evidence": {"quote": "Hello", "segments": [0]}},
              "accuracy": {"key": "accuracy", "score": 50, "evidence": "lone string"}}
    dims = result(by_key)["dimensions"]
    assert dims[0]["evidence"] == [{"quote": "Hello", "segments": [0], "start": 0.0, "end": 2.5}]
    assert dims[1]["evidence"] == [{"quote": "lone string", "segments": [], "start": None, "end": None}]


def test_missing_or_empty_evidence_is_an_empty_list():
    by_key = {"greeting": {"key": "greeting", "score": 50},
              "accuracy": {"key": "accuracy", "score": 50, "evidence": None}}
    dims = result(by_key)["dimensions"]
    assert dims[0]["evidence"] == [] and dims[1]["evidence"] == []
    assert dims[0]["spans"] == [] and dims[1]["spans"] == []


# ---------------------------------------------------------------------------
# build_result: spans + lanes
# ---------------------------------------------------------------------------
def test_dimension_spans_have_the_exact_section_3_shape():
    out = result()
    for dim in out["dimensions"]:
        for span in dim["spans"]:
            assert set(span) == SPAN_KEYS
            assert isinstance(span["segments"], list) and span["segments"]
            assert isinstance(span["start"], float) and isinstance(span["end"], float)
            assert span["level"] in ("good", "mid", "bad", "none")
            assert span["score"] == dim["score"]
            assert span["label"] == dim["name"]
            assert isinstance(span["detail"], str)


def test_spans_one_per_cited_run_coloured_by_the_dimension_score():
    greeting, accuracy = result()["dimensions"]
    assert greeting["spans"] == [
        {"segments": [0], "start": 0.0, "end": 2.5, "level": "good", "score": 85,
         "label": "Greeting", "detail": "Hello, thanks for calling."},
        {"segments": [1], "start": 2.6, "end": 5.0, "level": "good", "score": 85,
         "label": "Greeting", "detail": ""},
    ]
    assert [(s["segments"], s["level"], s["detail"]) for s in accuracy["spans"]] == [
        ([2, 3], "bad", "fee ... weekend"),
        ([5], "bad", "fee ... weekend"),
        ([5], "bad", "five"),
    ]


def test_lanes_mirror_the_dimensions_in_rubric_order():
    out = result()
    assert [set(lane) for lane in out["lanes"]] == [{"key", "name", "score", "spans"}] * 2
    assert [(l["key"], l["name"], l["score"]) for l in out["lanes"]] == [
        ("greeting", "Greeting", 85), ("accuracy", "Accuracy", 30)]
    for lane, dim in zip(out["lanes"], out["dimensions"]):
        assert lane["spans"] == dim["spans"]


def test_unscored_dimension_is_grey_with_null_score():
    by_key = {"accuracy": {"key": "accuracy", "score": "n/a",
                           "evidence": [{"quote": "q", "segments": [2]}]}}
    greeting, accuracy = result(by_key)["dimensions"]
    assert greeting["score"] is None and greeting["contribution"] == 0 and greeting["spans"] == []
    assert accuracy["score"] is None and accuracy["contribution"] == 0
    (span,) = accuracy["spans"]
    assert (span["level"], span["score"]) == ("none", None)


def test_text_mode_spans_exist_with_null_times():
    text_segs = [{"i": 0, "speaker": "agent", "start": None, "end": None, "text": "Hello there"},
                 {"i": 1, "speaker": "customer", "start": None, "end": None, "text": "hi"}]
    by_key = {"greeting": {"key": "greeting", "score": 72,
                           "evidence": [{"quote": "Hello there", "segments": [0, 3]}]}}
    greeting = result(by_key, segments=text_segs)["dimensions"][0]
    assert greeting["evidence"] == [{"quote": "Hello there", "segments": [0], "start": None, "end": None}]
    (span,) = greeting["spans"]
    assert span == {"segments": [0], "start": None, "end": None, "level": "good", "score": 72,
                    "label": "Greeting", "detail": "Hello there"}


def test_without_segments_citations_are_dropped_but_quotes_survive():
    out = SC.build_result(DIMS, BY_KEY, 7, "speaker_0")
    greeting = out["dimensions"][0]
    assert [e["quote"] for e in greeting["evidence"]] == ["Hello, thanks for calling.",
                                                          "legacy string quote", "far"]
    assert all(e["segments"] == [] and e["start"] is None for e in greeting["evidence"])
    assert all(d["spans"] == [] for d in out["dimensions"])
    assert all(l["spans"] == [] for l in out["lanes"])


# ---------------------------------------------------------------------------
# build_result: the weighted maths is still code's
# ---------------------------------------------------------------------------
def test_weighted_total_and_contributions():
    out = result()
    assert out["weighted_total"] == 46.5          # 85*0.3 + 30*0.7
    assert out["max_total"] == 100 and out["config_version"] == 7
    assert out["operator_speaker"] == "speaker_0"
    greeting, accuracy = out["dimensions"]
    assert (greeting["weight"], greeting["contribution"], greeting["max"]) == (30.0, 25.5, 100)
    assert (accuracy["weight"], accuracy["contribution"]) == (70.0, 21.0)
    assert [d["key"] for d in out["dimensions"]] == ["greeting", "accuracy"]   # unknown_key ignored


def test_scores_are_clamped_and_all_zero_weights_split_evenly():
    dims = SC.normalize_dimensions([{"key": "a", "name": "A", "weight": 0},
                                    {"key": "b", "name": "B", "weight": 0}])
    out = SC.build_result(dims, {"a": {"score": 250, "evidence": []},
                                 "b": {"score": -5, "evidence": []}}, 1, "unknown", segments=SEGS)
    assert [d["score"] for d in out["dimensions"]] == [100, 0]
    assert [d["weight"] for d in out["dimensions"]] == [50.0, 50.0]
    assert out["weighted_total"] == 50.0


# ---------------------------------------------------------------------------
# evidence_text / _as_str_list for pre-v2 renderers
# ---------------------------------------------------------------------------
def test_evidence_text_reads_objects_legacy_strings_and_bare_lists():
    greeting = result()["dimensions"][0]
    assert SC.evidence_text(greeting) == ["Hello, thanks for calling.", "legacy string quote", "far"]
    assert SC.evidence_text(["a", {"quote": " b "}, None, {"quote": ""}, ""]) == ["a", "b"]
    assert SC.evidence_text({"evidence": "single"}) == ["single"]
    assert SC.evidence_text({"evidence": None}) == [] and SC.evidence_text(None) == []
    assert SC.evidence_text({"quote": "not a dim"}) == []      # a dict without `evidence`


def test_as_str_list_is_unchanged():
    assert SC._as_str_list(None) == []
    assert SC._as_str_list("  x ") == ["x"] and SC._as_str_list("  ") == []
    assert SC._as_str_list(["a", None, "", {"q": "b", "s": None}]) == ["a", "b"]
    assert SC._as_str_list({"k": "v"}) == ["v"]
    assert SC._as_str_list(5) == ["5"]


# ---------------------------------------------------------------------------
# run_scoring: prompt, transport, fallbacks
# ---------------------------------------------------------------------------
class Fake:
    def __init__(self, answer=None, fail=None):
        self.answer = answer if answer is not None else {
            "operator_speaker": "speaker_0",
            "scores": [BY_KEY["greeting"], BY_KEY["accuracy"], "garbage", {"key": None, "score": 1}]}
        self.fail = fail
        self.calls: list[dict] = []

    async def __call__(self, **kw):
        self.calls.append(kw)
        if self.fail:
            raise self.fail
        return self.answer


@pytest.fixture
def fake(monkeypatch):
    f = Fake()
    monkeypatch.setattr(llm, "call_tool", f)
    return f


async def test_run_scoring_prompts_with_the_timeline_and_places_evidence(fake):
    out = await SC.run_scoring(TRANSCRIPT, CONFIG, "k", "m", client_id="tenant-a",
                               segments=SEGS, user_id="u1")
    (call,) = fake.calls
    assert call["feature"] == "scoring" and call["client_id"] == "tenant-a"
    assert call["tool"] is SC.SCORE_TOOL and call["opts"] is llm.ANALYSIS
    assert call["api_key"] == "k" and call["model"] == "m"
    assert "[#0 00:00.0-00:02.5 speaker_0] Hello, thanks for calling." in call["user"]
    assert "[#5 00:16.3-00:20.7 speaker_0] Weekend transfers are processed too." in call["user"]
    assert "`#` index" in call["system"] and "never invent timestamps" in call["system"]
    assert "Be strict." in call["system"] and "key='greeting'" in call["system"]
    assert out == result()                           # same maths, same placement


async def test_run_scoring_without_segments_uses_the_transcripts_lines(fake):
    fake.answer = {"operator_speaker": "agent",
                   "scores": [{"key": "greeting", "score": 80, "rationale": "",
                               "evidence": [{"quote": "Hello there", "segments": [0, 3]}]}]}
    out = await SC.run_scoring("Agent: Hello there\nCustomer: hi", CONFIG, "k", "m")
    (call,) = fake.calls
    assert "[#0 agent] Hello there" in call["user"] and "[#1 customer] hi" in call["user"]
    assert call["client_id"] is None
    ev = out["dimensions"][0]["evidence"]
    assert ev == [{"quote": "Hello there", "segments": [0], "start": None, "end": None}]
    (span,) = out["dimensions"][0]["spans"]
    assert (span["start"], span["end"], span["level"]) == (None, None, "good")
    assert out["operator_speaker"] == "agent"


@pytest.mark.parametrize("segments", [None, [], ["garbage", None, 3]])
async def test_empty_or_garbage_segments_fall_back_to_text(fake, segments):
    await SC.run_scoring("Agent: Hello there", CONFIG, "k", "m", segments=segments)
    assert "[#0 agent] Hello there" in fake.calls[0]["user"]


@pytest.mark.xfail(strict=True, reason=(
    "scoring._timeline_for only falls back when the timeline renders to NOTHING; dict rows "
    "with no text ([{}]) render a header-only line ('[#0 speaker_0] ') that is prompted to "
    "the model as the transcript while the real transcript is thrown away"))
async def test_segment_rows_without_text_fall_back_to_the_transcript(fake):
    await SC.run_scoring("Agent: Hello there", CONFIG, "k", "m", segments=[{}, {"text": "  "}])
    assert "[#0 agent] Hello there" in fake.calls[0]["user"]


async def test_legacy_positional_call_shape_still_works(fake):
    out = await SC.run_scoring(TRANSCRIPT, CONFIG, "k", "m")
    assert out["weighted_total"] == 46.5 and fake.calls[0]["client_id"] is None


async def test_missing_operator_speaker_defaults_to_unknown(fake):
    fake.answer = {"operator_speaker": "  ", "scores": []}
    out = await SC.run_scoring(TRANSCRIPT, CONFIG, "k", "m", segments=SEGS)
    assert out["operator_speaker"] == "unknown"
    assert all(d["score"] is None for d in out["dimensions"])


@pytest.mark.parametrize("transcript, config, api_key", [
    ("", CONFIG, "k"),
    ("   ", CONFIG, "k"),
    (TRANSCRIPT, None, "k"),
    (TRANSCRIPT, {}, "k"),
    (TRANSCRIPT, {"dimensions": []}, "k"),
    (TRANSCRIPT, {"dimensions": ["not a dim", {"name": ""}]}, "k"),
    (TRANSCRIPT, CONFIG, ""),
    (TRANSCRIPT, CONFIG, None),
])
async def test_nothing_to_score_returns_none_without_a_model_call(fake, transcript, config, api_key):
    assert await SC.run_scoring(transcript, config, api_key, "m", segments=SEGS) is None
    assert fake.calls == []


async def test_llm_failure_surfaces_as_scoringerror(fake):
    fake.fail = llm.LLMTruncatedError("cut off")
    with pytest.raises(SC.ScoringError) as exc:
        await SC.run_scoring(TRANSCRIPT, CONFIG, "k", "m", segments=SEGS)
    assert isinstance(exc.value.__cause__, llm.LLMTruncatedError)
