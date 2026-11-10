"""Official price lookup: a price reaches the operator only if the provider's own page says it.

The AI reads the page; what is pinned here is everything that keeps its answer honest and
repeatable — the lines it cites must be on the fetched page and contain the number, the unit
must fit the price and is converted by code, code (not the model) decides which column a
reading fills for a call's kind, two readings must AGREE before a price is proposed, the same
page gives the same answer on the next press, redirects never leave the providers' own hosts,
and the endpoint proposes without saving. No network and no model: the page fetch and the AI
call are stubbed.
"""
from datetime import date

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
MD_LINES = pl.page_lines(MD_PAGE)
OPUS = MD_LINES.index(next(ln for ln in MD_LINES if ln.startswith("| Claude Opus 4.8")))

HTML_PAGE = ("<html><body><script>var x='$999 per hour'</script><div>Scribe v2</div><div>Speech to Text"
             "</div><div>$0.22</div><div>Price per hour</div><table><tr><td>v3</td><td>$0.08</td>"
             "<td>Price per 1K characters</td></tr></table><p>" + "words " * 60 + "</p></body></html>")


def _p(field, value, unit, a, b=None, when="", modality="text"):
    return {"field": field, "modality": modality, "value": value, "unit": unit,
            "from_line": a, "to_line": a if b is None else b, "effective_from": when}


# ---------------------------------------------------------------------------
# Text, lines and verification
# ---------------------------------------------------------------------------
def test_html_becomes_text_with_rows_on_one_line_and_no_scripts():
    text = pl.page_text(HTML_PAGE, "text/html")
    assert "$999" not in text                      # script content is not page text
    assert "v3 | $0.08 | Price per 1K characters" in text
    assert "Scribe v2" in text


def test_the_page_is_shown_to_the_model_with_numbered_lines():
    shown = pl.numbered(["alpha", "beta"])
    assert shown == "[0] alpha\n[1] beta"


def test_a_cited_table_row_verifies_and_converts():
    item = {"prices": [
        _p("input", 5, "per_1m_tokens", OPUS),
        _p("cache_write", 6.25, "per_1m_tokens", OPUS),
        _p("output", 25, "per_1m_tokens", OPUS),
    ]}
    versions, rejected = pl.verify(item, MD_LINES)
    assert not rejected
    cur = versions[""]
    assert cur[("input", "text")]["value"] == 5
    assert cur[("cache_write", "text")]["value"] == 6.25
    assert cur[("output", "text")]["value"] == 25
    assert "Claude Opus 4.8" in cur[("input", "text")]["quote"]       # code read the line itself


def test_a_price_split_over_several_lines_verifies_when_all_are_cited():
    lines = pl.page_lines(pl.page_text(HTML_PAGE, "text/html") + "\n" + "pad\n" * 40)
    i = lines.index("Scribe v2")
    versions, rejected = pl.verify({"prices": [_p("audio_total", 0.22, "per_hour", i, i + 3)]}, lines)
    assert not rejected and versions[""][("audio_total", "other")]["value"] == 0.22


@pytest.mark.parametrize("price,reason", [
    (_p("input", 4, "per_1m_tokens", OPUS), "number not in the cited lines"),
    (_p("input", 5, "per_1m_tokens", OPUS + 1), "number not in the cited lines"),     # Sonnet's row
    (_p("input", 5, "per_1m_tokens", 9999), "not on the page"),
    (_p("input", 5, "per_1m_tokens", OPUS, OPUS - 1), "not on the page"),
    (_p("input", 5, "per_1m_tokens", 0, 20), "not on the page"),                       # too many lines
    (_p("audio_total", 5, "per_1m_tokens", OPUS), "unit does not fit"),
    (_p("input", 5, "per_hour", OPUS), "unit does not fit"),
    (_p("input", -5, "per_1m_tokens", OPUS), "negative"),
    (_p("input", "five", "per_1m_tokens", OPUS), "not a number"),
    (_p("input", 5, "per_1m_tokens", OPUS, when="soon"), "not a date"),
    ({**_p("input", 5, "per_1m_tokens", OPUS), "from_line": "x"}, "not on the page"),
])
def test_an_unbacked_price_is_rejected_with_the_reason(price, reason):
    versions, rejected = pl.verify({"prices": [price]}, MD_LINES)
    assert not versions[""]
    assert reason in rejected[0]["reason"]


