"""The voice-tone sidecar's status, and the failure it used to hide.

The prosody half of sentiment fails SILENTLY by design: when the sidecar is missing or broken
the API returns the text half alone and every call is still a 200. That is right for a
customer and wrong for an operator, who then has no way to learn the tone model is dead.

The console's probe DID ask the sidecar's /health and then threw the answer away: it read
`loaded`, found it false, and reported "reachable; model loads on first use" — true for the
first minute after a deploy and a lie every minute after that, because the sidecar also
returns `warm_error` when the checkpoint failed to load. A permanently broken model therefore
showed a GREEN row forever. These tests pin the distinction that fix turns on.
"""
import httpx
import pytest

from app.services import sentiment


def _sidecar(monkeypatch, payload, *, status_code=200, url="http://sentiment:8080"):
    """Point the service at a fake sidecar. `payload` may be a dict, or an exception to raise."""
    async def _cfg():
        return {"sentiment_url": url}
    monkeypatch.setattr(sentiment.settings_store, "get_effective", _cfg)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/health"
        if isinstance(payload, Exception):
            raise payload
        return httpx.Response(status_code, json=payload)

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def client(*a, **kw):
        kw.pop("transport", None)
        return real_client(*a, transport=transport, **kw)

    monkeypatch.setattr(sentiment.httpx, "AsyncClient", client)


@pytest.fixture(autouse=True)
def _no_cache(monkeypatch):
    """`status()` caches for 10 s; every test here wants a fresh probe."""
    monkeypatch.setattr(sentiment, "_status_cache", None)


@pytest.mark.asyncio
async def test_a_loaded_model_is_ok(monkeypatch):
    _sidecar(monkeypatch, {"status": "ok", "model": "superb/wav2vec2-base-superb-er",
                           "loaded": True, "warm_error": None})
    out = await sentiment.status()
    assert out["state"] == "ok" and "superb/wav2vec2" in out["detail"]


@pytest.mark.asyncio
async def test_still_loading_is_warming_not_broken(monkeypatch):
    """The genuine post-deploy state: not loaded, but nothing has failed yet."""
    _sidecar(monkeypatch, {"model": "m", "loaded": False, "warm_error": None})
    assert (await sentiment.status())["state"] == "warming"


@pytest.mark.asyncio
async def test_a_failed_checkpoint_is_model_error_and_carries_the_reason(monkeypatch):
    """THE REGRESSION THIS FILE EXISTS FOR. `loaded: false` + `warm_error` is not "warming":
    it is a model that will never load, and retrying forever will not help."""
    _sidecar(monkeypatch, {"model": "m", "loaded": False,
                           "warm_error": "OSError: can't load feature extractor for 'm'"})
    out = await sentiment.status()
    assert out["state"] == "model_error"
    assert "can't load feature extractor" in out["detail"]   # the operator needs the cause


@pytest.mark.asyncio
async def test_an_unreachable_sidecar_is_not_a_model_error(monkeypatch):
    """Not built, not running, wrong URL, crash loop — a different fix from a bad checkpoint."""
    _sidecar(monkeypatch, httpx.ConnectError("connection refused"))
    out = await sentiment.status()
    assert out["state"] == "unreachable" and "text-only" in out["detail"]


@pytest.mark.asyncio
async def test_no_url_configured_is_disabled_on_purpose(monkeypatch):
    _sidecar(monkeypatch, {}, url="")
    assert (await sentiment.status())["state"] == "disabled"


@pytest.mark.asyncio
async def test_a_non_object_health_body_degrades_rather_than_raising(monkeypatch):
    """A status probe never raises — it is called from /health, which must answer."""
    _sidecar(monkeypatch, ["not", "an", "object"])
    assert (await sentiment.status())["state"] == "unreachable"


@pytest.mark.asyncio
async def test_the_result_is_cached_so_health_does_not_probe_per_request(monkeypatch):
    calls = {"n": 0}

    async def _cfg():
        calls["n"] += 1
        return {"sentiment_url": ""}
    monkeypatch.setattr(sentiment.settings_store, "get_effective", _cfg)

    first = await sentiment.status()
    second = await sentiment.status()
    assert first == second and calls["n"] == 1
    await sentiment.status(force=True)          # the operator's own probe skips the cache
    assert calls["n"] == 2
