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
    with pytest.raises(si.RubricImportError, match="too large"):
        await si.rubric_from_text("x" * (si.MAX_INPUT_CHARS + 1), client_id="c1")

    async def truncated(**kw):
        raise si.llm.LLMTruncatedError("budget")
    monkeypatch.setattr(si.llm, "call_tool", truncated)
    with pytest.raises(si.RubricImportError, match="too long"):
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
