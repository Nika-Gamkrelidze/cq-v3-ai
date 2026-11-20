"""The NOC live feed: the hub's latest-wins queue, the collector loop, and the SSE route."""
import asyncio
import json
import time

import pytest

from app.config import settings
from app.routers import admin
from app.services import noc

ADMIN = {"X-Admin-Token": settings.admin_token}


def run(coro):
    return asyncio.run(coro)


def test_hub_latest_wins_for_a_reader_that_was_busy():
    async def go():
        hub = noc.Hub()
        async with hub.subscribe() as q:
            for i in (1, 2, 3):
                hub.publish({"n": i})
            assert q.get_nowait() == {"n": 3} and q.empty()
    run(go())


def test_hub_every_subscriber_gets_it_and_unsubscribes_on_exit():
    async def go():
        hub = noc.Hub()
        async with hub.subscribe() as a, hub.subscribe() as b:
            hub.publish({"n": 1})
            assert a.get_nowait() == b.get_nowait() == {"n": 1}
            assert len(hub._subs) == 2
        assert not hub._subs
        hub.publish({"n": 2})            # nobody listening: must not raise
        assert hub.latest == {"n": 2}
    run(go())


def test_current_serves_the_hub_when_fresh_and_computes_when_stale(monkeypatch):
    calls = []

    async def fake_overview():
        calls.append(1)
        return {"status": "ok", "checks": []}
    monkeypatch.setattr(noc, "overview", fake_overview)
    hub = noc.Hub()
    monkeypatch.setattr(noc, "hub", hub)

    empty = run(noc.current())
    assert len(calls) == 1 and empty["interval_s"] == noc.interval_s()

    hub.publish({"status": "warn", "interval_s": 10})
    assert run(noc.current())["status"] == "warn" and len(calls) == 1      # fresh: the hub copy

    hub.at = time.monotonic() - 3 * noc.interval_s() - 1                     # stale
    assert run(noc.current())["status"] == "ok" and len(calls) == 2


def test_collect_loop_runs_cycles_feeds_sinks_survives_a_bad_sink_and_cancels(monkeypatch):
    async def fake_overview():
        return {"status": "ok", "checks": []}
    monkeypatch.setattr(noc, "overview", fake_overview)
    hub = noc.Hub()
    monkeypatch.setattr(noc, "hub", hub)
    seen = []

    async def bad(_snap):
        raise RuntimeError("sink down")

    async def good(snap):
        seen.append(snap)
    monkeypatch.setattr(noc, "sinks", [bad, good])

    async def go():
        task = asyncio.create_task(noc.collect_loop(every_s=0.05))
        while len(seen) < 2:
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    run(asyncio.wait_for(go(), 2))
    assert seen[0]["interval_s"] == 0.05 and hub.latest["status"] == "ok"


def test_stream_needs_the_admin_token(api):
    assert api.get("/admin/noc/stream").status_code == 401


def test_stream_sends_a_snapshot_frame_at_once(api):
    # The TestClient buffers a response until it ends, so a never-ending stream is read through
    # the route's own generator, on the app's own loop (its DB pool belongs to that loop); the
    # route wiring (headers) is checked on the response object.
    async def first():
        gen = admin._noc_stream_body()
        try:
            return await gen.__anext__()
        finally:
            await gen.aclose()
    frame = api.portal.call(first)
    assert frame.startswith("event: snapshot\ndata: ") and frame.endswith("\n\n")
    snap = json.loads(frame.split("data: ", 1)[1])
    assert snap["checks"] and snap["interval_s"] > 0 and isinstance(snap["now"], str)

    resp = api.portal.call(admin.noc_stream)
    assert resp.media_type == "text/event-stream"
    assert resp.headers["x-accel-buffering"] == "no" and "no-transform" in resp.headers["cache-control"]
