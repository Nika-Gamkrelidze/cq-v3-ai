"""Sentiment of a call: what was said, and how it sounded.

Two independent signals, deliberately kept separate in the output:

  * **Prosody** — pitch, energy, timing. Computed by the self-hosted `cq-sentiment` sidecar
    from the raw audio. Returns continuous arousal / valence / dominance (0..1) plus a
    discrete label. This is the half that hears a caller who is calm but furious, or polite
    words delivered through gritted teeth.
  * **Text** — what the words mean. Already produced by Claude during analysis, and already
    cross-lingual, which is why Georgian works here at all: almost every open speech-emotion
    model is trained on English/German corpora, while Claude reads Georgian directly.

They are reported side by side rather than averaged into one number, because when they
DISAGREE that is the finding, not noise: positive words in a negative voice is the signature
of a frustrated customer being handled politely, and collapsing it to a mean hides exactly
the call a QA reviewer wants to hear.

The sidecar is optional. If it is not deployed, or is slow, or falls over, this module returns
the text half alone and marks prosody unavailable — a missing tone model must never cost a
tenant their transcript.
"""
from __future__ import annotations

import asyncio
import logging

import httpx

from . import llm, settings_store

log = logging.getLogger("cq")

# Read is generous — a cold CPU forward pass on a few minutes of audio is genuinely slow.
# Connect is tight and separate: when the sidecar is simply absent (not deployed, wrong URL)
# the failure is DNS/connect, and without its own budget that cost lands on EVERY analysis.
_TIMEOUT = httpx.Timeout(30.0, connect=3.0)
_MAX_UPLOAD_BYTES = 25 * 1024 * 1024

# Discrete labels the sidecar may return, mapped to the coarse polarity the UI colours by.
_POLARITY = {
    "angry": "negative", "disgusted": "negative", "fearful": "negative", "sad": "negative",
    "frustrated": "negative", "happy": "positive", "excited": "positive", "surprised": "neutral",
    "neutral": "neutral", "calm": "positive", "other": "neutral", "unknown": "neutral",
    # "mixed" is the LLM sentiment judge's own label (positive and negative both present) —
    # coloured neutral rather than picked apart, since collapsing it either way is exactly
    # the kind of averaging this module otherwise tries hard to avoid.
    "mixed": "neutral",
}


def _clamp01(v) -> float | None:
    try:
        return max(0.0, min(1.0, float(v)))
    except (TypeError, ValueError):
        return None


async def prosody(audio: bytes, filename: str | None = None,
                  content_type: str | None = None) -> dict | None:
    """Acoustic emotion from the sidecar, or None when it is unavailable.

    Never raises: every failure mode (not configured, connection refused, timeout, garbage
    body) resolves to None so the caller can carry on with the text signal alone.
    """
    cfg = await settings_store.get_effective()
    url = (cfg.get("sentiment_url") or "").strip()
    if not url or not audio:
        return None
    if len(audio) > _MAX_UPLOAD_BYTES:
        # Prosody over a whole long call averages out to "neutral" anyway; the sidecar's own
        # windowing is the right place to handle length, not a 100 MB HTTP body.
        audio = audio[:_MAX_UPLOAD_BYTES]

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                url.rstrip("/") + "/prosody",
                files={"file": (filename or "audio", audio, content_type or "application/octet-stream")},
            )
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPError, ValueError, asyncio.TimeoutError) as exc:
        log.warning("prosody sentiment unavailable: %s", exc)
        return None
    except Exception:  # noqa: BLE001 — a tone model must never break the pipeline
        log.exception("prosody sentiment failed")
        return None

    if not isinstance(data, dict):
        return None
    label = str(data.get("label") or "unknown").lower()
    out = {
        "label": label,
        "polarity": _POLARITY.get(label, "neutral"),
        "arousal": _clamp01(data.get("arousal")),
        "valence": _clamp01(data.get("valence")),
        "dominance": _clamp01(data.get("dominance")),
        "confidence": _clamp01(data.get("confidence")),
        "model": data.get("model"),
    }
    scores = data.get("scores")
    if isinstance(scores, dict):
        out["scores"] = {str(k): _clamp01(v) for k, v in list(scores.items())[:12]}
    return out


def _text_part(analysis: dict | None) -> dict | None:
    """Lift the sentiment Claude already produced into the same shape as the prosody half."""
    if not isinstance(analysis, dict):
        return None
    raw = analysis.get("sentiment")
    if raw is None:
        return None
    label = str(raw).strip().lower() or "neutral"
    polarity = ("positive" if "posit" in label else
                "negative" if ("negat" in label or "mixed-neg" in label) else
                "neutral" if "neutr" in label else _POLARITY.get(label, "neutral"))
    return {"label": label, "polarity": polarity, "source": "llm"}


