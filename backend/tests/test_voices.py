"""GET /voices — the customer-facing voice list.

The admin panel marks the system defaults (the configured voice and the Georgian voice) as an
always-on tick and leaves them OUT of the saved allowlist; /tts accepts them regardless. The
customer list has to add them back, or the one voice the operator was promised is "always on"
is the one voice a customer cannot see — which is exactly the bug this file pins.
"""
import pytest

from app.services import elevenlabs, settings_store
from app.services.providers import tts_elevenlabs as el

LAURA = el.GEORGIAN_VOICE                       # "Laura - Natural & Grounded"
CHARLIE = "IKne3meq5aSn9XLyUdCD"
RACHEL = "21m00Tcm4TlvDq8ikWAM"

LIVE = [
    {"voice_id": CHARLIE, "name": "Charlie", "category": "premade", "preview_url": "u1"},
    {"voice_id": LAURA, "name": "Laura - Natural & Grounded", "category": "professional",
     "preview_url": "u2"},
    {"voice_id": RACHEL, "name": "Rachel", "category": "premade", "preview_url": "u3"},
]


@pytest.fixture
def stubs(monkeypatch):
    state = {"mode": "allowlist", "voice_ids": [CHARLIE], "tts_voice_id": LAURA, "live": LIVE}

    async def _list_voices(_key):
        return list(state["live"])

    async def _voice_config():
        return {"mode": state["mode"], "voice_ids": list(state["voice_ids"])}

    async def _effective():
        return {"elevenlabs_api_key": "k", "tts_voice_id": state["tts_voice_id"]}

    monkeypatch.setattr(elevenlabs, "list_voices", _list_voices)
    monkeypatch.setattr(settings_store, "get_voice_config", _voice_config)
    monkeypatch.setattr(settings_store, "get_effective", _effective)
    return state


def _ids(body):
    return [v["voice_id"] for v in body]


def test_allowlist_still_shows_the_system_default_by_name(api, stubs):
    body = api.get("/voices").json()
    # Laura was never in voice_ids — she must be there anyway, first, and flagged.
    assert _ids(body) == [LAURA, CHARLIE]
    assert body[0]["is_default"] is True and body[1]["is_default"] is False


def test_allowlist_keeps_the_admin_order_after_the_defaults(api, stubs):
    stubs["voice_ids"] = [RACHEL, CHARLIE]
    assert _ids(api.get("/voices").json()) == [LAURA, RACHEL, CHARLIE]


def test_a_default_ticked_explicitly_is_not_duplicated(api, stubs):
    stubs["voice_ids"] = [LAURA, CHARLIE]
    assert _ids(api.get("/voices").json()) == [LAURA, CHARLIE]


def test_all_mode_returns_everything_with_default_flags(api, stubs):
    stubs["mode"] = "all"
    body = api.get("/voices").json()
    assert _ids(body) == [CHARLIE, LAURA, RACHEL]
    assert [v["is_default"] for v in body] == [False, True, False]


def test_stale_allowlist_fails_open_to_the_full_list(api, stubs):
    stubs["voice_ids"] = ["gone-1", "gone-2"]
    stubs["tts_voice_id"] = "gone-default"
    stubs["live"] = [v for v in LIVE if v["voice_id"] != LAURA]
    assert _ids(api.get("/voices").json()) == [CHARLIE, RACHEL]
