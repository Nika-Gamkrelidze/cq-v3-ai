"""scoring_import: the rubric-draft contract — weights are code's arithmetic, failures
are actionable, and model quirks normalize away. Claude transport is monkeypatched."""
import pytest

from app.services import scoring_import as si


def test_weights_are_proportional_and_total_exactly_100():
    dims = [{"max_points": 10, "weight": 0.0},
            {"max_points": 22, "weight": 0.0},
            {"max_points": 27, "weight": 0.0}]
    si._weights_from_points(dims)
    assert round(sum(d["weight"] for d in dims), 2) == 100
    assert dims[0]["weight"] == pytest.approx(16.95, abs=0.01)   # 10/59
    assert dims[1]["weight"] == pytest.approx(37.29, abs=0.01)   # 22/59


def test_no_points_leaves_weights_zero_for_even_split_on_save():
    dims = [{"max_points": 0, "weight": 0.0}, {"max_points": 0, "weight": 0.0}]
    si._weights_from_points(dims)
    assert all(d["weight"] == 0.0 for d in dims)


def test_normalize_drops_nameless_and_caps_dimensions():
    raw = {"general_instructions": "  წესები  ",
           "dimensions": [{"name": "A", "description": "d", "guidance": "g", "max_points": 5},
                          {"name": "", "description": "x", "guidance": "x", "max_points": 1},
                          "garbage",
                          {"name": "B", "description": None, "guidance": None,
                           "max_points": "oops"}]}
    dims, rubric = si._normalize(raw)
    assert [d["name"] for d in dims] == ["A", "B"]
    assert dims[1]["max_points"] == 0.0          # unparseable -> 0, not a crash
    assert rubric == "წესები"


@pytest.mark.asyncio
async def test_rubric_draft_shape(monkeypatch):
    async def fake_settings():
        return {"anthropic_api_key": "k", "llm_model": "m"}

    async def fake_call_tool(**kw):
        assert kw["feature"] == "scoring_import"
        return {"general_instructions": "1/-3 ნიშნავს +1/-3",
                "dimensions": [
                    {"name": "კონტაქტის დამყარება", "description": "დ1",
                     "guidance": "A1 (1/-3): ...", "max_points": 10},
                    {"name": "კომუნიკაცია", "description": "დ2",
                     "guidance": "B1 (1/-2): ...", "max_points": 22},
                ]}

    monkeypatch.setattr(si.settings_store, "get_effective", fake_settings)
    monkeypatch.setattr(si.llm, "call_tool", fake_call_tool)
    d = await si.rubric_from_text("სტანდარტი...", client_id="c1")
    assert d["rubric"] == "1/-3 ნიშნავს +1/-3"
    assert [x["name"] for x in d["dimensions"]] == ["კონტაქტის დამყარება", "კომუნიკაცია"]
    assert round(sum(x["weight"] for x in d["dimensions"]), 2) == 100
    assert all("max_points" not in x for x in d["dimensions"])   # editor never sees it


@pytest.mark.asyncio
async def test_rubric_failures_are_actionable(monkeypatch):
    async def fake_settings():
        return {"anthropic_api_key": "k", "llm_model": "m"}
    monkeypatch.setattr(si.settings_store, "get_effective", fake_settings)

    with pytest.raises(si.RubricImportError, match="no text"):
        await si.rubric_from_text("   ", client_id="c1")
    with pytest.raises(si.RubricImportError, match="too long"):
        await si.rubric_from_text("x" * (si.MAX_INPUT_CHARS + 1), client_id="c1")

    async def truncated(**kw):
        raise si.llm.LLMTruncatedError("budget")
    monkeypatch.setattr(si.llm, "call_tool", truncated)
    with pytest.raises(si.RubricImportError, match="output budget"):
        await si.rubric_from_text("standard", client_id="c1")

    async def empty(**kw):
        return {"general_instructions": "", "dimensions": []}
    monkeypatch.setattr(si.llm, "call_tool", empty)
    with pytest.raises(si.RubricImportError, match="manually"):
        await si.rubric_from_text("standard", client_id="c1")

    async def broken(**kw):
        raise si.llm.LLMError("secret upstream text")
    monkeypatch.setattr(si.llm, "call_tool", broken)
    with pytest.raises(si.RubricImportError, match="Try again") as e:
        await si.rubric_from_text("standard", client_id="c1")
    assert "secret upstream text" not in str(e.value)


