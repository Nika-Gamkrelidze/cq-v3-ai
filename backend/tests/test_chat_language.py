"""The public bot's language: the model decides it, and an empty config box means the built-in text.

Two things a Georgian customer on an English widget hit at once (the pilot, 2026-11-20): the
AI-disclosure line and the off-topic warning came out in English because (a) the chat widget's
`locale` is its UI language, not the customer's, and (b) the Default bot's English-only text beat
the engine's own Georgian for every language the tenant never wrote. Pure — no database, no model.
"""
from app.services import chat_copy, chat_prompts, llm
from tests.test_autopilot import (CID, ON, UNGROUNDED_RESULT, _ctx, _no_model, _run,
                                  _stub_kill_switch, _stub_retrieval)

KA = "რამდენი ბენზინი დამჭირდება მთვარემდე და უკან?"
RU = "сколько бензина нужно до луны?"


# --------------------------------------------------------------------------- #
# Reading the language
# --------------------------------------------------------------------------- #
def test_script_decides_georgian_and_russian_and_leaves_latin_to_the_model():
    assert chat_prompts.script_locale(KA) == "ka"
    assert chat_prompts.script_locale(RU) == "ru"
    assert chat_prompts.script_locale("hello there") is None
    assert chat_prompts.script_locale("gamarjoba") is None          # Georgian in Latin letters
    assert chat_prompts.script_locale("👍") is None and chat_prompts.script_locale("") is None
    assert chat_prompts.script_locale(None) is None
    assert chat_prompts.script_locale("CommuniQ-ის შესახებ") == "ka"  # mixed: the letters win


def test_the_models_reading_beats_the_script_which_beats_the_widgets_hint():
    decide = chat_prompts.decide_locale
    assert decide("en", text="gamarjoba", ai="ka") == "ka"
    assert decide("en", text=KA) == "ka"
    assert decide("ka", text="hello") == "ka"                       # nothing better: keep the hint
    assert decide("en", text="gamarjoba", ai="other") == "en"       # a language with no wording
    assert decide("en-US", text="") == "en" and decide(None, text="") == "en"
    assert decide("en", text="", ai="ru") == "ru"


# --------------------------------------------------------------------------- #
# An empty box means the built-in text, per language
# --------------------------------------------------------------------------- #
def test_a_language_the_tenant_left_empty_gets_the_built_in_text_not_their_english():
    only_english = {"en": "Ours, in English."}
    assert chat_copy.pick(only_english, "en", chat_copy.DEFAULT_REFUSAL) == "Ours, in English."
    assert chat_copy.pick(only_english, "ka", chat_copy.DEFAULT_REFUSAL) == chat_copy.DEFAULT_REFUSAL["ka"]
    assert chat_copy.pick({"ka": "  ", "en": "x"}, "ka", chat_copy.DEFAULT_REFUSAL) == chat_copy.DEFAULT_REFUSAL["ka"]
    assert chat_copy.pick({"ka": "ჩვენი"}, "ka", chat_copy.DEFAULT_REFUSAL) == "ჩვენი"
    assert chat_copy.pick(None, "de", chat_copy.DEFAULT_REFUSAL) == chat_copy.DEFAULT_REFUSAL["en"]


def test_refusal_follows_the_same_rule():
    cfg = {"refusal_copy": {"en": "Ours, in English."}}
    assert chat_prompts.refusal_text(cfg, "en") == "Ours, in English."
    assert chat_prompts.refusal_text(cfg, "ka") == chat_copy.DEFAULT_REFUSAL["ka"]
    assert chat_prompts.refusal_text({}, "ru") == chat_copy.DEFAULT_REFUSAL["ru"]


def test_disclosure_keeps_present_but_empty_as_suppression_but_only_per_language():
    english_only = {"settings": {"disclosure": {"en": "Bot."}}}
    assert chat_prompts.disclosure_text(english_only, "en") == "Bot."
    assert chat_prompts.disclosure_text(english_only, "ka") == chat_copy.DEFAULT_DISCLOSURE["ka"]
    suppressed_ka = {"settings": {"disclosure": {"en": "Bot.", "ka": ""}}}
    assert chat_prompts.disclosure_text(suppressed_ka, "ka") == ""
    assert chat_prompts.disclosure_text(suppressed_ka, "ru") == chat_copy.DEFAULT_DISCLOSURE["ru"]
    per_channel = {"settings": {"disclosure_channels": {"whatsapp": {"ka": "ბოტი"}},
                                "disclosure": {"en": "Bot."}}}
    assert chat_prompts.disclosure_text(per_channel, "ka", "whatsapp") == "ბოტი"
    assert chat_prompts.disclosure_text(per_channel, "ka", "web") == chat_copy.DEFAULT_DISCLOSURE["ka"]


def test_the_console_placeholder_for_the_disclosure_is_the_text_that_is_used():
    assert chat_copy.builtin_copy()["disclosure"] == chat_prompts.DEFAULT_DISCLOSURE


# --------------------------------------------------------------------------- #
# The prompt no longer tells the model the conversation is English
# --------------------------------------------------------------------------- #
def test_the_prompts_name_no_conversation_locale_and_ask_for_the_customers_language():
    for system in (chat_prompts.build_system({}, mode="autopilot", locale="en"),
                   chat_prompts.build_triage_system({}, locale="en", policy="kb_only")):
        assert "conversation locale" not in system
        assert "latest message is written in" in system
        assert "gamarjoba" in system and "When you write Georgian" in system


def test_the_system_prompt_does_not_vary_with_the_turns_locale():
    """Per-turn locale would split a tenant's cached system prompt into one copy per language."""
    a = chat_prompts.build_system({"languages": ["ka"]}, mode="autopilot", locale="en")
    b = chat_prompts.build_system({"languages": ["ka"]}, mode="autopilot", locale="ru")
    assert a == b and "write in Georgian" in a


