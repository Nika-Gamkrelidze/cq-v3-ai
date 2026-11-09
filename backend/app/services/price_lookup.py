"""Look a model's prices up on its provider's OFFICIAL pricing page — never from our own code.

The operator presses one button on the usage page's *Prices & markup* tab; for every provider
involved this module fetches that provider's own pricing page, hands its text to the text AI
with a strict tool schema, and returns PROPOSED prices for the operator to review and save.
Nothing here writes to the price list: a price that bills customers is saved by a person.

The model's answer is not taken on trust. For every price it must return the line of the page
it read the price from; code then checks that the quote really is on the fetched page and that
the number is in the quote, and converts the unit itself (per minute -> per hour, per token ->
per million). A price that fails either check is dropped and reported as unverified, so what
reaches the review table is something the operator can find on the provider's own page.

No list price is hardcoded anywhere (CLAUDE.md §4): only WHERE each provider publishes them.
The page is untrusted input — it can say anything — which is why the answer is schema-bound,
quote-checked and human-approved before it is used.
"""
import asyncio
import html
import logging
import re
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx

from . import llm, settings_store

log = logging.getLogger(__name__)

# Where each provider publishes its prices. Anthropic and Google serve Markdown versions of
# their docs pages, which read far better than the rendered HTML.
SOURCES: dict[str, str] = {
    "anthropic": "https://platform.claude.com/docs/en/about-claude/pricing.md",
    "gemini": "https://ai.google.dev/gemini-api/docs/pricing.md.txt",
    "openai": "https://developers.openai.com/api/docs/pricing",
    "elevenlabs": "https://elevenlabs.io/pricing/api",
}
# Redirects are followed only within these (the Google page moves to aistudio.google.com).
ALLOWED_HOSTS = ("claude.com", "anthropic.com", "google.dev", "google.com", "openai.com",
                 "elevenlabs.io")
MAX_PAGE_BYTES = 4_000_000
MAX_TEXT_CHARS = 150_000
FETCH_TIMEOUT_S = 20.0
USER_AGENT = "Mozilla/5.0 (compatible; CommuniQ-PriceLookup/1.0)"

FIELDS = ("input", "output", "cache_write", "cache_read", "audio", "characters")
# Our price-list columns (services/pricing.PRICE_FIELDS) per extracted field.
COLUMN = {"input": "input_per_mtok", "output": "output_per_mtok",
          "cache_write": "cache_write_per_mtok", "cache_read": "cache_read_per_mtok",
          "audio": "audio_per_hour", "characters": "chars_per_k"}
# Stated unit -> (the field kinds it may describe, multiplier into our unit).
UNITS: dict[str, tuple[tuple[str, ...], float]] = {
    "per_1m_tokens": (("input", "output", "cache_write", "cache_read"), 1.0),
    "per_1k_tokens": (("input", "output", "cache_write", "cache_read"), 1000.0),
    "per_token": (("input", "output", "cache_write", "cache_read"), 1_000_000.0),
    "per_hour": (("audio",), 1.0),
    "per_minute": (("audio",), 60.0),
    "per_second": (("audio",), 3600.0),
    "per_1k_characters": (("characters",), 1.0),
    "per_1m_characters": (("characters",), 0.001),
    "per_character": (("characters",), 1000.0),
}


class LookupError_(Exception):
    """A source could not be read; reported per provider, never raised to the caller."""


# ---------------------------------------------------------------------------------------------
# Fetch + text
# ---------------------------------------------------------------------------------------------
def _host_allowed(url: str) -> bool:
    p = urlparse(url)
    host = (p.hostname or "").lower()
    return p.scheme == "https" and any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS)


