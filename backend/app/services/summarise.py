"""Summarise one call, or several related calls, into one reviewer-facing digest.

A "thread" here is 1..N recordings of the same people (a customer and the support team)
uploaded together in chronological order. The reviewer wants one short answer to "what was
this about and where did it end up", per-call cards, and who took part — not N separate
analyses they have to stitch together themselves.

Two design choices worth knowing:

* **One forced-tool call, normally.** The whole thread goes into a single `submit_summary`
  call so the model can see that call 3 resolves what call 1 opened. Only when the
  transcripts are too big for one request (`TWO_STAGE_TOKENS`, measured with
  `llm.estimate_tokens` so Georgian is counted at its real weight) does it fall back to
  two stages: the SAME tool on each call alone, at most `PER_CALL_CONCURRENCY` at a time,
  then the combined pass over those per-call summaries. The result says which happened in
  `stages`, because a two-stage digest has seen summaries, not speech, and a reviewer
  reading a surprising claim deserves to know that.
* **Indices, never ids.** The model refers to calls by their position in the upload
  (`index`), which is the one coordinate it cannot mistype into a foreign job. Code maps
  positions back to `job_id`/`filename` and drops anything out of range, exactly as
  segments.py does for `#` lines.

Prompted with `render_timeline(segments)` so the model sees who said what and when; a
pasted transcript without segments is segmented on the fly, like every other analyser.
"""
import asyncio
import logging
import re

from . import llm
from .segments import render_timeline, segments_from_text

log = logging.getLogger("cq")

# Above this many estimated prompt tokens across the thread the single combined request
# stops being safe (context ceiling, and a 100k-token read plus a long answer comfortably
# outruns a normal read timeout), so the thread is summarised per call first.
TWO_STAGE_TOKENS = 120_000
# How many per-call passes run at once in stage one. Three keeps a 10-call thread from
# hogging the whole service-wide admission ceiling (`llm_max_concurrency`).
PER_CALL_CONCURRENCY = 3
# Output budget — the FLOOR, not the whole story. A digest grows with the thread, and in
# Georgian (2 tokens/char by `llm.estimate_tokens`) one 90-character sentence is ~160 tokens.
# At the lengths this file's own system prompt asks for — a 2-4 sentence card per call, plus
# a 2-4 sentence short_summary and up to 8 key points and 8 action items — ten calls come to
# ~8.8k tokens with 3-sentence cards and ~10.8k with 4, and overflow starts at seven calls.
# 8192 therefore truncated the route's own maximum (10 files) in the product's main language,
# after every file had been transcribed and every quota unit spent. So the budget is sized
# from the work, like `scoring_import` does, and this constant is what a one- or two-call
# thread still gets.
MAX_OUTPUT_TOKENS = 8_192
PER_CALL_OUTPUT_TOKENS = 1_200   # one Georgian card (title + 2-4 sentences + outcome)
THREAD_OUTPUT_TOKENS = 2_500     # short_summary + key_points + action_items + participants
OUTPUT_TOKENS_CEILING = 32_000


def output_budget(count: int) -> int:
    """max_tokens for a digest of `count` calls, never below MAX_OUTPUT_TOKENS."""
    sized = THREAD_OUTPUT_TOKENS + PER_CALL_OUTPUT_TOKENS * max(count, 1)
    return min(OUTPUT_TOKENS_CEILING, max(MAX_OUTPUT_TOKENS, sized))
# By the time summarise() runs, minutes of transcription have already been paid for. Waiting
# tens of seconds for an admission slot is far cheaper than throwing that away with a 429.
ADMIT_PATIENCE_S = 30.0

ROLES = ("agent", "customer", "other")


class SummariseError(RuntimeError):
    """The model call failed (any `llm.LLMError`), or there was nothing to summarise."""


