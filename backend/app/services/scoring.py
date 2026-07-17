"""Per-tenant rubric scoring of a call transcript.

The tenant defines its own scoring dimensions (name, weight, guidance) + an overall
rubric. Claude scores each dimension 0-100 with a rationale and verbatim transcript
evidence (forced tool-use, strict schema). CODE then applies the tenant's weights to
compute the weighted total — deterministic, auditable, and re-weightable without a new
LLM call. Nothing here is hardcoded to an industry; dimensions are fully user-defined.

Tenant-scoped: the caller passes in only that tenant's active config. Works across
Georgian / Russian / English (the model scores meaning regardless of language).
"""
import logging

import anthropic

log = logging.getLogger("cq")

MAX_DIMENSIONS = 30


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
                "description": "Which speaker in the transcript is the support agent/operator "
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
                            "items": {"type": "string"},
                            "description": "Short verbatim quotes from the transcript that justify the score.",
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
    ]
    rubric = str(config.get("rubric") or "").strip()
    if rubric:
        lines.append("\nOverall rubric / guidance from the client:\n" + rubric)
    lines.append("\nDimensions to score (use the exact key):")
    for d in dims:
        g = f" — {d['guidance']}" if d["guidance"] else (f" — {d['description']}" if d["description"] else "")
        lines.append(f"  • key='{d['key']}' \"{d['name']}\" (weight {d['weight']:g}){g}")
    return "\n".join(lines)


async def run_scoring(transcript: str, config: dict, api_key: str, model: str) -> dict | None:
    """Score the transcript against the tenant's rubric. Returns None if nothing to score."""
    if not (transcript or "").strip() or not api_key or not config:
        return None
    dims = normalize_dimensions(config.get("dimensions"))
    if not dims:
        return None

    system = _build_system(config, dims)
    client = anthropic.AsyncAnthropic(api_key=api_key)
    try:
        msg = await client.messages.create(
            model=model, max_tokens=4096, system=system,
            tools=[SCORE_TOOL], tool_choice={"type": "tool", "name": "submit_scores"},
            messages=[{"role": "user", "content": f"<transcript>\n{transcript}\n</transcript>"}])
    except anthropic.APIError as exc:
        raise ScoringError(f"Scoring request failed: {getattr(exc, 'message', str(exc))}") from exc
    finally:
        await client.close()

    raw = {}
    for block in msg.content:
        if block.type == "tool_use" and block.name == "submit_scores":
            raw = dict(block.input)
            break

    by_key = {}
    for s in (raw.get("scores") or []):
        if isinstance(s, dict) and s.get("key") is not None:
            by_key[str(s["key"]).strip()] = s

    return build_result(dims, by_key, config.get("version"),
                        str(raw.get("operator_speaker") or "unknown").strip() or "unknown")


def build_result(dims: list[dict], by_key: dict, version, operator_speaker: str) -> dict:
    """Apply weights in code → per-dimension contribution + weighted total (0-100)."""
    total_weight = sum(d["weight"] for d in dims) or float(len(dims))  # equal weights if all 0
    out_dims, weighted_total = [], 0.0
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
        out_dims.append({
            "key": d["key"], "name": d["name"], "weight": weight_pct,
            "score": score, "max": 100, "contribution": contribution,
            "rationale": str(raw.get("rationale") or "").strip(),
            "evidence": _as_str_list(raw.get("evidence")),
        })
    return {
        "config_version": version,
        "operator_speaker": operator_speaker,
        "dimensions": out_dims,
        "weighted_total": round(weighted_total, 1),
        "max_total": 100,
    }
