"""AI restructuring: turn a messy extracted document into clean, self-contained KB entries.

The deterministic extractors (`kb_ingest.extract_text`) preserve every fact but not the
*shape*: a QA scorecard flattens to "A1 | criterion text | 1/-3 | 1" rows, a policy DOCX to
wall-of-text paragraphs. Character-window chunking over that is honest but retrieval-hostile —
an answer can straddle two chunks, and a chunk can mix six unrelated criteria.

This module is the opt-in fix the uploader chooses when their file does NOT follow the KB
templates: Claude reads the raw extracted text and rewrites it as discrete entries, each one
a self-contained topic + content pair in the document's own language. Entries then flow down
the exact same path CSV rows already use — one entry, one chunk, fields preserved in metadata.

House rules apply: forced tool-use with a strict schema, defensive normalization of every
field, model and API key from `settings_store` (never hardcoded), and loud failure — a
restructure that loses data must surface as an error the uploader sees, never a quiet
"ready". Three hard-won guards from the adversarial review of the first version:

  * **Truncation is handled, not ignored.** Output must be a SUPERSET of the input (facts
    verbatim + topics + repeated section context), and Georgian tokenizes at >1 token/char,
    so a dense segment can overflow any output budget. `llm.call_tool` now raises
    LLMTruncatedError on stop_reason=max_tokens, and this module bisects the segment and
    retries the halves rather than keeping a silently-shortened result.
  * **The size guard caps characters, not segments.** Segments split on line boundaries, so
    a low-newline document (one enormous flattened line) used to become a single unbounded
    segment and sail past a segment-count cap straight into the API. The cap is now on total
    input characters, checked before anything is sent — and `MAX_INPUT_CHARS` is exported so
    upload routes can reject an oversized file at upload time instead of minutes later.
  * **One restructure call in flight per process.** Segment calls run sequentially under a
    module gate, so a big import occupies exactly one slot of the shared LLM concurrency
    ceiling instead of starving interactive analyze/chat/scoring — and it waits patiently
    for that slot (long admit timeout) instead of dying at the interactive 1-second one.
"""
import asyncio
import logging
import re

from . import llm, settings_store

log = logging.getLogger("cq")

# One segment must fit comfortably in a single call alongside the instructions, and its
# ENTIRETY must fit back inside MAX_OUTPUT_TOKENS as entries (output ⊇ input): for Georgian
# at ~1.5 tokens/char, 6k chars of input is ~9k tokens of facts — bigger than that and even
# a perfect answer cannot fit, so the bisection fallback would run every time.
SEGMENT_CHARS = 6_000
MAX_OUTPUT_TOKENS = 8_192
# Absolute input cap, enforced on characters BEFORE segmentation (see module docstring).
MAX_INPUT_CHARS = 100_000
# Bisection can multiply calls; this is the runaway backstop, not a sizing knob.
MAX_CALLS = 40
# Below this a segment no longer bisects — content this dense that still truncates is an
# error worth surfacing, not something to shred into confetti.
MIN_SEGMENT_CHARS = 500
ADMIT_PATIENCE_S = 300.0

OVERSIZE_MESSAGE = (f"The file is too large for AI restructuring (over "
                    f"{MAX_INPUT_CHARS // 1000}k characters of text). Split it into smaller "
                    "documents, or import it without restructuring.")

# One restructure conversation with the model at a time, process-wide.
_gate = asyncio.Semaphore(1)


class RestructureError(RuntimeError):
    pass


