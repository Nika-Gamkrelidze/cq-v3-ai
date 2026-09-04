"""Rubric scoring of a call transcript against a weighted, owner-defined rubric.

The rubric's owner (a tenant, or a registered user with a personal rubric) defines the
dimensions (name, weight, guidance) + an overall rubric. Claude scores each dimension 0-100
with a rationale and verbatim transcript evidence (forced tool-use, strict schema). CODE then
applies the weights to compute the weighted total — deterministic, auditable, and
re-weightable without a new LLM call. Nothing here is hardcoded to an industry; dimensions
are fully user-defined.

v2 — evidence lands on the timeline. The model is prompted with `segments.render_timeline()`
(one `[#i mm:ss.s-mm:ss.s speaker] text` line per segment) and every evidence quote carries
the `#` indices it comes from; code turns those into seconds (`spans_from_indices`) and into
one lane of coloured spans per dimension, the shape §3 of the design contract says the player
consumes. The model is never asked for seconds: it invents them.

Scoped by the caller: it passes in only the owner's active config and its own `client_id` /
`user_id`. Works across Georgian / Russian / English (the model scores meaning regardless of
language).
"""
import logging

from . import llm
from .segments import render_timeline, segments_from_text, spans_from_indices

log = logging.getLogger("cq")

MAX_DIMENSIONS = 30

# §3 level thresholds for a 0-100 score. Chosen once here so the lane colour, the evidence
# chip and any later renderer agree on where "good" ends.
GOOD_MIN = 70
MID_MIN = 40

# Output budget, sized from the work like `scoring_import` does — the answer grows with the
# rubric, so a single constant cannot fit both a 4-dimension default and a 30-dimension
# imported scorecard. One dimension comes back as a score, one or two sentences of rationale
# IN THE TRANSCRIPT'S LANGUAGE and a couple of {quote, segments} objects; measured with
# `llm.estimate_tokens` (Georgian = 2 tokens/char) that is ~500 tokens, against ~95 in
# English. The old default of 4096 therefore truncated a Georgian rubric from ~8 dimensions
# up — LLMTruncatedError, which the workbench's Score button shows as a 502.
BASE_OUTPUT_TOKENS = 1_000
PER_DIMENSION_TOKENS = 600     # the ~500 above, rounded up for a wordy model
MAX_OUTPUT_TOKENS = 32_000     # same ceiling the rubric import uses


def output_budget(dim_count: int) -> int:
    """max_tokens for a rubric of `dim_count` dimensions."""
    return min(MAX_OUTPUT_TOKENS, BASE_OUTPUT_TOKENS + PER_DIMENSION_TOKENS * max(dim_count, 1))


class ScoringError(RuntimeError):
    pass


SCORE_TOOL = {
    "name": "submit_scores",
    "description": "Return a 0-100 score with rationale and evidence for each rubric dimension.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "operator_speaker": {
                "type": "string",
                "description": "Which speaker label in the transcript is the support agent/operator "
                               "being evaluated (e.g. 'speaker_0'), or 'unknown'.",
            },
            "scores": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string", "description": "The dimension key being scored (from the rubric)."},
                        "score": {"type": "integer", "description": "Score for this dimension, 0-100."},
                        "rationale": {"type": "string", "description": "One or two sentences justifying the score."},
                        "evidence": {
                            "type": "array",
                            "description": "Short verbatim quotes from the transcript that justify the score, "
                                           "each with the # indices of the transcript lines it comes from.",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "quote": {"type": "string",
                                              "description": "A short quote copied verbatim from the transcript."},
                                    "segments": {
                                        "type": "array",
                                        "items": {"type": "integer"},
                                        "description": "The # indices of the transcript lines the quote comes from.",
                                    },
                                },
                                "required": ["quote", "segments"],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "required": ["key", "score", "rationale", "evidence"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["operator_speaker", "scores"],
        "additionalProperties": False,
    },
}


