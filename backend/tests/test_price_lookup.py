"""Official price lookup: a price reaches the operator only if the provider's own page says it.

The AI reads the page; what is pinned here is everything that keeps its answer honest — the
quote must be on the fetched page and contain the number, the unit must fit the price and is
converted by code, redirects never leave the providers' own hosts, and the endpoint proposes
without saving. No network and no model: the page fetch and the AI call are stubbed.
"""
import httpx
import pytest

from app.config import settings
from app.services import price_lookup as pl
from app.services import pricing

ADMIN = {"X-Admin-Token": settings.admin_token}

# Shaped like the real pages: a Markdown table (Anthropic, Google) and stripped HTML (ElevenLabs).
MD_PAGE = """# Pricing
| Model | Base Input Tokens | 5m Cache Writes | 1h Cache Writes | Cache Hits & Refreshes | Output Tokens |
|---|---|---|---|---|---|
| Claude Opus 4.8 | $5 / MTok | $6.25 / MTok | $10 / MTok | $0.50 / MTok | $25 / MTok |
| Claude Sonnet 4.6 | $3 / MTok | $3.75 / MTok | $6 / MTok | $0.30 / MTok | $15 / MTok |
""" + "filler text. " * 30

HTML_PAGE = ("<html><body><script>var x='$999 per hour'</script><div>Scribe v2</div><div>Speech to Text"
             "</div><div>$0.22</div><div>Price per hour</div><table><tr><td>v3</td><td>$0.08</td>"
             "<td>Price per 1K characters</td></tr></table><p>" + "words " * 60 + "</p></body></html>")


def _p(field, value, unit, quote, effective_from=""):
    return {"field": field, "value": value, "unit": unit, "quote": quote,
            "effective_from": effective_from}


# ---------------------------------------------------------------------------
# Text and verification
# ---------------------------------------------------------------------------
def test_html_becomes_text_with_rows_on_one_line_and_no_scripts():
    text = pl.page_text(HTML_PAGE, "text/html")
    assert "$999" not in text                      # script content is not page text
    assert "v3 | $0.08 | Price per 1K characters" in text
    assert "Scribe v2" in text


def test_a_quoted_table_row_verifies_and_converts():
    item = {"prices": [
        _p("input", 5, "per_1m_tokens", "| Claude Opus 4.8 | $5 / MTok | $6.25 / MTok |"),
        _p("cache_write", 6.25, "per_1m_tokens", "Claude Opus 4.8 | $5 / MTok | $6.25 / MTok"),
        _p("output", 25, "per_1m_tokens", "$0.50 / MTok | $25 / MTok"),
    ]}
    kept, _, rejected = pl.verify(item, MD_PAGE)
    assert not rejected
    assert kept["input_per_mtok"]["value"] == 5
    assert kept["cache_write_per_mtok"]["value"] == 6.25
    assert kept["output_per_mtok"]["value"] == 25


@pytest.mark.parametrize("price,reason", [
    (_p("input", 4, "per_1m_tokens", "Claude Opus 4.8 | $5 / MTok"), "number not in the quote"),
    (_p("input", 5, "per_1m_tokens", "Claude Opus 4.8 costs $5 per million"), "quote not found"),
    (_p("audio", 5, "per_1m_tokens", "Claude Opus 4.8 | $5 / MTok"), "unit does not fit"),
    (_p("input", -5, "per_1m_tokens", "Claude Opus 4.8 | $5 / MTok"), "negative"),
    (_p("input", "five", "per_1m_tokens", "Claude Opus 4.8 | $5 / MTok"), "not a number"),
    (_p("input", 5, "per_1m_tokens", "$5"), "quote not found"),      # too short to mean anything
])
def test_an_unbacked_price_is_rejected_with_the_reason(price, reason):
    kept, _, rejected = pl.verify({"prices": [price]}, MD_PAGE)
    assert not kept
    assert reason in rejected[0]["reason"]


