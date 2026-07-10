"""KB correctness / fact-check of a call transcript against the tenant's knowledge base.

Pipeline (tenant-scoped end to end):
  1. Claude extracts the factual, verifiable claims asserted in the call (esp. by the agent).
  2. For each claim we retrieve the tenant's most relevant KB chunks (reuses embeddings +
     retrieval, filtered by client_id — never another tenant's KB).
  3. Claude judges each claim vs. only that claim's evidence: SUPPORTED / CONTRADICTED /
     NOT_IN_KB, with rationale, confidence, and which evidence snippet it used.
  4. We aggregate an overall accuracy score + counts + a list of CONTRADICTED claims.

Works across org types (no hardcoded claim categories) and across Georgian/Russian/English
(cross-lingual retrieval + the model compares meaning regardless of language).
"""
import logging

import anthropic

from . import retrieval

log = logging.getLogger("cq")

VERDICTS = {"SUPPORTED", "CONTRADICTED", "NOT_IN_KB"}
_KEY = {"SUPPORTED": "supported", "CONTRADICTED": "contradicted", "NOT_IN_KB": "not_in_kb"}
MAX_CLAIMS = 25
EVIDENCE_K = 4


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
                "items": {
                    "type": "object",
                    "properties": {
                        "claim": {"type": "string",
                                  "description": "One self-contained factual assertion, understandable without the transcript."},
                        "speaker": {"type": "string", "enum": ["agent", "customer", "unknown"]},
                        "category": {"type": "string",
                                     "description": "Free-form topic label, e.g. pricing, policy, eligibility, hours, coverage."},
                    },
                    "required": ["claim", "speaker", "category"],
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
                        "verdict": {"type": "string", "enum": ["SUPPORTED", "CONTRADICTED", "NOT_IN_KB"]},
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
    "You extract factual, verifiable assertions from a customer-support call transcript. "
    "Focus on statements the AGENT makes that a customer would rely on: prices, fees, "
    "policies, eligibility, procedures, hours, coverage, deadlines, medical/financial facts. "
    "Ignore greetings, opinions, questions, and small talk. Each claim must be self-contained "
    "and understandable on its own. The transcript may be in Georgian, Russian, or English — "
    "keep each claim in the language it was said."
)

_VERIFY_INTRO = (
    "For each claim below, judge it using ONLY that claim's KB evidence. "
    "SUPPORTED = the evidence confirms the claim. CONTRADICTED = the evidence states something "
    "different or incompatible (this is misinformation the agent gave). NOT_IN_KB = the evidence "
    "does not contain enough information to confirm or deny. Compare meaning even if the claim and "
    "the knowledge base are in different languages. Return, per claim: the verdict, a one-sentence "
    "rationale, a confidence 0-1, and evidence_used = the [index] of the snippet you relied on (or -1).\n\n"
)


def _norm_speaker(v) -> str:
    v = str(v or "").strip().lower()
    return v if v in ("agent", "customer") else "unknown"


async def _extract_claims(client, transcript: str, model: str) -> list[dict]:
    msg = await client.messages.create(
        model=model, max_tokens=4096, system=_EXTRACT_SYS,
        tools=[CLAIMS_TOOL], tool_choice={"type": "tool", "name": "submit_claims"},
        messages=[{"role": "user", "content": f"<transcript>\n{transcript}\n</transcript>"}])
    for block in msg.content:
        if block.type == "tool_use" and block.name == "submit_claims":
            return list(dict(block.input).get("claims") or [])
    return []


async def _verify(client, model: str, items: list[dict]) -> list[dict]:
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
    msg = await client.messages.create(
        model=model, max_tokens=4096,
        tools=[VERIFY_TOOL], tool_choice={"type": "tool", "name": "submit_verifications"},
        messages=[{"role": "user", "content": user}])
    for block in msg.content:
        if block.type == "tool_use" and block.name == "submit_verifications":
            return list(dict(block.input).get("verifications") or [])
    return []


async def run_factcheck(transcript: str, client_id: str, api_key: str, model: str) -> dict | None:
    """Returns the KB-correctness result, or None if there's nothing to check."""
    if not (transcript or "").strip() or not client_id or not api_key:
        return None

    client = anthropic.AsyncAnthropic(api_key=api_key)
    try:
        raw = await _extract_claims(client, transcript, model)
        claims = []
        for c in raw[:MAX_CLAIMS]:
            text = str((c or {}).get("claim") or "").strip()
            if text:
                claims.append({"claim": text, "speaker": _norm_speaker((c or {}).get("speaker")),
                               "category": str((c or {}).get("category") or "").strip()})
        if not claims:
            return {"accuracy_score": None,
                    "counts": {"supported": 0, "contradicted": 0, "not_in_kb": 0, "total": 0},
                    "claims": [], "contradicted": []}

        # Retrieve evidence per claim — STRICTLY tenant-scoped (client_id filter in retrieval).
        items = []
        for c in claims:
            hits = await retrieval.retrieve(client_id, c["claim"], top_k=EVIDENCE_K)
            items.append({"claim": c["claim"], "evidence": hits})

        verifs = await _verify(client, model, items)
        by_idx = {}
        for v in verifs:
            try:
                by_idx[int(v.get("index"))] = v
            except (TypeError, ValueError):
                continue

        out_claims, counts = [], {"supported": 0, "contradicted": 0, "not_in_kb": 0}
        for i, c in enumerate(claims):
            v = by_idx.get(i, {})
            verdict = str(v.get("verdict") or "NOT_IN_KB").upper()
            if verdict not in VERDICTS:
                verdict = "NOT_IN_KB"
            try:
                ev_used = int(v.get("evidence_used"))
            except (TypeError, ValueError):
                ev_used = -1
            evidence = None
            hits = items[i]["evidence"]
            if 0 <= ev_used < len(hits):
                h = hits[ev_used]
                evidence = {"title": h.get("title"), "doc_type": h.get("doc_type"),
                            "snippet": (h.get("content") or "").strip()[:400],
                            "score": round(float(h["score"]), 3) if h.get("score") is not None else None}
            try:
                conf = round(float(v.get("confidence")), 2)
            except (TypeError, ValueError):
                conf = None
            counts[_KEY[verdict]] += 1
            out_claims.append({
                "claim": c["claim"], "speaker": c["speaker"], "category": c["category"],
                "verdict": verdict, "rationale": str(v.get("rationale") or "").strip(),
                "confidence": conf, "evidence": evidence,
            })

        verifiable = counts["supported"] + counts["contradicted"]
        accuracy = round(100 * counts["supported"] / verifiable) if verifiable else None
        return {
            "accuracy_score": accuracy,
            "counts": {**counts, "total": len(out_claims)},
            "claims": out_claims,
            "contradicted": [c for c in out_claims if c["verdict"] == "CONTRADICTED"],
        }
    except anthropic.APIError as exc:
        raise FactCheckError(f"Fact-check request failed: {getattr(exc, 'message', str(exc))}") from exc
    finally:
        await client.close()
