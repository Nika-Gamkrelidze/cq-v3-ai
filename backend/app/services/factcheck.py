"""KB correctness / fact-check of a call transcript against the tenant's knowledge base.

Pipeline (tenant-scoped end to end):
  1. Claude extracts the factual, verifiable claims asserted in the call (esp. by the agent),
     citing the `#` index of the transcript segment(s) each claim is made in.
  2. For each claim we retrieve the tenant's most relevant KB chunks (reuses embeddings +
     retrieval, filtered by client_id — never another tenant's KB).
  3. Claude judges each claim vs. only that claim's evidence: SUPPORTED / PARTIALLY_SUPPORTED /
     CONTRADICTED / NOT_IN_KB, with rationale, confidence, and which evidence snippet it used.
  4. We aggregate an overall accuracy score + counts + a list of CONTRADICTED claims, and turn
     the cited indices into timeline spans (`segments.spans_from_indices`) so the player can
     colour the moment each claim was made. The model is never asked for seconds — it invents
     them; it cites line numbers and code looks the times up.

The transcript is prompted as `segments.render_timeline(segments)`. Callers that have no
segments (the legacy `/analyze` pipeline, the partner `/v1/analyses`, the score-text
playground) pass a bare transcript and the segments are rebuilt from its lines — the same
prompt shape, so one code path serves both; their result then says `segments_available:
false` because nothing they persisted can be highlighted by the indices we return.

Works across org types (no hardcoded claim categories) and across Georgian/Russian/English
(cross-lingual retrieval + the model compares meaning regardless of language).
"""
import logging
import math

from . import llm, retrieval
from .segments import render_timeline, segments_from_text, spans_from_indices

log = logging.getLogger("cq")

VERDICTS = {"SUPPORTED", "PARTIALLY_SUPPORTED", "CONTRADICTED", "NOT_IN_KB"}
_KEY = {"SUPPORTED": "supported", "PARTIALLY_SUPPORTED": "partially_supported",
        "CONTRADICTED": "contradicted", "NOT_IN_KB": "not_in_kb"}
# Timeline colour per verdict (design §3): green / amber / red / grey.
_LEVEL = {"SUPPORTED": "good", "PARTIALLY_SUPPORTED": "mid",
          "CONTRADICTED": "bad", "NOT_IN_KB": "none"}
# The enum the model chooses from — listed, not `sorted(VERDICTS)`, so the schema (and hence
# the prompt cache key) is byte-stable across interpreter runs.
_VERDICT_ENUM = ["SUPPORTED", "PARTIALLY_SUPPORTED", "CONTRADICTED", "NOT_IN_KB"]
MAX_CLAIMS = 25
EVIDENCE_K = 4
LABEL_CHARS = 80       # a span label is a tooltip headline, not the claim itself

# Output budgets, sized from the work. Both calls used to run at `call_tool`'s 4096 default,
# and MAX_CLAIMS was enforced AFTER generation (`raw[:MAX_CLAIMS]`) with nothing in the prompt
# saying so — so a long call spent the whole budget on claims that were then thrown away and
# truncated mid-answer instead. Measured with `llm.estimate_tokens` (Georgian = 2 tokens per
# character): a claim with its cited indices is ~172 tokens and a verdict with a one-sentence
# rationale ~153, i.e. 25 of either is ~4k — over the old default before the cap could bite.
# The cap now lives in the prompt as well, and these give ~2x headroom on top of it.
CLAIM_TOKENS = 320
VERDICT_TOKENS = 300
BASE_OUTPUT_TOKENS = 1_000
MAX_OUTPUT_TOKENS = 16_000


def _output_budget(per_item: int, count: int) -> int:
    return min(MAX_OUTPUT_TOKENS, BASE_OUTPUT_TOKENS + per_item * max(count, 1))


class FactCheckError(RuntimeError):
    pass