@pytest.mark.parametrize("value,unit,column,expected", [
    (0.006, "per_minute", "audio_per_hour", 0.36),
    (0.0001, "per_second", "audio_per_hour", 0.36),
    (0.22, "per_hour", "audio_per_hour", 0.22),
    (0.000003, "per_token", "input_per_mtok", 3.0),
    (0.003, "per_1k_tokens", "input_per_mtok", 3.0),
    (80, "per_1m_characters", "chars_per_k", 0.08),
    (0.00008, "per_character", "chars_per_k", 0.08),
])
def test_code_converts_the_stated_unit_into_ours(value, unit, column, expected):
    field = {"audio_per_hour": "audio", "input_per_mtok": "input", "chars_per_k": "characters"}[column]
    written = f"{value:.10f}".rstrip("0").rstrip(".")      # as a page writes it, never 3e-06
    page = f"Model X costs ${written} {unit.replace('_', ' ')} on this plan. " + "pad " * 50
    kept, _, _ = pl.verify({"prices": [_p(field, value, unit, f"Model X costs ${written}")]}, page)
    assert kept[column]["value"] == pytest.approx(expected)


GOOGLE_LIKE = """## Gemini 3.8 Flash
* Input price
* $0.75 through December 31, 2026.
$1.50 starting January 1, 2027.
* Output price (including thinking tokens)
* $3.75 through December 31, 2026.
$7.50 starting January 1, 2027.
""" + "filler " * 40


def test_a_dated_price_change_becomes_a_scheduled_version():
    from datetime import date
    item = {"prices": [
        _p("input", 0.75, "per_1m_tokens", "$0.75 through December 31, 2026."),
        _p("input", 1.50, "per_1m_tokens", "$1.50 starting January 1, 2027.", "2027-01-01"),
        _p("output", 3.75, "per_1m_tokens", "$3.75 through December 31, 2026."),
        _p("output", 7.50, "per_1m_tokens", "$7.50 starting January 1, 2027.", "2027-01-01"),
    ]}
    current, scheduled, rejected = pl.verify(item, GOOGLE_LIKE, today=date(2026, 11, 10))
    assert not rejected
    assert current["input_per_mtok"]["value"] == 0.75 and current["output_per_mtok"]["value"] == 3.75
    assert [v["effective_from"] for v in scheduled] == ["2027-01-01"]
    assert scheduled[0]["prices"]["input_per_mtok"]["value"] == 1.5
    assert scheduled[0]["prices"]["output_per_mtok"]["value"] == 7.5
    # Read after the change took effect: the dated price is the one in force now.
    current, scheduled, _ = pl.verify(item, GOOGLE_LIKE, today=date(2027, 2, 1))
    assert current["input_per_mtok"]["value"] == 1.5 and not scheduled


def test_numbers_are_read_the_way_pages_write_them():
    assert pl.number_in(2.5, "$2.50 / MTok")
    assert pl.number_in(1000, "1,000 characters")
    assert not pl.number_in(2.5, "$25 / MTok")


def test_quote_matching_ignores_case_whitespace_and_table_pipes():
    assert pl.quote_ok("claude opus 4.8  $5 / MTok", MD_PAGE)
    assert not pl.quote_ok("Claude Opus 4.9 | $5 / MTok", MD_PAGE)


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------
async def _fetch_with(handler):
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        return await pl.fetch_source("gemini", c)


async def test_redirects_are_followed_only_within_the_providers_hosts():
    def ok(req):
        if req.url.host == "ai.google.dev":
            return httpx.Response(301, headers={"location": "https://aistudio.google.com/docs/pricing.md"})
        return httpx.Response(200, text=MD_PAGE, headers={"content-type": "text/markdown"})
    url, text = await _fetch_with(ok)
    assert url.startswith("https://aistudio.google.com/") and "Claude Opus 4.8" in text

    def evil(req):
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data"})
    with pytest.raises(pl.LookupError_, match="unexpected host"):
        await _fetch_with(evil)


async def test_an_empty_or_failing_page_is_a_reported_error():
    with pytest.raises(pl.LookupError_, match="HTTP 503"):
        await _fetch_with(lambda req: httpx.Response(503))
    with pytest.raises(pl.LookupError_, match="empty"):
        await _fetch_with(lambda req: httpx.Response(200, text="<html></html>"))
    with pytest.raises(pl.LookupError_, match="No official pricing page"):
        await pl.fetch_source("local")


