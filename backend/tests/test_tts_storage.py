"""Which TTS callers get their clip kept on disk (`routers/tts.py::_keeps_clip`).

Every synthesis is recorded as a row — text, voice, model, who, when — because a paid public
endpoint has to be investigable. Keeping the MP3 as well is a separate decision, and it is a
disk decision: the clip sits on the shared media volume for `retention_days` (30 by default),
in the same place every stored recording now lives. So it is kept only where somebody will
actually press play — a registered user's account History, a tenant login's portal History,
and the anonymous case that predates all of this — and NOT for `X-API-Key` traffic, which is
the server-to-server bulk path (5000 characters a call, uncapped by default).

Pure: no database, no network, no keys.
"""
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.routers import tts                 # noqa: E402 — follows the sys.path bootstrap
from app.services.auth import Principal     # noqa: E402


@pytest.mark.parametrize("principal, kept", [
    (Principal(kind="anonymous", anon_key="203.0.113.7", via="none"), True),
    (Principal(kind="user", user_id="u-1", via="token"), True),
    (Principal(kind="tenant", client_id="c-1", user_id="tu-1", role="owner", via="token"), True),
    (Principal(kind="tenant", client_id="c-1", role="apikey", via="apikey"), False),
    (Principal(kind="superadmin", via="admin"), False),
    (Principal(kind="integration", integration_id="i-1", via="integration"), False),
])
def test_who_keeps_a_clip(principal, kept):
    assert tts._keeps_clip(principal) is kept


def test_the_bulk_path_is_told_apart_by_via_not_by_kind():
    """The two tenant credentials are the same `kind`; only `via` separates the person in the
    portal from the integration looping over a catalogue. If a future refactor drops `via`
    from the tenant Principal, this is what fails instead of a disk filling up quietly."""
    login = Principal(kind="tenant", client_id="c-1", via="token")
    apikey = Principal(kind="tenant", client_id="c-1", via="apikey")
    assert tts._keeps_clip(login) and not tts._keeps_clip(apikey)
    assert "tenant" not in tts._STORED_KINDS      # never unconditionally, by kind alone
