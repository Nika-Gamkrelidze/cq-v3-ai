"""AI rubric import: turn an uploaded scoring standard (an evaluation scorecard file)
into a DRAFT scoring config — dimensions + weights + guidance.

Customer QA teams keep their standards as spreadsheets: sections (A კონტაქტის დამყარება,
B მომხმარებელთან კომუნიკაცია...), criterion rows with codes and point rules ("1/-3"),
section maxima, formula debris. That is a *rubric definition*, not knowledge content — it
belongs in `scoring_configs`, where the pipeline scores calls against it.

The model maps the document to dimensions; THE CODE assigns the weights (house rule: Claude
provides judgement and evidence, arithmetic is ours). Each dimension reports the section's
max points from the document, and the weight is that share of the total, normalized to 100.

The result is a DRAFT returned to the editor — never saved directly. A human reviews the
criteria, adjusts weights, and presses save; that keeps the existing versioning/audit flow
(`scoring_store.save_config`) as the only write path.
"""
import logging

from . import llm, settings_store
from .scoring import MAX_DIMENSIONS

log = logging.getLogger("cq")

# A rubric is a bounded document; anything bigger than this is not a scoring standard.
MAX_INPUT_CHARS = 40_000
MAX_OUTPUT_TOKENS = 8_192
ADMIT_PATIENCE_S = 30.0

OVERSIZE_MESSAGE = (f"The file is too large for rubric import (over {MAX_INPUT_CHARS // 1000}k "
                    "characters of text). Import the part that defines the scoring standard.")


class RubricImportError(RuntimeError):
    pass


RUBRIC_TOOL = {
    "name": "submit_rubric",
    "description": "Return the scoring standard extracted from the document as rubric dimensions.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "general_instructions": {
                "type": "string",
                "description": "Overall scoring instructions that apply across dimensions "
                               "(grading rules, penalty conventions like '1/-3' meaning +1 "
                               "if met / -3 if violated, rounding, language). In the "
                               "document's own language. Empty string if none.",
            },
            "dimensions": {
                "type": "array",
                "description": "One dimension per SECTION of the standard (or logical group "
                               "of criteria when the document has no sections). Never one "
                               "dimension per individual criterion row.",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "The section/group name in the document's own "
                                           "language (e.g. 'კონტაქტის დამყარება').",
                        },
                        "description": {
                            "type": "string",
                            "description": "One or two sentences: what this dimension "
                                           "measures, in the document's own language.",
                        },
                        "guidance": {
                            "type": "string",
                            "description": "The COMPLETE list of this section's criteria, "
                                           "verbatim from the document, each with its code "
                                           "and point rule (e.g. 'A1 (1/-3): ...'). This is "
                                           "what the scoring model reads, so nothing may be "
                                           "dropped or paraphrased away.",
                        },
                        "max_points": {
                            "type": "number",
                            "description": "The section's maximum attainable points per the "
                                           "document; 0 if the document does not say.",
                        },
                    },
                    "required": ["name", "description", "guidance", "max_points"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["general_instructions", "dimensions"],
        "additionalProperties": False,
    },
}

SYSTEM = (
    "You convert a customer's scoring standard document (a call/service evaluation "
    "scorecard, often Georgian or Russian: sections, criterion rows with codes like A1/B7, "
    "point rules like '1/-3', section maxima, sometimes filled-in example scores) into a "
    "scoring rubric definition.\n"
    "Rules:\n"
    "- One dimension per section (or logical group). Fold the section's individual criteria "
    "into that dimension's guidance, verbatim, with their codes and point rules.\n"
    "- The document defines the STANDARD. Ignore any filled-in scores of a particular "
    "evaluation (a column of achieved points, percentages of one call) — extract what is "
    "being measured and how, not one call's results.\n"
    "- Preserve wording, codes and point values exactly; skip formula debris (#REF!, "
    "#DIV/0!) and empty layout rows.\n"
    "- Write everything in the document's own language.\n"
    f"- At most {MAX_DIMENSIONS} dimensions."
)


