"""Ask the REAL ElevenLabs API which audio formats it accepts, one request per format.

Run this before changing `transcription.CODE_DEFAULTS["audio_format"]`. The default decides how
EVERY call in the product is encoded, so "lossless cannot be worse than lossy" is an argument,
not evidence — this is the evidence.

    docker compose run --rm -T -v "$PWD/backend:/app" api python scripts/verify_stt_formats.py

It needs a working ELEVENLABS_API_KEY (env, or the admin panel's stored integrations blob) and
costs a few seconds of Scribe per format. Give it a real recording to be sure the transcript
itself is right, not just the HTTP status:

    ... python scripts/verify_stt_formats.py /path/to/call.m4a

With no file it uses 0.4 s of generated silence, which proves the format is ACCEPTED but tells
you nothing about accuracy.
"""
import asyncio
import sys
import time

sys.path.insert(0, "/app")

from app import db                                          # noqa: E402
from app.services import elevenlabs, settings_store          # noqa: E402
from app.services.audio import STT_FORMATS, to_stt_format    # noqa: E402


async def main(path: str | None) -> int:
    # `get_effective` reads the admin panel's stored keys, which means the pool: this script
    # runs outside the app's lifespan, so open one of its own.
    await db.connect()
    cfg = await settings_store.get_effective()
    key, model = cfg.get("elevenlabs_api_key"), cfg.get("stt_model")
    if not key:
        print("no ElevenLabs API key configured (.env ELEVENLABS_API_KEY or the admin panel)")
        return 2

    if path:
        with open(path, "rb") as f:
            raw = f.read()
        name, ctype = path.rsplit("/", 1)[-1], ""
    else:
        raw, name, ctype = elevenlabs.silence_wav(), "probe.wav", "audio/wav"
    print(f"model={model}  source={name}  {len(raw)} bytes\n")

    worst = 0
    for fmt in sorted(STT_FORMATS):
        payload = await to_stt_format(raw, name, ctype, fmt)
        started = time.monotonic()
        try:
            out = await elevenlabs.transcribe(raw, name, ctype, key, model, audio_format=fmt)
        except elevenlabs.ElevenLabsError as exc:
            worst = max(worst, 1)
            print(f"  {fmt:<10} REFUSED  ({exc.code}) {exc}")
            continue
        ms = int((time.monotonic() - started) * 1000)
        text = (out.get("text") or "").strip().replace("\n", " ")
        print(f"  {fmt:<10} OK  {len(payload.data):>9} bytes  {ms:>5} ms  "
              f"lang={out.get('language_code') or '?':<4} {text[:90]}")
    return worst


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else None)))
