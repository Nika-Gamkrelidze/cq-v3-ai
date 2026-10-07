"""Normalize an uploaded audio/video file into the shape the STT model is sent.

Users/partners can upload almost anything — mp3, wav, m4a, aac, flac, ogg, opus, wma, amr,
3gp, aiff, or a video (mp4/mov/mkv/webm) — so ffmpeg strips the video track and downmixes to
mono, which is what diarization wants and what stops us shipping a video's whole bitrate to
ElevenLabs.

WHAT the audio is encoded as is now a **setting**, not a constant (services/transcription.py).
The reason is the Georgian bug this module is implicated in: the pipeline always re-encoded to
**lossy** MP3, and lossy compression is a genuine suspect for blurring the minimal pair
წ/თ ("36 თვემდე" → "36 წლამდე" — months read as years). 16 kHz is not the suspect: it is the
rate ElevenLabs expects. So the choices below hold the rate and vary the *lossiness*:

    original    the uploaded bytes, untouched (ffmpeg is never invoked)
    flac_full   FLAC, mono, the file's own sample rate   — lossless, full bandwidth
    flac_16k    FLAC, mono, 16 kHz                       — lossless at the expected rate
    wav_16k     WAV PCM s16le, mono, 16 kHz              — plus file_format=pcm_s16le_16
    mp3_16k     lossy MP3, mono, 16 kHz                  — what the product always did

**Never blocks the pipeline.** If ffmpeg cannot handle a file we fall back to the ORIGINAL
bytes so the STT still gets a chance at it. A failed convert must never lose a customer's
recording — and the fallback deliberately clears `file_format` too: the bytes we end up
sending are no longer the PCM we promised, and a stale hint would be a lie about the payload.
"""
import asyncio
import logging
import os
import shutil
import tempfile
from typing import NamedTuple

log = logging.getLogger("cq")

_TIMEOUT = 180  # seconds — a long/large video can take a while to demux


class SttPayload(NamedTuple):
    """Exactly what goes on the wire: the bytes, how they are named and typed in the
    multipart part, and the ElevenLabs `file_format` hint (None = do not send one)."""

    data: bytes
    filename: str
    content_type: str
    file_format: str | None = None


class _Spec(NamedTuple):
    args: tuple[str, ...] | None      # ffmpeg args; None = passthrough, no ffmpeg at all
    ext: str
    content_type: str
    file_format: str | None


# ffmpeg args are the OUTPUT half only (input is a temp file, output is a temp file).
# `-vn` everywhere: a video upload must never ship its picture to a speech model.
STT_FORMATS: dict[str, _Spec] = {
    "original":  _Spec(None, "", "", None),
    "flac_full": _Spec(("-vn", "-ac", "1", "-f", "flac"), ".flac", "audio/flac", None),
    "flac_16k":  _Spec(("-vn", "-ac", "1", "-ar", "16000", "-f", "flac"),
                       ".flac", "audio/flac", None),
    # `pcm_s16le_16` is ElevenLabs' documented low-latency path and it describes exactly what
    # these args produce: 16-bit little-endian PCM, mono, 16 kHz. The RIFF header rides along
    # (a real .wav is what every other consumer of these bytes could read); it is 44 bytes =
    # 22 whole samples, so even if the server treated the header as audio the stream stays
    # sample-aligned and the cost is ~1.4 ms of noise before speech starts.
    "wav_16k":   _Spec(("-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", "-f", "wav"),
                       ".wav", "audio/wav", "pcm_s16le_16"),
    "mp3_16k":   _Spec(("-vn", "-ac", "1", "-ar", "16000", "-f", "mp3"),
                       ".mp3", "audio/mpeg", None),
}

# The format the product ships with. See services/transcription.py::CODE_DEFAULTS for why
# this is still the lossy one — it is an evidence question, and the evidence is not in yet.
DEFAULT_STT_FORMAT = "mp3_16k"

# ---------------------------------------------------------------------------------------------
# What a provider can READ. ElevenLabs Scribe takes nearly any audio or video container, so the
# workspace's `audio_format` (above) was only ever a quality choice. Google and OpenAI accept a
# fixed list, and the same choice — `original` above all, or a conversion that fell back to the
# upload — could hand them a container they reject (a phone's .amr, a .wma, a .3gp, a video).
# Each speech-to-text adapter therefore declares the MIME types it accepts and the format to
# convert to when the chosen one is not among them (`for_provider`).
# ---------------------------------------------------------------------------------------------