def test_the_same_price_twice_is_a_duplicate():
    _, rejected = pl.verify({"prices": [_p("input", 5, "per_1m_tokens", OPUS),
                                        _p("input", 5, "per_1m_tokens", OPUS)]}, MD_LINES)
    assert rejected[0]["reason"] == "duplicate"


@pytest.mark.parametrize("value,unit,field,expected", [
    (0.006, "per_minute", "audio_total", 0.36),
    (0.0001, "per_second", "audio_total", 0.36),
    (0.22, "per_hour", "audio_total", 0.22),
    (0.000003, "per_token", "input", 3.0),
    (0.003, "per_1k_tokens", "input", 3.0),
    (80, "per_1m_characters", "characters", 0.08),
    (0.00008, "per_character", "characters", 0.08),
])
def test_code_converts_the_stated_unit_into_ours(value, unit, field, expected):
    written = f"{value:.10f}".rstrip("0").rstrip(".")      # as a page writes it, never 3e-06
    lines = [f"Model X costs ${written} {unit.replace('_', ' ')} on this plan."] + ["pad"] * 50
    versions, rejected = pl.verify({"prices": [_p(field, value, unit, 0, modality="other")]}, lines)
    assert not rejected
    (info,) = versions[""].values()
    assert info["value"] == pytest.approx(expected)


GOOGLE_LINES = pl.page_lines("""## Gemini 3.8 Flash
* Input price
* $0.75 through December 31, 2026.
$1.50 starting January 1, 2027.
* Output price (including thinking tokens)
* $3.75 through December 31, 2026.
$7.50 starting January 1, 2027.
""" + "filler\n" * 40)


def test_a_dated_price_change_becomes_a_scheduled_version():
    item = {"prices": [
        _p("input", 0.75, "per_1m_tokens", 2),
        _p("input", 1.50, "per_1m_tokens", 3, when="2027-01-01"),
        _p("output", 3.75, "per_1m_tokens", 5),
        _p("output", 7.50, "per_1m_tokens", 6, when="2027-01-01"),
    ]}
    versions, rejected = pl.verify(item, GOOGLE_LINES, today=date(2026, 11, 10))
    assert not rejected
    assert versions[""][("input", "text")]["value"] == 0.75 and versions[""][("output", "text")]["value"] == 3.75
    assert versions["2027-01-01"][("input", "text")]["value"] == 1.5
    # Read after the change took effect: the dated price is the one in force now.
    versions, _ = pl.verify(item, GOOGLE_LINES, today=date(2027, 2, 1))
    assert versions[""][("input", "text")]["value"] == 1.5 and list(versions) == [""]


def test_numbers_are_read_the_way_pages_write_them():
    assert pl.number_in(2.5, "$2.50 / MTok")
    assert pl.number_in(1000, "1,000 characters")
    assert not pl.number_in(2.5, "$25 / MTok")


# ---------------------------------------------------------------------------
# Two readings must agree
# ---------------------------------------------------------------------------
def test_only_prices_every_reading_agrees_on_are_proposed():
    a = {"": {("input", "text"): {"value": 5.0, "quote": "q", "stated": "s"},
              ("audio_total", "other"): {"value": 0.18, "quote": "q", "stated": "s"}}}
    b = {"": {("input", "text"): {"value": 5.0, "quote": "q", "stated": "s"},
              ("audio_total", "other"): {"value": 0.30, "quote": "q", "stated": "s"}}}
    kept, unstable = pl.consolidate([a, b])
    assert list(kept[""]) == [("input", "text")]
    assert unstable == [{"when": "", "field": "audio_total", "modality": "other",
                         "values": [0.18, 0.30], "quote": "q"}]