class _Text(HTMLParser):
    """HTML -> readable text that keeps table rows on one line (`a | b | c`), which is how
    pricing pages lay out a model's prices."""
    BLOCK = {"tr", "li", "p", "div", "h1", "h2", "h3", "h4", "h5", "br", "table", "section",
             "article", "header", "footer", "ul", "ol", "dt", "dd"}
    SKIP = {"script", "style", "noscript", "svg", "template"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag in self.BLOCK:
            self.out.append("\n")
        elif tag in ("td", "th"):
            self.out.append(" | ")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def page_text(body: str, content_type: str = "") -> str:
    """The page as plain text. Markdown passes through; HTML is stripped to its words."""
    looks_html = "html" in content_type.lower() or body.lstrip()[:200].lower().startswith(("<!doctype", "<html"))
    if looks_html:
        p = _Text()
        p.feed(body)
        body = "".join(p.out)
    body = html.unescape(body)
    # Google's docs are Markdoc: `$0.003/min{% sup %}*{% /sup %}` reads "$0.003/min*" on screen.
    body = re.sub(r"\{%.*?%\}", "", body)
    body = re.sub(r"[ \t ]+", " ", body)
    body = re.sub(r"\n\s*\n+", "\n", body)
    return body.strip()


async def fetch_source(provider: str, client: httpx.AsyncClient | None = None) -> tuple[str, str]:
    """(final url, page text) for a provider's official pricing page. Raises LookupError_."""
    url = SOURCES.get(provider)
    if not url:
        raise LookupError_(f"No official pricing page is known for provider {provider!r}.")
    own = client is None
    client = client or httpx.AsyncClient(timeout=FETCH_TIMEOUT_S, headers={"User-Agent": USER_AGENT})
    try:
        for _ in range(5):                      # follow redirects by hand, host-checked
            if not _host_allowed(url):
                raise LookupError_(f"The pricing page redirected to an unexpected host ({url}).")
            resp = await client.get(url, follow_redirects=False)
            if resp.status_code in (301, 302, 303, 307, 308) and resp.headers.get("location"):
                url = str(resp.url.join(resp.headers["location"]))
                continue
            break
        else:
            raise LookupError_("The pricing page redirected too many times.")
        if resp.status_code != 200:
            raise LookupError_(f"The pricing page answered HTTP {resp.status_code}.")
        if len(resp.content) > MAX_PAGE_BYTES:
            raise LookupError_("The pricing page is unexpectedly large.")
        text = page_text(resp.text, resp.headers.get("content-type", ""))
    except httpx.HTTPError as exc:
        raise LookupError_(f"Could not reach the pricing page ({exc.__class__.__name__}).") from exc
    finally:
        if own:
            await client.aclose()
    if len(text) < 200:
        raise LookupError_("The pricing page came back empty.")
    return url, text[:MAX_TEXT_CHARS]


# ---------------------------------------------------------------------------------------------
# Verification — pure, unit-tested
# ---------------------------------------------------------------------------------------------
def _squash(s: str) -> str:
    """For comparing a quote with the page: case, whitespace, table pipes and Markdown
    punctuation (`*`, backticks, backslash escapes) are presentation, not content."""
    s = re.sub(r"[|*`\\]", " ", s)
    return re.sub(r"\s+", " ", s).strip().lower()


_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def quote_ok(quote: str, page: str) -> bool:
    """The quote is on the page (whitespace, case and table pipes ignored)."""
    q = _squash(quote)
    return len(q) >= 4 and q in _squash(page)


def number_in(value: float, quote: str) -> bool:
    """The stated number is written in the quote ($2.50 for 2.5, 1,000 for 1000)."""
    for m in _NUM.finditer(quote):
        try:
            if abs(float(m.group().replace(",", "")) - value) < 1e-9:
                return True
        except ValueError:
            continue
    return False


def verify(item: dict, page: str, today: date | None = None) -> tuple[dict, list[dict], list[dict]]:
    """One model's extracted prices -> (current {column: {value, quote, stated}},
    scheduled [{effective_from, prices}], rejected [...]).

    A price is kept only when its quote is on the page, its number is in the quote, and its
    unit fits its field; it is then converted into our column's unit. A price the page dates in
    the future ("$1.50 starting January 1, 2027") becomes a SCHEDULED version — the price list
    is versioned for exactly this. One whose date has already come is the price in force now,
    over the page's undated one (a page read after the change but not yet rewritten)."""
    today = today or datetime.now(timezone.utc).date()
    versions: dict[str, dict[str, dict]] = {}
    rejected: list[dict] = []
    for p in item.get("prices") or []:
        field, unit = p.get("field"), p.get("unit")
        quote = str(p.get("quote") or "")
        when = str(p.get("effective_from") or "").strip()
        try:
            value = float(p.get("value"))
        except (TypeError, ValueError):
            rejected.append({**p, "reason": "not a number"})
            continue
        if when:
            try:
                d = date.fromisoformat(when)
            except ValueError:
                rejected.append({**p, "reason": "not a date"})
                continue
            when = d.isoformat() if d > today else "past:" + d.isoformat()
        if field not in FIELDS or unit not in UNITS or field not in UNITS[unit][0]:
            rejected.append({**p, "reason": "unit does not fit the price"})
        elif value < 0:
            rejected.append({**p, "reason": "negative"})
        elif not quote_ok(quote, page):
            rejected.append({**p, "reason": "quote not found on the page"})
        elif not number_in(value, quote):
            rejected.append({**p, "reason": "number not in the quote"})
        elif COLUMN[field] in versions.get(when, {}):
            rejected.append({**p, "reason": "duplicate"})
        else:
            versions.setdefault(when, {})[COLUMN[field]] = {
                "value": round(value * UNITS[unit][1], 6), "quote": quote.strip(),
                "stated": f"{p.get('value')} {unit.replace('_', ' ')}"}
    current = dict(versions.pop("", {}))
    for when in sorted(k for k in list(versions) if k.startswith("past:")):
        current.update(versions.pop(when))          # the latest start date already reached wins
    scheduled = [{"effective_from": when, "prices": versions[when]} for when in sorted(versions)]
    return current, scheduled, rejected


# ---------------------------------------------------------------------------------------------
# The model call
# ---------------------------------------------------------------------------------------------
_PRICE = {
    "type": "object",
    "additionalProperties": False,
    "required": ["field", "value", "unit", "quote", "effective_from"],
    "properties": {
        "field": {"type": "string", "enum": list(FIELDS),
                  "description": "input / output / cache_write / cache_read are token prices; "
                                 "audio is a price per length of audio; characters is a price "
                                 "per characters of text."},
        "value": {"type": "number", "description": "The number exactly as the page writes it, in USD."},
        "unit": {"type": "string", "enum": list(UNITS), "description": "The unit as the page states it."},
        "quote": {"type": "string",
                  "description": "The exact text from the page this price was read from — a table "
                                 "row or line copied verbatim, including the number."},
        "effective_from": {"type": "string",
                           "description": "YYYY-MM-DD when the page says this price starts on a "
                                          "date (e.g. 'starting January 1, 2027'); empty string "
                                          "for the price in force now."},
    },
}

TOOL = {
    "name": "report_prices",
    "description": "Report each requested model's prices as stated on the provider's pricing page.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["models"],
        "properties": {
            "models": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["request_index", "matched", "page_model_name", "prices", "notes"],
                    "properties": {
                        "request_index": {"type": "integer"},
                        "matched": {"type": "boolean",
                                    "description": "False when the page does not list this model."},
                        "page_model_name": {"type": "string",
                                            "description": "The model's name as the page writes it; empty if not matched."},
                        "prices": {"type": "array", "items": _PRICE},
                        "notes": {"type": "string",
                                  "description": "Which tier or plan was chosen and anything the "
                                                 "operator should know. Empty if nothing."},
                    },
                },
            },
        },
    },
}

