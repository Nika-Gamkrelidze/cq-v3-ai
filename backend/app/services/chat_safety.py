"""Post-generation output validation for the public autopilot — all of it in Python.

This module runs AFTER the model has produced text and BEFORE that text is treated as an
answer. Nothing in here is a prompt instruction, and that is the whole point: the customer
half of a WhatsApp conversation is written by an anonymous stranger, so any rule that lives
only in the system prompt is a rule an injection gets to argue with. These rules are code.

What it defends, in order of how much money a failure costs:

1. **Links.** `strip_unsafe_markup` deletes markdown images outright and unwraps markdown
   links to their anchor text; `drop_foreign_urls` then deletes any bare URL that does not
   appear verbatim in a chunk we retrieved. A link is the only part of a chat reply that can
   *move a user somewhere*, which makes an injected phishing link the one directly
   monetisable outcome of a successful injection. The tenant's own KB is the allowlist.
2. **Commitments.** `detect_commitment` routes anything price/discount/refund/deadline/
   assurance-shaped to a human instead of letting the bot commit the tenant. See the honest
   caveat on `COMMITMENT_PATTERNS` below.
3. **Citations.** `resolve_citations` maps `[n]` markers onto the SERVER-held hits list.
4. **Length**, and 5. **escalation** — smaller, but both are "hand to a human" decisions and
   belong next to the others rather than scattered through the engine.

Everything here is pure, total and synchronous: no DB, no network, no model. That is what
lets `chat.py` call it on the hot path after the stream closes without another await, and
what makes each rule testable with a string literal.
"""
import logging
import re

log = logging.getLogger("cq")

# A chat reply is a chat message. Long answers are also where a model wanders off the
# passages, so the cap is a quality control as much as a channel constraint (WhatsApp
# renders ~4096 chars; we are nowhere near it and do not want to be).
DEFAULT_MAX_REPLY_CHARS = 1200


# --- citations ------------------------------------------------------------------

_CITE_RE = re.compile(r"\[(\d{1,2})\]")


def resolve_citations(text: str, hits: list[dict]) -> tuple[str, list[dict]]:
    """Map inline `[n]` markers onto `hits[n-1]`, dropping any marker with no hit.

    **The marker is not a control channel.** `n` is used for exactly one thing: an index into
    a list this process retrieved and holds. The model never sees, names, or emits a document
    id. So the worst a forged or hallucinated `[9]` can do is (a) get dropped, or (b) point at
    the wrong one of *our own* passages — a wrong citation index. It can never manufacture
    grounding state, because whether we were allowed to answer at all was decided by
    `chat.gate()`, in code, before this text existed.

    The rejected alternative was letting the model emit a metadata trailer naming its sources
    (`SOURCES: doc-abc, doc-def`). That is strictly worse: it puts a *control channel inside
    the token stream*, and the input to that stream is untrusted WhatsApp text. An injection
    that can write the trailer can name documents that were never retrieved — including
    internal ones — and we would be parsing attacker-authored ids. Index-into-server-list has
    no such failure mode, which is why the marker syntax is deliberately this dumb.

    Returns `(text with dead markers removed, [citation dicts])`. The citation dict shape
    matches `chat_prompts.build_citations` so the envelope has one citation shape end to end.
    """
    hits = hits or []
    count = len(hits)
    used: list[int] = []

    def _sub(match: re.Match) -> str:
        n = int(match.group(1))
        if 1 <= n <= count:
            if n not in used:
                used.append(n)
            return match.group(0)
        return ""  # dead marker: a customer reading "[9]" with five passages learns nothing

    cleaned = _CITE_RE.sub(_sub, text or "")
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned).strip()

    cites: list[dict] = []
    for n in sorted(used):
        h = hits[n - 1] or {}
        cites.append({
            "n": n,
            "document_id": h.get("document_id"),
            "chunk_id": h.get("chunk_id"),
            "title": h.get("title") or h.get("doc_type") or "KB",
            "score": h.get("score"),
        })
    return cleaned, cites


# --- markup and URLs ------------------------------------------------------------

# Images first, then links: an image is `![alt](src)` and its `[alt](src)` tail would
# otherwise be unwrapped by the link rule, leaving a stray "!".
_MD_IMAGE_RE = re.compile(r"!\[[^\]\n]*\]\((?:[^)\n]*)\)")
_MD_LINK_RE = re.compile(r"\[([^\]\n]*)\]\((?:[^)\n]*)\)")
_URL_RE = re.compile(r"https?://\S+|www\.[^\s<>()]+", re.IGNORECASE)