def test_a_price_only_one_reading_found_is_unstable_not_kept():
    a = {"": {("input", "text"): {"value": 5.0, "quote": "q", "stated": "s"}}}
    b = {"": {}}
    kept, unstable = pl.consolidate([a, b])
    assert not kept[""] and unstable[0]["values"] == [5.0, None]


def test_a_single_reading_is_taken_as_it_is():
    a = {"": {("input", "text"): {"value": 5.0, "quote": "q", "stated": "s"}}}
    assert pl.consolidate([a]) == (a, [])


def test_scheduled_versions_must_agree_too():
    a = {"": {}, "2027-01-01": {("input", "text"): {"value": 1.5, "quote": "q", "stated": "s"}}}
    b = {"": {}, "2027-01-01": {("input", "text"): {"value": 1.5, "quote": "q", "stated": "s"}}}
    kept, unstable = pl.consolidate([a, b])
    assert kept["2027-01-01"][("input", "text")]["value"] == 1.5 and not unstable


# ---------------------------------------------------------------------------
# Which column a reading fills is decided by code, per kind
# ---------------------------------------------------------------------------
def _info(v):
    return {"value": v, "quote": "q", "stated": "s"}


TRANSCRIBE = {("input", "audio"): _info(2.0), ("output", "text"): _info(12.0),
              ("audio_total", "other"): _info(0.30), ("audio_input", "other"): _info(0.18)}


def test_a_speech_model_takes_audio_tokens_and_the_all_in_per_hour_price():
    cols = pl.columns_for("stt", TRANSCRIBE)
    assert cols["input_per_mtok"]["value"] == 2.0 and cols["output_per_mtok"]["value"] == 12.0
    assert cols["audio_per_hour"]["value"] == 0.30          # the total, never the input part alone


def test_the_input_part_is_used_only_when_the_page_has_no_total():
    cols = pl.columns_for("stt", {("audio_input", "other"): _info(0.18)})
    assert cols["audio_per_hour"]["value"] == 0.18


def test_a_text_model_ignores_audio_prices_and_a_speech_kind_falls_back_to_text_tokens():
    chat = {("input", "text"): _info(0.75), ("input", "audio"): _info(2.0), ("output", "text"): _info(3.75)}
    assert pl.columns_for("llm", chat)["input_per_mtok"]["value"] == 0.75
    assert "audio_per_hour" not in pl.columns_for("llm", TRANSCRIBE)
    # The same chat model used as a stand-in transcriber is charged its audio input price.
    assert pl.columns_for("stt", chat)["input_per_mtok"]["value"] == 2.0
    assert pl.columns_for("stt", {("input", "text"): _info(0.75)})["input_per_mtok"]["value"] == 0.75


def test_characters_belong_to_text_to_speech_only():
    v = {("characters", "other"): _info(0.08)}
    assert pl.columns_for("tts", v)["chars_per_k"]["value"] == 0.08
    assert "chars_per_k" not in pl.columns_for("llm", v)


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
class Brain:
    """Stands in for the AI: answers per call from a script, and counts the calls."""

    def __init__(self):
        self.calls = []
        self.answers = []          # one list of model answers per call, in order; the last repeats

    async def ask(self, provider, url, lines, models):
        self.calls.append((provider, list(models)))
        a = self.answers[min(len(self.calls) - 1, len(self.answers) - 1)]
        return a(models) if callable(a) else a


def _opus(output=25):
    def answer(models):
        out = []
        for i, m in enumerate(models):
            if m == "claude-opus-4-8":
                out.append({"request_index": i, "matched": True, "page_model_name": "Claude Opus 4.8",
                            "notes": "standard tier", "prices": [
                                _p("input", 5, "per_1m_tokens", OPUS),
                                _p("output", output, "per_1m_tokens", OPUS)]})
            else:
                out.append({"request_index": i, "matched": False, "page_model_name": "",
                            "notes": "", "prices": []})
        return out
    return answer