def test_triage_reports_its_language_first():
    props = chat_prompts.TRIAGE_TOOL["input_schema"]["properties"]
    assert list(props)[:2] == ["language", "kind"]
    assert set(props["language"]["enum"]) == {"ka", "ru", "en", "other"}
    assert set(chat_prompts.TRIAGE_TOOL["input_schema"]["required"]) == {"language", "kind", "reply"}


# --------------------------------------------------------------------------- #
# The engine
# --------------------------------------------------------------------------- #
def _triage_says(monkeypatch, language, kind, reply):
    async def _tool(**kw):
        if kw.get("feature") == "handoff":
            return {"summary": "s", "customer_goal": "g"}
        assert kw.get("feature") == "triage"
        return {"language": language, "kind": kind, "reply": reply}

    async def _stream(**kw):
        raise AssertionError("the answer model must not be reached")
        yield ""  # pragma: no cover

    monkeypatch.setattr(llm, "call_tool", _tool, raising=False)
    monkeypatch.setattr(llm, "stream_text", _stream, raising=False)


def test_georgian_in_latin_letters_on_an_english_widget_is_disclosed_in_georgian(monkeypatch):
    """The first message of the pilot conversation: "gamarjoba", locale `en`."""
    _stub_retrieval(monkeypatch, UNGROUNDED_RESULT)
    _stub_kill_switch(monkeypatch)
    _triage_says(monkeypatch, "ka", "chitchat", "გამარჯობა! რით შემიძლია დაგეხმაროთ?")
    done = _run(_ctx(CID, cfg=ON, text="gamarjoba", locale="en"))
    text = done["reply"]["text"]
    assert text.startswith("გამარჯობა!") and chat_copy.DEFAULT_DISCLOSURE["ka"] in text
    assert chat_copy.DEFAULT_DISCLOSURE["en"] not in text
    assert done["locale"] == "ka"                                    # what the turn was served in


def test_the_off_topic_warning_follows_the_customers_language_not_the_widgets(monkeypatch):
    _stub_retrieval(monkeypatch, UNGROUNDED_RESULT)
    _stub_kill_switch(monkeypatch)
    _triage_says(monkeypatch, "ka", "off_topic", "ამ კითხვაზე ვერ გიპასუხებთ.")
    import dataclasses
    ctx = dataclasses.replace(_ctx(CID, cfg=ON, text=KA, locale="en"), off_topic_count=2)
    text = _run(ctx)["reply"]["text"]
    assert chat_copy.DEFAULT_OFF_TOPIC_WARNING["ka"] in text
    assert chat_copy.DEFAULT_OFF_TOPIC_WARNING["en"] not in text


def test_a_tenants_english_only_copy_no_longer_speaks_for_a_georgian_customer(monkeypatch):
    _stub_retrieval(monkeypatch, UNGROUNDED_RESULT)
    _stub_kill_switch(monkeypatch)
    _triage_says(monkeypatch, "ka", "business", "")
    cfg = {**ON, "refusal_copy": {"en": "We cannot answer that."},
           "settings": {"disclosure": {"en": "Bot."}}}
    text = _run(_ctx(CID, cfg=cfg, text=KA, locale="en"))["reply"]["text"]
    assert text.startswith(chat_copy.DEFAULT_REFUSAL["ka"])
    assert chat_copy.DEFAULT_DISCLOSURE["ka"] in text and "Bot." not in text


def test_a_zero_token_exit_still_reads_the_script_with_no_model_call(monkeypatch):
    """The cut-off calls no model, so the only language signal is the customer's letters."""
    import dataclasses
    _stub_retrieval(monkeypatch, UNGROUNDED_RESULT)
    _stub_kill_switch(monkeypatch)
    silent = _no_model(monkeypatch)
    ctx = dataclasses.replace(_ctx(CID, cfg=ON, text=KA, locale="en"), off_topic_count=9)
    done = _run(ctx)
    assert done["scope"]["source"] == "cutoff" and silent.total == 0
    assert done["reply"]["text"].startswith(chat_copy.DEFAULT_OFF_TOPIC_CUTOFF["ka"])


def test_a_triage_reply_without_a_language_changes_nothing(monkeypatch):
    """A model (or a test fake) that omits `language` leaves the caller's locale in force."""
    _stub_retrieval(monkeypatch, UNGROUNDED_RESULT)
    _stub_kill_switch(monkeypatch)
    _triage_says(monkeypatch, None, "chitchat", "Hello!")
    done = _run(_ctx(CID, cfg=ON, text="hello", locale="ru"))
    assert chat_copy.DEFAULT_DISCLOSURE["ru"] in done["reply"]["text"] and done["locale"] == "ru"


# --------------------------------------------------------------------------- #
# A typo'd question is read for its likely meaning, then clarified — not answered as another one
# --------------------------------------------------------------------------- #
def test_the_public_bot_and_triage_are_told_to_clarify_rather_than_answer_a_different_question():
    """Seen live: "რემდანი ტონაა შავი ზღვა?" ("რამდენი" misspelt) was answered as a question
    about the sea's temperature. The copilot is not told: a person reads its drafts."""
    auto = chat_prompts.build_system({}, mode="autopilot", locale="en")
    triage = chat_prompts.build_triage_system({}, locale="en", policy="kb_only")
    assist = chat_prompts.build_system({}, mode="assist", locale="en")
    for system in (auto, triage):
        assert "most likely meaning" in system and "ONE short clarifying question" in system
    assert "use kind chitchat" in triage
    assert "clarifying question" not in assist
    # the triage template still formats: the new sentence adds no stray braces
    assert "{" not in triage and "}" not in triage