RESTRUCTURE_TOOL = {
    "name": "submit_kb_entries",
    "description": "Return the document rewritten as discrete knowledge-base entries.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "entries": {
                "type": "array",
                "description": "Every distinct fact, rule, Q&A pair, criterion or data row "
                               "in the text, each as one self-contained entry.",
                "items": {
                    "type": "object",
                    "properties": {
                        "topic": {
                            "type": "string",
                            "description": "Short label or question for the entry, in the "
                                           "document's own language.",
                        },
                        "content": {
                            "type": "string",
                            "description": "The complete, self-contained statement of the "
                                           "fact/answer, in the document's own language. "
                                           "Include concrete values (amounts, terms, "
                                           "percentages, phone numbers) verbatim.",
                        },
                    },
                    "required": ["topic", "content"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["entries"],
        "additionalProperties": False,
    },
}

SYSTEM = (
    "You convert raw text extracted from customer documents (tables flattened to "
    "'cell | cell' lines, scorecards, policies, tariff sheets — often Georgian, Russian or "
    "English) into clean knowledge-base entries.\n"
    "Rules:\n"
    "- Every entry must be fully self-contained: understandable without the rest of the "
    "document. Repeat the section context in the entry when a line only makes sense with it.\n"
    "- Preserve every concrete fact VERBATIM: amounts, date ranges, percentages, scores, "
    "weights, phone numbers, product names. Never invent, estimate or drop a value.\n"
    "- Write topic and content in the SAME language as the source text.\n"
    "- One fact/rule/criterion/row per entry. Merge lines only when they are one fact split "
    "by formatting.\n"
    "- Skip layout debris that carries no meaning (lone punctuation, page numbers, empty "
    "headers), but never skip data.\n"
    "- Document titles, brand lines and section banners ARE data — including anything "
    "before the first heading. Make an entry for what they name.\n"
    "- Keep EVERY name variant: when a product/section has names in two languages "
    "(e.g. 'იპოთეკური სესხი (Mortgage Loan)'), every entry for it must carry both.\n"
    "- One fact per entry: never bundle unrelated values (a phone number and a URL) "
    "into one entry."
)


def _segments(text: str, limit: int = SEGMENT_CHARS) -> list[str]:
    """Split on line boundaries into <= limit pieces (a table row never splits)."""
    lines = text.splitlines()
    out: list[str] = []
    buf: list[str] = []
    size = 0
    for line in lines:
        # +1 for the newline; an oversized single line still becomes its own segment.
        if buf and size + len(line) + 1 > limit:
            out.append("\n".join(buf))
            buf, size = [], 0
        buf.append(line)
        size += len(line) + 1
    if buf:
        out.append("\n".join(buf))
    return [s for s in out if s.strip()]


def _bisect(seg: str) -> list[str]:
    """Halve a segment near its middle, preferring a line boundary."""
    mid = len(seg) // 2
    cut = seg.rfind("\n", 0, mid)
    if cut < MIN_SEGMENT_CHARS // 2:
        cut = mid
    return [p for p in (seg[:cut], seg[cut:]) if p.strip()]


# ---- coverage verification -------------------------------------------------
# The model CAN silently drop content: verified in production against a real loans DOCX —
# the pre-heading brand banner and 3 of 4 parenthetical English product names never made
# it into the entries, with every number intact. Trusting the output is not an option, so
# this gate deterministically extracts the tokens that must survive VERBATIM and proves
# each one appears in the output; what is missing gets one targeted repair call, and what
# is still missing after that fails the import loudly, naming the lost fragments.

_URL_RE = re.compile(r"\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}\b", re.IGNORECASE)
# Real TLDs only: without this, PDF-extraction artifacts like "loans.Read" (a glued
# sentence boundary) become mandatory "URLs" and fail legitimate rewrites.
_TLDS = {"ge", "com", "net", "org", "io", "edu", "gov", "info", "biz", "me", "eu", "ru",
         "uk", "de", "fr", "ai", "app", "dev", "online", "site", "shop", "cloud"}
_PHONE_RE = re.compile(r"\d(?:[\d \-]{5,})\d")
_PCT_RE = re.compile(r"\d+(?:[.,]\d+)?\s*%")
_NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")
_LATIN_RE = re.compile(r"[A-Za-z]{3,}")
_THOUSANDS_RE = re.compile(r"(?<=\d)[ ,](?=\d{3}\b)")


def _significant_tokens(text: str, *, include_latin: bool
                        ) -> tuple[dict[str, str], dict[str, list[str]]]:
    """(token -> example source line, phone-token -> component number groups).

    Tokens are the things a rewrite must carry verbatim: URLs, phone numbers (normalized
    to digits), percentages, multi-digit numbers (thousand separators collapsed), and —
    in a predominantly non-Latin document — Latin-script words, because there they are
    product/brand names, not prose. The component map lets a digit run that is really a
    RANGE ("2024 - 2025", "100 000 - 500 000") pass when the rewrite keeps both numbers
    but not adjacent, while a many-group run (a real phone) still demands contiguity.
    """
    out: dict[str, str] = {}
    comps: dict[str, list[str]] = {}
    for line in text.splitlines():
        work = line
        for m in _URL_RE.findall(work):
            tld = m.rsplit(".", 1)[-1].lower()
            if m == m.lower() and tld in _TLDS and not m.replace(".", "").isdigit():
                out.setdefault("u:" + m, line)
        work = _URL_RE.sub(" ", work)

        def _phone_repl(match):
            digits = re.sub(r"\D", "", match.group())
            if len(digits) < 7:
                return match.group()     # not a phone: leave for the number pass below
            tok = "t:" + digits
            out.setdefault(tok, line)
            groups = re.findall(r"\d{2,}", match.group())   # raw: phones keep >3 groups
            comps.setdefault(tok, groups)
            return " "
        work = _PHONE_RE.sub(_phone_repl, work)

        work = _THOUSANDS_RE.sub("", work)
        for m in _PCT_RE.findall(work):
            out.setdefault("p:" + re.sub(r"[\s%]", "", m).replace(",", "."), line)
        work = _PCT_RE.sub(" ", work)
        for m in _NUM_RE.findall(work):
            if len(m) >= 2:                      # lone digits are list markers
                out.setdefault("n:" + m.replace(",", "."), line)
        if include_latin:
            for m in _LATIN_RE.findall(line):
                out.setdefault("l:" + m.lower(), line)
    return out, comps


def _latin_light(text: str) -> bool:
    """True when Latin letters are the minority script (Georgian/Russian documents) —
    exactly when a Latin word is a NAME that must survive, not rephraseable prose.
    Covers all three Georgian scripts (Mkhedruli, Asomtavruli, Mtavruli) and Cyrillic."""
    latin = len(re.findall(r"[A-Za-z]", text))
    other = len(re.findall(r"[\u10A0-\u10FF\u1C90-\u1CBF\u0400-\u04FF]", text))
    return other > 0 and latin < (latin + other) * 0.3


def _num_present(val: str, hay: str) -> bool:
    """Number match with digit boundaries: a dropped "12" is NOT satisfied by "120"."""
    return re.search(r"(?<!\d)" + re.escape(val) + r"(?!\d)", hay) is not None


def _missing_tokens(source: str, output: str) -> dict[str, str]:
    """Which of the source's significant tokens do NOT appear in the output."""
    # One normalized haystack: thousands collapsed, decimal commas -> dots, lowercased —
    # so "100 000", "100,000" and "21,5" match their source forms symmetrically.
    hay = _THOUSANDS_RE.sub("", output).lower().replace(",", ".")
    hay_digits = re.sub(r"\D", "", output)
    tokens, comps = _significant_tokens(source, include_latin=_latin_light(source))
    missing: dict[str, str] = {}
    for tok, line in tokens.items():
        kind, val = tok[:2], tok[2:]
        if kind == "t:":
            # Contiguous digits = the phone survived as one value. Otherwise a run of
            # up to 3 groups is a range/spaced amount — every group present counts.
            groups = comps.get(tok) or []
            ok = val in hay_digits or (
                0 < len(groups) <= 3 and all(_num_present(g, hay) for g in groups))
        elif kind in ("p:", "n:"):
            ok = _num_present(val, hay)
        else:
            ok = val in hay
        if not ok:
            missing[tok] = line
    return missing


def _normalize(raw: dict) -> list[dict]:
    entries = raw.get("entries")
    if isinstance(entries, dict):
        entries = list(entries.values())
    if not isinstance(entries, (list, tuple)):
        return []
    out: list[dict] = []
    for e in entries:
        if isinstance(e, str):
            e = {"topic": "", "content": e}
        if not isinstance(e, dict):
            continue
        topic = str(e.get("topic") or "").strip()
        content = str(e.get("content") or "").strip()
        if content:
            out.append({"topic": topic, "content": content})
    return out


async def restructure(text: str, *, client_id: str | None) -> list[tuple[str, dict]]:
    """Raw extracted text -> [(chunk_content, chunk_metadata)], CSV-row shaped.

    Raises RestructureError with an uploader-actionable message on any failure — the
    caller stores it on the document, so the sentence must stand on its own (raw upstream
    error text stays in the server log, not in the tenant-visible field).
    """
    if len(text) > MAX_INPUT_CHARS:
        raise RestructureError(OVERSIZE_MESSAGE)
    queue = _segments(text)
    if not queue:
        raise RestructureError("The file contains no text to restructure.")

    cfg = await settings_store.get_effective()
    api_key = cfg.get("anthropic_api_key")
    if not api_key:
        raise RestructureError("AI restructuring is not configured on this server "
                               "(Anthropic API key is missing).")
    model = cfg.get("llm_model")

    entries: list[dict] = []
    calls = 0
    async with _gate:
        while queue:
            seg = queue.pop(0)
            calls += 1
            if calls > MAX_CALLS:
                raise RestructureError(OVERSIZE_MESSAGE)
            user = ("Convert this extracted document text (one part of the document) into "
                    f"knowledge-base entries:\n\n<document>\n{seg}\n</document>")
            try:
                raw = await llm.call_tool(
                    feature="kb_restructure", client_id=client_id, api_key=api_key,
                    model=model, system=SYSTEM, user=user, tool=RESTRUCTURE_TOOL,
                    opts=llm.RESTRUCTURE, max_tokens=MAX_OUTPUT_TOKENS, cache_system=True,
                    admit_timeout_s=ADMIT_PATIENCE_S, stream=True)
            except llm.LLMTruncatedError:
                # The answer did not fit the output budget. A partial tool input parses as
                # a smaller-but-valid entry list — keeping it would silently drop facts, so
                # split the segment and do the halves properly instead.
                if len(seg) <= MIN_SEGMENT_CHARS:
                    log.error("kb_restructure: %d-char segment still truncates (client=%s)",
                              len(seg), client_id)
                    raise RestructureError(
                        "This file is too dense for AI restructuring — a small part of it "
                        "alone overflows the model's output budget. Split the file into "
                        "smaller documents, or import it without restructuring.")
                queue[:0] = _bisect(seg)
                continue
            except llm.LLMError as exc:
                log.error("kb_restructure call failed (client=%s): %s", client_id, exc)
                raise RestructureError(
                    "AI restructuring failed partway through "
                    f"({exc.__class__.__name__}). Try again, or import without "
                    "restructuring.") from exc
            entries.extend(_normalize(raw))

    if not entries:
        raise RestructureError("AI restructuring produced no entries from this file. "
                               "Import it without restructuring instead.")

    # Coverage gate: every significant source token must appear in the entries. One
    # targeted repair call for the gaps; whatever survives that fails the import loudly.
    def _joined() -> str:
        return "\n".join(f"{e['topic']}\n{e['content']}" for e in entries)

    # Pure-CPU token scan over up to 100k+100k chars: off the event loop, like chunking.
    missing = await asyncio.to_thread(_missing_tokens, text, _joined())
    repair_failed: str | None = None
    if missing:
        lines = list(dict.fromkeys(missing.values()))[:80]
        log.warning("kb_restructure coverage gap client=%s tokens=%d — repairing",
                    client_id, len(missing))
        repair_user = ("Your earlier conversion of this document MISSED the facts below. "
                       "Create knowledge-base entries covering EVERY fragment, keeping all "
                       "names, numbers and values verbatim:\n\n<missed>\n"
                       + "\n".join(lines) + "\n</missed>")
        async with _gate:
            try:
                raw = await llm.call_tool(
                    feature="kb_restructure", client_id=client_id, api_key=api_key,
                    model=model, system=SYSTEM, user=repair_user, tool=RESTRUCTURE_TOOL,
                    opts=llm.RESTRUCTURE, max_tokens=MAX_OUTPUT_TOKENS, cache_system=True,
                    admit_timeout_s=ADMIT_PATIENCE_S, stream=True)
                entries.extend(_normalize(raw))
            except llm.LLMError as exc:
                repair_failed = exc.__class__.__name__
                log.error("kb_restructure repair call failed (client=%s): %s", client_id, exc)
        missing = await asyncio.to_thread(_missing_tokens, text, _joined())
        if missing:
            if repair_failed:
                # The repair CALL failed — an infrastructure error, not proven data loss;
                # saying "lost facts" here would blame the document for a network blip.
                raise RestructureError(
                    f"AI restructuring failed partway through ({repair_failed}). "
                    "Nothing was imported — try again, or import without restructuring.")
            frags = list(dict.fromkeys(missing.values()))
            shown = "; ".join(f"\u00ab{fr[:60]}\u00bb" for fr in frags[:5])
            raise RestructureError(
                f"AI restructuring lost {len(missing)} fact(s) from the document, e.g.: "
                f"{shown}. Nothing was imported — try again, or import without "
                "restructuring.")

    out: list[tuple[str, dict]] = []
    for e in entries:
        content = f"{e['topic']}: {e['content']}" if e["topic"] else e["content"]
        out.append((content, {"entry": e, "restructured": True}))
    log.info("kb_restructure client=%s calls=%d entries=%d chars=%d", client_id, calls,
             len(entries), len(text))
    return out