SUMMARY_TOOL = {
    "name": "submit_summary",
    "description": "Return the digest of one or several related customer-support calls.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "language": {
                "type": "string",
                "description": "The language the calls are in (e.g. Georgian, Russian, English).",
            },
            "short_summary": {
                "type": "string",
                "description": "2-4 sentences covering the whole thread: what it was about, "
                               "what was decided, what is still open.",
            },
            "key_points": {
                "type": "array",
                "items": {"type": "string"},
                "description": "At most 8 concrete facts or decisions (amounts, dates, conditions, promises).",
            },
            "action_items": {
                "type": "array",
                "items": {"type": "string"},
                "description": "At most 8 things somebody still has to do, each naming who.",
            },
            "participants": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string",
                                  "description": "How to refer to this person, e.g. 'the agent' or 'the customer, Nino'."},
                        "role": {"type": "string", "enum": list(ROLES)},
                        "appears_in": {
                            "type": "array",
                            "items": {"type": "integer"},
                            "description": "The index of every call this person takes part in.",
                        },
                    },
                    "required": ["label", "role", "appears_in"],
                    "additionalProperties": False,
                },
            },
            "calls": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {"type": "integer", "description": "The call's index attribute."},
                        "title": {"type": "string", "description": "A short title for the call."},
                        "summary": {"type": "string", "description": "2-4 sentences on this call alone."},
                        "outcome": {"type": "string",
                                    "description": "How the call ended, in words: resolved, escalated, "
                                                   "callback promised, unresolved, ..."},
                    },
                    "required": ["index", "title", "summary", "outcome"],
                    "additionalProperties": False,
                },
                "description": "EXACTLY one entry per call, in the same order as the calls.",
            },
        },
        "required": ["language", "short_summary", "key_points", "action_items",
                     "participants", "calls"],
        "additionalProperties": False,
    },
}

_SYSTEM = (
    "You summarise customer-support calls for a quality reviewer. You receive ONE call, or "
    "SEVERAL related calls (the same people, separate conversations) in chronological order, "
    "each as a numbered transcript with speaker labels and times, or — when a call was too long "
    "to include verbatim — as a summary already produced from its full transcript.\n\n"
    "Return, through the tool:\n"
    "- language: the language the calls are in.\n"
    "- short_summary: 2-4 sentences covering the whole thread — what it was about, what was "
    "decided, what is still open.\n"
    "- key_points: at most 8 concrete facts or decisions (amounts, dates, conditions, promises).\n"
    "- action_items: at most 8 things somebody still has to do, each naming who.\n"
    "- participants: each distinct person (e.g. 'the agent', 'the customer, Nino'), their role, "
    "and appears_in = the index of every call they take part in.\n"
    "- calls: EXACTLY one entry per call, index = the call's index attribute, in the same order: "
    "a short title, a 2-4 sentence summary of that call alone, and its outcome in words.\n\n"
    "Write EVERY field in the language the calls are in — the language most of the speech is "
    "in; if they mix, the dominant one. The calls may be in Georgian, Russian or English: NEVER "
    "translate into English, never invent details that are not in the transcripts, and never "
    "quote long passages."
)

_ONE_CALL_INTRO = "Summarise this ONE call.\n\n"
_THREAD_INTRO = "Summarise this thread of related calls as a whole.\n\n"
_COMBINED_INTRO = (
    "The calls below were too long to include verbatim; each is represented by a summary "
    "produced from its full transcript. Combine them into one digest of the whole thread.\n\n"
)

_WS = re.compile(r"\s+")


# ---------------------------------------------------------------------------
# Result normalisation — the model's output is data, never trusted for shape
# ---------------------------------------------------------------------------

def _str(value) -> str:
    return "" if value is None else str(value).strip()


