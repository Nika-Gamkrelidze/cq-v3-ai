"""`services/factcheck.py` v2 — verdicts, accuracy maths and timeline spans (design §4).

No database, no network, no keys: `llm.call_tool` and `retrieval.retrieve` are replaced on
their module objects (the conftest detonator is overridden per test). What is pinned here
is the CONTRACT the workbench and the timeline consume — the new PARTIALLY_SUPPORTED
verdict and how sloppy spellings of it are normalised, the accuracy formula, the exact §3
span shape, and the promise that the model's cited `#` indices (however garbled) map back
to the caller's own segments — plus the legacy bare-transcript path that `/analyze` and the
partner API still use.
"""
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.services import factcheck as F  # noqa: E402 — follows the sys.path bootstrap
from app.services import llm  # noqa: E402
from app.services import retrieval  # noqa: E402
from app.services.segments import render_timeline  # noqa: E402

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

HITS = [{"title": "Fees", "doc_type": "policy", "content": "Wire fee is 25 GEL.", "score": 0.912},
        {"title": "Timing", "doc_type": "faq", "content": "1-2 business days.", "score": None}]

# What a sloppy-but-schema-valid model answer looks like: string/float indices, spelling
# variants of the verdicts, garbage entries, an uncited claim.
CLAIMS = [
    {"claim": "The wire fee is 25 lari.", "speaker": "AGENT", "category": "fees", "segments": [2]},
    {"claim": "Transfers arrive within two business days.", "speaker": "agent",
     "category": "timing", "segments": [3, 2, 3]},
    {"claim": "Weekend transfers are processed too.", "speaker": "agent", "category": "timing",
     "segments": ["5", 5.0, None, "x", True, 99, 2.5]},
    {"claim": "The bank was founded in 1903.", "speaker": "customer", "category": "trivia",
     "segments": []},
    "just a string, not a claim",
    {"claim": "   ", "speaker": "agent", "category": "x", "segments": [1]},
    {"claim": None, "segments": [1]},
]
VERIFICATIONS = [
    {"index": "0", "verdict": "supported", "rationale": "KB says 25 GEL.",
     "confidence": "0.95", "evidence_used": "0"},
    {"index": 1.0, "verdict": "partially supported", "rationale": "KB says 1-2 days.",
     "confidence": 0.8, "evidence_used": 1},
    {"index": 2, "verdict": "Contradicted", "rationale": "KB says weekdays only.",
     "confidence": 0.9, "evidence_used": -1},
    {"index": 3, "verdict": "bogus", "rationale": None, "confidence": None, "evidence_used": 99},
    "garbage",
    {"index": True, "verdict": "SUPPORTED", "rationale": "", "confidence": 1, "evidence_used": 0},
    {"index": 42, "verdict": "SUPPORTED", "rationale": "", "confidence": 1, "evidence_used": 0},
]


class Fake:
    """Records every model call and every retrieval; answers by feature name."""

    def __init__(self, claims=None, verifications=None, hits=None, fail=None):
        self.claims = CLAIMS if claims is None else claims
        self.verifications = VERIFICATIONS if verifications is None else verifications
        self.hits = HITS if hits is None else hits
        self.fail = fail
        self.calls: list[dict] = []
        self.retrievals: list[tuple] = []

    async def call_tool(self, **kw):
        self.calls.append(kw)
        if self.fail:
            raise self.fail
        if kw["feature"] == "factcheck_claims":
            return {"claims": self.claims}
        if kw["feature"] == "factcheck_verdict":
            return {"verifications": self.verifications}
        raise AssertionError(f"unexpected feature {kw['feature']!r}")

    async def retrieve(self, client_id, query, top_k=None, **kw):
        self.retrievals.append((client_id, query, top_k))
        return list(self.hits)


@pytest.fixture
def fake(monkeypatch):
    f = Fake()
    monkeypatch.setattr(llm, "call_tool", f.call_tool)
    monkeypatch.setattr(retrieval, "retrieve", f.retrieve)
    return f


async def run(fake, transcript=TRANSCRIPT, segments=SEGS, client_id="tenant-a",
              api_key="k", model="m"):
    return await F.run_factcheck(transcript, client_id, api_key, model, segments=segments)


# ---------------------------------------------------------------------------
# Vocabulary, schemas, pure helpers
# ---------------------------------------------------------------------------
def test_verdict_vocabulary_gained_partially_supported():
    assert F.VERDICTS == {"SUPPORTED", "PARTIALLY_SUPPORTED", "CONTRADICTED", "NOT_IN_KB"}
    assert F._KEY["PARTIALLY_SUPPORTED"] == "partially_supported"
    assert F._LEVEL == {"SUPPORTED": "good", "PARTIALLY_SUPPORTED": "mid",
                        "CONTRADICTED": "bad", "NOT_IN_KB": "none"}
    assert "PARTIALLY_SUPPORTED" in F._VERIFY_INTRO