def _weights_from_points(dims: list[dict]) -> None:
    """Weights are percentages summing to 100, proportional to each dimension's share of
    the document's total points; even split when the document gave no points at all.
    Mirrors save_config's remainder trick so the total is exactly 100."""
    total = sum(d["max_points"] for d in dims)
    if total <= 0:
        return  # all zero -> save_config distributes evenly on save
    for d in dims:
        d["weight"] = round(d["max_points"] / total * 100, 2)
    drift = round(100 - sum(d["weight"] for d in dims), 2)
    dims[-1]["weight"] = round(dims[-1]["weight"] + drift, 2)


def _normalize(raw: dict) -> tuple[list[dict], str]:
    dims_in = raw.get("dimensions")
    if isinstance(dims_in, dict):
        dims_in = list(dims_in.values())
    if not isinstance(dims_in, (list, tuple)):
        dims_in = []
    dims: list[dict] = []
    for d in dims_in:
        if not isinstance(d, dict):
            continue
        name = str(d.get("name") or "").strip()
        if not name:
            continue
        try:
            max_points = max(0.0, float(d.get("max_points") or 0))
        except (TypeError, ValueError):
            max_points = 0.0
        dims.append({
            "name": name,
            "description": str(d.get("description") or "").strip(),
            "guidance": str(d.get("guidance") or "").strip(),
            "max_points": max_points,
            "weight": 0.0,
        })
        if len(dims) >= MAX_DIMENSIONS:
            break
    rubric = str(raw.get("general_instructions") or "").strip()
    return dims, rubric


async def rubric_from_text(text: str, *, client_id: str | None) -> dict:
    """Extracted document text -> {"dimensions": [...], "rubric": str} DRAFT (not saved).

    Dimension shape matches the editor/save contract: key-less (save derives keys),
    name/description/guidance strings, weight as a percentage. Raises RubricImportError
    with an uploader-actionable message on any failure.
    """
    if not text.strip():
        raise RubricImportError("The file contains no text to read a scoring standard from.")
    if len(text) > MAX_INPUT_CHARS:
        raise RubricImportError(OVERSIZE_MESSAGE)

    cfg = await settings_store.get_effective()
    api_key = cfg.get("anthropic_api_key")
    if not api_key:
        raise RubricImportError("AI rubric import is not configured on this server "
                                "(Anthropic API key is missing).")

    user = ("Extract the scoring standard from this document text as rubric dimensions:"
            f"\n\n<document>\n{text}\n</document>")
    try:
        raw = await llm.call_tool(
            feature="scoring_import", client_id=client_id, api_key=api_key,
            model=cfg.get("llm_model"), system=SYSTEM, user=user, tool=RUBRIC_TOOL,
            opts=llm.RESTRUCTURE, max_tokens=MAX_OUTPUT_TOKENS,
            admit_timeout_s=ADMIT_PATIENCE_S, stream=True)
    except llm.LLMTruncatedError:
        raise RubricImportError(
            "The scoring standard is too long to import in one piece — split the file, or "
            "remove content that is not part of the standard, and try again.") from None
    except llm.LLMError as exc:
        log.error("scoring_import call failed (client=%s): %s", client_id, exc)
        raise RubricImportError(
            f"AI rubric import failed ({exc.__class__.__name__}). Try again.") from exc

    dims, rubric = _normalize(raw)
    if not dims:
        raise RubricImportError("No scoring dimensions were found in this file. Check that "
                                "it contains the evaluation criteria, or build the rubric "
                                "manually in the editor.")
    _weights_from_points(dims)
    for d in dims:
        d.pop("max_points", None)
    log.info("scoring_import client=%s dims=%d chars=%d", client_id, len(dims), len(text))
    return {"dimensions": dims, "rubric": rubric}