CLAIMS_TOOL = {
    "name": "submit_claims",
    "description": "Return the factual, verifiable claims asserted in the call.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "claims": {
                "type": "array",
                "description": f"At most {MAX_CLAIMS} claims — the ones a customer would most "
                               f"rely on. Anything beyond that is discarded, so choose.",
                "items": {
                    "type": "object",
                    "properties": {
                        "claim": {"type": "string",
                                  "description": "One self-contained factual assertion, understandable without the transcript."},
                        "speaker": {"type": "string", "enum": ["agent", "customer", "unknown"]},
                        "category": {"type": "string",
                                     "description": "Free-form topic label, e.g. pricing, policy, eligibility, hours, coverage."},
                        "segments": {"type": "array", "items": {"type": "integer"},
                                     "description": "The `#` indices of the transcript lines where this claim is made."},
                    },
                    "required": ["claim", "speaker", "category", "segments"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["claims"],
        "additionalProperties": False,
    },
}

VERIFY_TOOL = {
    "name": "submit_verifications",
    "description": "Return a verdict for each claim against its provided KB evidence.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "verifications": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {"type": "integer", "description": "The claim index being judged."},
                        "verdict": {"type": "string", "enum": _VERDICT_ENUM},
                        "rationale": {"type": "string", "description": "One sentence explaining the verdict."},
                        "confidence": {"type": "number", "description": "Confidence 0-1."},
                        "evidence_used": {"type": "integer",
                                          "description": "The [index] of the evidence snippet relied on, or -1 if none."},
                    },
                    "required": ["index", "verdict", "rationale", "confidence", "evidence_used"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["verifications"],
        "additionalProperties": False,
    },
}

_EXTRACT_SYS = (
    f"Return AT MOST {MAX_CLAIMS} claims: if the call contains more, keep the ones a customer "
    f"would most rely on and leave the rest out — extra claims are discarded, not checked.\n\n"
    "You extract factual, verifiable assertions from a customer-support call transcript. "
    "Focus on statements the AGENT makes that a customer would rely on: prices, fees, "
    "policies, eligibility, procedures, hours, coverage, deadlines, medical/financial facts. "
    "Ignore greetings, opinions, questions, and small talk. Each claim must be self-contained "
    "and understandable on its own. The transcript may be in Georgian, Russian, or English — "
    "write every claim in the SAME language as the transcript. NEVER translate a claim to English.\n\n"
    "The transcript is given one line per segment, each line starting with a tag like "
    "`[#12 00:34.2-00:41.8 speaker_0]` (or `[#12 speaker_0]` when there are no times). For every "
    "claim return `segments` = the `#` numbers of the line(s) where that claim is made — usually "
    "one line, sometimes two adjacent ones. Cite only `#` numbers that appear in the transcript."
)

_VERIFY_INTRO = (
    "For each claim below, judge it using ONLY that claim's KB evidence. "
    "SUPPORTED = the evidence confirms the claim. PARTIALLY_SUPPORTED = the substance of the "
    "claim is right but a detail is wrong or missing (a wrong number, a missing condition, an "
    "outdated figure). CONTRADICTED = the evidence states something different or incompatible "
    "(this is misinformation the agent gave). NOT_IN_KB = the evidence does not contain enough "
    "information to confirm or deny. Compare meaning even if the claim and the knowledge base are "
    "in different languages. Return, per claim: the verdict, a one-sentence rationale WRITTEN IN "
    "THE SAME LANGUAGE AS THE CLAIM, a confidence 0-1, and evidence_used = the [index] of the "
    "snippet you relied on (or -1).\n\n"
)


# ---------------------------------------------------------------------------
# Normalisation of what the model returns — strict schemas bound the shape, not the values
# ---------------------------------------------------------------------------
def _norm_speaker(v) -> str:
    v = str(v or "").strip().lower()
    return v if v in ("agent", "customer") else "unknown"


def _norm_verdict(v) -> str:
    """Upper-cased with spaces/hyphens as underscores, so `partially supported` still counts;
    anything outside VERDICTS is the safe default NOT_IN_KB (never a false SUPPORTED)."""
    verdict = str(v or "").strip().upper().replace(" ", "_").replace("-", "_")
    return verdict if verdict in VERDICTS else "NOT_IN_KB"


def _int(value) -> int | None:
    """int for 3, 3.0, "3"; None for bools, 2.5, "x", None — the same rule segments.py applies
    to cited indices, so a claim index and a segment index are coerced alike."""
    if value is None or isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return int(f) if math.isfinite(f) and f == int(f) else None