def strip_unsafe_markup(text: str) -> str:
    """Delete markdown images entirely; unwrap markdown links to their anchor text.

    Anchor text is information the customer may need ("see our returns policy"); the target
    is the part that can carry them to an attacker's domain. v1 policy from ADR-001 and
    deliberately blunt — a bot that occasionally drops a legitimate link is a support ticket,
    a bot that renders an injected `[click here](https://evil/)` is a fraud incident.
    """
    if not text:
        return ""
    out = _MD_IMAGE_RE.sub("", text)
    out = _MD_LINK_RE.sub(lambda m: m.group(1), out)
    return re.sub(r"[ \t]{2,}", " ", out).strip()


def drop_foreign_urls(text: str, hits: list[dict]) -> str:
    """Remove any bare URL that does not appear verbatim in the retrieved chunk text.

    The tenant's own published KB is the allowlist. This is intentionally an exact substring
    test rather than a domain match: "the domain is ours" is not the property we want, since
    an open redirect or a user-content path on a tenant's own domain is still an attacker's
    destination. If a URL is worth sending, it is worth putting in the KB.
    """
    if not text:
        return ""
    corpus = "\n".join(str(h.get("content") or "") for h in (hits or []))

    def _url(match: re.Match) -> str:
        url = match.group(0).rstrip(".,;:)]}")
        return match.group(0) if url and url in corpus else ""

    return re.sub(r"[ \t]{2,}", " ", _URL_RE.sub(_url, text)).strip()


def sanitize_output(text: str, hits: list[dict]) -> str:
    """`strip_unsafe_markup` then `drop_foreign_urls` — the order both paths use."""
    return drop_foreign_urls(strip_unsafe_markup(text), hits)


# --- commitment detection -------------------------------------------------------

# Commitment-shaped output, as (label, pattern) pairs. The label is what gets stored on the
# turn and shown to the operator who inherits the handoff, so it is part of the contract;
# add rows freely, rename them reluctantly.
#
# BE HONEST ABOUT WHAT THIS IS: a regex list is a blunt instrument. It over-fires (a KB
# passage quoting a price gets quoted back and trips `money`), it under-fires (a model can
# promise a refund in words none of these patterns contain), and it is trivially evadable by
# anyone who wants to evade it. It is NOT a safety classifier and must never be described as
# one in a security review.
#
# It is sized for exactly one job: *escalate to a human*. Every failure mode is asymmetric in
# our favour — a false positive costs one handoff (a human reads a fine answer and sends it),
# a false negative costs a promise the tenant has to honour. So it fails toward handoff, which
# is the safe direction, and it is meant to be extended by a human who has just watched their
# bot say something expensive. Extend it here; do not move this logic into the prompt, where
# an injected instruction gets a vote.
COMMITMENT_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("money", re.compile(
        r"\b\d[\d\s.,]*\s?(?:gel|usd|eur|lari|ლარ\w*|₾|\$|€|₽|руб\w*|доллар\w*|евро)",
        re.IGNORECASE)),
    ("percentage", re.compile(r"\b\d{1,3}\s?%")),
    ("discount", re.compile(
        r"\b(?:discount|promo\s?code|voucher|coupon)\b|скидк|ფასდაკლებ", re.IGNORECASE)),
    ("refund", re.compile(
        r"\b(?:refund\w*|reimburse\w*|compensat\w*|chargeback|money\s+back)\b"
        r"|возврат|компенсац|თანხის\s*დაბრუნებ|კომპენსაც", re.IGNORECASE)),
    ("guarantee", re.compile(
        r"\b(?:guarantee\w*|warrant(?:y|ies|ed)|we\s+promise|i\s+promise|you\s+will\s+"
        r"definitely|waive\w*)\b|гаранти|обещаю|гарантирую|გარანტი|გპირდებით",
        re.IGNORECASE)),
    ("deadline", re.compile(
        r"\b(?:within\s+\d+\s+(?:hour|day|week|business\s+day)s?|by\s+(?:tomorrow|monday|"
        r"tuesday|wednesday|thursday|friday|saturday|sunday)|delivered?\s+(?:on|by)\s+\d)"
        r"|в\s+течение\s+\d+\s+(?:час|дн|недел)|доставим\s+\d"
        r"|\d+\s*(?:დღე|საათ)ში|მოგიტანთ", re.IGNORECASE)),
    ("assurance", re.compile(
        r"\b(?:legally\s+entitled|you\s+are\s+covered|this\s+is\s+(?:safe|legal)\s+for\s+you"
        r"|(?:is|are)\s+not\s+(?:dangerous|harmful)|you\s+(?:do\s+not|don't)\s+need\s+a\s+"
        r"(?:doctor|lawyer))\b"
        r"|по\s+закону\s+вы|это\s+безопасно\s+для\s+вас"
        r"|კანონით\s+თქვენ|უსაფრთხოა\s+თქვენთვის", re.IGNORECASE)),
]


