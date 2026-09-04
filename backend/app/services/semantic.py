"""Semantic analysis of a call: the tone of the WORDS, and separately the tone of the VOICE.

Two independent judges, deliberately never averaged (the same principle as `sentiment.py`):

  * **Text tone** — Claude reads the timestamped transcript and rates every line's wording
    (polite … aggressive) plus each speaker overall. It is told, explicitly, that how a line
    SOUNDED is not its job: the customer who says "thank you so much" through gritted teeth
    must come out polite here and angry below, because that disagreement is the finding a QA
    reviewer wants to hear.
  * **Voice tone** — the `cq-sentiment` sidecar classifies the audio of each segment on its
    own (`POST /prosody/segments`), so a talker who starts calm and ends shouting is a red
    patch on the timeline, not a lukewarm average over the whole call.

Everything is cited by segment INDEX (`render_timeline` prints `#12`, the model cites 12,
`spans_from_indices` turns 12 back into seconds). The model is never asked for a timestamp.

The voice half is optional in every sense: it is only run when asked for AND audio exists,
and when the sidecar is not deployed the result says so (`voice_available: False`) rather
than failing the text half that already cost a model call.
"""
from __future__ import annotations

import asyncio
import logging
import math
import re

from . import llm, sentiment
from .segments import render_timeline, segments_from_text, spans_from_indices

log = logging.getLogger("cq")


class SemanticError(RuntimeError):
    """The text-tone model call failed or answered garbage. Voice-only failures never raise —
    they degrade to `voice_available: False`."""


# --------------------------------------------------------------------------- #
# Vocabulary and level maps — the numbers the UI colours by and the tests pin
# --------------------------------------------------------------------------- #
TONES = ("polite", "neutral", "curt", "impolite", "rude", "aggressive")
ROLES = ("agent", "customer", "unknown")
DEFAULT_TONE = "neutral"

# Words → timeline level. polite and neutral both draw green: a neutral informational line
# is fine wording, and painting it amber would make every call look half-suspicious.
TEXT_LEVEL = {"polite": "good", "neutral": "good", "curt": "mid",
              "impolite": "bad", "rude": "bad", "aggressive": "bad"}

# Sidecar label → timeline level. sad is the one "mid": low arousal, not hostility.
# "unknown" is the sidecar's word for a slice too short to classify — it is NOT drawn and
# is left out of the per-speaker shares, otherwise a call of two-word turns would come out
# 90 % "unknown" and every verdict below would be meaningless.
VOICE_LEVEL = {"angry": "bad", "frustrated": "bad", "disgusted": "bad", "fearful": "bad",
               "sad": "mid",
               "neutral": "good", "calm": "good", "happy": "good", "excited": "good",
               "surprised": "good", "other": "good",
               "unknown": "none"}

# Per-speaker verdict thresholds (share of classified segments).
AGGRESSIVE_SHARE = 0.30   # this much of the talk was hostile → "aggressive"
PATIENT_SHARE = 0.80      # this much good, and NO bad at all → "patient"

# Above this many segments the transcript is prompted in merged form (§6 cap): the model's
# per-line answers would otherwise outgrow the output budget on a long Georgian call.
PROMPT_SEGMENT_CAP = 160

# Output budget for the tone call, with the arithmetic that fixes it. Per listed line the
# model returns `{i, tone, note}`: ~11 tokens of JSON scaffolding plus the note itself, and a
# Georgian character costs ~2 tokens (`llm.estimate_tokens`). Two speakers with rationales and
# flags plus the summary are ~600 more. So 160 lines with a 24-character note is ~9.5k and
# with a 50-character note ~19k — over the old 8192 exactly on the heated call this feature
# exists for, and truncation loses the whole text half (a 502).
#
# PROMPT_SEGMENT_CAP does NOT bound this: it merges only same-speaker neighbours, so a call
# whose speakers alternate every line is prompted uncapped. Hence a budget with real headroom
# AND the shrink-and-retry in `_text_tone` for the case that still overflows it.
TONE_MAX_TOKENS = 16_384

