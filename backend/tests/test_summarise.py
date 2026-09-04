"""`services/summarise.py` — one digest for a thread of related calls (§7).

`llm.call_tool` is replaced on the module with a fake that behaves like a model would: it
reads the `<call index="N">` blocks it was prompted with and answers one card per block.
That is what lets the tests pin the two things the reviewer relies on — that the cards come
back in UPLOAD order carrying their own `job_id`/`filename` whatever the model did with the
indices, and that a thread too big for one request goes through the per-call stage first
(at most three at a time) and the combined pass never sees a transcript. `llm.estimate_tokens`
is monkeypatched to trip the guard cheaply; one test uses the real estimate so Georgian's
two-tokens-a-character weight stays measured, not assumed.
"""
import asyncio
import re
import sys
import uuid
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.services import llm  # noqa: E402 — follows the sys.path bootstrap
from app.services import summarise as SU  # noqa: E402

BLANK = {"title": "", "summary": "", "outcome": ""}
RESULT_KEYS = {"language", "short_summary", "key_points", "action_items", "participants",
               "calls", "stages"}
CARD_KEYS = {"index", "job_id", "filename", "title", "summary", "outcome"}

_HEAD = re.compile(r'<call index="(\d+)"(?: filename="([^"]*)")?')


def call(job_id, filename, transcript="", segments=None, language="ka"):
    return {"job_id": job_id, "filename": filename, "language": language,
            "transcript": transcript, "segments": segments or []}


CALLS = [
    call("j1", "first.mp3", "გამარჯობა",
         [{"i": 0, "speaker": "speaker_0", "start": 0.0, "end": 1.5, "text": "გამარჯობა"}]),
    call("j2", 'sec"ond.wav', "Agent: hi\nCustomer: hello"),      # no segments → text fallback
]


def echo(user: str) -> dict:
    """A well-behaved model: one card per `<call>` block, titled after its filename."""
    heads = _HEAD.findall(user)
    return {
        "language": "Georgian", "short_summary": " short ",
        "key_points": ["kp1", "kp2"], "action_items": ["ai1"],
        "participants": [{"label": "Agent", "role": "agent", "appears_in": list(range(len(heads)))}],
        "calls": [{"index": i, "title": f"T-{fn}", "summary": f"S{i}", "outcome": f"O{i}"}
                  for i, fn in heads],
    }


# A schema-valid-but-sloppy answer for a two-call thread: stray whitespace, string/float
# indices, a duplicate and an out-of-range card, an unlabelled participant, a bad role,
# bools and fractions in appears_in, garbage entries.
MESSY = {
    "language": " Georgian ",
    "short_summary": " short ",
    "key_points": ["kp1", "", None, {"a": "kp2", "b": ""}],
    "action_items": "single string item",
    "participants": [
        {"label": " Agent ", "role": "AGENT", "appears_in": [0, "1", 99, True, 2.5, 0]},
        {"label": "", "role": "customer", "appears_in": [0]},
        {"label": "Nino", "role": "villain", "appears_in": "1"},
        "garbage",
    ],
    "calls": [
        {"index": "1", "title": "Second", "summary": "s", "outcome": "o"},
        {"index": 0.0, "title": "First", "summary": "", "outcome": ""},
        {"index": 0, "title": "DUP", "summary": "dup", "outcome": "dup"},
        {"index": 99, "title": "OOR", "summary": "x", "outcome": "x"},
        {"index": True, "title": "BOOL", "summary": "b", "outcome": "b"},
        "junk",
    ],
}


class Fake:
    """Records every model call, tracks how many are in flight, fails on demand."""

    def __init__(self):
        self.calls: list[dict] = []
        self.answer = echo            # callable(user) → raw tool output
        self.fail_on: int | None = None   # 1-based invocation number that raises
        self.fail: Exception = llm.LLMError("boom")
        self.delay = 0.0
        self.inflight = 0
        self.max_inflight = 0

    async def __call__(self, **kw):
        n = len(self.calls) + 1
        self.calls.append(kw)
        self.inflight += 1
        self.max_inflight = max(self.max_inflight, self.inflight)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.fail_on == n:
                raise self.fail
        finally:
            self.inflight -= 1
        return self.answer(kw["user"])


@pytest.fixture
def fake(monkeypatch):
    f = Fake()
    monkeypatch.setattr(llm, "call_tool", f)
    return f


@pytest.fixture
def huge(monkeypatch):
    """Every rendered block counts as far more than the whole two-stage budget."""
    monkeypatch.setattr(llm, "estimate_tokens", lambda text: 10 ** 6)


