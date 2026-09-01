"""kb_restructure: segmentation, normalization, and the failure contract.

The Claude transport is monkeypatched — these tests pin the pure logic around it:
segments never split a line, model quirks normalize away, and every failure mode raises
a RestructureError whose message an uploader can act on (it lands verbatim on the
document's error field).
"""
import pytest

from app.services import kb_restructure as kr


# ---- _segments -------------------------------------------------------------
def test_segments_split_on_line_boundaries_only():
    line = "A" * 4000
    text = "\n".join([line] * 10)          # 40k chars -> must split, but never mid-line
    segs = kr._segments(text)
    assert len(segs) > 1
    for s in segs:
        for ln in s.splitlines():
            assert ln == line              # every line intact


def test_segments_drop_blank_input():
    assert kr._segments("") == []
    assert kr._segments("\n\n  \n") == []


def test_oversized_single_line_becomes_its_own_segment():
    text = "short\n" + "B" * (kr.SEGMENT_CHARS + 100) + "\nshort2"
    segs = kr._segments(text)
    assert any("B" * 100 in s for s in segs)
    joined = "\n".join(segs)
    assert "short" in joined and "short2" in joined


# ---- _normalize ------------------------------------------------------------
def test_normalize_survives_model_quirks():
    raw = {"entries": [
        {"topic": "ვადა", "content": "1-დან 36 თვემდე"},
        "bare string entry",                       # string instead of object
        {"topic": None, "content": "  topicless  "},
        {"topic": "empty", "content": "   "},      # blank content -> dropped
        None,
        42,
    ]}
    out = kr._normalize(raw)
    assert out == [
        {"topic": "ვადა", "content": "1-დან 36 თვემდე"},
        {"topic": "", "content": "bare string entry"},
        {"topic": "", "content": "topicless"},
    ]


def test_normalize_tolerates_non_list_entries():
    assert kr._normalize({"entries": None}) == []
    assert kr._normalize({"entries": "oops"}) == []
    assert kr._normalize({}) == []
    assert kr._normalize({"entries": {"a": {"topic": "t", "content": "c"}}}) == [
        {"topic": "t", "content": "c"}]


# ---- restructure() contract ------------------------------------------------
@pytest.mark.asyncio
async def test_restructure_shapes_entries_like_csv_rows(monkeypatch):
    async def fake_settings():
        return {"anthropic_api_key": "k", "llm_model": "m"}

    async def fake_call_tool(**kw):
        assert kw["feature"] == "kb_restructure"
        return {"entries": [{"topic": "ვადა", "content": "1-36 თვე"},
                            {"topic": "", "content": "0%-დან"}]}

    monkeypatch.setattr(kr.settings_store, "get_effective", fake_settings)
    monkeypatch.setattr(kr.llm, "call_tool", fake_call_tool)
    pairs = await kr.restructure("სესხის ვადა | 1-36 თვე", client_id="c1")
    assert pairs[0][0] == "ვადა: 1-36 თვე"          # topic prefixes the content
    assert pairs[1][0] == "0%-დან"                   # no topic -> bare content
    assert all(m["restructured"] is True for _, m in pairs)
    assert pairs[0][1]["entry"] == {"topic": "ვადა", "content": "1-36 თვე"}


@pytest.mark.asyncio
async def test_restructure_failures_are_actionable(monkeypatch):
    async def fake_settings():
        return {"anthropic_api_key": "k", "llm_model": "m"}
    monkeypatch.setattr(kr.settings_store, "get_effective", fake_settings)

    with pytest.raises(kr.RestructureError):        # nothing to send
        await kr.restructure("   ", client_id="c1")

    async def empty_call(**kw):
        return {"entries": []}
    monkeypatch.setattr(kr.llm, "call_tool", empty_call)
    with pytest.raises(kr.RestructureError, match="without restructuring"):
        await kr.restructure("some text", client_id="c1")

    async def broken_call(**kw):
        raise kr.llm.LLMError("upstream 529: secret internal details")
    monkeypatch.setattr(kr.llm, "call_tool", broken_call)
    with pytest.raises(kr.RestructureError, match="Try again") as e:
        await kr.restructure("some text", client_id="c1")
    # The tenant-visible message names the failure class, never the raw upstream text.
    assert "secret internal details" not in str(e.value)


@pytest.mark.asyncio
async def test_restructure_refuses_oversized_files(monkeypatch):
    async def fake_settings():
        return {"anthropic_api_key": "k", "llm_model": "m"}
    monkeypatch.setattr(kr.settings_store, "get_effective", fake_settings)
    huge = "x" * (kr.MAX_INPUT_CHARS + 1)          # deliberately newline-free: the guard
    with pytest.raises(kr.RestructureError, match="too large"):   # must cap CHARACTERS,
        await kr.restructure(huge, client_id="c1")                # not line-based segments


@pytest.mark.asyncio
async def test_truncated_segments_bisect_instead_of_losing_entries(monkeypatch):
    """stop_reason=max_tokens must never keep a partial result: the segment splits and
    both halves are converted in full."""
    async def fake_settings():
        return {"anthropic_api_key": "k", "llm_model": "m"}
    monkeypatch.setattr(kr.settings_store, "get_effective", fake_settings)

    calls = []
    async def fake_call_tool(**kw):
        seg = kw["user"]
        calls.append(len(seg))
        if len(seg) > 3000:                     # "too big" -> truncated answer
            raise kr.llm.LLMTruncatedError("out of budget")
        return {"entries": [{"topic": "t", "content": f"piece of {len(seg)}"}]}
    monkeypatch.setattr(kr.llm, "call_tool", fake_call_tool)

    text = "\n".join("line " + "y" * 90 for _ in range(50))     # ~5k chars, one segment
    pairs = await kr.restructure(text, client_id="c1")
    assert len(pairs) >= 2                      # both halves contributed entries
    assert len(calls) >= 3                      # original + at least two halves


@pytest.mark.asyncio
async def test_hopelessly_dense_content_fails_loudly(monkeypatch):
    async def fake_settings():
        return {"anthropic_api_key": "k", "llm_model": "m"}
    monkeypatch.setattr(kr.settings_store, "get_effective", fake_settings)
    async def always_truncated(**kw):
        raise kr.llm.LLMTruncatedError("out of budget")
    monkeypatch.setattr(kr.llm, "call_tool", always_truncated)
    with pytest.raises(kr.RestructureError, match="too dense"):
        await kr.restructure("z" * 2000, client_id="c1")


@pytest.mark.asyncio
async def test_restructure_requires_configured_key(monkeypatch):
    async def fake_settings():
        return {"anthropic_api_key": "", "llm_model": "m"}
    monkeypatch.setattr(kr.settings_store, "get_effective", fake_settings)
    with pytest.raises(kr.RestructureError, match="not configured"):
        await kr.restructure("some text", client_id="c1")
