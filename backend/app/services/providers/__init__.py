"""Provider adapters, one module per (capability, provider).

The rest of the app never imports a provider SDK directly: the LLM call sites go through
`services/llm.py`, the voice call sites through `services/voice.py`, and those two resolve WHICH
provider a tenant runs on (`services/ai_resolve.py`) and dispatch here. That is what lets a
tenant be moved from Anthropic to Gemini, or from ElevenLabs to another voice provider, with a
dropdown rather than a deploy.

Naming: `llm_<provider>.py`, `stt_<provider>.py`, `tts_<provider>.py`. Each exposes a module-level
adapter object; `services/llm.py` and `services/voice.py` own the registries of which ids map to
which module, so adding a provider is one file plus one dictionary entry.
"""