# --- output-budget sizing -----------------------------------------------------------
# A real Georgian call-centre scorecard (92 rows, 10.3k characters) failed to import: the
# schema makes guidance reproduce every criterion VERBATIM, so output ⊇ input, and Georgian
# costs ~2 tokens per character against ~0.16 for English. It needed ~20k output tokens
# against a budget of 8,192, hit stop_reason=max_tokens, and told the uploader to split a
# file that was never too big. These pin the sizing so that cannot come back.

GEORGIAN_ROW = ("A1 (1/-3): თანამშრომელმა უპასუხა ზარს სტანდარტული ფრაზით და მიესალმა "
                "მომხმარებელს, დაუდასტურა დახმარებისთვის მზადყოფნა.\n")


def test_a_real_georgian_scorecard_fits_the_output_budget():
    """The exact failure reported: ~10k characters of mostly-Georgian criteria."""
    text = GEORGIAN_ROW * 92
    assert 9_000 < len(text) < 13_000, "fixture should match the reported file's size"
    needed = si.estimate_output_tokens(text)
    assert needed > 8_192, "if this fails the fixture stopped reproducing the bug"
    assert needed <= si.MAX_OUTPUT_TOKENS, (
        "a routine Georgian scorecard must import in one piece; needed %d, budget %d"
        % (needed, si.MAX_OUTPUT_TOKENS))


def test_english_is_not_charged_the_georgian_rate():
    """The old flat character limit could not tell these apart, so it had to be wrong for
    one of them. Same length, ~12x cheaper — English must stay comfortably inside."""
    english = ("A1 (1/-3): The employee answered the call with the standard phrase and "
               "greeted the customer, confirming readiness to help.\n") * 92
    assert si.estimate_output_tokens(english) < si.estimate_output_tokens(GEORGIAN_ROW * 92) / 4
    assert si.estimate_output_tokens(english) <= si.MAX_OUTPUT_TOKENS


def test_the_guard_predicts_truncation_instead_of_paying_for_it():
    """Genuinely oversized input is refused before the call, not after a multi-minute
    stream ends in stop_reason=max_tokens."""
    huge = GEORGIAN_ROW * 92 * 4
    needed = si.estimate_output_tokens(huge)
    assert needed > si.MAX_OUTPUT_TOKENS
    msg = si.oversize_message(huge, needed)
    assert str(si.MAX_OUTPUT_TOKENS // 1000) in msg.replace(",", "")[:200] or "32,000" in msg
    assert "too long" in msg


def test_input_and_output_limits_are_mutually_satisfiable():
    """The regression in one line: output ⊇ input, so the character cap must not admit a
    document whose verbatim reproduction cannot fit the token budget. The old pair
    (40,000 chars / 8,192 tokens) failed this for every Georgian document over ~4k."""
    worst_case_at_cap = si.MAX_INPUT_CHARS * si.WIDE_TOKENS_PER_CHAR * si.OUTPUT_OVERHEAD
    assert worst_case_at_cap > si.MAX_OUTPUT_TOKENS, (
        "MAX_INPUT_CHARS is a backstop; the token estimate must be the binding guard")
    # and the estimator must actually bind before the character backstop does
    georgian_at_budget = si.MAX_OUTPUT_TOKENS / (si.WIDE_TOKENS_PER_CHAR * si.OUTPUT_OVERHEAD)
    assert georgian_at_budget < si.MAX_INPUT_CHARS