def test_claims_tool_requires_segments_and_verify_tool_offers_the_new_verdict():
    item = F.CLAIMS_TOOL["input_schema"]["properties"]["claims"]["items"]
    assert item["properties"]["segments"] == {
        "type": "array", "items": {"type": "integer"},
        "description": "The `#` indices of the transcript lines where this claim is made."}
    assert "segments" in item["required"] and item["additionalProperties"] is False
    assert F.CLAIMS_TOOL["strict"] is True and F.VERIFY_TOOL["strict"] is True
    verdict = F.VERIFY_TOOL["input_schema"]["properties"]["verifications"]["items"]["properties"]["verdict"]
    assert verdict["enum"] == ["SUPPORTED", "PARTIALLY_SUPPORTED", "CONTRADICTED", "NOT_IN_KB"]


@pytest.mark.parametrize("raw, expected", [
    ("SUPPORTED", "SUPPORTED"),
    ("supported", "SUPPORTED"),
    ("partially supported", "PARTIALLY_SUPPORTED"),
    ("Partially-Supported", "PARTIALLY_SUPPORTED"),
    (" contradicted ", "CONTRADICTED"),
    ("not in kb", "NOT_IN_KB"),
    ("bogus", "NOT_IN_KB"),          # unknown → the safe default, never a false SUPPORTED
    ("", "NOT_IN_KB"),
    (None, "NOT_IN_KB"),
    (42, "NOT_IN_KB"),
])
def test_verdict_normalisation(raw, expected):
    assert F._norm_verdict(raw) == expected


@pytest.mark.parametrize("counts, expected", [
    ({"supported": 2, "partially_supported": 1, "contradicted": 1}, 62),   # 2.5/4
    ({"supported": 1, "partially_supported": 1, "contradicted": 1}, 50),   # 1.5/3
    ({"supported": 3, "partially_supported": 0, "contradicted": 0}, 100),
    ({"supported": 0, "partially_supported": 2, "contradicted": 0}, 50),
    ({"supported": 0, "partially_supported": 0, "contradicted": 2}, 0),
    ({"supported": 0, "partially_supported": 0, "contradicted": 0, "not_in_kb": 5}, None),
    ({}, None),                                                             # missing keys tolerated
])
def test_accuracy_score_counts_partials_at_half_and_ignores_not_in_kb(counts, expected):
    assert F.accuracy_score(counts) == expected


def test_span_label_is_capped_at_80_chars_with_an_ellipsis():
    short = "x" * 80
    assert F._label(short) == short
    long = "word " * 30
    label = F._label(long)
    assert len(label) <= F.LABEL_CHARS and label.endswith("…") and not label[:-1].endswith(" ")


@pytest.mark.parametrize("value, expected", [
    (3, 3), (3.0, 3), ("3", 3), ("0", 0), (-1, -1),
    (True, None), (False, None), (2.5, None), ("x", None), (None, None), (float("nan"), None),
])
def test_index_coercion_matches_segments_rule(value, expected):
    assert F._int(value) == expected


# ---------------------------------------------------------------------------
# The pipeline with the caller's own segments
# ---------------------------------------------------------------------------
async def test_extraction_is_prompted_with_the_timeline_and_scoped_to_the_tenant(fake):
    await run(fake)
    extract, verify = fake.calls
    assert extract["feature"] == "factcheck_claims" and verify["feature"] == "factcheck_verdict"
    assert extract["client_id"] == "tenant-a" and verify["client_id"] == "tenant-a"
    assert extract["model"] == "m" and extract["api_key"] == "k"
    assert extract["tool"] is F.CLAIMS_TOOL and verify["tool"] is F.VERIFY_TOOL
    assert extract["opts"] is llm.ANALYSIS
    assert render_timeline(SEGS) in extract["user"]
    assert "[#2 00:05.2-00:09.9 speaker_0] The wire fee is 25 lari." in extract["user"]
    assert "`#` numbers" in extract["system"]


async def test_one_tenant_scoped_retrieval_per_claim(fake):
    await run(fake)
    # 4 usable claims (the string, the blank and the None claim are dropped before retrieval)
    assert [r[0] for r in fake.retrievals] == ["tenant-a"] * 4
    assert [r[2] for r in fake.retrievals] == [F.EVIDENCE_K] * 4
    assert fake.retrievals[0][1] == "The wire fee is 25 lari."
    # the verify prompt carries the evidence snippets it retrieved
    assert "[0] (Fees) Wire fee is 25 GEL." in fake.calls[1]["user"]
    assert "Claim 3: The bank was founded in 1903." in fake.calls[1]["user"]


