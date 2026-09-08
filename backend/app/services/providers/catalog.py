"""The provider catalog: which providers exist for each capability, and what each one takes.

DATA ONLY. This module imports no adapter and no SDK, so the console, the resolver and the
tests can read it without a provider's client library being installed or a key being present.
`services/llm.py` and `services/voice.py` own the mapping from these ids to adapter modules.

`known_models` is exactly that — KNOWN, NOT EXHAUSTIVE. Providers ship models faster than this
file changes, so the UI lets an operator type any id; the list is what a dropdown offers first
and what a fresh connection is pre-filled with.

`allows_base_url`: a gateway in front of Anthropic or OpenAI is a real deployment (a corporate
proxy, a region pin, a spend-control gateway), so those two accept one. Gemini and ElevenLabs
have no compatible-gateway ecosystem worth a field. Whoever CAN set it is decided elsewhere and
is narrower still: a tenant never can (routers/ai_tenant.py), whatever the catalog says.

`fields`: extra per-connection settings keys the provider takes for this capability, beyond
model / base_url / key. TTS needs a voice; nothing else needs anything today.
"""
from __future__ import annotations

CAPABILITIES = ("llm", "stt", "tts")

CATALOG: dict[str, dict[str, dict]] = {
    "llm": {
        "anthropic": {
            "label": "Anthropic (Claude)",
            # The three ids the console's Integrations tab already offers.
            "known_models": ["claude-opus-4-8", "claude-sonnet-5", "claude-haiku-4-5"],
            "allows_base_url": True,
            "fields": [],
        },
        "openai": {
            "label": "OpenAI (GPT)",
            # Known, not exhaustive: the current flagship and the fast model.
            "known_models": ["gpt-5", "gpt-5-mini"],
            "allows_base_url": True,
            "fields": [],
        },
        "gemini": {
            "label": "Google (Gemini)",
            # Known, not exhaustive: the current flagship and the fast model.
            "known_models": ["gemini-2.5-pro", "gemini-2.5-flash"],
            "allows_base_url": False,
            "fields": [],
        },
    },
    "stt": {
        "elevenlabs": {
            "label": "ElevenLabs (Scribe)",
            "known_models": ["scribe_v1"],
            "allows_base_url": False,
            "fields": [],
        },
        "openai": {
            "label": "OpenAI (transcription)",
            "known_models": ["gpt-4o-transcribe", "gpt-4o-mini-transcribe", "whisper-1"],
            "allows_base_url": True,
            "fields": [],
        },
    
        "gemini": {
            "label": "Google (Gemini)",
            # Multimodal generateContent with a transcript schema — no transcription endpoint.
            # Known, not exhaustive; both take audio. Flash is the sensible default for calls.
            "known_models": ["gemini-2.5-flash", "gemini-2.5-pro"],
            "allows_base_url": False,
            "fields": [],
        },
    },
    "tts": {
        "elevenlabs": {
            "label": "ElevenLabs (text-to-speech)",
            "known_models": ["eleven_multilingual_v2", "eleven_v3", "eleven_flash_v2_5",
                             "eleven_turbo_v2_5"],
            "allows_base_url": False,
            "fields": ["voice_id"],
        },
        "openai": {
            "label": "OpenAI (text-to-speech)",
            "known_models": ["gpt-4o-mini-tts", "tts-1", "tts-1-hd"],
            "allows_base_url": True,
            "fields": ["voice_id"],
        },
    },
}


def providers_for(capability: str) -> dict[str, dict]:
    """The catalog entries for one capability ({} for an unknown capability — never raises,
    so a UI can render an empty dropdown rather than a 500)."""
    return dict(CATALOG.get(capability) or {})


def is_known(capability: str, provider: str) -> bool:
    return bool(provider) and provider in (CATALOG.get(capability) or {})


def allows_base_url(capability: str, provider: str) -> bool:
    entry = (CATALOG.get(capability) or {}).get(provider) or {}
    return bool(entry.get("allows_base_url"))