SYSTEM = """You read an AI provider's official pricing page and report the list prices of the
models you are asked about. Use ONLY the page text you are given — never prior knowledge. If the
page does not list a model, report matched=false with no prices; do not guess a near relative
unless the page clearly gives it the same name.

Model ids are API ids (e.g. claude-opus-4-8, gemini-3.8-flash, scribe_v2, eleven_v3); the page
uses display names (Claude Opus 4.8, Gemini 3.8 Flash, Scribe v2, v3). Match them carefully:
the version numbers must agree.

Report the STANDARD paid price: the first, default table for the model — not batch / flex /
priority, not the free tier, not a discount — and for prices that change with prompt length the
shortest-prompt tier. For cache writes report the default (5-minute) write price; for context
caching report the per-token price, not the storage-per-hour price.

When the page says a price changes on a date ("$0.75 through December 31, 2026. $1.50 starting
January 1, 2027"), report BOTH: the current one with effective_from "" and the later one with
its start date as effective_from.

Report what applies to the requested KIND:
- llm (text model): input, output, cache_write, cache_read text-token prices.
- stt (speech to text): the token prices (audio input as input, text output as output) when the
  page lists them, AND the price per length of audio when the page gives one (field audio). For
  audio report the TOTAL per-minute/hour figure if the page states one (e.g. a blended rate),
  otherwise the input per-minute figure, and say which in notes.
- tts (text to speech): the price per characters if given (field characters), and the token
  prices if given.
Omit any price the page does not state. Every price needs the verbatim line it came from as
quote, containing that number. Say in notes which tier or plan you used when there was a choice."""


