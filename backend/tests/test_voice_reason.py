"""Why voice tone was unavailable must be OBSERVED, not guessed.

The failure this pins: every failed prosody call reported "timeout", which the UI renders as
"the voice model was still starting up". That is a fair description for the first minute
after a deploy and a lie for ever after — and it was the only thing anyone saw when the
sidecar could not load its model at all, so a permanently broken service looked like one
that needed another minute. `_why_unavailable` asks the sidecar's own `/health` instead.
"""
import httpx
import pytest

from app.services import sentiment


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _client_returning(payload, status=200, raises=None):
    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            if raises:
                raise raises
            return _Resp(payload, status)

    return _Client


@pytest.mark.asyncio
@pytest.mark.parametrize("health,expected", [
    # Still loading: retrying really is worth it.
    ({"status": "ok", "loaded": False, "warm_error": None}, "warming"),
    # Loaded and STILL failing: the model is fine, the call was not — keep the caller's reason.
    ({"status": "ok", "loaded": True, "warm_error": None}, "timeout"),
    # The load itself failed. Retrying forever will not help; an operator has to look.
    ({"status": "ok", "loaded": False, "warm_error": "OSError: cannot reach hub"}, "model_error"),
])
async def test_reason_comes_from_the_sidecars_own_health(monkeypatch, health, expected):
    monkeypatch.setattr(httpx, "AsyncClient", _client_returning(health))
    assert await sentiment._why_unavailable("http://sentiment:8080", "timeout") == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [
    {"payload": "not a dict"},
    {"status": 500},
    {"raises": httpx.ConnectError("refused")},
])
async def test_an_unusable_probe_keeps_the_callers_reason(monkeypatch, bad):
    """The probe explains a failure; it must never become a second one."""
    monkeypatch.setattr(httpx, "AsyncClient", _client_returning(
        bad.get("payload", {"loaded": False}), bad.get("status", 200), bad.get("raises")))
    assert await sentiment._why_unavailable("http://sentiment:8080", "unreachable") == "unreachable"


def test_every_reason_the_backend_can_return_has_a_message():
    """A status with no string renders as a blank explanation — the same dead end in a new
    disguise. Checked against the workbench's own dictionary."""
    import pathlib
    import re

    js = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "public" / "workbench.js"
    if not js.exists():
        pytest.skip("frontend is not mounted in this container")
    text = js.read_text(encoding="utf-8")
    for reason in ("timeout", "unreachable", "disabled", "error", "no_timestamps", "no_audio",
                   "warming", "model_error"):
        assert re.search(rf"'wb\.novoice\.{reason}'\s*:", text), f"no message for {reason!r}"
