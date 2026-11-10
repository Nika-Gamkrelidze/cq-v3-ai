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
import hashlib
import html
import logging
import re
import time
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

FIELDS = ("input", "output", "cache_write", "cache_read", "audio_total", "audio_input",
          "audio_output", "characters")
# Our price-list columns (services/pricing.PRICE_FIELDS) per extracted field.
COLUMN = {"input": "input_per_mtok", "output": "output_per_mtok",
          "cache_write": "cache_write_per_mtok", "cache_read": "cache_read_per_mtok",
          "audio_total": "audio_per_hour", "audio_input": "audio_per_hour",
          "audio_output": "audio_per_hour", "characters": "chars_per_k"}
_TOKENS = ("input", "output", "cache_write", "cache_read")
_AUDIO = ("audio_total", "audio_input", "audio_output")
# Stated unit -> (the fields it may describe, multiplier into our unit).
UNITS: dict[str, tuple[tuple[str, ...], float]] = {
    "per_1m_tokens": (_TOKENS, 1.0),
    "per_1k_tokens": (_TOKENS, 1000.0),
    "per_token": (_TOKENS, 1_000_000.0),
    "per_hour": (_AUDIO, 1.0),
    "per_minute": (_AUDIO, 60.0),
    "per_second": (_AUDIO, 3600.0),
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
# The model does not copy text: it names the LINES of the page a price is on, and code reads
# those lines itself. A model that must reproduce a table row verbatim sometimes reformats it
# (pipes, spacing, a dropped word) and the price is thrown out as "could not verify"; a line
# number cannot be misquoted, and the check that matters — the number really is in the cited
# lines — is exact.
MAX_CITED_LINES = 8
TOKEN_FIELDS = ("input", "output", "cache_write", "cache_read")
AUDIO_FIELDS = ("audio_total", "audio_input", "audio_output")
MODALITIES = ("text", "audio", "other")

_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def page_lines(page: str) -> list[str]:
    return page.split("\n")


def numbered(lines: list[str]) -> str:
    """The page as the model reads it: every line led by its number, `[12] text`."""
    return "\n".join(f"[{i}] {ln}" for i, ln in enumerate(lines))


def number_in(value: float, quote: str) -> bool:
    """The stated number is written in the quote ($2.50 for 2.5, 1,000 for 1000)."""
    for m in _NUM.finditer(quote):
        try:
            if abs(float(m.group().replace(",", "")) - value) < 1e-9:
                return True
        except ValueError:
            continue
    return False


def _cited(p: dict, lines: list[str]) -> str | None:
    """The text of the lines a price cites, or None when the citation is not on the page."""
    try:
        a, b = int(p.get("from_line")), int(p.get("to_line"))
    except (TypeError, ValueError):
        return None
    if a < 0 or b < a or b >= len(lines) or b - a + 1 > MAX_CITED_LINES:
        return None
    return " ".join(ln.strip() for ln in lines[a:b + 1] if ln.strip())


def verify(item: dict, lines: list[str], today: date | None = None
           ) -> tuple[dict[str, dict[tuple, dict]], list[dict]]:
    """One model's extracted prices -> ({when: {(field, modality): {value, quote, stated}}},
    rejected). `when` is "" for the price in force now, an ISO date for one the page dates in
    the future ("$1.50 starting January 1, 2027"), and a date already reached replaces the
    undated price (a page read after the change but not yet rewritten).

    A price is kept only when its cited lines are on the page, its number is in them, and its
    unit fits its field; the unit is then converted into our column's. Which COLUMN it fills
    for a call of a given kind is `columns_for`'s decision, not the model's."""
    today = today or datetime.now(timezone.utc).date()
    versions: dict[str, dict[tuple, dict]] = {}
    rejected: list[dict] = []
    for p in item.get("prices") or []:
        field, unit = p.get("field"), p.get("unit")
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
        modality = p.get("modality") if p.get("modality") in MODALITIES else "text"
        if field not in TOKEN_FIELDS:
            modality = "other"            # audio length and characters have no token modality
        quote = _cited(p, lines)
        key = (field, modality)
        if field not in FIELDS or unit not in UNITS or field not in UNITS[unit][0]:
            rejected.append({**p, "reason": "unit does not fit the price"})
        elif value < 0:
            rejected.append({**p, "reason": "negative"})
        elif quote is None:
            rejected.append({**p, "reason": "cited lines are not on the page"})
        elif not number_in(value, quote):
            rejected.append({**p, "reason": "number not in the cited lines"})
        elif key in versions.get(when, {}):
            rejected.append({**p, "reason": "duplicate"})
        else:
            versions.setdefault(when, {})[key] = {
                "value": round(value * UNITS[unit][1], 6), "quote": quote[:400],
                "stated": f"{p.get('value')} {unit.replace('_', ' ')}"}
    current = dict(versions.pop("", {}))
    for when in sorted(k for k in list(versions) if k.startswith("past:")):
        current.update(versions.pop(when))          # the latest start date already reached wins
    out = {"": current}
    out.update({w: v for w, v in versions.items()})
    return out, rejected


def consolidate(runs: list[dict[str, dict[tuple, dict]]]
                ) -> tuple[dict[str, dict[tuple, dict]], list[dict]]:
    """Several independent readings of one page -> the prices ALL of them agree on, and the
    ones they do not.

    An AI reads the same page a little differently each time, and a price list must not: a
    figure is proposed only when every reading found it and every reading found the same
    number. The rest come back as `unstable`, with each reading's value, for the operator to
    settle on the provider's page."""
    if len(runs) == 1:
        return runs[0], []
    keys = {(w, k) for r in runs for w, v in r.items() for k in v}
    agreed: dict[str, dict[tuple, dict]] = {}
    unstable: list[dict] = []
    for w, k in sorted(keys, key=lambda x: (x[0], x[1])):
        seen = [r.get(w, {}).get(k) for r in runs]
        vals = [s["value"] if s else None for s in seen]
        if all(s is not None for s in seen) and all(abs(v - vals[0]) < 1e-9 for v in vals):
            agreed.setdefault(w, {})[k] = seen[0]
        else:
            quote = next((s["quote"] for s in seen if s), "")
            unstable.append({"when": w, "field": k[0], "modality": k[1], "values": vals,
                             "quote": quote})
    agreed.setdefault("", {})
    return agreed, unstable


# What a call of each KIND is charged by. The same page row can serve several kinds (a Gemini
# chat model is also used as a stand-in transcriber), so the lookup reads everything the page
# says about a model once, and the kind only chooses among the readings — in code, so the
# choice cannot differ from one run to the next.
def _relevant(kind: str, field: str, modality: str) -> bool:
    if field == "characters":
        return kind == "tts"
    if field in AUDIO_FIELDS:
        return kind == "stt"
    if kind == "stt":
        return modality in ("audio", "text")
    return modality == "text"


def columns_for(kind: str, prices: dict[tuple, dict]) -> dict[str, dict]:
    """{price column: {value, quote, stated}} for a call of `kind` from one version's readings."""
    out: dict[str, dict] = {}

    def first(*keys):
        return next((prices[k] for k in keys if k in prices), None)

    pick = {
        "input_per_mtok": first(("input", "audio"), ("input", "text")) if kind == "stt"
        else first(("input", "text")),
        "output_per_mtok": first(("output", "text")),
        "cache_write_per_mtok": first(("cache_write", "text")),
        "cache_read_per_mtok": first(("cache_read", "text")),
        # One all-in figure when the page states one; otherwise the input part.
        "audio_per_hour": first(("audio_total", "other"), ("audio_input", "other")) if kind == "stt" else None,
        "chars_per_k": first(("characters", "other")) if kind == "tts" else None,
    }
    for column, info in pick.items():
        if info:
            out[column] = info
    return out


# ---------------------------------------------------------------------------------------------
# The model call
# ---------------------------------------------------------------------------------------------
_PRICE = {
    "type": "object",
    "additionalProperties": False,
    "required": ["field", "modality", "value", "unit", "from_line", "to_line", "effective_from"],
    "properties": {
        "field": {"type": "string", "enum": list(FIELDS),
                  "description": "input / output / cache_write / cache_read: token prices. "
                                 "audio_total: ONE all-in price per length of audio for the "
                                 "model; audio_input / audio_output: only when the page splits "
                                 "the per-length price into an input and an output part. "
                                 "characters: a price per characters of text."},
        "modality": {"type": "string", "enum": list(MODALITIES),
                     "description": "For token prices: text, or audio when the page prices audio "
                                    "tokens separately from text. Use other for audio_* and "
                                    "characters, and for image/video token prices."},
        "value": {"type": "number", "description": "The number exactly as the page writes it, in USD."},
        "unit": {"type": "string", "enum": list(UNITS), "description": "The unit as the page states it."},
        "from_line": {"type": "integer", "description": "Number of the first page line holding this "
                                                        "price (the n in `[n]`)."},
        "to_line": {"type": "integer", "description": "Number of the last line needed to read it "
                                                      "(equal to from_line for a single line)."},
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

# Bump when SYSTEM or the schema changes: a cached reading was made under the old rules.
PROMPT_VERSION = 3

SYSTEM = """You read an AI provider's official pricing page and report the list prices of the
models you are asked about. Use ONLY the page text you are given — never prior knowledge. If the
page does not list a model, report matched=false with no prices; do not guess a near relative
unless the page clearly gives it the same name.

The page is given with a number in front of every line, like `[12] text`. For EVERY price you
report, give the number of the line it is on (from_line) and, if you need more than one line to
read it (a table whose cells are on separate lines), the last line (to_line). Never copy text.

Model ids are API ids (e.g. claude-opus-4-8, gemini-3.8-flash, scribe_v2, eleven_v3); the page
uses display names (Claude Opus 4.8, Gemini 3.8 Flash, Scribe v2, v3). Match them carefully:
the version numbers must agree.

For each model report EVERYTHING the page states about its price — you are not told what the
model is used for, and the same model can be used in different ways:
- Text-token prices: input, output, cache_write, cache_read with modality text. Cache writes:
  the default (5-minute) write price. Context caching: the per-token price, never the
  storage-per-hour price.
- When the page prices AUDIO tokens separately (an "audio" row beside the text one), report that
  input price again with modality audio.
- A price per length of audio (per minute / hour): field audio_total when the page gives one
  figure for the model — an effective, blended or "approximately" figure counts. Only when the
  page gives the per-length price as two parts (an input part and an output part) report them as
  audio_input and audio_output instead. Never invent a total by adding parts.
- A price per characters of text: field characters.
- Ignore image and video token prices, tool prices and anything for another model.

Always the STANDARD paid price: the first, default table for the model — not batch / flex /
priority, not the free tier, not a discount — and for prices that change with prompt length the
shortest-prompt tier.

When the page says a price changes on a date ("$0.75 through December 31, 2026. $1.50 starting
January 1, 2027"), report BOTH: the current one with effective_from "" and the later one with
its start date as effective_from.

Omit any price the page does not state. Say in notes which tier or plan you used when there was
a choice."""


def _user(provider: str, url: str, lines: list[str], models: list[str]) -> str:
    asked = "\n".join(f"{i}. {m}" for i, m in enumerate(models))
    return (f"Provider: {provider}\nPricing page: {url}\n\nModels to report (request_index. id):\n"
            f"{asked}\n\n<pricing_page>\n{numbered(lines)}\n</pricing_page>")


async def _ask(provider: str, url: str, lines: list[str], models: list[str]) -> list[dict]:
    cfg = await settings_store.get_effective()
    out = await llm.call_tool(
        feature="price_lookup", client_id=None, api_key=cfg.get("anthropic_api_key") or "",
        model=cfg.get("llm_model"), system=SYSTEM, user=_user(provider, url, lines, models),
        tool=TOOL, opts=llm.RESTRUCTURE, max_tokens=8000, admit_timeout_s=30.0)
    answers = out.get("models") if isinstance(out, dict) else None
    return answers if isinstance(answers, list) else []


# Readings per provider that must AGREE before a price is proposed. Two, not one: a single
# reading of the same page differs from the next often enough that the first version of this
# button showed a different number on every press.
READINGS = 2
# A provider's page is read by the AI once per CACHE_TTL_S unless the operator asks again: the
# same page bytes give the same answer, which is what "the prices did not change in two minutes"
# needs to mean on screen.
CACHE_TTL_S = 6 * 3600
_cache: dict[tuple, tuple[float, dict]] = {}


def _cache_key(provider: str, page: str, models: list[str]) -> tuple:
    return (provider, hashlib.sha256(page.encode()).hexdigest(), tuple(sorted(models)), PROMPT_VERSION)


async def _read_models(provider: str, url: str, page: str, models: list[str]) -> dict[str, dict]:
    """Model id -> its reading of the page (kind-independent): matched, page_model_name, notes,
    `versions` ({when: {(field, modality): price}}), `unstable`, `rejected`, `readings`."""
    lines = page_lines(page)
    runs = await asyncio.gather(*(_ask(provider, url, lines, models) for _ in range(READINGS)),
                                return_exceptions=True)
    good = [r for r in runs if not isinstance(r, BaseException)]
    if not good:
        raise next(r for r in runs if isinstance(r, BaseException))
    out: dict[str, dict] = {}
    for i, model in enumerate(models):
        answers = [next((a for a in r if isinstance(a, dict) and a.get("request_index") == i), {})
                   for r in good]
        verified, rejected = [], []
        for a in answers:
            v, rej = verify(a, lines) if a.get("matched") else ({"": {}}, [])
            verified.append(v)
            rejected += rej
        versions, unstable = consolidate(verified)
        first = next((a for a in answers if a.get("matched")), {})
        out[model] = {
            "matched": any(a.get("matched") for a in answers),
            "page_model_name": first.get("page_model_name") or "",
            "notes": first.get("notes") or "",
            "versions": versions, "unstable": unstable,
            "rejected": rejected, "readings": len(good),
        }
    return out


def project(kind: str, read: dict) -> dict:
    """One model's reading -> the result for a call of `kind`: its columns, scheduled versions,
    unstable figures and rejected prices that matter to that kind."""
    versions = read["versions"]
    prices = columns_for(kind, versions.get("", {}))
    scheduled = []
    for when in sorted(w for w in versions if w):
        cols = columns_for(kind, versions[when])
        if cols:
            scheduled.append({"effective_from": when, "prices": cols})
    unstable = []
    for u in read["unstable"]:
        if _relevant(kind, u["field"], u["modality"]):
            unstable.append({"field": COLUMN[u["field"]], "when": u["when"], "values": u["values"],
                             "quote": u["quote"]})
    rejected = [{k: r.get(k) for k in ("field", "value", "unit", "reason")} for r in read["rejected"]
                if _relevant(kind, r.get("field") or "", r.get("modality") or "text")]
    if prices:
        status = "found"
    elif unstable:
        status = "unstable"
    elif rejected or scheduled:
        status = "unverified"
    else:
        status = "not_listed"
    return {"status": status, "page_model_name": read["page_model_name"], "notes": read["notes"],
            "prices": prices, "scheduled": scheduled, "unstable": unstable, "rejected": rejected}


async def _one_provider(provider: str, items: list[dict], client: httpx.AsyncClient,
                        fresh: bool) -> list[dict]:
    fetched_at = datetime.now(timezone.utc).isoformat()
    base = [{"provider": provider, "model": it["model"], "capability": it["capability"],
             "source_url": SOURCES.get(provider), "fetched_at": fetched_at, "cached": False}
            for it in items]
    try:
        url, page = await fetch_source(provider, client)
    except LookupError_ as exc:
        return [{**b, "status": "error", "error": str(exc), "prices": {}} for b in base]
    models = sorted({it["model"] for it in items})
    key = _cache_key(provider, page, models)
    hit = _cache.get(key)
    cached = bool(hit and not fresh and time.time() - hit[0] < CACHE_TTL_S)
    if cached:
        reads, read_at = hit[1], hit[0]
    else:
        try:
            reads = await _read_models(provider, url, page, models)
        except llm.LLMError as exc:
            log.warning("price lookup failed for %s: %s", provider, exc)
            return [{**b, "source_url": url, "status": "error", "prices": {},
                     "error": f"The AI could not read the page ({exc.__class__.__name__})."}
                    for b in base]
        read_at = time.time()
        _cache[key] = (read_at, reads)
        if len(_cache) > 64:                         # bounded: oldest first
            for k in sorted(_cache, key=lambda k_: _cache[k_][0])[:len(_cache) - 64]:
                del _cache[k]
    when = datetime.fromtimestamp(read_at, timezone.utc).isoformat()
    return [{**b, "source_url": url, "fetched_at": when, "cached": cached,
             **project(b["capability"], reads[b["model"]])} for b in base]


async def lookup(items: list[dict], fresh: bool = False) -> dict:
    """Official prices for (provider, model, capability) items, grouped into one page fetch and
    two independent AI readings per provider, providers in parallel. A reading is reused for
    CACHE_TTL_S while the page is byte-for-byte the same, unless `fresh`. Never raises for a
    source: each result carries status found | unstable | unverified | not_listed | unsupported
    | error."""
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
        done = await asyncio.gather(*(_one_provider(p, its, client, fresh) for p, its in groups.items()))
    results = [r for group in done for r in group] + unsupported
    return {"results": results, "sources": SOURCES}