TONE_TOOL = {
    "name": "submit_tone",
    "description": "Return the tone of the wording, per transcript line and per speaker.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "segments": {
                "type": "array",
                "description": "Only the lines whose wording is NOT neutral. Every line not "
                               "listed here is taken as neutral.",
                "items": {
                    "type": "object",
                    "properties": {
                        "i": {"type": "integer",
                              "description": "The # number of the transcript line, as printed."},
                        "tone": {"type": "string", "enum": list(TONES)},
                        "note": {"type": "string",
                                 "description": "One short phrase naming what in the wording "
                                                "signals this tone."},
                    },
                    "required": ["i", "tone", "note"],
                    "additionalProperties": False,
                },
            },
            "speakers": {
                "type": "array",
                "description": "One entry per speaker id that appears in the transcript.",
                "items": {
                    "type": "object",
                    "properties": {
                        "speaker": {"type": "string",
                                    "description": "The speaker id exactly as printed, e.g. speaker_0."},
                        "role": {"type": "string", "enum": list(ROLES)},
                        "politeness": {"type": "integer",
                                       "description": "0-100; 100 = consistently courteous wording."},
                        "overall": {"type": "string", "enum": list(TONES)},
                        "flags": {"type": "array", "items": {"type": "string"},
                                  "description": "Short labels for specific problems in this "
                                                 "speaker's wording (e.g. 'blames the customer'). "
                                                 "Empty when there are none."},
                        "rationale": {"type": "string",
                                      "description": "One sentence justifying the overall tone."},
                    },
                    "required": ["speaker", "role", "politeness", "overall", "flags", "rationale"],
                    "additionalProperties": False,
                },
            },
            "summary": {"type": "string",
                        "description": "Two sentences on the tone of the conversation as a whole."},
        },
        "required": ["segments", "speakers", "summary"],
        "additionalProperties": False,
    },
}

_TONE_SYSTEM = """\
You assess the tone of a customer-support conversation from its transcript — the WORDS ONLY. \
Tone of voice (pitch, loudness, pace) is measured separately from the audio and is not your \
job: judge what was said, never how it may have sounded.

Each transcript line is printed as [#N time speaker] text. Rate the wording of each line:
- polite: courteous wording — thanks, apologies, offers to help, patient explanations
- neutral: plain informational wording with no politeness marker either way
- curt: abrupt or clipped, borderline dismissive, but not offensive
- impolite: disrespectful, patronising, blaming, talking over or ignoring the other person
- rude: insulting, mocking, contemptuous
- aggressive: threats, intimidation, swearing AT the other person, shouting-style wording

List ONLY the lines whose tone is not neutral, citing each by its # number exactly as \
printed; every line you leave out is taken as neutral. Keep each note to one short phrase.

Then judge every speaker id that appears: their role (agent = the support operator, \
customer, or unknown), a politeness score 0-100 (100 = consistently courteous wording), \
the overall tone word, short flags naming specific problems (or none), and a one-sentence \
rationale. Finish with a two-sentence summary of the conversation's tone.

Write notes, flags, rationales and the summary in the SAME language as the transcript. \
Directness that is normal in the transcript's language (e.g. Georgian or Russian \
imperatives) is not rudeness by itself; judge disrespect, not brevity."""


# --------------------------------------------------------------------------- #
# Small coercions — the model's answer and the sidecar's answer are both untrusted shapes
# --------------------------------------------------------------------------- #
def _index(value) -> int | None:
    """int for 3, 3.0, "3"; None for bools, 3.5, "x", None."""
    if value is None or isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return int(f) if math.isfinite(f) and f == int(f) else None


def _clamp01(value) -> float | None:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return None


def _clamp_int(value, lo: int, hi: int) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return max(lo, min(hi, int(round(float(value)))))
    except (TypeError, ValueError):
        return None


