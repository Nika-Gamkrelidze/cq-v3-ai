"""The chat bot's own model (llm._with_model): swapped in after resolution, and only where it
cannot break a call or overrule a tenant's own choice. Pure — no database, no model call."""
from app.services import llm
from app.services.ai_resolve import Resolved


def _res(provider="anthropic", source="default", model="claude-opus-4-8"):
    return Resolved("llm", provider, model, "key", None, source=source)


def test_the_chat_model_replaces_the_model_on_our_anthropic_connection():
    got = llm._with_model(_res(), "claude-haiku-4-5")
    assert got.model == "claude-haiku-4-5"
    assert (got.provider, got.api_key, got.source) == ("anthropic", "key", "default")


def test_no_chat_model_leaves_the_resolved_model_alone():
    assert llm._with_model(_res(), None).model == "claude-opus-4-8"
    assert llm._with_model(_res(), "").model == "claude-opus-4-8"


def test_a_tenant_on_its_own_subscription_keeps_its_model():
    assert llm._with_model(_res(source="byo"), "claude-haiku-4-5").model == "claude-opus-4-8"


def test_another_provider_is_never_sent_a_claude_model():
    res = _res(provider="openai", model="gpt-5")
    assert llm._with_model(res, "claude-haiku-4-5").model == "gpt-5"