def _as_str_list(value) -> list[str]:
    """Coerce whatever the model returned into a clean list of non-empty strings."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, dict):
        value = list(value.values())
    if isinstance(value, (list, tuple)):
        out = []
        for item in value:
            if item is None:
                continue
            s = " — ".join(str(v).strip() for v in item.values() if v not in (None, "")) \
                if isinstance(item, dict) else str(item).strip()
            if s:
                out.append(s)
        return out
    return [str(value).strip()]


def normalize_dimensions(dimensions) -> list[dict]:
    """Clean a config's dimension list: valid key/name, non-negative weight, string guidance."""
    out, seen = [], set()
    for i, d in enumerate(dimensions or []):
        if not isinstance(d, dict):
            continue
        name = str(d.get("name") or "").strip()
        key = str(d.get("key") or "").strip() or _slug(name) or f"dim{i+1}"
        if not name or key in seen:
            if not name:
                continue
            key = f"{key}_{i}"
        seen.add(key)
        try:
            weight = float(d.get("weight"))
        except (TypeError, ValueError):
            weight = 0.0
        out.append({
            "key": key,
            "name": name,
            "description": str(d.get("description") or "").strip(),
            "guidance": str(d.get("guidance") or "").strip(),
            "weight": max(0.0, weight),
        })
        if len(out) >= MAX_DIMENSIONS:
            break
    return out


def _slug(name: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in (name or "").lower()).strip("_")[:40]


def _build_system(config: dict, dims: list[dict]) -> str:
    lines = [
        "You are a quality-assurance evaluator for customer-support calls. Score the OPERATOR "
        "(the support agent, not the customer) against the rubric below. For each dimension give "
        "an integer 0-100 (0 = failed entirely, 100 = excellent), a short rationale, and verbatim "
        "quotes from the transcript as evidence. Judge meaning even if the transcript is in "
        "Georgian, Russian, or English. Be fair and consistent; base scores only on the transcript. "
        "Write each rationale in the SAME language as the transcript; keep the evidence quotes verbatim.",
        "\nThe transcript is a numbered timeline: one line per speaker turn, formatted "
        "`[#index start-end speaker] text` (or `[#index speaker] text` when there are no "
        "timestamps). For every evidence quote, list in `segments` the `#` index numbers of the "
        "line(s) the quote is copied from — never invent timestamps or indices that are not in "
        "the transcript. Name `operator_speaker` with the speaker label exactly as it appears "
        "in the transcript.",
    ]
    rubric = str(config.get("rubric") or "").strip()
    if rubric:
        lines.append("\nOverall rubric / guidance from the client:\n" + rubric)
    lines.append("\nDimensions to score (use the exact key):")
    for d in dims:
        g = f" — {d['guidance']}" if d["guidance"] else (f" — {d['description']}" if d["description"] else "")
        lines.append(f"  • key='{d['key']}' \"{d['name']}\" (weight {d['weight']:g}){g}")
    return "\n".join(lines)


def _timeline_for(transcript: str, segments) -> tuple[list[dict], str]:
    """The segments the model is prompted with and their rendering.

    Timed segments come from the caller (Scribe words already grouped); without them — the
    legacy `/analyze` pipeline, the score-text playground, the probe — the transcript's own
    lines become untimed segments so the model still cites `#` indices and the UI can still
    highlight the transcript. A segment list that renders to nothing (a jsonb column full of
    garbage) falls back the same way rather than prompting with an empty transcript.
    """
    segs = list(segments) if segments else []
    timeline = render_timeline(segs)
    if not timeline.strip():
        segs = segments_from_text(transcript)
        timeline = render_timeline(segs)
    return segs, timeline