@pytest.fixture
def brain(monkeypatch):
    b = Brain()

    async def fetch(provider, client=None):
        return f"https://{provider}.example/pricing", MD_PAGE

    monkeypatch.setattr(pl, "fetch_source", fetch)
    monkeypatch.setattr(pl, "_ask", b.ask)
    pl._cache.clear()
    yield b
    pl._cache.clear()


ITEMS = [
    {"provider": "anthropic", "model": "claude-opus-4-8", "capability": "llm"},
    {"provider": "anthropic", "model": "claude-nope", "capability": "llm"},
    {"provider": "local", "model": "tone", "capability": "voice_tone"},
]


async def test_lookup_reads_one_page_per_provider_and_keeps_only_verified_agreed_prices(brain):
    brain.answers = [_opus()]
    out = await pl.lookup(ITEMS)
    assert [c[0] for c in brain.calls] == ["anthropic"] * pl.READINGS      # readings, one provider
    assert brain.calls[0][1] == ["claude-nope", "claude-opus-4-8"]          # both models, one call
    by = {r["model"]: r for r in out["results"]}
    opus = by["claude-opus-4-8"]
    assert opus["status"] == "found" and opus["page_model_name"] == "Claude Opus 4.8"
    assert opus["prices"]["input_per_mtok"]["value"] == 5.0
    assert opus["prices"]["output_per_mtok"]["value"] == 25.0
    assert "Claude Opus 4.8" in opus["prices"]["input_per_mtok"]["quote"]
    assert by["claude-nope"]["status"] == "not_listed"
    assert by["tone"]["status"] == "unsupported"


async def test_a_wrong_number_is_rejected_and_reported_with_its_reason(brain):
    brain.answers = [_opus(output=30)]                      # the page says $25
    opus = (await pl.lookup(ITEMS[:1]))["results"][0]
    assert opus["status"] == "found" and "output_per_mtok" not in opus["prices"]
    assert opus["rejected"][0]["field"] == "output"
    assert "number not in the cited lines" in opus["rejected"][0]["reason"]


async def test_readings_that_disagree_are_not_proposed(brain):
    brain.answers = [_opus(output=25), _opus(output=15)]
    # 15 is not on the line, so the second reading rejects it: the two readings disagree.
    opus = (await pl.lookup(ITEMS[:1]))["results"][0]
    assert opus["prices"]["input_per_mtok"]["value"] == 5.0
    assert "output_per_mtok" not in opus["prices"]
    assert opus["unstable"] == [{"field": "output_per_mtok", "when": "", "values": [25.0, None],
                                 "quote": opus["unstable"][0]["quote"]}]


async def test_everything_unstable_is_its_own_status(brain):
    sonnet_line = OPUS + 1                                   # 5 is on Opus's row, 3 on Sonnet's
    brain.answers = [lambda m: [{"request_index": 0, "matched": True, "page_model_name": "x", "notes": "",
                                 "prices": [_p("input", 5, "per_1m_tokens", OPUS)]}],
                     lambda m: [{"request_index": 0, "matched": True, "page_model_name": "x", "notes": "",
                                 "prices": [_p("input", 3, "per_1m_tokens", sonnet_line)]}]]
    r = (await pl.lookup(ITEMS[:1]))["results"][0]
    assert r["status"] == "unstable" and not r["prices"]
    assert r["unstable"][0]["values"] == [5.0, 3.0]