def _user(provider: str, url: str, page: str, items: list[dict]) -> str:
    asked = "\n".join(f"{i}. {it['model']} (kind: {it['capability']})" for i, it in enumerate(items))
    return (f"Provider: {provider}\nPricing page: {url}\n\nModels to report (request_index. id):\n"
            f"{asked}\n\n<pricing_page>\n{page}\n</pricing_page>")


async def _ask(provider: str, url: str, page: str, items: list[dict]) -> list[dict]:
    cfg = await settings_store.get_effective()
    out = await llm.call_tool(
        feature="price_lookup", client_id=None, api_key=cfg.get("anthropic_api_key") or "",
        model=cfg.get("llm_model"), system=SYSTEM, user=_user(provider, url, page, items),
        tool=TOOL, opts=llm.RESTRUCTURE, max_tokens=8000, admit_timeout_s=30.0)
    models = out.get("models") if isinstance(out, dict) else None
    return models if isinstance(models, list) else []


async def _one_provider(provider: str, items: list[dict], client: httpx.AsyncClient) -> list[dict]:
    fetched_at = datetime.now(timezone.utc).isoformat()
    base = [{"provider": provider, "model": it["model"], "capability": it["capability"],
             "source_url": SOURCES.get(provider), "fetched_at": fetched_at} for it in items]
    try:
        url, page = await fetch_source(provider, client)
    except LookupError_ as exc:
        return [{**b, "status": "error", "error": str(exc), "prices": {}} for b in base]
    try:
        answers = await _ask(provider, url, page, items)
    except llm.LLMError as exc:
        log.warning("price lookup failed for %s: %s", provider, exc)
        return [{**b, "source_url": url, "status": "error", "prices": {},
                 "error": f"The AI could not read the page ({exc.__class__.__name__})."} for b in base]

    by_index = {a.get("request_index"): a for a in answers if isinstance(a, dict)}
    results = []
    for i, b in enumerate(base):
        a = by_index.get(i) or {}
        prices, scheduled, rejected = verify(a, page) if a.get("matched") else ({}, [], [])
        status = "found" if prices else ("unverified" if rejected or scheduled else "not_listed")
        results.append({**b, "source_url": url, "status": status,
                        "page_model_name": a.get("page_model_name") or "",
                        "notes": a.get("notes") or "", "prices": prices, "scheduled": scheduled,
                        "rejected": [{k: r.get(k) for k in ("field", "value", "unit", "reason")}
                                     for r in rejected]})
    return results


async def lookup(items: list[dict]) -> dict:
    """Official prices for (provider, model, capability) items, grouped into one page fetch and
    one AI call per provider, providers in parallel. Never raises for a source: each result
    carries status found | unverified | not_listed | unsupported | error."""
    groups: dict[str, list[dict]] = {}
    unsupported = []
    for it in items:
        if it["provider"] in SOURCES:
            groups.setdefault(it["provider"], []).append(it)
        else:
            unsupported.append({**it, "status": "unsupported", "prices": {}, "source_url": None,
                                "error": "No official pricing page is known for this provider; "
                                         "enter its price by hand."})
    async with httpx.AsyncClient(timeout=FETCH_TIMEOUT_S, headers={"User-Agent": USER_AGENT}) as client:
        done = await asyncio.gather(*(_one_provider(p, its, client) for p, its in groups.items()))
    results = [r for group in done for r in group] + unsupported
    return {"results": results, "sources": SOURCES}