async def run_scoring(transcript: str, config: dict, api_key: str, model: str,
                      client_id: str | None = None, segments: list[dict] | None = None,
                      user_id: str | None = None) -> dict | None:
    """Score the transcript against the owner's rubric. Returns None if nothing to score.

    `segments` (§2) places the evidence on the player's timeline; `segments=None` keeps the
    pre-v2 callers working unchanged. `user_id` names a registered user's personal rubric run
    for the log — `llm.call_tool` records usage by `client_id` only.
    """
    if not (transcript or "").strip() or not api_key or not config:
        return None
    dims = normalize_dimensions(config.get("dimensions"))
    if not dims:
        return None

    segs, timeline = _timeline_for(transcript, segments)
    system = _build_system(config, dims)
    try:
        # stream=True for the same reason the KB imports stream: llm.ANALYSIS budgets 60 s of
        # READ, and a non-streamed answer of thousands of Georgian tokens outlives that (and
        # is exactly the long request Anthropic drops). Streaming makes the budget per chunk.
        raw = await llm.call_tool(
            feature="scoring", client_id=client_id, api_key=api_key, model=model,
            system=system, user=f"<transcript>\n{timeline}\n</transcript>",
            tool=SCORE_TOOL, opts=llm.ANALYSIS,
            max_tokens=output_budget(len(dims)), stream=True)
    except llm.LLMError as exc:
        raise ScoringError(f"Scoring request failed: {exc}") from exc

    by_key = {}
    for s in (raw.get("scores") or []):
        if isinstance(s, dict) and s.get("key") is not None:
            by_key[str(s["key"]).strip()] = s

    log.info("scoring client=%s user=%s dims=%d scored=%d segments=%d",
             client_id, user_id, len(dims), len(by_key), len(segs))
    return build_result(dims, by_key, config.get("version"),
                        str(raw.get("operator_speaker") or "unknown").strip() or "unknown",
                        segments=segs)


def _level(score: int | None) -> str:
    """§3 level for a dimension score; an unscored dimension is grey, not red."""
    if score is None:
        return "none"
    return "good" if score >= GOOD_MIN else "mid" if score >= MID_MIN else "bad"


def _quote(value) -> str:
    return " ".join(str(value).split()) if value is not None else ""


def _evidence_item(item, segments) -> dict | None:
    """One model evidence entry → `{"quote", "segments", "start", "end"}`, or None to drop it.

    The strict schema yields `{"quote", "segments"}` objects, but a plain string is accepted
    too (the pre-v2 shape, still what an old stored result or a lenient model returns) and
    becomes an unplaced quote. Cited indices are validated against `segments` the same way
    fact-check does it: sorted, deduped, out-of-range dropped; `start`/`end` are the first
    cited run's bounds, so clicking the quote seeks to where it begins.
    """
    if isinstance(item, dict):
        quote, cited = _quote(item.get("quote")), item.get("segments")
    elif item is None:
        return None
    else:
        quote, cited = _quote(item), None
    spans = spans_from_indices(segments, cited) if cited else []
    indices = [i for span in spans for i in span["segments"]]
    if not quote and not indices:
        return None
    first = spans[0] if spans else {}
    return {"quote": quote, "segments": indices,
            "start": first.get("start"), "end": first.get("end")}


def _evidence(value, segments) -> list[dict]:
    """Every evidence entry normalised; a lone string or dict is treated as one entry."""
    items = value if isinstance(value, (list, tuple)) else [value]
    return [e for e in (_evidence_item(item, segments) for item in items) if e is not None]


def _dimension_spans(d: dict, score: int | None, evidence: list[dict], segments) -> list[dict]:
    """The dimension's lane: one §3 span per cited run of adjacent segments. `score` is set
    so the UI colours by its gradient; `level` is there for renderers that only know levels."""
    extra = {"level": _level(score), "score": score, "label": d["name"]}
    return [span for e in evidence
            for span in spans_from_indices(segments, e["segments"], detail=e["quote"], **extra)]