async def run(calls, **kw):
    return await SU.summarise(calls, **{"api_key": "k", "model": "m", **kw})


# ---------------------------------------------------------------------------
# Schema and the contract's numbers
# ---------------------------------------------------------------------------
def test_summary_tool_is_strict_and_matches_the_section_7_shape():
    assert SU.SUMMARY_TOOL["name"] == "submit_summary" and SU.SUMMARY_TOOL["strict"] is True
    schema = SU.SUMMARY_TOOL["input_schema"]
    assert schema["required"] == ["language", "short_summary", "key_points", "action_items",
                                  "participants", "calls"]
    assert schema["additionalProperties"] is False
    props = schema["properties"]
    assert props["key_points"] == {"type": "array", "items": {"type": "string"},
                                   "description": props["key_points"]["description"]}
    participant = props["participants"]["items"]
    assert participant["required"] == ["label", "role", "appears_in"]
    assert participant["additionalProperties"] is False
    assert participant["properties"]["role"]["enum"] == ["agent", "customer", "other"]
    assert participant["properties"]["appears_in"]["items"] == {"type": "integer"}
    card = props["calls"]["items"]
    assert card["required"] == ["index", "title", "summary", "outcome"]
    assert card["additionalProperties"] is False
    assert card["properties"]["index"]["type"] == "integer"


def test_budgets_are_the_documented_numbers():
    assert SU.TWO_STAGE_TOKENS == 120_000 and SU.PER_CALL_CONCURRENCY == 3
    assert SU.MAX_OUTPUT_TOKENS == 8_192 and SU.ADMIT_PATIENCE_S == 30.0
    assert SU.ROLES == ("agent", "customer", "other")


# ---------------------------------------------------------------------------
# One combined pass
# ---------------------------------------------------------------------------
async def test_short_thread_is_one_combined_call(fake):
    out = await run(CALLS, client_id="tenant-a", user_id="u1")
    (c,) = fake.calls
    assert out["stages"] == 1
    assert c["feature"] == "summarise" and c["tool"] is SU.SUMMARY_TOOL
    assert c["opts"] is llm.RESTRUCTURE and c["stream"] is True
    assert c["max_tokens"] == SU.MAX_OUTPUT_TOKENS and c["admit_timeout_s"] == SU.ADMIT_PATIENCE_S
    assert c["client_id"] == "tenant-a" and c["api_key"] == "k" and c["model"] == "m"
    assert c["system"] == SU._SYSTEM
    user = c["user"]
    assert user.startswith(SU._THREAD_INTRO)
    assert '<call index="0" filename="first.mp3" language="ka" form="transcript">' in user
    assert "[#0 00:00.0-00:01.5 speaker_0] გამარჯობა" in user
    assert '<call index="1" filename="sec\'ond.wav" language="ka" form="transcript">' in user
    assert "[#0 agent] hi\n[#1 customer] hello" in user             # text fallback, segmented
    assert user.count("</call>") == 2
    assert [c["filename"] for c in out["calls"]] == ["first.mp3", 'sec"ond.wav']   # original kept


async def test_a_single_call_is_prompted_as_one_call(fake):
    out = await run(CALLS[:1])
    assert fake.calls[0]["user"].startswith(SU._ONE_CALL_INTRO)
    assert out["stages"] == 1 and len(out["calls"]) == 1


async def test_cards_follow_upload_order_and_carry_job_id_and_filename(fake):
    jid = uuid.uuid4()
    calls = [call(jid, "a.mp3", "x"), call(None, "b.mp3", "y"), call("j3", "c.mp3", "z")]
    out = await run(calls)
    assert set(out) == RESULT_KEYS
    assert [set(c) for c in out["calls"]] == [CARD_KEYS] * 3
    assert [(c["index"], c["job_id"], c["filename"], c["title"], c["summary"], c["outcome"])
            for c in out["calls"]] == [
        (0, str(jid), "a.mp3", "T-a.mp3", "S0", "O0"),         # uuid → str, jsonb-safe
        (1, None, "b.mp3", "T-b.mp3", "S1", "O1"),
        (2, "j3", "c.mp3", "T-c.mp3", "S2", "O2"),
    ]
    assert out["participants"] == [{"label": "Agent", "role": "agent", "appears_in": [0, 1, 2]}]