async def test_counts_accuracy_and_contradicted_list(fake):
    out = await run(fake)
    assert out["counts"] == {"supported": 1, "partially_supported": 1, "contradicted": 1,
                             "not_in_kb": 1, "total": 4}
    assert out["accuracy_score"] == 50
    assert [c["claim"] for c in out["contradicted"]] == ["Weekend transfers are processed too."]
    assert out["segments_available"] is True


async def test_claims_are_normalised_and_placed_on_the_callers_segments(fake):
    out = await run(fake)
    c0, c1, c2, c3 = out["claims"]

    assert c0["verdict"] == "SUPPORTED" and c0["speaker"] == "agent"      # "AGENT" lower-cased
    assert (c0["segments"], c0["start"], c0["end"]) == ([2], 5.2, 9.9)
    assert c0["confidence"] == 0.95                                        # "0.95" → float
    assert c0["evidence"] == {"title": "Fees", "doc_type": "policy",
                              "snippet": "Wire fee is 25 GEL.", "score": 0.912}

    assert c1["verdict"] == "PARTIALLY_SUPPORTED"                          # "partially supported"
    assert (c1["segments"], c1["start"], c1["end"]) == ([2, 3], 5.2, 14.0)   # [3,2,3] merged
    assert c1["evidence"]["title"] == "Timing" and c1["evidence"]["score"] is None

    assert c2["verdict"] == "CONTRADICTED"
    assert (c2["segments"], c2["start"], c2["end"]) == ([5], 16.3, 20.7)   # garbage indices dropped
    assert c2["evidence"] is None                                          # evidence_used -1

    assert c3["verdict"] == "NOT_IN_KB"                                    # "bogus" → default
    assert (c3["segments"], c3["start"], c3["end"]) == ([], None, None)    # uncited
    assert c3["rationale"] == "" and c3["confidence"] is None
    assert c3["evidence"] is None                                          # evidence_used 99 out of range
    assert c3["speaker"] == "customer" and c3["category"] == "trivia"


async def test_spans_have_the_exact_section_3_shape(fake):
    out = await run(fake)
    assert len(out["spans"]) == 3            # the uncited claim cannot be drawn anywhere
    for span in out["spans"]:
        assert set(span) == SPAN_KEYS
        assert isinstance(span["segments"], list) and span["segments"]
        assert isinstance(span["start"], float) and isinstance(span["end"], float)
        assert span["level"] in ("good", "mid", "bad", "none")
        assert span["score"] is None
        assert isinstance(span["label"], str) and len(span["label"]) <= 80
        assert isinstance(span["detail"], str)
    s0, s1, s2 = out["spans"]
    assert s0 == {"segments": [2], "start": 5.2, "end": 9.9, "level": "good", "score": None,
                  "label": "The wire fee is 25 lari.", "detail": "SUPPORTED: KB says 25 GEL."}
    assert (s1["segments"], s1["level"], s1["detail"]) == ([2, 3], "mid",
                                                          "PARTIALLY_SUPPORTED: KB says 1-2 days.")
    assert (s2["segments"], s2["level"], s2["detail"]) == ([5], "bad",
                                                          "CONTRADICTED: KB says weekdays only.")


async def test_span_detail_without_a_rationale_is_the_bare_verdict_and_long_labels_truncate(fake):
    long_claim = "The bank charges a " + "very " * 30 + "small fee."
    fake.claims = [{"claim": long_claim, "speaker": "agent", "category": "fees", "segments": [2]}]
    fake.verifications = [{"index": 0, "verdict": "NOT_IN_KB", "rationale": "  ",
                           "confidence": 0.1, "evidence_used": -1}]
    out = await run(fake)
    (span,) = out["spans"]
    assert span["detail"] == "NOT_IN_KB" and span["level"] == "none"
    assert len(span["label"]) <= 80 and span["label"].endswith("…")
    assert out["claims"][0]["claim"] == long_claim       # the claim itself is never truncated


async def test_claims_are_capped_at_max_claims(fake):
    fake.claims = [{"claim": f"Claim number {k}.", "speaker": "agent", "category": "c",
                    "segments": [k % len(SEGS)]} for k in range(F.MAX_CLAIMS + 10)]
    fake.verifications = []
    out = await run(fake)
    assert out["counts"]["total"] == F.MAX_CLAIMS and len(fake.retrievals) == F.MAX_CLAIMS
    # no verification returned for any claim → every one is NOT_IN_KB, accuracy undefined
    assert out["counts"]["not_in_kb"] == F.MAX_CLAIMS and out["accuracy_score"] is None