# ---------------------------------------------------------------------------
# The whole lookup, with the page and the AI stubbed
# ---------------------------------------------------------------------------
@pytest.fixture
def stubbed(monkeypatch):
    calls = []

    async def fetch(provider, client=None):
        return f"https://{provider}.example/pricing", MD_PAGE

    async def ask(provider, url, page, items):
        calls.append((provider, [i["model"] for i in items]))
        out = []
        for i, it in enumerate(items):
            if it["model"] == "claude-opus-4-8":
                out.append({"request_index": i, "matched": True, "page_model_name": "Claude Opus 4.8",
                            "notes": "standard tier", "prices": [
                                _p("input", 5, "per_1m_tokens", "Claude Opus 4.8 | $5 / MTok"),
                                _p("output", 30, "per_1m_tokens", "| $25 / MTok |")]})   # wrong number
            else:
                out.append({"request_index": i, "matched": False, "page_model_name": "",
                            "notes": "", "prices": []})
        return out

    monkeypatch.setattr(pl, "fetch_source", fetch)
    monkeypatch.setattr(pl, "_ask", ask)
    return calls


async def test_lookup_reads_one_page_per_provider_and_keeps_only_verified_prices(stubbed):
    out = await pl.lookup([
        {"provider": "anthropic", "model": "claude-opus-4-8", "capability": "llm"},
        {"provider": "anthropic", "model": "claude-nope", "capability": "llm"},
        {"provider": "local", "model": "tone", "capability": "voice_tone"},
    ])
    assert stubbed == [("anthropic", ["claude-opus-4-8", "claude-nope"])]   # one call, both models
    by = {r["model"]: r for r in out["results"]}
    opus = by["claude-opus-4-8"]
    assert opus["status"] == "found" and opus["page_model_name"] == "Claude Opus 4.8"
    assert opus["prices"] == {"input_per_mtok": {"value": 5.0, "quote": "Claude Opus 4.8 | $5 / MTok",
                                                 "stated": "5 per 1m tokens"}}
    assert opus["rejected"][0]["field"] == "output"           # $30 is not what the page says
    assert by["claude-nope"]["status"] == "not_listed"
    assert by["tone"]["status"] == "unsupported"


# ---------------------------------------------------------------------------
# The endpoint (database)
# ---------------------------------------------------------------------------
def test_lookup_endpoint_proposes_and_saves_nothing(api, stubbed):
    from conftest import sql
    before = sql(lambda c: c.fetchval("SELECT count(*) FROM ai_prices"))
    r = api.post("/admin/usage/prices/lookup", headers=ADMIN,
                 json={"items": [{"provider": "anthropic", "model": "claude-opus-4-8", "capability": "llm"}]})
    assert r.status_code == 200, r.text
    assert r.json()["results"][0]["prices"]["input_per_mtok"]["value"] == 5
    assert sql(lambda c: c.fetchval("SELECT count(*) FROM ai_prices")) == before


@pytest.mark.parametrize("items,msg", [
    ([{"provider": "anthropic", "model": "*"}], "named model"),
    ([{"provider": "anthropic", "model": "m", "capability": "video"}], "kind"),
    ("all", "list"),
    ([{"provider": "x", "model": "m"}] * 41, "at most"),
])
async def test_lookup_items_are_validated(items, msg):
    with pytest.raises(pricing.PricingError, match=msg):
        await pricing.lookup_items(items)


def test_lookup_is_superadmin_only(api):
    assert api.post("/admin/usage/prices/lookup", json={}).status_code == 401
    assert api.post("/admin/usage/prices/lookup", json={},
                    headers={"X-Admin-Token": "guess"}).status_code == 401


def test_markdoc_tags_and_markdown_punctuation_do_not_break_a_quote():
    page = pl.page_text("* Input price\n* $2.00 or $0.003/min{% sup %}*{% /sup %} (audio)\n" + "pad " * 60, "text/markdown")
    assert "{%" not in page
    assert pl.quote_ok("$2.00 or $0.003/min* (audio)", page)
    assert pl.quote_ok("$2.00 or $0.003/min (audio)", page)