async def test_model_output_is_normalised_field_by_field(fake):
    fake.answer = lambda user: MESSY
    out = await run(CALLS)
    assert out["language"] == "Georgian" and out["short_summary"] == "short"
    assert out["key_points"] == ["kp1", "kp2"]
    assert out["action_items"] == ["single string item"]
    assert out["participants"] == [
        {"label": "Agent", "role": "agent", "appears_in": [0, 1]},
        {"label": "Nino", "role": "other", "appears_in": [1]},
    ]
    assert out["calls"] == [
        {"index": 0, "job_id": "j1", "filename": "first.mp3",
         "title": "First", "summary": "", "outcome": ""},        # first card for 0 wins over DUP
        {"index": 1, "job_id": "j2", "filename": 'sec"ond.wav',
         "title": "Second", "summary": "s", "outcome": "o"},
    ]


async def test_single_stage_blank_or_missing_cards_stay_blank(fake):
    fake.answer = lambda user: {**echo(user), "calls": []}
    out = await run(CALLS)
    assert out["calls"] == [{"index": 0, "job_id": "j1", "filename": "first.mp3", **BLANK},
                            {"index": 1, "job_id": "j2", "filename": 'sec"ond.wav', **BLANK}]


# ---------------------------------------------------------------------------
# normalise() on its own
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("raw", [None, "nope", [], {}, 7])
def test_normalise_garbage_is_the_blank_shape(raw):
    assert SU.normalise(raw, 2) == {"language": "", "short_summary": "", "key_points": [],
                                    "action_items": [], "participants": [], "calls": [BLANK, BLANK]}


def test_normalise_accepts_dict_shaped_arrays_and_skips_missing_positions():
    n = SU.normalise({"calls": {"x": {"index": 2, "title": "third"}},
                      "participants": {"p": {"label": "C", "role": 7}},
                      "key_points": {"a": "k1"}, "action_items": None}, 3)
    assert n["calls"] == [BLANK, BLANK, {"title": "third", "summary": "", "outcome": ""}]
    assert n["participants"] == [{"label": "C", "role": "other", "appears_in": []}]
    assert n["key_points"] == ["k1"] and n["action_items"] == []
    assert SU.normalise({"calls": "nope"}, 0)["calls"] == []


# ---------------------------------------------------------------------------
# The two-stage guard
# ---------------------------------------------------------------------------
async def test_guard_is_strictly_greater_than_the_threshold(fake, monkeypatch):
    monkeypatch.setattr(llm, "estimate_tokens", lambda text: SU.TWO_STAGE_TOKENS)
    assert (await run(CALLS[:1]))["stages"] == 1 and len(fake.calls) == 1
    monkeypatch.setattr(llm, "estimate_tokens", lambda text: SU.TWO_STAGE_TOKENS + 1)
    assert (await run(CALLS[:1]))["stages"] == 2 and len(fake.calls) == 3