async def test_no_claims_returns_the_empty_result_without_verifying(fake):
    fake.claims = []
    out = await run(fake)
    assert out == {"accuracy_score": None,
                   "counts": {"supported": 0, "partially_supported": 0, "contradicted": 0,
                              "not_in_kb": 0, "total": 0},
                   "claims": [], "contradicted": [], "spans": [], "segments_available": True}
    assert len(fake.calls) == 1 and fake.retrievals == []


async def test_only_garbage_claims_counts_as_no_claims(fake):
    fake.claims = ["string", None, 7, {"claim": ""}, {"segments": [1]}]
    out = await run(fake)
    assert out["claims"] == [] and out["spans"] == [] and len(fake.calls) == 1


async def test_null_arrays_from_the_model_are_tolerated(fake):
    fake.claims = None                       # {"claims": null} → nothing to check
    out = await run(fake)
    assert out["claims"] == [] and len(fake.calls) == 1 and fake.retrievals == []

    fake.claims = CLAIMS
    fake.verifications = None                # {"verifications": null} → every claim unjudged
    out = await run(fake)
    assert out["counts"] == {"supported": 0, "partially_supported": 0, "contradicted": 0,
                             "not_in_kb": 4, "total": 4}
    assert out["accuracy_score"] is None
    assert all(c["rationale"] == "" and c["confidence"] is None and c["evidence"] is None
               for c in out["claims"])
    assert [s["level"] for s in out["spans"]] == ["none"] * 3


# ---------------------------------------------------------------------------
# Legacy callers: a bare transcript, no segments
# ---------------------------------------------------------------------------
async def test_segments_none_falls_back_to_the_transcripts_own_lines(fake):
    fake.claims = [{"claim": "The fee is 25 lari.", "speaker": "agent", "category": "fees",
                    "segments": [0]}]
    fake.verifications = [{"index": 0, "verdict": "SUPPORTED", "rationale": "yes",
                           "confidence": 0.9, "evidence_used": 0}]
    out = await F.run_factcheck("Agent: The fee is 25 lari.\nCustomer: Thanks.", "tenant-a", "k", "m")
    assert "[#0 agent] The fee is 25 lari." in fake.calls[0]["user"]
    assert out["segments_available"] is False
    assert out["accuracy_score"] == 100
    (c,) = out["claims"]
    assert (c["segments"], c["start"], c["end"]) == ([0], None, None)
    (span,) = out["spans"]
    assert set(span) == SPAN_KEYS
    assert (span["segments"], span["start"], span["end"], span["level"]) == ([0], None, None, "good")


async def test_empty_segments_list_behaves_like_none(fake):
    out = await run(fake, segments=[])
    assert out["segments_available"] is False
    assert "[#0 speaker_0] Hello, thanks for calling." in fake.calls[0]["user"]


@pytest.mark.parametrize("transcript, client_id, api_key", [
    ("", "tenant-a", "k"),
    ("   \n  ", "tenant-a", "k"),
    (TRANSCRIPT, None, "k"),
    (TRANSCRIPT, "", "k"),
    (TRANSCRIPT, "tenant-a", None),
    (TRANSCRIPT, "tenant-a", ""),
])
async def test_nothing_to_check_returns_none_without_a_model_call(fake, transcript, client_id, api_key):
    out = await F.run_factcheck(transcript, client_id, api_key, "m")
    assert out is None and fake.calls == [] and fake.retrievals == []


async def test_llm_failure_surfaces_as_factcheckerror(fake):
    fake.fail = llm.LLMError("upstream down")
    with pytest.raises(F.FactCheckError) as exc:
        await run(fake)
    assert isinstance(exc.value.__cause__, llm.LLMError)
    assert fake.retrievals == []


async def test_busy_error_is_still_a_factcheckerror_with_its_cause_intact(fake):
    fake.fail = llm.LLMBusyError("at capacity")
    with pytest.raises(F.FactCheckError) as exc:
        await run(fake)
    assert isinstance(exc.value.__cause__, llm.LLMBusyError)


async def test_probe_tools_needs_no_tenant_and_no_retrieval(fake):
    fake.claims = [{"claim": "Support is open 24/7.", "speaker": "agent", "category": "hours",
                    "segments": [0]}]
    n = await F.probe_tools("k", "m")
    assert n == 1
    assert [c["feature"] for c in fake.calls] == ["factcheck_claims", "factcheck_verdict"]
    assert all(c["client_id"] is None for c in fake.calls)
    assert "[#0 agent] Our support line is open 24/7." in fake.calls[0]["user"]
    assert fake.retrievals == []
