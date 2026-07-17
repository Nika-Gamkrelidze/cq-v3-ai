"""Normalize any uploaded audio/video into the format the STT model handles best.

Users/partners can upload almost anything — mp3, wav, m4a, aac, flac, ogg, opus, wma, amr,
3gp, aiff, or a video (mp4/mov/mkv/webm). ffmpeg transcodes it all to **mono 16 kHz MP3**,
the standard for speech recognition: it guarantees a format ElevenLabs accepts, strips the
video track (which just wastes bandwidth), and downmixes to mono for diarization.

Never blocks the pipeline — if ffmpeg can't handle a file, we fall back to the original
bytes so the STT still gets a chance at it.
"""
import asyncio
import logging
import os
import shutil
import tempfile

log = logging.getLogger("cq")

# mono, 16 kHz — wideband speech, what ASR models expect.
_FFMPEG_ARGS = ["-vn", "-ac", "1", "-ar", "16000", "-f", "mp3"]
_TIMEOUT = 180  # seconds — a long/large video can take a while to demux


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


async def to_stt_format(data: bytes, filename: str = "audio",
                        content_type: str = "") -> tuple[bytes, str, str]:
    """Convert `data` to mono 16 kHz MP3. Returns (bytes, filename, content_type).
    On any failure returns the ORIGINAL (bytes, filename, content_type) unchanged."""
    original = (data, filename or "audio", content_type or "application/octet-stream")
    if not data or not ffmpeg_available():
        return original

    in_path = out_path = None
    try:
        # ffmpeg needs seekable input for some containers (mp4/mov), so write a temp file.
        base = os.path.splitext(os.path.basename(filename or "audio"))[0] or "audio"
        fd_in, in_path = tempfile.mkstemp(suffix="_in")
        with os.fdopen(fd_in, "wb") as f:
            f.write(data)
        fd_out, out_path = tempfile.mkstemp(suffix=".mp3")
        os.close(fd_out)

        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-nostdin", "-y", "-i", in_path, *_FFMPEG_ARGS, out_path,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=_TIMEOUT)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            raise RuntimeError("audio conversion timed out")
        if proc.returncode != 0:
            raise RuntimeError((stderr or b"").decode(errors="replace")[-400:])

        with open(out_path, "rb") as f:
            out = f.read()
        if not out:
            raise RuntimeError("conversion produced no output")
        return out, base + ".mp3", "audio/mpeg"
    except Exception as exc:  # noqa: BLE001 — never block; let the STT try the raw bytes
        log.warning("audio conversion failed (%s); sending the original file", exc)
        return original
    finally:
        for p in (in_path, out_path):
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