def _str_list(value) -> list[str]:
    """Clean list of non-empty strings, whatever the model sent (see scoring._as_str_list)."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, dict):
        value = list(value.values())
    if not isinstance(value, (list, tuple)):
        return [_str(value)] if _str(value) else []
    out = []
    for item in value:
        if item is None:
            continue
        s = " — ".join(_str(v) for v in item.values() if v not in (None, "")) \
            if isinstance(item, dict) else _str(item)
        if s:
            out.append(s)
    return out


def _index(value, count: int) -> int | None:
    """A call position the model cited → int, or None when it is not a valid position.
    Accepts '3' and 3.0 (models do that); rejects bools, 2.5 and anything out of range."""
    if isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != int(f):
        return None
    i = int(f)
    return i if 0 <= i < count else None


def _indices(value, count: int) -> list[int]:
    if isinstance(value, dict):
        value = list(value.values())
    if not isinstance(value, (list, tuple)):
        value = [value]
    return sorted({i for i in (_index(v, count) for v in value) if i is not None})


def _role(value) -> str:
    v = _str(value).lower()
    return v if v in ROLES else "other"


def _participants(value, count: int) -> list[dict]:
    if isinstance(value, dict):
        value = list(value.values())
    out = []
    for p in value if isinstance(value, (list, tuple)) else []:
        if not isinstance(p, dict):
            continue
        label = _str(p.get("label"))
        if label:
            out.append({"label": label, "role": _role(p.get("role")),
                        "appears_in": _indices(p.get("appears_in"), count)})
    return out


def _call_cards(value, count: int) -> list[dict]:
    """One `{title, summary, outcome}` per input position, in input order. The model's
    `index` decides where an entry lands; the first entry for a position wins, an entry for
    a position that does not exist is dropped, and a position the model skipped is blank."""
    if isinstance(value, dict):
        value = list(value.values())
    by_pos: dict[int, dict] = {}
    for c in value if isinstance(value, (list, tuple)) else []:
        if not isinstance(c, dict):
            continue
        i = _index(c.get("index"), count)
        if i is not None and i not in by_pos:
            by_pos[i] = c
    return [{"title": _str(by_pos.get(i, {}).get("title")),
             "summary": _str(by_pos.get(i, {}).get("summary")),
             "outcome": _str(by_pos.get(i, {}).get("outcome"))} for i in range(count)]


def normalise(raw: dict, count: int) -> dict:
    """The tool output as the fixed shape the rest of the app relies on."""
    raw = raw if isinstance(raw, dict) else {}
    return {
        "language": _str(raw.get("language")),
        "short_summary": _str(raw.get("short_summary")),
        "key_points": _str_list(raw.get("key_points")),
        "action_items": _str_list(raw.get("action_items")),
        "participants": _participants(raw.get("participants"), count),
        "calls": _call_cards(raw.get("calls"), count),
    }


# ---------------------------------------------------------------------------
# Prompt rendering
# ---------------------------------------------------------------------------

def _attr(value, limit: int = 120) -> str:
    """A filename or language as an XML-ish attribute value: one line, no double quotes."""
    return _WS.sub(" ", _str(value)).replace('"', "'")[:limit]


def _open_tag(i: int, call: dict, form: str) -> str:
    parts = [f'<call index="{i}"']
    if _attr(call.get("filename")):
        parts.append(f'filename="{_attr(call.get("filename"))}"')
    if _attr(call.get("language")):
        parts.append(f'language="{_attr(call.get("language"), 40)}"')
    parts.append(f'form="{form}">')
    return " ".join(parts)


def render_call(i: int, call: dict) -> str:
    """One call as the timeline the model reads. Segments come from the recording; a pasted
    transcript without them is segmented here so both look the same to the model."""
    transcript = _str(call.get("transcript"))
    segments = call.get("segments") or segments_from_text(transcript)
    body = render_timeline(segments) or "(no speech transcribed)"
    return f"{_open_tag(i, call, 'transcript')}\n{body}\n</call>"


def render_summary(i: int, call: dict, summary: dict) -> str:
    """One call as its stage-one digest, for the combined pass."""
    card = summary["calls"][0] if summary.get("calls") else {}
    lines = [_open_tag(i, call, "summary"),
             f"Title: {card.get('title', '')}",
             f"Summary: {card.get('summary', '')}",
             f"Outcome: {card.get('outcome', '')}"]
    for heading, items in (("Key points", summary.get("key_points")),
                           ("Action items", summary.get("action_items"))):
        if items:
            lines.append(f"{heading}:")
            lines.extend(f"- {item}" for item in items)
    if summary.get("participants"):
        lines.append("Participants:")
        lines.extend(f"- {p['label']} ({p['role']})" for p in summary["participants"])
    lines.append("</call>")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# The passes
# ---------------------------------------------------------------------------

async def _submit(user: str, count: int, *, api_key: str, model: str,
                  client_id: str | None) -> dict:
    """One forced `submit_summary` call, normalised. Streamed for the same reason the KB
    imports stream: a long read followed by a long answer is what Anthropic drops when it
    is not streamed, and a thread of ten Georgian calls is exactly that."""
    raw = await llm.call_tool(
        feature="summarise", client_id=client_id, api_key=api_key, model=model,
        system=_SYSTEM, user=user, tool=SUMMARY_TOOL, opts=llm.RESTRUCTURE,
        max_tokens=output_budget(count), admit_timeout_s=ADMIT_PATIENCE_S, stream=True)
    return normalise(raw, count)


async def _per_call_pass(calls: list[dict], **kw) -> list[dict]:
    """Stage one: each call summarised alone, at most PER_CALL_CONCURRENCY in flight,
    results in call order. One failure cancels the rest — a thread digest with a call
    missing from it would be misleading, so there is no point finishing the others.

    A call summarised alone is rendered as call 0 of a one-call thread, so the model's
    `index` maps back; its real position is restored when the combined pass is rendered."""
    sem = asyncio.Semaphore(PER_CALL_CONCURRENCY)

    async def one(call: dict) -> dict:
        async with sem:
            return await _submit(_ONE_CALL_INTRO + render_call(0, call), 1, **kw)

    tasks = [asyncio.create_task(one(c)) for c in calls]
    try:
        return await asyncio.gather(*tasks)
    except BaseException:
        for t in tasks:
            t.cancel()
        raise


def _enrich(result: dict, calls: list[dict], per_call: list[dict] | None) -> dict:
    """Attach each card to its recording. In stage two the combined pass may leave a card
    blank; the stage-one card for that call is the better answer, so it fills in."""
    cards = []
    for i, (call, card) in enumerate(zip(calls, result["calls"])):
        if per_call is not None and not any(card.values()):
            card = per_call[i]["calls"][0]
        job_id = call.get("job_id")
        cards.append({"index": i, "job_id": None if job_id is None else str(job_id),
                      "filename": _str(call.get("filename")), **card})
    return {**result, "calls": cards}


async def summarise(calls: list[dict], *, api_key: str, model: str,
                    client_id: str | None = None, user_id: str | None = None) -> dict:
    """Digest of `calls` (`[{job_id, filename, language, transcript, segments}]`, upload
    order = chronological). Returns the normalised tool output with `calls[]` enriched with
    `job_id`/`filename` and `stages` = 1 (one combined pass) or 2 (per-call passes first).

    `client_id` is only for usage accounting; `user_id` is logged so a registered user's
    thread can be traced in the api log (llm_usage has no user column). Raises
    SummariseError when the model call fails or there is nothing to summarise.
    """
    calls = [c for c in (calls or []) if isinstance(c, dict)]
    if not calls:
        raise SummariseError("Nothing to summarise.")
    kw = dict(api_key=api_key, model=model, client_id=client_id)
    blocks = [render_call(i, c) for i, c in enumerate(calls)]
    # Measured on what is actually sent (headers included), so the guard can only fire
    # earlier than one measured on the bare transcripts, never later.
    tokens = sum(llm.estimate_tokens(b) for b in blocks)
    stages = 2 if tokens > TWO_STAGE_TOKENS else 1
    log.info("summarise: %d call(s), ~%d prompt tokens, stages=%d, client=%s, user=%s",
             len(calls), int(tokens), stages, client_id, user_id)

    try:
        if stages == 2:
            per_call = await _per_call_pass(calls, **kw)
            user = _COMBINED_INTRO + "\n\n".join(
                render_summary(i, c, s) for i, (c, s) in enumerate(zip(calls, per_call)))
        else:
            per_call = None
            user = (_ONE_CALL_INTRO if len(calls) == 1 else _THREAD_INTRO) + "\n\n".join(blocks)
        combined = await _submit(user, len(calls), **kw)
    except llm.LLMError as exc:
        raise SummariseError(f"Summarise request failed: {exc}") from exc

    return {**_enrich(combined, calls, per_call), "stages": stages}