# --------------------------------------------------------------------------- #
# Standalone sentiment: a dedicated, cheap Claude call — NOT the full analysis pass.
#
# The bundled /analyze pipeline already produces a "sentiment" field as a side effect of the
# full structured analysis (see claude.ANALYSIS_TOOL), and `_text_part` above just lifts it —
# that path is unconfigured and unchanged by this module. The standalone Sentiment tab
# (public site, tenant/KB playground) has no analysis to piggyback on: it exists specifically
# so a caller can get JUST a sentiment read, without paying for or waiting on the rest. Its
# own forced-tool-use call is also the only place per-tenant/public "guidance" text can apply
# — there is no other Claude call in that path to steer.
# --------------------------------------------------------------------------- #
SENTIMENT_TOOL = {
    "name": "submit_sentiment",
    "description": "Return the sentiment of the speaker in this transcript.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "label": {
                "type": "string",
                "enum": ["positive", "neutral", "negative", "mixed"],
                "description": "Overall sentiment of the speaker.",
            },
            "rationale": {
                "type": "string",
                "description": "One short sentence on what in the text signals this.",
            },
        },
        "required": ["label", "rationale"],
        "additionalProperties": False,
    },
}

_SENTIMENT_SYSTEM = (
    "You judge the sentiment of a speaker from a transcript of their speech. Base the "
    "judgment strictly on the words — tone of voice is assessed separately from the audio "
    "and is not your job. Respond in the SAME language as the transcript for the rationale."
)


async def judge_text(transcript: str, *, api_key: str, model: str, guidance: str = "",
                     client_id: str | None = None) -> dict | None:
    """The text half of standalone sentiment: one small forced-tool-use call.

    Returns None on an empty transcript or any LLM failure — this must never be the reason a
    caller gets nothing back; `combine()` already handles a missing text half by falling back
    to prosody_only.
    """
    text = (transcript or "").strip()
    if not text or not api_key:
        return None
    system = _SENTIMENT_SYSTEM
    if guidance.strip():
        system += f"\n\nAdditional guidance from the operator:\n{guidance.strip()}"
    user = f"Transcript:\n\n<transcript>\n{text}\n</transcript>"
    try:
        raw = await llm.call_tool(
            feature="sentiment", client_id=client_id, api_key=api_key, model=model,
            system=system, user=user, tool=SENTIMENT_TOOL, opts=llm.ANSWER)
    except llm.LLMError as exc:
        log.warning("text sentiment judge failed: %s", exc)
        return None
    label = str(raw.get("label") or "neutral").lower()
    if label not in ("positive", "neutral", "negative", "mixed"):
        label = "neutral"
    # NOT _POLARITY.get(label, ...): that dict maps PROSODY labels (angry, happy, calm...) to
    # a polarity — its keys are acoustic emotions, not polarity words. This tool's own labels
    # ARE already the polarity (mixed being the one exception), so map them directly; running
    # "negative" through _POLARITY silently fell through to its "neutral" default every time.
    polarity = "neutral" if label in ("neutral", "mixed") else label
    return {"label": label, "polarity": polarity, "source": "llm",
            "rationale": raw.get("rationale")}


async def standalone(transcript: str, audio: bytes | None, *, api_key: str, model: str,
                     guidance: str = "", client_id: str | None = None,
                     filename: str | None = None, content_type: str | None = None) -> dict:
    """Full standalone-sentiment record: text judge + prosody, run concurrently."""
    text_task = judge_text(transcript, api_key=api_key, model=model, guidance=guidance,
                           client_id=client_id)
    pros_task = prosody(audio, filename, content_type) if audio else _none()
    text, pros = await asyncio.gather(text_task, pros_task)
    return combine(text, pros)


async def _none():
    return None


def combine(text: dict | None, pros: dict | None) -> dict:
    """Merge the two halves into the record stored on the job.

    `agreement` is the interesting field: 'conflict' means the words and the voice point
    opposite ways, which is the case a reviewer should listen to first.
    """
    overall = None
    agreement = "unknown"
    if text and pros:
        if text["polarity"] == pros["polarity"]:
            overall, agreement = text["polarity"], "agree"
        elif "neutral" in (text["polarity"], pros["polarity"]):
            # One side is uncommitted — take the side that actually has an opinion.
            overall = pros["polarity"] if text["polarity"] == "neutral" else text["polarity"]
            agreement = "partial"
        else:
            # Genuine disagreement. Trust the voice for the headline: how something was said
            # survives translation and politeness conventions better than what was said.
            overall, agreement = pros["polarity"], "conflict"
    elif text:
        overall, agreement = text["polarity"], "text_only"
    elif pros:
        overall, agreement = pros["polarity"], "prosody_only"

    return {"overall": overall, "agreement": agreement, "text": text, "prosody": pros}


async def analyse(audio: bytes | None, analysis: dict | None, *, filename: str | None = None,
                  content_type: str | None = None) -> dict:
    """Full sentiment record for one job. Safe to call unconditionally."""
    pros = await prosody(audio, filename, content_type) if audio else None
    return combine(_text_part(analysis), pros)
