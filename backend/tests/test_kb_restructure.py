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


# ---- coverage gate ---------------------------------------------------------
def test_significant_tokens_normalize_values():
    src = ("კრისტალი — ჩემთვის / Consumer Loans\n"
           "მაქსიმალური თანხა: 100,000 ₾, განაკვეთი 21.5%-დან\n"
           "ტელეფონი: 032 202 20 20, პორტალი crystalone.ge\n"
           "1. სექცია")
    toks, comps = kr._significant_tokens(src, include_latin=True)
    assert "n:100000" in toks          # thousands separator collapsed
    assert "p:21.5" in toks            # percentage with decimal
    assert "t:0322022020" in toks      # phone normalized to digits
    assert comps["t:0322022020"] == ["032", "202", "20", "20"]
    assert "u:crystalone.ge" in toks   # url
    assert "l:consumer" in toks and "l:loans" in toks
    assert not any(t == "n:1" for t in toks)   # lone digit = list marker, skipped


def test_spaced_thousands_and_ranges_are_not_swallowed_by_the_phone_pass():
    """Regression: «100 000» (7 chars, 6 digits) and «12 - 36» matched the phone regex,
    were rejected as phones, and then deleted before the number pass — Georgian tariff
    amounts and term ranges were exempt from the whole gate."""
    toks, _ = kr._significant_tokens(
        "მაქსიმალური თანხა 100 000 ლარი, ვადა 12 - 36 თვე", include_latin=False)
    assert "n:100000" in toks
    assert "n:12" in toks and "n:36" in toks


def test_ranges_pass_when_both_numbers_survive_non_adjacent():
    """«2024 - 2025» is one >=7-digit run; a rewrite keeping both years with text
    between them must pass (components fallback), not fail on digit contiguity."""
    src = "აქცია მოქმედებს 2024 - 2025 წლებში"
    out = "აქცია მოქმედებს 2024 წლიდან (12 თვე) 2025 წლამდე"
    assert kr._missing_tokens(src, out) == {}
    out_dropped = "აქცია მოქმედებს 2024 წლიდან"          # 2025 gone -> caught
    assert any(t.startswith("t:") for t in kr._missing_tokens(src, out_dropped))


def test_decimal_commas_match_both_directions():
    assert kr._missing_tokens("ქულა: 21.5", "ქულა არის 21,5") == {}
    assert kr._missing_tokens("ქულა: 21,5", "ქულა არის 21.5") == {}


def test_number_matching_respects_digit_boundaries():
    """A dropped «12» is NOT satisfied by «120» elsewhere in the output."""
    missing = kr._missing_tokens("ვადა: 12 თვე", "თანხა 120 ლარი")
    assert "n:12" in missing


def test_sentence_glue_is_not_a_mandatory_url():
    """PDF artifacts like «loans.Read» must not become required tokens."""
    toks, _ = kr._significant_tokens("იხილეთ loans.Read ვებგვერდზე crystalone.ge",
                                     include_latin=False)
    assert "u:crystalone.ge" in toks
    assert not any(t.startswith("u:loans") for t in toks)


def test_latin_light_counts_mtavruli_georgian():
    """ALL-CAPS Georgian headings use Mtavruli (U+1C90+); they must still count as
    non-Latin script so English product names stay protected."""
    src = ("\u1c9b\u1c94\u1ca1\u1c9b\u1c98 \u1c93\u1c90\u1c93\u1c90 " * 4 + "(Loans) ") * 3
    assert kr._latin_light(src)


def test_missing_tokens_ignores_formatting_differences():
    src = "თანხა: 100,000 ₾. ტელეფონი: 032 202 20 20."
    out = "მაქსიმალური თანხა არის 100 000 ლარი. დაგვირეკეთ: 0322 02 20 20."
    assert kr._missing_tokens(src, out) == {}


def test_missing_tokens_catches_dropped_names_in_georgian_doc():
    src = ("კრისტალი — ჩემთვის / Consumer Loans\n"
           "სესხის მიზნობრიობა: უძრავი ქონების შეძენა, მშენებლობა და რემონტი\n"
           "ვადა: 120 თვემდე, საპროცენტო განაკვეთი მერყეობს")
    out = ("სესხის მიზნობრიობა: უძრავი ქონების შეძენა, მშენებლობა და რემონტი. "
           "ვადა: 120 თვემდე.")             # banner dropped, numbers intact
    missing = kr._missing_tokens(src, out)
    assert "l:consumer" in missing and "l:loans" in missing
    assert missing["l:loans"].startswith("კრისტალი")   # maps back to the source line


def test_latin_words_not_enforced_for_english_documents():
    src = "The maximum amount is 5,000 GEL for consumer loans."
    assert not kr._latin_light(src)     # English doc -> prose words are rephraseable


@pytest.mark.asyncio
async def test_coverage_gap_triggers_repair_call(monkeypatch):
    async def fake_settings():
        return {"anthropic_api_key": "k", "llm_model": "m"}
    monkeypatch.setattr(kr.settings_store, "get_effective", fake_settings)

    prompts = []
    async def fake_call_tool(**kw):
        prompts.append(kw["user"])
        if "<missed>" in kw["user"]:      # the repair round covers the banner
            return {"entries": [{"topic": "ჩემთვის",
                                 "content": "კრისტალი — ჩემთვის / Consumer Loans მიმართულება"}]}
        return {"entries": [{"topic": "ვადა", "content": "მიზნობრიობა: უძრავი ქონების "
                             "შეძენა, მშენებლობა და რემონტი. ვადა: 120 თვემდე."}]}
    monkeypatch.setattr(kr.llm, "call_tool", fake_call_tool)

    src = ("კრისტალი — ჩემთვის / Consumer Loans\n"
           "სესხის მიზნობრიობა: უძრავი ქონების შეძენა, მშენებლობა და რემონტი\n"
           "ვადა: 120 თვემდე, საპროცენტო განაკვეთი მერყეობს")
    pairs = await kr.restructure(src, client_id="c1")
    assert len(prompts) == 2 and "<missed>" in prompts[1]
    assert any("Consumer Loans" in c for c, _ in pairs)


@pytest.mark.asyncio
async def test_unrepaired_loss_fails_loudly_with_fragments(monkeypatch):
    async def fake_settings():
        return {"anthropic_api_key": "k", "llm_model": "m"}
    monkeypatch.setattr(kr.settings_store, "get_effective", fake_settings)

    async def stubborn(**kw):             # never covers the banner, even when asked
        return {"entries": [{"topic": "ვადა", "content": "მიზნობრიობა: უძრავი ქონების "
                             "შეძენა, მშენებლობა და რემონტი. ვადა: 120 თვემდე."}]}
    monkeypatch.setattr(kr.llm, "call_tool", stubborn)

    src = ("კრისტალი — ჩემთვის / Consumer Loans\n"
           "სესხის მიზნობრიობა: უძრავი ქონების შეძენა, მშენებლობა და რემონტი\n"
           "ვადა: 120 თვემდე, საპროცენტო განაკვეთი მერყეობს")
    with pytest.raises(kr.RestructureError, match="Nothing was imported") as e:
        await kr.restructure(src, client_id="c1")
    assert "Consumer Loans" in str(e.value)   # names the lost fragment


@pytest.mark.asyncio
async def test_restructure_requires_configured_key(monkeypatch):
    async def fake_settings():
        return {"anthropic_api_key": "", "llm_model": "m"}
    monkeypatch.setattr(kr.settings_store, "get_effective", fake_settings)
    with pytest.raises(kr.RestructureError, match="not configured"):
        await kr.restructure("some text", client_id="c1")