def _str(value) -> str:
    return " ".join(str(value).split()) if value is not None else ""


def _str_list(value) -> list[str]:
    """Clean list of non-empty strings from whatever the model put in an array slot."""
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
        s = " — ".join(_str(v) for v in item.values() if v not in (None, "")) \
            if isinstance(item, dict) else _str(item)
        if s:
            out.append(s)
    return out


def _enum(value, allowed: tuple, default: str) -> str:
    v = _str(value).lower()
    return v if v in allowed else default


_SPEAKER_JUNK = re.compile(r"[\s\-]+")


def _speaker_key(name) -> str:
    """Match the model's spelling of a speaker id back to ours: `Speaker 0` → `speaker_0`."""
    return _SPEAKER_JUNK.sub("_", _str(name).lower()).strip("_")


def _as_segments(segments, transcript: str) -> list[dict]:
    """The rows the analysis is over: the stored segments, or a text fallback when a caller
    only has a transcript (legacy paths). Positions are re-numbered densely so a stale `i`
    on a stored row can never disagree with the `#` the model is shown."""
    rows = [s for s in segments if isinstance(s, dict)] if isinstance(segments, (list, tuple)) else []
    if not rows:
        rows = segments_from_text(transcript or "")
    return [{"i": pos, "speaker": _str(s.get("speaker")) or "speaker_0",
             "start": s.get("start"), "end": s.get("end"), "text": _str(s.get("text"))}
            for pos, s in enumerate(rows)]


# --------------------------------------------------------------------------- #
# Level + verdict rules (pure — the tests pin these)
# --------------------------------------------------------------------------- #
def text_level(tone) -> str:
    return TEXT_LEVEL.get(_str(tone).lower(), TEXT_LEVEL[DEFAULT_TONE])


def voice_level(label) -> str:
    """Any label the checkpoint invents that is not in the map counts as "other" → good,
    per the contract's bucket for everything that is neither hostile nor sad."""
    return VOICE_LEVEL.get(_str(label).lower(), "good")


def voice_verdict(share_bad: float, share_good: float, classified: int) -> str:
    """One word for a speaker's voice over the call, from the share of their classified
    segments (unknown ones excluded) that were hostile / fine."""
    if classified <= 0:
        return "unknown"
    if share_bad >= AGGRESSIVE_SHARE:
        return "aggressive"
    if share_bad > 0:
        return "tense"
    if share_good >= PATIENT_SHARE:
        return "patient"
    return "calm"


def speaker_voice(levels: list[str]) -> dict:
    """`{"voice", "share_bad", "share_good"}` for one speaker from its segments' voice levels."""
    counted = [lv for lv in levels if lv in ("good", "mid", "bad")]
    n = len(counted)
    share_bad = counted.count("bad") / n if n else 0.0
    share_good = counted.count("good") / n if n else 0.0
    return {"voice": voice_verdict(share_bad, share_good, n),
            "share_bad": round(share_bad, 3), "share_good": round(share_good, 3)}


# --------------------------------------------------------------------------- #
# Prompt-size cap: merge same-speaker neighbours, remember what each merged line covers
# --------------------------------------------------------------------------- #
def _group_runs(segments: list[dict], k: int) -> list[list[int]]:
    """Positions grouped into runs of at most `k` consecutive same-speaker segments."""
    groups: list[list[int]] = []
    current: list[int] = []
    for pos, seg in enumerate(segments):
        if current and (seg["speaker"] != segments[current[-1]]["speaker"] or len(current) >= k):
            groups.append(current)
            current = []
        current.append(pos)
    if current:
        groups.append(current)
    return groups


