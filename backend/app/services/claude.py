"""Claude integration: analyse a call/conversation transcript into structured JSON.

Uses forced tool-use for a reliable structured result that works across model choices
(structured `output_config.format` is only on a subset of models). The API key and
model are passed in per call from runtime settings.

The transport (client reuse, timeouts, admission control, usage accounting) lives in
`services/llm.py` — this module only owns the schema and the prompt.
"""
from . import llm


class ClaudeError(RuntimeError):
    pass


ANALYSIS_TOOL = {
    "name": "submit_analysis",
    "description": "Return the structured analysis of the transcript.",
    # strict:true makes the API enforce the schema, so array fields come back as arrays.
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "language": {
                "type": "string",
                "description": "Primary language of the conversation (e.g. Georgian, Russian, English).",
            },
            "summary": {
                "type": "string",
                "description": "2-4 sentence summary of what happened in the conversation.",
            },
            "sentiment": {
                "type": "string",
                "enum": ["positive", "neutral", "negative", "mixed"],
                "description": "Overall sentiment of the conversation.",
            },
            "topics": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Main topics discussed.",
            },
            "key_points": {
                "type": "array",
                "items": {"type": "string"},
                "description": "The most important points or statements.",
            },
            # NO action_items here, deliberately. This tool once returned them and no page
            # ever displayed them — the per-call card was not carried into the React port —
            # so every upload paid output tokens for a list nobody read. Follow-ups live in
            # ONE place now: the Summarise digest (`services/summarise.py`), which sees a
            # whole thread and can tell that call 3 closed what call 1 opened. Adding the
            # field back here would resurrect the duplicate, not fill a gap.
            "quality_score": {
                "type": "integer",
                "description": "Overall quality/clarity of the interaction, 0-100.",
            },
        },
        "required": [
            "language", "summary", "sentiment", "topics",
            "key_points", "quality_score",
        ],
        "additionalProperties": False,
    },
}


async def analyze(transcript: str, api_key: str, model: str, instructions: str,
                  kb_context: str = "", client_id: str | None = None) -> dict:
    if not api_key:
        raise ClaudeError("Anthropic API key is not configured (set it in the admin panel).")
    if not transcript.strip():
        raise ClaudeError("Transcript is empty — nothing to analyse.")

    user_content = f"Analyse the following transcript:\n\n<transcript>\n{transcript}\n</transcript>"
    if kb_context.strip():
        user_content += (
            "\n\nUse this client knowledge base as authoritative context — check the call "
            "against it (policies, expected answers, procedures) and reflect it in the "
            "analysis:\n\n<knowledge_base>\n" + kb_context + "\n</knowledge_base>"
        )

    # Respond in the caller's language: the summary, topics, key points and action items must
    # be written in the SAME language as the transcript (e.g. Georgian in, Georgian out).
    user_content += (
        "\n\nWrite the summary, topics and key_points in the SAME language as the "
        "transcript above. Keep 'sentiment' as one of the allowed enum values."
    )

    try:
        raw = await llm.call_tool(
            feature="analysis", client_id=client_id, api_key=api_key, model=model,
            system=instructions, user=user_content, tool=ANALYSIS_TOOL, opts=llm.ANALYSIS)
    except llm.LLMError as exc:
        raise ClaudeError(f"Claude request failed: {exc}") from exc
    return _normalize(raw)


def _as_str_list(value) -> list[str]:
    """Coerce whatever the model returned into a clean list of non-empty strings.

    The tool schema asks for arrays of strings, but models occasionally return a
    plain string, a dict, or null. Normalize all of those so the UI can always .map().
    """
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
            if isinstance(item, (str, int, float, bool)):
                s = str(item).strip()
            elif isinstance(item, dict):
                # e.g. {"topic": "..."} or {"item": "...", "owner": "..."}
                s = " — ".join(str(v).strip() for v in item.values() if v not in (None, ""))
            else:
                s = str(item).strip()
            if s:
                out.append(s)
        return out
    return [str(value).strip()]


def _normalize(analysis: dict) -> dict:
    """Guarantee a stable analysis shape regardless of model quirks."""
    for field in ("topics", "key_points"):
        analysis[field] = _as_str_list(analysis.get(field))
    # A job analysed before action items were dropped keeps whatever it stored; this shape is
    # for what the model returns NOW, so nothing re-creates the key on a fresh analysis.
    analysis.pop("action_items", None)
    analysis["summary"] = "" if analysis.get("summary") is None else str(analysis.get("summary"))
    analysis["language"] = "" if analysis.get("language") is None else str(analysis.get("language"))
    sentiment = analysis.get("sentiment")
    analysis["sentiment"] = str(sentiment) if sentiment else "neutral"
    try:
        analysis["quality_score"] = int(analysis.get("quality_score"))
    except (TypeError, ValueError):
        analysis["quality_score"] = None
    return analysis