# Spellings browsers and recorders use for the same thing -> the one the provider lists.
_MIME_ALIASES = {
    "audio/x-wav": "audio/wav", "audio/wave": "audio/wav", "audio/vnd.wave": "audio/wav",
    "audio/x-pn-wav": "audio/wav", "audio/x-flac": "audio/flac", "audio/x-aiff": "audio/aiff",
    "audio/x-m4a": "audio/m4a", "audio/mp4": "audio/m4a", "audio/x-mp3": "audio/mpeg",
    "audio/mpeg3": "audio/mpeg", "audio/x-mpeg": "audio/mpeg", "audio/mpg": "audio/mpeg",
    "audio/x-aac": "audio/aac", "audio/vorbis": "audio/ogg", "audio/x-ogg": "audio/ogg",
}
_EXT_MIME = {
    "mp3": "audio/mpeg", "mpga": "audio/mpeg", "wav": "audio/wav", "flac": "audio/flac",
    "m4a": "audio/m4a", "aac": "audio/aac", "ogg": "audio/ogg", "oga": "audio/ogg",
    "opus": "audio/opus", "weba": "audio/webm", "aif": "audio/aiff", "aiff": "audio/aiff",
    "amr": "audio/amr", "3gp": "audio/3gpp", "wma": "audio/x-ms-wma",
    "mp4": "video/mp4", "m4v": "video/mp4", "mov": "video/quicktime", "webm": "video/webm",
    "mkv": "video/x-matroska", "avi": "video/x-msvideo",
}


def mime_of(content_type: str | None, filename: str | None) -> str:
    """The audio's MIME type in the spelling providers list: the declared type when it names
    audio or video, else the file extension's, else application/octet-stream."""
    ct = (content_type or "").split(";", 1)[0].strip().lower()
    if ct.startswith(("audio/", "video/")):
        return _MIME_ALIASES.get(ct, ct)
    name = (filename or "").rsplit("/", 1)[-1]
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return _EXT_MIME.get(ext, "application/octet-stream")


async def for_provider(data: bytes, filename: str, content_type: str, audio_format: str | None,
                       *, accepts: frozenset[str] | None, fallback: str) -> SttPayload:
    """`to_stt_format`, then make sure the provider can read the result.

    `accepts` is the provider's list of MIME types (None = it reads anything, as Scribe does).
    When the workspace's format leaves the audio in a container outside that list, the ORIGINAL
    upload is converted again to `fallback` — one of `STT_FORMATS` the provider accepts. The
    returned payload carries the canonical MIME type. If even that cannot be produced (no ffmpeg,
    a file ffmpeg cannot decode) the first payload goes as it is: the provider then refuses it
    with its own sentence, which now reaches the user (`voice_base.json_object`).
    """
    payload = await to_stt_format(data, filename, content_type, audio_format)
    mime = mime_of(payload.content_type, payload.filename)
    if accepts is None or mime in accepts:
        return payload._replace(content_type=mime) if accepts is not None else payload
    log.info("stt: %s is not readable by this provider; converting to %s", mime, fallback)
    alt = await to_stt_format(data, filename, content_type, fallback)
    alt_mime = mime_of(alt.content_type, alt.filename)
    if alt_mime in accepts:
        return alt._replace(content_type=alt_mime)
    log.warning("stt: could not convert %s to %s; sending it as it is", mime, fallback)
    return payload._replace(content_type=mime)

def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


async def to_stt_format(data: bytes, filename: str = "audio", content_type: str = "",
                        audio_format: str | None = None) -> SttPayload:
    """Encode `data` for the STT request. Returns an `SttPayload`.

    `audio_format` is one of `STT_FORMATS` (default `DEFAULT_STT_FORMAT`); an unknown id falls
    back to the default rather than raising — validation belongs at the door
    (`transcription.validate`), and a config that somehow drifted must not stop a transcription.

    On ANY conversion failure — no ffmpeg, a codec it cannot read, a timeout, empty output —
    the ORIGINAL bytes come back with `file_format=None`.
    """
    original = SttPayload(data, filename or "audio", content_type or "application/octet-stream")
    spec = STT_FORMATS.get(audio_format or DEFAULT_STT_FORMAT)
    if spec is None:
        log.warning("unknown STT audio_format %r; using %s", audio_format, DEFAULT_STT_FORMAT)
        spec = STT_FORMATS[DEFAULT_STT_FORMAT]
    # 'original' is the one format that is not a conversion: no ffmpeg, no temp files, and
    # nothing that can fail. Checked before `ffmpeg_available()` so it works on a host without
    # ffmpeg at all.
    if spec.args is None:
        return original
    if not data or not ffmpeg_available():
        return original

    in_path = out_path = None
    try:
        # ffmpeg needs seekable input for some containers (mp4/mov), so write a temp file.
        base = os.path.splitext(os.path.basename(filename or "audio"))[0] or "audio"
        fd_in, in_path = tempfile.mkstemp(suffix="_in")
        with os.fdopen(fd_in, "wb") as f:
            f.write(data)
        fd_out, out_path = tempfile.mkstemp(suffix=spec.ext)
        os.close(fd_out)

        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-nostdin", "-y", "-i", in_path, *spec.args, out_path,
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
        return SttPayload(out, base + spec.ext, spec.content_type, spec.file_format)
    except Exception as exc:  # noqa: BLE001 — never block; let the STT try the raw bytes
        log.warning("audio conversion to %s failed (%s); sending the original file",
                    audio_format or DEFAULT_STT_FORMAT, exc)
        return original
    finally:
        for p in (in_path, out_path):
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
