"""Built-in customer-facing copy for the public bot, in the three supported languages.

Pure constants and one lookup, no imports from this package: `chat_prompts` (the prompt layer)
and `chat_store` (which hands these to the console as placeholders) both read them, and a
persistence module importing the prompt layer — which pulls in retrieval — is the wrong
direction.

Every string here is what a customer reads when the tenant wrote nothing of their own, so the
refusal copy's rules apply to all of them: plain, honest, never guessing, never apologising for
a policy it does not know, and always leaving a way to reach a person.
"""

SUPPORTED = ("en", "ka", "ru")

# The KB could not answer. Tenants override it in `chat_configs.refusal_copy`; ADR-001 flags that
# copy as something a lawyer reads.
DEFAULT_REFUSAL = {
    "en": "I don't have that in my knowledge base, so I don't want to guess. "
          "Let me pass you to a colleague who can help.",
    "ka": "ეს ინფორმაცია ჩემს ცოდნის ბაზაში არ მაქვს და ვარაუდი არ მინდა. "
          "გადაგაბარებთ კოლეგას, რომელიც დაგეხმარებათ.",
    "ru": "У меня нет этой информации в базе знаний, и я не хочу гадать. "
          "Передам вас коллеге, который сможет помочь.",
}

# The customer asked for a person, complained, or threatened legal action. Before this existed
# those turns got the refusal copy — "I don't have that in my knowledge base" said to someone
# who had just asked for a human. Also served to the chat site as `handoff_notice`, the line it
# sends when IT hands a conversation over (circuit open, bot paused, an attachment), where a
# silent handoff left the customer with no reply at all.
DEFAULT_HANDOFF_NOTICE = {
    "en": "I'm passing this conversation to a colleague, who will reply here as soon as possible.",
    "ka": "ამ საუბარს კოლეგას გადავცემ — ის რაც შეიძლება მალე გიპასუხებთ აქვე.",
    "ru": "Передаю разговор коллеге — он ответит здесь как можно скорее.",
}

# Distress markers (emergency, ambulance, self-harm). The emergency number comes first because
# it is the one sentence that must not be missed; `{number}` is the tenant's `emergency_number`.
DEFAULT_SAFETY_NOTICE = {
    "en": "If you or someone else may be in danger or needs urgent medical help, call {number} "
          "now. I'm also passing this conversation to a colleague.",
    "ka": "თუ თქვენ ან ვინმე სხვა შესაძლოა საფრთხეში იყოს ან სასწრაფო სამედიცინო დახმარება "
          "სჭირდებოდეს, ახლავე დარეკეთ {number}-ზე. ამ საუბარს ასევე კოლეგას გადავცემ.",
    "ru": "Если вам или кому-то рядом может угрожать опасность или нужна срочная медицинская "
          "помощь, прямо сейчас позвоните по номеру {number}. Я также передаю разговор коллеге.",
}

# An off-topic question, when the triage model returned no redirect of its own.
DEFAULT_OFF_TOPIC_REDIRECT = {
    "en": "That's outside what I can help with here — I can answer questions about our services.",
    "ka": "ამ კითხვაზე აქ დახმარება არ შემიძლია — შემიძლია გიპასუხოთ ჩვენს მომსახურებასთან "
          "დაკავშირებულ კითხვებზე.",
    "ru": "С этим я здесь помочь не могу — я отвечаю на вопросы о наших услугах.",
}

# Appended once, on the turn the conversation's off-topic count reaches `off_topic_warn_after`.
DEFAULT_OFF_TOPIC_WARNING = {
    "en": "Just so you know: I'm here for questions about our services. If the next questions "
          "are unrelated too, I'll stop answering them in this chat.",
    "ka": "გაცნობებთ: აქ ჩვენს მომსახურებასთან დაკავშირებულ კითხვებზე გიპასუხებთ. თუ "
          "მომდევნო კითხვებიც სხვა თემაზე იქნება, ამ ჩატში მათ აღარ ვუპასუხებ.",
    "ru": "Обратите внимание: здесь я отвечаю на вопросы о наших услугах. Если следующие "
          "вопросы тоже будут не по теме, в этом чате я перестану на них отвечать.",
}

# From `off_topic_cutoff_after` on, instead of any model call for a message the KB does not
# clearly cover. The words it tells the customer to write are ones `chat_safety.
# ESCALATION_PATTERNS` recognises, so following the instruction really does reach a person on
# channels with no "talk to a human" button (WhatsApp, Messenger).
DEFAULT_OFF_TOPIC_CUTOFF = {
    "en": "I can only help with questions about our services, so I won't answer other topics in "
          "this chat. Ask me about our services, or write \"talk to a person\" and a colleague "
          "will take over.",
    "ka": "აქ მხოლოდ ჩვენს მომსახურებასთან დაკავშირებულ კითხვებზე შემიძლია დაგეხმაროთ, ამიტომ "
          "ამ ჩატში სხვა თემებზე აღარ ვუპასუხებ. მკითხეთ ჩვენი მომსახურების შესახებ ან "
          "დაწერეთ „ოპერატორი“, და კოლეგა ჩაერთვება.",
    "ru": "Я могу помочь только с вопросами о наших услугах, поэтому на другие темы в этом чате "
          "больше не отвечаю. Спросите о наших услугах или напишите «оператор» — и подключится "
          "коллега.",
}


def pick(copy: dict | None, locale: str, fallback: dict) -> str:
    """The first non-empty text for `locale`, then English, from `copy`, then from `fallback`.

    Empty means "use the built-in wording" for every map this module backs — unlike the
    disclosure line, where present-but-empty is a deliberate suppression.
    """
    loc = (locale or "").strip().lower()[:2]
    loc = loc if loc in SUPPORTED else "en"
    for source in (copy if isinstance(copy, dict) else {}, fallback):
        for key in (loc, "en"):
            text = str(source.get(key) or "").strip()
            if text:
                return text
    return ""


def builtin_copy() -> dict:
    """The placeholders the console forms show: what a customer reads when a box is empty."""
    return {
        "refusal": dict(DEFAULT_REFUSAL),
        "off_topic_warning": dict(DEFAULT_OFF_TOPIC_WARNING),
        "off_topic_cutoff": dict(DEFAULT_OFF_TOPIC_CUTOFF),
    }
