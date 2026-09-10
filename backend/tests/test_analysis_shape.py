"""The per-call analysis tool's shape — and the one field deliberately NOT in it.

`services/claude.py` once returned `action_items` on every upload. Nothing rendered them: the
legacy analysis card was not carried into the React port, so each analysis paid output tokens
for a list no operator could see. Follow-ups now live in exactly one place, the Summarise
digest, which is prompted with a whole thread and can therefore tell that call 3 closed what
call 1 opened — something a single-call pass cannot do.

These tests exist so the field cannot drift back in unnoticed on either side of the split.
"""
from app.services import claude, summarise

SCHEMA = claude.ANALYSIS_TOOL["input_schema"]


def test_the_per_call_analysis_does_not_ask_for_action_items():
    assert "action_items" not in SCHEMA["properties"]
    assert "action_items" not in SCHEMA["required"]
    # Strict tool-use: `additionalProperties: False` means a model cannot volunteer them either.
    assert SCHEMA["additionalProperties"] is False
    assert claude.ANALYSIS_TOOL["strict"] is True


def test_the_analysis_shape_is_otherwise_unchanged():
    assert set(SCHEMA["properties"]) == {"language", "summary", "sentiment", "topics",
                                         "key_points", "quality_score"}
    assert set(SCHEMA["required"]) == set(SCHEMA["properties"])


def test_normalise_never_re_creates_the_key():
    """Not even from a model that answers with one anyway, or from a replayed old payload."""
    out = claude._normalize({"language": "ka", "summary": "s", "sentiment": "neutral",
                             "topics": ["t"], "key_points": None, "quality_score": "88",
                             "action_items": ["should not survive"]})
    assert "action_items" not in out
    assert out["key_points"] == [] and out["topics"] == ["t"] and out["quality_score"] == 88


def test_the_summarise_digest_still_carries_them():
    """The whole point of removing them from the per-call pass: one home, not two."""
    props = summarise.SUMMARY_TOOL["input_schema"]["properties"]
    assert "action_items" in props and "action_items" in \
        summarise.SUMMARY_TOOL["input_schema"]["required"]