def detect_commitment(text: str) -> str | None:
    """The label of the first commitment pattern the text trips, or None.

    ADR-001 security bar item 7: commitment-shaped output forces a handoff *even when it is
    perfectly grounded*, because the cost of being wrong is not symmetric with the cost of
    being slow.
    """
    if not text:
        return None
    for label, pattern in COMMITMENT_PATTERNS:
        if pattern.search(text):
            return label
    return None


# --- length ---------------------------------------------------------------------

def enforce_length(text: str, max_chars: int = DEFAULT_MAX_REPLY_CHARS) -> str:
    """Trim to `max_chars` on a whitespace boundary, without leaving a half-written marker.

    Truncating mid-`[n` would leave a bracket the citation resolver has already run over, so
    the tail is swept of any dangling opening bracket after the cut.
    """
    if not text:
        return ""
    limit = max(1, int(max_chars or DEFAULT_MAX_REPLY_CHARS))
    if len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    if space > limit // 2:  # only snap back to a word boundary if it is not a huge loss
        cut = cut[:space]
    cut = re.sub(r"\[\d{0,2}$", "", cut).rstrip(" \t\n,;:")
    return cut + "…"


# --- escalation -----------------------------------------------------------------

# Signals that a human should take this conversation, independent of whether the bot had a
# good answer. Same honesty caveat as COMMITMENT_PATTERNS: blunt, extendable, fails toward
# handoff. Keyed by the reason string that ends up on the turn.
ESCALATION_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("legal_threat", re.compile(
        r"\b(?:lawyer|attorney|solicitor|sue\s+you|legal\s+action|court|ombudsman|"
        r"regulator|data\s+protection|gdpr)\b"
        r"|юрист|адвокат|в\s+суд|подам\s+в\s+суд|жалоб\w*\s+в"
        r"|ადვოკატ|სასამართლო|სარჩელ", re.IGNORECASE)),
    ("complaint", re.compile(
        r"\b(?:complaint|complain|unacceptable|scam|fraud|terrible\s+service|"
        r"speak\s+to\s+(?:a\s+)?(?:human|manager|supervisor|person)|"
        r"real\s+person|talk\s+to\s+someone)\b"
        r"|жалоба|обман|мошенн|безобрази|позов(?:и|ите)\s+человек|оператор[ауы]?\b"
        r"|საჩივარ|თაღლით|ოპერატორ|ადამიან(?:თან|ს)", re.IGNORECASE)),
    ("distress", re.compile(
        r"\b(?:emergency|urgent(?:ly)?|ambulance|suicid\w*|self[-\s]?harm|"
        r"i\s+(?:can'?t|cannot)\s+(?:cope|go\s+on)|help\s+me\s+please)\b"
        r"|срочно|скорая|неотложк|суицид|мне\s+плохо"
        r"|სასწრაფო|გადაუდებელ|თვითმკვლელ", re.IGNORECASE)),
]


def _cfg_keywords(cfg: dict) -> list[str]:
    """Tenant escalation keywords: top level first, then the `settings` jsonb — the same
    two-level lookup `chat._cfg` does, kept local so this module stays import-free."""
    cfg = cfg or {}
    raw = cfg.get("escalation_keywords")
    if raw is None:
        blob = cfg.get("settings")
        raw = blob.get("escalation_keywords") if isinstance(blob, dict) else None
    if not isinstance(raw, (list, tuple)):
        return []
    return [str(k).strip().lower() for k in raw if str(k or "").strip()]


def should_escalate(text: str, cfg: dict) -> str | None:
    """Reason this conversation wants a human, or None.

    Two sources, tenant first: the tenant's own `escalation_keywords` (a clinic wants
    "chest pain", a bank wants "card stolen" — we cannot guess those), then the built-in
    distress / complaint / legal-threat markers above.

    Called on the CUSTOMER's message, not the bot's: "let me speak to a human" is a request
    the bot must honour even when it has a perfectly grounded answer to the literal question.
    """
    if not text:
        return None
    lowered = text.lower()
    for keyword in _cfg_keywords(cfg):
        if keyword and keyword in lowered:
            return "keyword"
    for reason, pattern in ESCALATION_PATTERNS:
        if pattern.search(text):
            return reason
    return None