def _confidence(value) -> float | None:
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _label(text: str) -> str:
    return text if len(text) <= LABEL_CHARS else text[:LABEL_CHARS - 1].rstrip() + "…"


def _timeline(transcript: str, segments) -> tuple[list, bool]:
    """(segments to cite, whether they are the caller's own).

    A caller with persisted segments passes them and gets indices it can map back. Without
    any, the transcript's lines stand in — same prompt shape — but the indices then refer to
    a list nobody stored, which `segments_available=False` tells the UI.
    """
    if segments:
        return segments, True
    return segments_from_text(transcript), False


def _empty_result(segments_available: bool) -> dict:
    return {"accuracy_score": None,
            "counts": {"supported": 0, "partially_supported": 0, "contradicted": 0,
                       "not_in_kb": 0, "total": 0},
            "claims": [], "contradicted": [], "spans": [],
            "segments_available": segments_available}


def accuracy_score(counts: dict) -> int | None:
    """Share of verifiable claims that were right, a partial counting half. NOT_IN_KB claims
    are neither right nor wrong, so they are left out of the denominator — an agent is not
    penalised for things the knowledge base does not cover."""
    supported = counts.get("supported", 0)
    partially = counts.get("partially_supported", 0)
    verifiable = supported + partially + counts.get("contradicted", 0)
    return round(100 * (supported + 0.5 * partially) / verifiable) if verifiable else None


# ---------------------------------------------------------------------------
# Model calls
# ---------------------------------------------------------------------------
async def _extract_claims(client_id: str | None, api_key: str, timeline: str, model: str) -> list[dict]:
    # Budgeted for a full MAX_CLAIMS answer in Georgian, and streamed so llm.ANALYSIS's 60 s
    # read budget applies per chunk rather than to the whole answer.
    raw = await llm.call_tool(
        feature="factcheck_claims", client_id=client_id, api_key=api_key, model=model,
        system=_EXTRACT_SYS,
        user=f"<transcript>\n{timeline}\n</transcript>",
        tool=CLAIMS_TOOL, opts=llm.ANALYSIS,
        max_tokens=_output_budget(CLAIM_TOKENS, MAX_CLAIMS), stream=True)
    return list(raw.get("claims") or [])


async def _verify(client_id: str | None, api_key: str, model: str, items: list[dict]) -> list[dict]:
    blocks = []
    for i, it in enumerate(items):
        ev = it["evidence"]
        if ev:
            ev_txt = "\n".join(
                f"   [{j}] ({e.get('title') or e.get('doc_type') or 'KB'}) {(e.get('content') or '').strip()[:600]}"
                for j, e in enumerate(ev))
        else:
            ev_txt = "   (no relevant knowledge base entry found)"
        blocks.append(f"Claim {i}: {it['claim']}\nKB evidence for claim {i}:\n{ev_txt}")
    user = _VERIFY_INTRO + "\n\n".join(blocks)
    raw = await llm.call_tool(
        feature="factcheck_verdict", client_id=client_id, api_key=api_key, model=model,
        system="", user=user, tool=VERIFY_TOOL, opts=llm.ANALYSIS,
        max_tokens=_output_budget(VERDICT_TOKENS, len(items)), stream=True)
    return list(raw.get("verifications") or [])


async def probe_tools(api_key: str, model: str) -> int:
    """Connectivity probe for the two fact-check tool schemas (submit_claims and
    submit_verifications). Not part of the pipeline — only /admin/test?deep=1 calls it.
    Runs no retrieval, so it needs no tenant (client_id=None) and touches no KB; the two-line
    transcript goes through the same text fallback a segment-less caller gets."""
    segs, _ = _timeline("Agent: Our support line is open 24/7.\nCustomer: Good to know.", None)
    claims = await _extract_claims(None, api_key, render_timeline(segs), model)
    await _verify(None, api_key, model, [{"claim": "Support is open 24/7.", "evidence": []}])
    return len(claims)