def apply_manual_scores(result: dict, edits: dict, *, edited_by: str) -> dict:
    """A reviewer's own scores over the model's scorecard, with the totals recomputed in code.

    `edits` is {dimension key: score 0-100}. Only the numbers move: the model's rationale and
    evidence stay attached to the dimension, because they are what the reviewer was reading
    when they disagreed, and deleting them would hide the disagreement. An edited dimension is
    marked so the UI can show which numbers are a person's and which are the model's.

    The weighted total is recomputed HERE rather than trusted from the client, for the same
    reason `build_result` does it: the model (and now the browser) proposes per-dimension
    judgements, and code alone owns the arithmetic that turns them into a total someone is
    assessed on.
    """
    dims = [dict(d) for d in (result.get("dimensions") or []) if isinstance(d, dict)]
    # Stored weights are already percentages summing to ~100; falling back to an equal split
    # mirrors build_result's own rule for a rubric whose weights are all zero.
    total_weight = sum(float(d.get("weight") or 0) for d in dims) or float(len(dims) or 1)
    weighted_total = 0.0
    for d in dims:
        key = str(d.get("key"))
        if key in edits:
            new = edits[key]
            if new is not None and int(new) != (d.get("score") if isinstance(d.get("score"), int) else None):
                d["edited"] = True
                d["ai_score"] = d.get("ai_score", d.get("score"))
            d["score"] = None if new is None else max(0, min(100, int(new)))
        w = float(d.get("weight") or 0) or (1.0 if total_weight == len(dims) else 0.0)
        score = d.get("score")
        d["contribution"] = round((score or 0) * w / total_weight, 1)
        if score is not None:
            weighted_total += score * w / total_weight
    return {**result, "dimensions": dims,
            "weighted_total": round(weighted_total, 1),
            "edited_by": edited_by, "manually_edited": True}


def build_result(dims: list[dict], by_key: dict, version, operator_speaker: str,
                 segments: list[dict] | None = None) -> dict:
    """Apply weights in code → per-dimension contribution + weighted total (0-100), plus the
    evidence placed on `segments` and one timeline lane per dimension.

    Without `segments` there is no coordinate system to resolve cited `#` indices against, so
    every citation is dropped (an unverifiable index is as useless to the UI as an
    out-of-range one) and the quotes are kept as unplaced evidence.
    """
    segments = segments or []
    total_weight = sum(d["weight"] for d in dims) or float(len(dims))  # equal weights if all 0
    out_dims, lanes, weighted_total = [], [], 0.0
    for d in dims:
        raw = by_key.get(d["key"], {})
        try:
            score = int(round(float(raw.get("score"))))
        except (TypeError, ValueError):
            score = None
        score = None if score is None else max(0, min(100, score))
        w = d["weight"] if any(x["weight"] for x in dims) else 1.0
        weight_pct = round(100 * w / total_weight, 1)
        contribution = round((score or 0) * w / total_weight, 1)
        if score is not None:
            weighted_total += (score * w / total_weight)
        evidence = _evidence(raw.get("evidence"), segments)
        spans = _dimension_spans(d, score, evidence, segments)
        out_dims.append({
            "key": d["key"], "name": d["name"], "weight": weight_pct,
            "score": score, "max": 100, "contribution": contribution,
            "rationale": str(raw.get("rationale") or "").strip(),
            "evidence": evidence,
            "spans": spans,
        })
        lanes.append({"key": d["key"], "name": d["name"], "score": score, "spans": spans})
    return {
        "config_version": version,
        "operator_speaker": operator_speaker,
        "dimensions": out_dims,
        "weighted_total": round(weighted_total, 1),
        "max_total": 100,
        "lanes": lanes,
    }


def evidence_text(dim) -> list[str]:
    """The dimension's evidence as plain quote strings, for renderers written against the
    pre-v2 result shape. Accepts a dimension dict (reads its `evidence`) or the evidence list
    itself, in either the object or the legacy plain-string form."""
    items = dim.get("evidence") if isinstance(dim, dict) else dim
    if not isinstance(items, (list, tuple)):
        items = [items]
    quotes = (_quote(e.get("quote")) if isinstance(e, dict) else _quote(e)
              for e in items if e is not None)
    return [q for q in quotes if q]
