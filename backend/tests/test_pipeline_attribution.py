"""A pipeline run bills its AI calls to ITS recording, whatever the context it inherited.

The partner batch creates several recordings in a loop and then runs one background pipeline
per row in the same request context, so `create_job`'s attribution held only the last row's id
and every run's transcription and checks were billed to that one recording. `run_pipeline` now
sets the job itself. No database and no provider: the transcription stub records what a real
call would have been billed to, then fails the run at its first stage.
"""
import asyncio

from app.services import analysis, attribution


def test_every_run_attributes_to_its_own_job(monkeypatch):
    billed: list[str | None] = []

    async def _noop(*a, **kw):
        return None

    async def _effective():
        return {}

    async def _transcribe(client_id, audio, filename, content_type, **kw):
        billed.append(attribution.current()[1])
        raise RuntimeError("stop here")

    monkeypatch.setattr(analysis, "_update", _noop)
    monkeypatch.setattr(analysis.settings_store, "get_effective", _effective)
    monkeypatch.setattr(analysis.voice, "transcribe", _transcribe)

    async def _batch():
        # What the partner batch did: the context names the LAST row created...
        attribution.set_job("33333333-3333-4333-8333-333333333333")
        for job in ("11111111-1111-4111-8111-111111111111",
                    "22222222-2222-4222-8222-222222222222"):
            out = await analysis.run_pipeline(job, b"", "a.wav", "audio/wav", None, False,
                                              stt_settings={})
            assert out["status"] == "error"

    asyncio.run(_batch())
    # ...and each run still bills its own.
    assert billed == ["11111111-1111-4111-8111-111111111111",
                      "22222222-2222-4222-8222-222222222222"]
