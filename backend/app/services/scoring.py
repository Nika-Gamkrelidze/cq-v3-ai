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


# --------------------------------------------------------------------------- #
# System dimensions: scored by CODE from another analyser, weighted by the tenant
# --------------------------------------------------------------------------- #
# Two rubric criteria kept being written by hand as free-text dimensions the model judged from
# the transcript — "correctness of information" and "courtesy/empathy" — while this product
# already answers both with a purpose-built analyser that does it better:
#
#   kb_factcheck    the KB fact-check (`services/factcheck.py`) verifies each claim against the
#                   tenant's OWN knowledge base and returns `accuracy_score`. A model asked
#                   "was the information correct?" from the transcript alone cannot do this at
#                   all — it has no documents to check against.
#   agent_courtesy  the tone analyser (`services/semantic.py`) returns `politeness` 0-100 PER
#                   SPEAKER with a role. That matters: overall call sentiment is dominated by
#                   the CUSTOMER, so scoring an agent on it punishes them for handling an angry
#                   caller. This reads the agent's own wording.
#
# They are `source`-marked dimensions: excluded from the model's prompt (so no tokens are spent
# re-judging what is already measured), scored by `system_scores()` below, and weighted exactly
# like any other dimension. A tenant may set the WEIGHT and nothing else — the name and the
# meaning are the product's, because the number's provenance is the whole point.
SYSTEM_SOURCES = ("factcheck", "sentiment")

SYSTEM_DIMENSIONS = {
    "factcheck": {
        "key": "kb_factcheck",
        "name": "Knowledge Base fact check",
        "description": "How much of what the agent stated matches this workspace's knowledge base.",
        "guidance": "Scored from the fact-check, not from the transcript: the share of "
                    "checkable claims the knowledge base supports.",
    },
    "sentiment": {
        "key": "agent_courtesy",
        "name": "Courtesy & empathy",
        "description": "How courteous the agent's own wording was, independent of the customer's mood.",
        "guidance": "Scored from the tone analyser's per-speaker politeness for the agent, "
                    "so a difficult customer does not lower it.",
    },
}


def system_dimension(source: str, weight: float) -> dict:
    """A ready-to-store dimension row for one system source."""
    return {**SYSTEM_DIMENSIONS[source], "weight": max(0.0, float(weight)), "source": source}


def agent_politeness(semantic: dict | None) -> int | None:
    """The tone analyser's politeness for the AGENT, 0-100, or None.

    None whenever the analyser has not run, found no agent, or gave no number — never 0.
    A missing measurement is not a bad one, and `build_result` drops an unscored dimension out
    of the weighting rather than scoring it zero.
    """
    if not isinstance(semantic, dict):
        return None
    for sp in (semantic.get("speakers") or []):
        if not isinstance(sp, dict) or str(sp.get("role") or "").strip().lower() != "agent":
            continue
        try:
            return max(0, min(100, int(round(float(sp.get("politeness"))))))
        except (TypeError, ValueError):
            return None
    return None


def factcheck_accuracy(kb_check: dict | None) -> int | None:
    """The fact-check's accuracy score, 0-100, or None.

    Deliberately nullable in FOUR distinct cases, all of which mean "not measured" and none of
    which means "did badly": the tenant has no KB, the fact-check never ran, no checkable claim
    was extracted, or every claim came back NOT_IN_KB. `accuracy_score` already excludes
    NOT_IN_KB from its own denominator so an agent is not punished for gaps in the KB; scoring
    its absence as 0 here would reintroduce exactly that punishment one level up.
    """
    if not isinstance(kb_check, dict):
        return None
    score = kb_check.get("accuracy_score")
    if score is None:
        return None
    try:
        return max(0, min(100, int(round(float(score)))))
    except (TypeError, ValueError):
        return None


def system_scores(*, kb_check: dict | None = None, semantic: dict | None = None) -> dict:
    """`by_key` entries for the system dimensions, in the shape `build_result` consumes.

    A dimension whose signal is absent gets `score: None` and a rationale that says WHY in
    words — the scorecard shows "—" and the weighting drops it, so the total stays out of 100
    and remains comparable with a call where the signal was present.
    """
    acc = factcheck_accuracy(kb_check)
    pol = agent_politeness(semantic)
    return {
        SYSTEM_DIMENSIONS["factcheck"]["key"]: {
            "score": acc,
            "rationale": _factcheck_rationale(kb_check, acc),
            "evidence": [],
        },
        SYSTEM_DIMENSIONS["sentiment"]["key"]: {
            "score": pol,
            "rationale": ("Politeness of the agent's own wording, from the tone analyser."
                          if pol is not None else
                          "Not scored: the tone analyser has not run on this recording, or it "
                          "could not tell which speaker is the agent."),
            "evidence": [],
        },
    }