def merge_for_prompt(segments: list[dict], cap: int = PROMPT_SEGMENT_CAP) -> tuple[list[dict], list[list[int]]]:
    """The segments to prompt with, and for each one the ORIGINAL positions it covers.

    Under the cap nothing changes (`groups[i] == [i]`). Over it, neighbouring segments of the
    same speaker are merged in runs of up to `k`, with `k` the smallest value that brings the
    count under the cap — so a 161-segment call merges pairs, not everything a speaker said
    in a row. Only same-speaker neighbours ever merge: a tone judgment must never straddle
    two talkers. If the speakers alternate on every line the cap cannot be met and the call
    is prompted as-is (the output budget, not the prompt, is the real limit).
    """
    n = len(segments)
    if n <= cap:
        return list(segments), [[i] for i in range(n)]
    groups = _group_runs(segments, 1)
    longest_run = max(len(g) for g in _group_runs(segments, n)) if n else 1
    k = 2
    while len(groups) > cap and k <= longest_run:
        groups = _group_runs(segments, k)
        k += 1
    merged = []
    for gi, group in enumerate(groups):
        starts = [segments[p]["start"] for p in group if segments[p]["start"] is not None]
        ends = [segments[p]["end"] for p in group if segments[p]["end"] is not None]
        merged.append({"i": gi, "speaker": segments[group[0]]["speaker"],
                       "start": starts[0] if starts else None,
                       "end": ends[-1] if ends else None,
                       "text": " ".join(segments[p]["text"] for p in group if segments[p]["text"])})
    return merged, groups


# --------------------------------------------------------------------------- #
# Text tone — one forced-tool call
# --------------------------------------------------------------------------- #
def _tone_prompt(prompt_segments: list[dict]) -> str:
    speakers = []
    for seg in prompt_segments:
        if seg["speaker"] not in speakers:
            speakers.append(seg["speaker"])
    return (f"Speaker ids in this transcript: {', '.join(speakers)}\n\n"
            f"<transcript>\n{render_timeline(prompt_segments)}\n</transcript>")


def _normalise_tone(raw: dict, groups: list[list[int]], n: int) -> tuple[dict, dict, str]:
    """(per-position {tone, note}, per-speaker-key text verdict, summary) from the model's
    answer. Unknown tones → neutral, indices outside the prompt → dropped, a cited merged
    line fans out to every original position it covered."""
    by_pos: dict[int, dict] = {}
    for item in (raw.get("segments") if isinstance(raw.get("segments"), list) else []):
        if not isinstance(item, dict):
            continue
        gi = _index(item.get("i"))
        if gi is None or not 0 <= gi < len(groups):
            continue
        tone = _enum(item.get("tone"), TONES, DEFAULT_TONE)
        note = _str(item.get("note"))
        for pos in groups[gi]:
            if 0 <= pos < n:
                by_pos[pos] = {"tone": tone, "note": note}

    by_speaker: dict[str, dict] = {}
    for item in (raw.get("speakers") if isinstance(raw.get("speakers"), list) else []):
        if not isinstance(item, dict):
            continue
        key = _speaker_key(item.get("speaker"))
        if not key:
            continue
        by_speaker[key] = {
            "role": _enum(item.get("role"), ROLES, "unknown"),
            "politeness": _clamp_int(item.get("politeness"), 0, 100),
            "overall": _enum(item.get("overall"), TONES, DEFAULT_TONE),
            "flags": _str_list(item.get("flags")),
            "rationale": _str(item.get("rationale")),
        }
    return by_pos, by_speaker, _str(raw.get("summary"))