async def test_guard_sums_over_the_thread(fake, monkeypatch):
    monkeypatch.setattr(llm, "estimate_tokens", lambda text: SU.TWO_STAGE_TOKENS // 2 + 1)
    assert (await run(CALLS[:1]))["stages"] == 1
    assert (await run(CALLS))["stages"] == 2


async def test_guard_uses_the_real_estimate_on_the_rendered_prompt(fake):
    georgian = [call("g", "ka.mp3", "ა" * 61_000)]        # ~122k tokens at 2/char
    assert (await run(georgian))["stages"] == 2 and len(fake.calls) == 2
    fake.calls.clear()
    latin = [call("l", "en.mp3", "a" * 61_000)]           # ~15k tokens at 0.25/char
    assert (await run(latin))["stages"] == 1 and len(fake.calls) == 1


async def test_long_thread_runs_per_call_passes_then_one_combined_pass(fake, huge):
    fake.delay = 0.01
    calls = [call(f"job-{i}", f"call{i}.mp3", f"transcript {i} words") for i in range(5)]
    out = await run(calls, client_id="tenant-a")
    assert out["stages"] == 2 and len(fake.calls) == 6
    assert fake.max_inflight == SU.PER_CALL_CONCURRENCY
    assert all(c["feature"] == "summarise" and c["tool"] is SU.SUMMARY_TOOL
               and c["client_id"] == "tenant-a" for c in fake.calls)

    per_call = [c["user"] for c in fake.calls[:5]]
    assert all(u.startswith(SU._ONE_CALL_INTRO) and u.count("<call ") == 1 for u in per_call)
    # each call alone is call 0 of its own thread, and they start in upload order
    assert [_HEAD.findall(u)[0] for u in per_call] == [("0", f"call{i}.mp3") for i in range(5)]
    assert all(f"transcript {i} words" in u for i, u in enumerate(per_call))

    combined = fake.calls[5]["user"]
    assert combined.startswith(SU._COMBINED_INTRO)
    assert [h[0] for h in _HEAD.findall(combined)] == ["0", "1", "2", "3", "4"]   # real positions
    assert combined.count('form="summary"') == 5 and 'form="transcript"' not in combined
    assert "transcript 0 words" not in combined                                   # no speech
    assert "Title: T-call0.mp3" in combined and "Summary: S0" in combined
    assert "- kp1" in combined and "- ai1" in combined and "- Agent (agent)" in combined

    assert [(c["index"], c["job_id"], c["filename"], c["title"]) for c in out["calls"]] == [
        (i, f"job-{i}", f"call{i}.mp3", f"T-call{i}.mp3") for i in range(5)]


async def test_blank_combined_card_is_filled_from_that_calls_own_stage_one_card(fake, huge):
    def answer(user):
        raw = echo(user)
        if user.startswith(SU._COMBINED_INTRO):
            raw["calls"][1] = {"index": 1, "title": "", "summary": "", "outcome": ""}
        return raw
    fake.answer = answer
    out = await run([call(f"j{i}", f"c{i}.mp3", "t") for i in range(3)])
    assert out["stages"] == 2
    assert [c["title"] for c in out["calls"]] == ["T-c0.mp3", "T-c1.mp3", "T-c2.mp3"]
    assert out["calls"][1] == {"index": 1, "job_id": "j1", "filename": "c1.mp3",
                               "title": "T-c1.mp3", "summary": "S0", "outcome": "O0"}


# ---------------------------------------------------------------------------
# Failures
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("exc", [llm.LLMError("boom"), llm.LLMBusyError("at capacity"),
                                 llm.LLMTruncatedError("cut off")])
async def test_combined_pass_failure_is_a_summariseerror_with_its_cause(fake, exc):
    fake.fail, fake.fail_on = exc, 1
    with pytest.raises(SU.SummariseError) as info:
        await run(CALLS)
    assert info.value.__cause__ is exc


async def test_a_failed_per_call_pass_cancels_the_rest_and_skips_the_combined_pass(fake, huge):
    fake.delay, fake.fail_on = 0.01, 2
    with pytest.raises(SU.SummariseError) as info:
        await run([call(f"j{i}", f"c{i}.mp3", "t") for i in range(5)])
    assert isinstance(info.value.__cause__, llm.LLMError)
    await asyncio.sleep(0.05)                       # let the cancellations land
    assert len(fake.calls) <= 5 and fake.inflight == 0
    assert not any(c["user"].startswith(SU._COMBINED_INTRO) for c in fake.calls)


@pytest.mark.parametrize("calls", [[], None, ["junk", None, 3]])
async def test_nothing_to_summarise_raises_without_a_model_call(fake, calls):
    with pytest.raises(SU.SummariseError):
        await run(calls)
    assert fake.calls == []


# ---------------------------------------------------------------------------
# Prompt rendering (pure)
# ---------------------------------------------------------------------------
def test_render_call_segments_the_transcript_and_marks_silence():
    assert SU.render_call(3, call("j", "x.mp3", "")) == (
        '<call index="3" filename="x.mp3" language="ka" form="transcript">\n'
        "(no speech transcribed)\n</call>")
    assert SU.render_call(0, {"filename": "  a  b.mp3 ", "language": None,
                              "transcript": "Agent: hi"}) == (
        '<call index="0" filename="a b.mp3" form="transcript">\n[#0 agent] hi\n</call>')


def test_render_call_prefers_the_stored_segments_over_the_transcript():
    rendered = SU.render_call(1, CALLS[0])
    assert "[#0 00:00.0-00:01.5 speaker_0] გამარჯობა" in rendered
    assert rendered.startswith('<call index="1" filename="first.mp3" language="ka" form="transcript">')


def test_render_summary_lists_the_stage_one_digest():
    summary = {"calls": [{"title": "T", "summary": "S", "outcome": "O"}],
               "key_points": ["k1"], "action_items": [],
               "participants": [{"label": "Agent", "role": "agent", "appears_in": [0]}]}
    assert SU.render_summary(2, call("j", "f.mp3"), summary) == (
        '<call index="2" filename="f.mp3" language="ka" form="summary">\n'
        "Title: T\nSummary: S\nOutcome: O\nKey points:\n- k1\nParticipants:\n- Agent (agent)\n</call>")