def _factcheck_rationale(kb_check: dict | None, score: int | None) -> str:
    if score is None:
        if not isinstance(kb_check, dict):
            return ("Not scored: no fact-check for this recording — the workspace has no "
                    "knowledge base, or the check has not run.")
        return ("Not scored: nothing in this call could be checked against the knowledge "
                "base, so there is no accuracy to report.")
    counts = kb_check.get("counts") if isinstance(kb_check.get("counts"), dict) else {}
    verifiable = sum(int(counts.get(k) or 0)
                     for k in ("supported", "partially_supported", "contradicted"))
    return (f"{score}% of the {verifiable} checkable claim(s) in this call are supported by "
            f"the knowledge base.")


def normalize_dimensions(dimensions) -> list[dict]:
    """Clean a config's dimension list: valid key/name, non-negative weight, string guidance.

    `source` survives the whitelist (it is the only field a caller cannot invent freely — an
    unknown value is dropped), because it is what marks a dimension as scored by code rather
    than by the model.
    """
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
        source = str(d.get("source") or "").strip().lower()
        row = {
            "key": key,
            "name": name,
            "description": str(d.get("description") or "").strip(),
            "guidance": str(d.get("guidance") or "").strip(),
            "weight": max(0.0, weight),
        }
        if source in SYSTEM_SOURCES:
            # The product owns everything about a system dimension except its weight, so the
            # stored name/guidance are replaced rather than trusted: an old row, a hand-edited
            # import or a rename in the UI must not leave a code-scored number under a label
            # that no longer describes where it came from.
            row = {**row, **SYSTEM_DIMENSIONS[source], "weight": row["weight"], "source": source}
        out.append(row)
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
                      user_id: str | None = None, kb_check: dict | None = None,
                      semantic: dict | None = None) -> dict | None:
    """Score the transcript against the owner's rubric. Returns None if nothing to score.

    `segments` (§2) places the evidence on the player's timeline; `segments=None` keeps the
    pre-v2 callers working unchanged. `user_id` names a registered user's personal rubric run
    for the log — `llm.call_tool` records usage by `client_id` only.

    `kb_check` and `semantic` feed the SYSTEM dimensions (see SYSTEM_DIMENSIONS). Both are
    optional and both are nullable all the way down: a caller that has neither — the raw-text
    playground, a re-score of a pasted transcript — produces a scorecard whose system
    dimensions read "—" and drop out of the weighting, rather than one that scores them zero.
    """
    if not (transcript or "").strip() or not api_key or not config:
        return None
    dims = normalize_dimensions(config.get("dimensions"))
    if not dims:
        return None

    # The model never sees the system dimensions: it cannot check a knowledge base it has no
    # access to, and asking it to re-judge tone it is not measuring would spend tokens
    # producing a second, quieter opinion that disagrees with the one on the scorecard.
    model_dims = [d for d in dims if not d.get("source")]
    by_key: dict = {}
    operator_speaker = "unknown"
    segs, timeline = _timeline_for(transcript, segments)

    if model_dims:
        system = _build_system(config, model_dims)
        try:
            # stream=True for the same reason the KB imports stream: llm.ANALYSIS budgets 60 s
            # of READ, and a non-streamed answer of thousands of Georgian tokens outlives that
            # (and is exactly the long request Anthropic drops). Streaming makes it per chunk.
            raw = await llm.call_tool(
                feature="scoring", client_id=client_id, api_key=api_key, model=model,
                system=system, user=f"<transcript>\n{timeline}\n</transcript>",
                tool=SCORE_TOOL, opts=llm.ANALYSIS,
                max_tokens=output_budget(len(model_dims)), stream=True)
        except llm.LLMError as exc:
            raise ScoringError(f"Scoring request failed: {exc}") from exc
        for s in (raw.get("scores") or []):
            if isinstance(s, dict) and s.get("key") is not None:
                by_key[str(s["key"]).strip()] = s
        operator_speaker = str(raw.get("operator_speaker") or "unknown").strip() or "unknown"
    else:
        # A rubric made only of system dimensions is a real configuration, and it must cost
        # ZERO tokens — the same discipline the bot keeps when it refuses ungrounded.
        log.info("scoring client=%s: system dimensions only, no model call", client_id)

    # After the model, so a model that answered under a system dimension's key cannot overwrite
    # a measured number with a guessed one.
    by_key.update(system_scores(kb_check=kb_check, semantic=semantic))

    log.info("scoring client=%s user=%s dims=%d model_dims=%d scored=%d segments=%d",
             client_id, user_id, len(dims), len(model_dims), len(by_key), len(segs))
    return build_result(dims, by_key, config.get("version"), operator_speaker, segments=segs)


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

    for d in dims:
        key = str(d.get("key"))
        if key not in edits:
            continue
        if d.get("source") in SYSTEM_SOURCES:
            # A system dimension is a MEASUREMENT, not an opinion to overrule. Letting a
            # reviewer type over it would leave a scorecard asserting a fact-check result that
            # no fact-check produced, under a name that says otherwise — and the edit history
            # would record only that a number changed, not that its provenance was voided.
            # Disagreeing with the knowledge base is a reason to fix the knowledge base.
            log.info("manual edit ignored for system dimension %s", key)
            continue
        new = edits[key]
        if new is not None and int(new) != (d.get("score") if isinstance(d.get("score"), int) else None):
            d["edited"] = True
            d["ai_score"] = d.get("ai_score", d.get("score"))
        d["score"] = None if new is None else max(0, min(100, int(new)))

    # Same renormalisation as build_result: an unscored dimension leaves the denominator, so a
    # reviewer clearing a score raises the weight of the rest instead of silently deducting it.
    def _w(d):
        return float(d.get("weight") or 0) or (1.0 if total_weight == len(dims) else 0.0)

    scored_weight = sum(_w(d) for d in dims if d.get("score") is not None)
    denominator = scored_weight or total_weight
    weighted_total = 0.0
    unscored = []
    for d in dims:
        score = d.get("score")
        d["contribution"] = round((score or 0) * _w(d) / denominator, 1)
        if score is not None:
            weighted_total += score * _w(d) / denominator
        else:
            unscored.append(str(d.get("key")))
    return {**result, "dimensions": dims,
            "weighted_total": round(weighted_total, 1),
            "scored_weight": round(100 * scored_weight / total_weight, 1),
            "unscored": unscored,
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
    any_weight = any(x["weight"] for x in dims)
    weights = {d["key"]: (d["weight"] if any_weight else 1.0) for d in dims}
    total_weight = sum(weights.values()) or float(len(dims)) or 1.0

    # Resolve every score FIRST, because the denominator depends on which dimensions actually
    # got one. An unscored dimension is dropped from the weighting and the remaining weights
    # are renormalised, so the total stays a number out of 100 and remains comparable with a
    # call where everything scored.
    #
    # This is what makes the system dimensions honest. They are absent often and for reasons
    # that are nobody's fault — no knowledge base, no checkable claim in the call, the tone
    # analyser not run, a pasted transcript with no audio — and the previous arithmetic left
    # their weight in the denominator while contributing nothing, which silently deducted
    # points for a measurement that was never taken. A workspace giving the fact-check 30%
    # would have capped every call with an uncheckable transcript at 70.
    resolved: dict = {}
    for d in dims:
        raw = by_key.get(d["key"], {})
        try:
            score = int(round(float(raw.get("score"))))
        except (TypeError, ValueError):
            score = None
        resolved[d["key"]] = None if score is None else max(0, min(100, score))
    scored_weight = sum(weights[d["key"]] for d in dims if resolved[d["key"]] is not None)
    denominator = scored_weight or total_weight

    out_dims, lanes, weighted_total, unscored = [], [], 0.0, []
    for d in dims:
        raw = by_key.get(d["key"], {})
        score = resolved[d["key"]]
        w = weights[d["key"]]
        # `weight` stays the share of the WHOLE rubric — what the owner configured and expects
        # to see — while contributions and the total are computed over what was scored.
        weight_pct = round(100 * w / total_weight, 1)
        contribution = round((score or 0) * w / denominator, 1)
        if score is not None:
            weighted_total += (score * w / denominator)
        else:
            unscored.append(d["key"])
        evidence = _evidence(raw.get("evidence"), segments)
        spans = _dimension_spans(d, score, evidence, segments)
        out = {
            "key": d["key"], "name": d["name"], "weight": weight_pct,
            "score": score, "max": 100, "contribution": contribution,
            "rationale": str(raw.get("rationale") or "").strip(),
            "evidence": evidence,
            "spans": spans,
        }
        if d.get("source"):
            # Provenance travels WITH the stored scorecard, not just with the rubric. It is
            # what stops `apply_manual_scores` letting a reviewer type over a measurement
            # months later, and what lets the UI say where the number came from instead of
            # showing a bare score with no evidence and no explanation.
            out["source"] = d["source"]
        out_dims.append(out)
        lanes.append({"key": d["key"], "name": d["name"], "score": score, "spans": spans})
    return {
        "config_version": version,
        "operator_speaker": operator_speaker,
        "dimensions": out_dims,
        "weighted_total": round(weighted_total, 1),
        "max_total": 100,
        # How much of the rubric the total was actually computed from, and which dimensions
        # were left out. Without this a renormalised total is unauditable: 82 out of the whole
        # rubric and 82 out of the two thirds that could be measured are different claims
        # about an agent, and a QA review has to be able to tell them apart months later.
        "scored_weight": round(100 * scored_weight / total_weight, 1),
        "unscored": unscored,
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