async def _text_tone(segments: list[dict], *, api_key: str, model: str, guidance: str,
                     client_id: str | None) -> tuple[dict, dict, str]:
    """The words judged, once — or twice on a call whose answer does not fit the budget.

    A truncated answer is deterministic: the same prompt asks for the same too-long list every
    time, so a plain retry would burn a second call and fail identically. The retry therefore
    halves the addressable line count (`merge_for_prompt(cap=TONE_MAX_TOKENS's half-cap)`),
    which halves the number of items the model can return — coarser highlights, but a result
    instead of a 502 on the very call (long, heated, Georgian) the feature is for. When the
    prompt cannot actually shrink — few segments, or speakers alternating so nothing merges —
    there is nothing to retry with and the error is raised straight away.
    """
    if not api_key:
        raise SemanticError("Anthropic API key is not configured")
    system = _TONE_SYSTEM
    if (guidance or "").strip():
        system += f"\n\nAdditional guidance from the operator:\n{guidance.strip()}"

    async def submit(prompt_segments: list[dict]) -> dict:
        # stream=True: the transport that survives a long answer (llm.call_tool's docstring);
        # the result is the same dict a blocking call would return.
        return await llm.call_tool(
            feature="semantic_text", client_id=client_id, api_key=api_key, model=model,
            system=system, user=_tone_prompt(prompt_segments), tool=TONE_TOOL,
            opts=llm.ANALYSIS, max_tokens=TONE_MAX_TOKENS, stream=True)

    prompt_segments, groups = merge_for_prompt(segments)
    try:
        raw = await submit(prompt_segments)
    except llm.LLMTruncatedError as exc:
        shorter_segments, shorter_groups = merge_for_prompt(segments, cap=PROMPT_SEGMENT_CAP // 2)
        if len(shorter_groups) >= len(groups):
            raise SemanticError(f"Tone analysis failed: {exc}") from exc
        log.warning("tone answer hit the %d-token budget; retrying with %d merged lines "
                    "instead of %d", TONE_MAX_TOKENS, len(shorter_groups), len(groups))
        try:
            raw = await submit(shorter_segments)
        except llm.LLMError as retry_exc:
            raise SemanticError(f"Tone analysis failed: {retry_exc}") from retry_exc
        groups = shorter_groups
    except llm.LLMError as exc:
        raise SemanticError(f"Tone analysis failed: {exc}") from exc
    if not isinstance(raw, dict):
        raise SemanticError("Tone analysis returned no result")
    return _normalise_tone(raw, groups, len(segments))


# --------------------------------------------------------------------------- #
# Voice tone — the sidecar, per segment
# --------------------------------------------------------------------------- #
async def _voice_tone(segments: list[dict], audio: bytes, filename, content_type) -> dict | None:
    """Per-position `{label, confidence}` from the sidecar, or None when it is unavailable.
    Only segments that carry both times are sent; the rest simply get no voice reading."""
    ranges = [{"i": s["i"], "start": s["start"], "end": s["end"]}
              for s in segments if s["start"] is not None and s["end"] is not None]
    if not ranges:
        return None
    items = await sentiment.prosody_segments(audio, ranges, filename, content_type)
    if items is None:
        return None
    by_pos: dict[int, dict] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        pos = _index(item.get("i"))
        if pos is None or not 0 <= pos < len(segments):
            continue
        by_pos[pos] = {"label": _str(item.get("label")).lower() or "unknown",
                       "confidence": item.get("confidence")}
    return by_pos


# --------------------------------------------------------------------------- #
# Spans — runs of neighbouring segments with the same tone become one coloured block
# --------------------------------------------------------------------------- #
def _spans(segments: list[dict], keyed: list[tuple[str | None, str, str]]) -> list[dict]:
    """`keyed[pos] = (label, level, detail)`; a None label means "nothing to draw here".
    Neighbouring positions with the same label merge into one span whose detail joins the
    non-empty details, so a run of thirty neutral lines is one green block, not thirty."""
    spans: list[dict] = []
    run: list[int] = []
    details: list[str] = []
    current: tuple[str, str] | None = None

    def flush():
        if run:
            detail = " · ".join(d for d in details if d)
            spans.extend(spans_from_indices(segments, run, level=current[1], score=None,
                                            label=current[0], detail=detail))

    for pos, (label, level, detail) in enumerate(keyed):
        key = None if label is None else (label, level)
        if key != current or not run or pos != run[-1] + 1:
            flush()
            run, details, current = [], [], key
        if key is not None:
            run.append(pos)
            details.append(detail)
    flush()
    return spans


def _voice_detail(label: str, confidence) -> str:
    try:
        return f"{label} · {round(float(confidence) * 100)}%"
    except (TypeError, ValueError):
        return label


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
async def analyse(*, segments, transcript, audio: bytes | None, filename, content_type,
                  modes: set[str], api_key: str, model: str, guidance: str = "",
                  client_id: str | None = None, user_id: str | None = None,
                  language: str | None = None) -> dict:
    """Run the requested judges over one recording and return the §6 record.

    `modes` ⊆ {"text", "voice"}. "voice" needs audio and is dropped silently without it
    (text sources); an empty/unknown set falls back to "text", the one judge that always
    applies. Text and voice run concurrently. A text failure raises `SemanticError` — the
    caller asked for a judgment and got none; a voice failure only clears
    `voice_available`. `user_id` is accepted for parity with the other analysers: the LLM
    usage ledger keys on `client_id`, and nothing here is persisted.
    """
    rows = _as_segments(segments, transcript)
    wanted = {m for m in (modes or ()) if m in ("text", "voice")}
    if "voice" in wanted and not audio:
        wanted.discard("voice")
    if not wanted:
        wanted = {"text"}

    text_task = _text_tone(rows, api_key=api_key, model=model, guidance=guidance or "",
                           client_id=client_id) if "text" in wanted and rows else _none()
    voice_task = _voice_tone(rows, audio, filename, content_type) if "voice" in wanted else _none()
    text_res, voice_by_pos = await asyncio.gather(text_task, voice_task)

    tone_by_pos, text_by_speaker, summary = text_res if text_res else ({}, {}, "")
    text_ran = "text" in wanted and text_res is not None
    voice_ran = "voice" in wanted and voice_by_pos is not None

    speakers: list[str] = []
    for seg in rows:
        if seg["speaker"] not in speakers:
            speakers.append(seg["speaker"])

    out_segments: list[dict] = []
    text_keyed: list[tuple[str | None, str, str]] = []
    voice_keyed: list[tuple[str | None, str, str]] = []
    voice_levels: dict[str, list[str]] = {s: [] for s in speakers}
    for seg in rows:
        pos = seg["i"]
        row = {"i": pos, "speaker": seg["speaker"], "start": seg["start"], "end": seg["end"],
               "text_tone": None, "text_level": None, "text_note": None,
               "voice_label": None, "voice_level": None, "voice_confidence": None}
        if text_ran:
            t = tone_by_pos.get(pos, {"tone": DEFAULT_TONE, "note": ""})
            row.update(text_tone=t["tone"], text_level=text_level(t["tone"]), text_note=t["note"])
            text_keyed.append((t["tone"], row["text_level"], t["note"]))
        else:
            text_keyed.append((None, "none", ""))
        if voice_ran:
            v = voice_by_pos.get(pos)
            label = v["label"] if v else "unknown"
            level = voice_level(label)
            conf = _clamp01(v.get("confidence")) if v else None
            row.update(voice_label=label, voice_level=level, voice_confidence=conf)
            voice_levels[seg["speaker"]].append(level)
            voice_keyed.append((None, level, "") if level == "none"
                               else (label, level, _voice_detail(label, conf)))
        else:
            voice_keyed.append((None, "none", ""))
        out_segments.append(row)

    out_speakers = []
    for name in speakers:
        judged = text_by_speaker.get(_speaker_key(name)) if text_ran else None
        out_speakers.append({
            "speaker": name,
            "role": judged["role"] if judged else "unknown",
            "text": {k: v for k, v in judged.items() if k != "role"} if judged else None,
            "voice": speaker_voice(voice_levels[name]) if voice_ran else None,
        })

    return {
        "modes": sorted(wanted),
        "language": language,
        "speakers": out_speakers,
        "segments": out_segments,
        "spans": {"text": _spans(rows, text_keyed) if text_ran else [],
                  "voice": _spans(rows, voice_keyed) if voice_ran else []},
        "summary": summary,
        "voice_available": voice_ran,
    }


async def _none():
    return None