# ---------------------------------------------------------------------------
# The pipeline
# ---------------------------------------------------------------------------
def _clean_claims(raw: list, segs: list) -> list[dict]:
    """The model's claims, shape-checked, capped at MAX_CLAIMS, with their cited lines turned
    into ONE span each (the first run of adjacent indices — a claim repeated later in the call
    is highlighted where it was first made). Uncitable claims keep `segments: []`."""
    claims = []
    for c in raw[:MAX_CLAIMS]:
        c = c if isinstance(c, dict) else {}
        text = " ".join(str(c.get("claim") or "").split())
        if not text:
            continue
        spans = spans_from_indices(segs, c.get("segments"))
        first = spans[0] if spans else {"segments": [], "start": None, "end": None}
        claims.append({"claim": text, "speaker": _norm_speaker(c.get("speaker")),
                       "category": str(c.get("category") or "").strip(),
                       "segments": first["segments"], "start": first["start"], "end": first["end"]})
    return claims


def _evidence(hits: list[dict], ev_used) -> dict | None:
    idx = _int(ev_used)
    if idx is None or not 0 <= idx < len(hits):
        return None
    h = hits[idx]
    return {"title": h.get("title"), "doc_type": h.get("doc_type"),
            "snippet": (h.get("content") or "").strip()[:400],
            "score": round(float(h["score"]), 3) if h.get("score") is not None else None}


def _span_for(claim: dict) -> dict | None:
    """The §3 timeline span of a judged claim, or None when the model cited no usable line —
    a span with no segments and no times cannot be drawn on the timeline or the transcript."""
    if not claim["segments"]:
        return None
    rationale = claim["rationale"]
    return {"segments": claim["segments"], "start": claim["start"], "end": claim["end"],
            "level": _LEVEL[claim["verdict"]], "score": None,
            "label": _label(claim["claim"]),
            "detail": f"{claim['verdict']}: {rationale}" if rationale else claim["verdict"]}


async def run_factcheck(transcript: str, client_id: str, api_key: str, model: str,
                        segments: list[dict] | None = None) -> dict | None:
    """Returns the KB-correctness result, or None if there's nothing to check.

    `segments` are the recording's persisted §2 segments; the model is prompted with their
    timeline and each claim comes back with `segments`/`start`/`end` plus a `spans` list in
    the §3 shape. Pass None (legacy callers) and the transcript's own lines are used instead.
    """
    if not client_id or not api_key:
        return None
    segs, segments_available = _timeline(transcript or "", segments)
    if not segs:
        return None

    try:
        raw = await _extract_claims(client_id, api_key, render_timeline(segs), model)
        claims = _clean_claims(raw, segs)
        if not claims:
            return _empty_result(segments_available)

        # Retrieve evidence per claim — STRICTLY tenant-scoped (client_id filter in retrieval).
        items = []
        for c in claims:
            hits = await retrieval.retrieve(client_id, c["claim"], top_k=EVIDENCE_K)
            items.append({"claim": c["claim"], "evidence": hits})

        verifs = await _verify(client_id, api_key, model, items)
        by_idx = {}
        for v in verifs:
            if not isinstance(v, dict):
                continue
            idx = _int(v.get("index"))
            if idx is not None:
                by_idx[idx] = v

        out_claims, spans = [], []
        counts = {"supported": 0, "partially_supported": 0, "contradicted": 0, "not_in_kb": 0}
        for i, c in enumerate(claims):
            v = by_idx.get(i, {})
            verdict = _norm_verdict(v.get("verdict"))
            counts[_KEY[verdict]] += 1
            judged = {
                **c,
                "verdict": verdict, "rationale": str(v.get("rationale") or "").strip(),
                "confidence": _confidence(v.get("confidence")),
                "evidence": _evidence(items[i]["evidence"], v.get("evidence_used")),
            }
            out_claims.append(judged)
            span = _span_for(judged)
            if span is not None:
                spans.append(span)

        return {
            "accuracy_score": accuracy_score(counts),
            "counts": {**counts, "total": len(out_claims)},
            "claims": out_claims,
            "contradicted": [c for c in out_claims if c["verdict"] == "CONTRADICTED"],
            "spans": spans,
            "segments_available": segments_available,
        }
    except llm.LLMError as exc:
        raise FactCheckError(f"Fact-check request failed: {exc}") from exc