async def test_the_same_page_gets_the_same_answer_without_asking_the_ai_again(brain):
    brain.answers = [_opus()]
    first = await pl.lookup(ITEMS[:1])
    n = len(brain.calls)
    second = await pl.lookup(ITEMS[:1])
    assert len(brain.calls) == n                             # nothing was read again
    assert second["results"][0]["cached"] is True and first["results"][0]["cached"] is False
    assert second["results"][0]["prices"] == first["results"][0]["prices"]
    assert second["results"][0]["fetched_at"] == first["results"][0]["fetched_at"]    # when it was READ
    again = await pl.lookup(ITEMS[:1], fresh=True)
    assert len(brain.calls) == n + pl.READINGS and again["results"][0]["cached"] is False


async def test_a_changed_page_or_prompt_is_read_again(brain, monkeypatch):
    brain.answers = [_opus()]
    await pl.lookup(ITEMS[:1])
    n = len(brain.calls)

    async def other_page(provider, client=None):
        return f"https://{provider}.example/pricing", MD_PAGE + "\n| Claude Haiku 4.5 | $1 / MTok |"
    monkeypatch.setattr(pl, "fetch_source", other_page)
    await pl.lookup(ITEMS[:1])
    assert len(brain.calls) == n + pl.READINGS
    monkeypatch.setattr(pl, "PROMPT_VERSION", pl.PROMPT_VERSION + 1)
    await pl.lookup(ITEMS[:1])
    assert len(brain.calls) == n + 2 * pl.READINGS


async def test_one_model_asked_for_two_kinds_is_read_once_and_filled_per_kind(brain):
    def gemini(models):
        return [{"request_index": 0, "matched": True, "page_model_name": "Gemini X", "notes": "", "prices": [
            _p("input", 5, "per_1m_tokens", OPUS),
            _p("input", 3, "per_1m_tokens", OPUS + 1, modality="audio"),
            _p("output", 25, "per_1m_tokens", OPUS)]}]
    brain.answers = [gemini]
    out = await pl.lookup([{"provider": "anthropic", "model": "m", "capability": "llm"},
                           {"provider": "anthropic", "model": "m", "capability": "stt"}])
    assert brain.calls[0][1] == ["m"]                        # asked once for the model, not per kind
    llm_, stt = out["results"]
    assert llm_["prices"]["input_per_mtok"]["value"] == 5.0
    assert stt["prices"]["input_per_mtok"]["value"] == 3.0   # the audio-token price
    assert llm_["status"] == stt["status"] == "found"


async def test_one_failed_reading_does_not_sink_the_other(brain, monkeypatch):
    answers = [_opus(), RuntimeError("boom")]

    async def ask(provider, url, lines, models):
        a = answers[len(brain.calls)]
        brain.calls.append(provider)
        if isinstance(a, BaseException):
            raise a
        return a(models)
    monkeypatch.setattr(pl, "_ask", ask)
    r = (await pl.lookup(ITEMS[:1]))["results"][0]
    assert r["status"] == "found" and r["prices"]["input_per_mtok"]["value"] == 5.0


# ---------------------------------------------------------------------------
# The endpoint (database)
# ---------------------------------------------------------------------------
def test_lookup_endpoint_proposes_and_saves_nothing(api, brain):
    from conftest import sql
    brain.answers = [_opus()]
    before = sql(lambda c: c.fetchval("SELECT count(*) FROM ai_prices"))
    r = api.post("/admin/usage/prices/lookup", headers=ADMIN,
                 json={"items": [{"provider": "anthropic", "model": "claude-opus-4-8", "capability": "llm"}]})
    assert r.status_code == 200, r.text
    assert r.json()["results"][0]["prices"]["input_per_mtok"]["value"] == 5
    n = len(brain.calls)
    again = api.post("/admin/usage/prices/lookup", headers=ADMIN,
                     json={"items": [{"provider": "anthropic", "model": "claude-opus-4-8", "capability": "llm"}]})
    assert again.json()["results"][0]["cached"] is True and len(brain.calls) == n
    api.post("/admin/usage/prices/lookup", headers=ADMIN, json={"fresh": True, "items": [
        {"provider": "anthropic", "model": "claude-opus-4-8", "capability": "llm"}]})
    assert len(brain.calls) == n + pl.READINGS
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
