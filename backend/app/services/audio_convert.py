"""Turn any uploaded recording into the file formats an Asterisk dialplan can actually play.

Why this is its own module when `audio.py` already drives the same binary: those two calls
have opposite failure policies, and mixing them would be a bug waiting to happen.
`audio.to_stt_format` normalises INPUT for a speech model, so falling back to the original
bytes is the kind answer — a file it could not transcode still might transcribe. This module
produces a DELIVERABLE. A file that is quietly handed back unconverted loads into a dialplan
and fails at call time, hours later and nowhere near the cause, so every failure here is
raised and named. `ConvertError` is that contract.

The catalog below is the single source of truth for what we offer. `GET /convert/formats`
serves it verbatim so the browser's dropdown can never drift from what ffmpeg is actually
asked to do. Every entry is MONO at a fixed sample rate, and that is not a preference we
could expose as a knob: it is how the telephony codecs are defined, and Asterisk picks the
codec from the FILE EXTENSION, so the extension is part of the format, not decoration.

Storage: a finished batch is one ZIP plus one JSON manifest in a directory named by an
unguessable token, on the same `media` volume the retention work uses. The BYTES are
deliberately NOT in a Postgres table, unlike `media.py`'s recordings — those are durable
business data that a retention or abuse question is asked of months later, whereas a
conversion batch is scratch: two hours for a visitor, up to a week for a signed-in caller
(the router decides; `Batch.close` takes the TTL). Keeping the manifest beside the bytes means
"delete the directory" deletes every trace of the bytes, with no second half to fall out of
step with the first. The router keeps a small `convert_batches` row per batch for a signed-in
caller's History; that row is a record of the batch, never the authority on the bytes, which
is why `locate()` reads the manifest and nothing else.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
import re
import secrets
import shutil
import tempfile
import time
import zipfile
from pathlib import Path

from . import media

log = logging.getLogger("cq")

# --------------------------------------------------------------------------- #
# The catalog
# --------------------------------------------------------------------------- #
# Applied to every conversion, whatever the target: drop the video track (a converted call
# recording is audio by definition), and drop the source metadata rather than carrying an
# uploader's ID3 comments into a file that will sit on a PBX.
_PRELUDE = ("-vn", "-map_metadata", "-1")

# Ordered: this IS the dropdown order, most generally useful first. `args` are the encoder
# half only — `_PRELUDE` and the input flags are added by `convert()` so they cannot be
# forgotten in a new row.
FORMATS: dict[str, dict] = {
    "wav": {
        "id": "wav",
        "label": "WAV 8 kHz · 16-bit PCM",
        "ext": ".wav",
        "rate": 8000,
        "args": ("-ar", "8000", "-ac", "1", "-c:a", "pcm_s16le", "-f", "wav"),
        "description": ("Asterisk's `wav` format. Also plays in any media player, so this is "
                        "the safe choice when you want to listen to the prompt yourself "
                        "before putting it on the PBX."),
    },
    "wav16": {
        "id": "wav16",
        "label": "WAV 16 kHz · 16-bit PCM (wideband)",
        "ext": ".wav16",
        "rate": 16000,
        "args": ("-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", "-f", "wav"),
        "description": ("Asterisk's wideband `WAV`. Twice the size of the 8 kHz file and worth "
                        "it only if the call legs are wideband — on a narrowband call Asterisk "
                        "downsamples it again."),
    },
    "alaw": {
        "id": "alaw",
        "label": "G.711 A-law · raw",
        "ext": ".alaw",
        "rate": 8000,
        "args": ("-ar", "8000", "-ac", "1", "-c:a", "pcm_alaw", "-f", "alaw"),
        "description": ("The European and Georgian trunk standard. Headerless samples in the "
                        "codec the call is already using, so Asterisk streams it to the "
                        "channel with no transcoding at all."),
    },
    "ulaw": {
        "id": "ulaw",
        "label": "G.711 μ-law · raw",
        "ext": ".ulaw",
        "rate": 8000,
        "args": ("-ar", "8000", "-ac", "1", "-c:a", "pcm_mulaw", "-f", "mulaw"),
        "description": ("The North American G.711 variant — same size and quality as A-law. "
                        "Pick it only if your carrier negotiates μ-law."),
    },
    "gsm": {
        "id": "gsm",
        "label": "GSM 06.10",
        "ext": ".gsm",
        "rate": 8000,
        "args": ("-ar", "8000", "-ac", "1", "-c:a", "libgsm", "-f", "gsm"),
        "description": ("Roughly a tenth the size of G.711, at an audible cost. Sensible for a "
                        "large library of long prompts on a space-constrained box; every "
                        "Asterisk build plays it natively."),
    },
    "g722": {
        "id": "g722",
        "label": "G.722 · wideband HD",
        "ext": ".g722",
        "rate": 16000,
        "args": ("-ar", "16000", "-ac", "1", "-c:a", "g722", "-f", "g722"),
        "description": ("16 kHz wideband at the same bitrate as G.711. Use it when your "
                        "endpoints negotiate G.722 — the prompt then sounds as wide as the "
                        "call does."),
    },
    "sln": {
        "id": "sln",
        "label": "Signed linear 8 kHz · raw",
        "ext": ".sln",
        "rate": 8000,
        "args": ("-ar", "8000", "-ac", "1", "-c:a", "pcm_s16le", "-f", "s16le"),
        "description": ("Asterisk's own internal format: nothing to decode at playback, so it "
                        "is the cheapest on CPU and the largest on disk. The usual choice for "
                        "hot prompts on a busy system."),
    },
    "sln16": {
        "id": "sln16",
        "label": "Signed linear 16 kHz · raw",
        "ext": ".sln16",
        "rate": 16000,
        "args": ("-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", "-f", "s16le"),
        "description": ("The wideband twin of `sln`, for a system running at 16 kHz "
                        "internally. Same zero-decode playback."),
    },
}

DEFAULT_FORMAT = "wav"

# --------------------------------------------------------------------------- #
# Limits
# --------------------------------------------------------------------------- #
# Sized against nginx's `client_max_body_size 200m` with room for multipart overhead, and
# against the fact that a batch is read into memory before either transport starts (see the
# router — the SSE generator must not outlive the UploadFile objects). A call recording is
# single-digit megabytes, so these bite only on video.
MAX_FILE_BYTES = 60 * 1024 * 1024
MAX_BATCH_FILES = 30
MAX_BATCH_BYTES = 150 * 1024 * 1024

# Matches `audio._TIMEOUT` on purpose: it is the same binary demuxing the same kind of file,
# and two different ceilings for "how long may ffmpeg take" is how one of them ends up wrong.
TIMEOUT_S = 180

# ffmpeg processes allowed to run at once ACROSS the whole api process, not per request.
# Conversion shares this box with the TEI encoder that serves live retrieval, so ten visitors
# clicking Convert must not become ten transcodes. Batches are converted file-by-file anyway;
# this is the ceiling on how many batches can be in ffmpeg simultaneously.
_SLOTS = asyncio.Semaphore(2)

# How long a finished batch stays downloadable BY DEFAULT — the anonymous TTL. Long enough that
# someone who converts thirty files, gets distracted and comes back still gets their ZIP; short
# enough that a public, unauthenticated endpoint cannot be used as free file hosting. A
# signed-in caller's batch gets a longer deadline from the router (`Batch.close(ttl_seconds=)`);
# this constant also remains the mtime rule the sweep applies to a directory with no readable
# manifest, whatever its owner would have been granted.
TTL_SECONDS = 2 * 3600

CONVERT_ROOT = Path(os.getenv("CONVERT_ROOT", str(media.MEDIA_ROOT / "convert")))
_ZIP_NAME = "bundle.zip"
_MANIFEST_NAME = "manifest.json"

# The token is the whole authorisation story for the bytes on disk, so it is validated before
# it is ever joined onto a path — a token is a path segment, and a path segment from a URL is
# how a download endpoint turns into an arbitrary-file reader.
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{22,64}$")


class ConvertError(Exception):
    """A conversion that failed, phrased for the person who uploaded the file."""


# --------------------------------------------------------------------------- #
# Filenames
# --------------------------------------------------------------------------- #
_PATH_SEP = re.compile(r"[\\/]+")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
# Reserved on Windows, and `:` also makes a mess on macOS. The user extracts this ZIP
# somewhere we do not control.
_RESERVED = re.compile(r'[<>:"|?*]')
_MAX_STEM = 80
# Trimmed off the FRONT of every stem. A leading dot hides the file; a leading dash is read as
# an OPTION by the tools this ZIP's audience actually uses on the extracted files — `ffmpeg -i
# *`, `sox * out.wav`, `cp * /var/lib/asterisk/sounds/`. Inside the archive such a name is
# inert, and it never reaches our own ffmpeg (the user's filename is a label here, never an
# argument); the damage would happen afterwards, on their box, to a name we chose for them.
_LEADING = "-. \t"


def _trim(stem: str) -> str:
    """Drop the leading characters above, and the trailing dots/spaces that break a Windows
    extraction. To a fixed point, so `-. -x` is not merely `. -x`."""
    prev = None
    while stem != prev:
        prev = stem
        stem = stem.strip(". ").lstrip(_LEADING)
    return stem


def catalog() -> list[dict]:
    """The dropdown, in the order it should be drawn.

    The ffmpeg arguments are deliberately left out: they are ours to change, and publishing
    them invites a caller to believe they are a parameter they can send.
    """
    return [{"id": f["id"], "label": f["label"], "ext": f["ext"], "rate": f["rate"],
             "channels": 1, "description": f["description"]} for f in FORMATS.values()]


def safe_output_name(filename: str | None, fmt: str) -> str:
    """A local, harmless name that still looks like the file the user uploaded.

    Recognisability matters — someone converting thirty prompts needs to know which is which —
    so the stem is kept, INCLUDING non-Latin characters: most of this product's users name
    their files in Georgian, and reducing those to underscores would make the ZIP useless.
    What is removed is everything that makes a name dangerous rather than foreign: directory
    components, control characters and NUL, leading dots and dashes, and the characters that
    break an extraction on Windows or macOS. The extension is never the user's — it is the format's,
    because Asterisk reads the codec off it.
    """
    name = _CONTROL.sub("", filename or "")
    name = _PATH_SEP.split(name)[-1]        # "../../etc/passwd" -> "passwd"; "C:\a\b.mp3" -> "b.mp3"
    name = _RESERVED.sub("", name)
    stem = name.rsplit(".", 1)[0] if "." in name[1:] else name
    stem = " ".join(stem.split())           # collapses newlines/tabs/runs of spaces
    stem = _trim(stem)                      # no ".hidden", no "..", no "--help"
    stem = _trim(stem[:_MAX_STEM]) or "audio"
    return stem + FORMATS[fmt]["ext"]


def dedupe(name: str, taken: set[str]) -> str:
    """Make `name` unique within one batch, remembering it in `taken`.

    Two uploads called `call.mp3` produce one output name, and a ZIP with two identical
    entries silently loses one of them on extraction — the exact failure a bulk converter
    exists to avoid. Case-insensitive because the filesystems people extract onto are.
    """
    key = name.casefold()
    if key not in taken:
        taken.add(key)
        return name
    stem, ext = os.path.splitext(name)
    n = 2
    while f"{stem}-{n}{ext}".casefold() in taken:
        n += 1
    out = f"{stem}-{n}{ext}"
    taken.add(out.casefold())
    return out


# --------------------------------------------------------------------------- #
# Conversion
# --------------------------------------------------------------------------- #
# ffmpeg's stderr is a build banner followed by the real reason. These are the failures a
# user can actually do something about, so they get said in words instead of a codec dump.
_FRIENDLY = (
    ("does not contain any stream",
     "This file has no audio track, so there is nothing to convert."),
    ("output file is empty",
     "Nothing could be decoded from this file — it may be corrupt or truncated."),
    ("invalid data found when processing input",
     "This does not look like an audio or video file we can read."),
    ("moov atom not found",
     "This video is truncated or still uploading — its index is missing."),
    ("decoder (codec none) not found",
     "This file's audio uses a codec this server cannot decode."),
    ("permission denied",
     "The server could not read the uploaded file."),
)


def _explain(stderr: bytes) -> str:
    text = (stderr or b"").decode(errors="replace")
    low = text.lower()
    for needle, message in _FRIENDLY:
        if needle in low:
            return message
    # Nothing recognised: the last few lines are where ffmpeg says what went wrong, and a
    # 400 KB banner is not something a human can act on.
    tail = "; ".join([ln.strip() for ln in text.strip().splitlines() if ln.strip()][-3:])
    return f"ffmpeg could not convert this file: {tail[-240:]}" if tail else \
        "ffmpeg could not convert this file."


async def convert(data: bytes, filename: str, fmt: str) -> tuple[bytes, str]:
    """Convert `data` to `fmt`. Returns (converted bytes, safe output filename).

    Raises `ConvertError` on anything that goes wrong — never the original bytes. See the
    module docstring for why that differs from `audio.to_stt_format`.
    """
    spec = FORMATS.get(fmt)
    if spec is None:
        raise ConvertError(f"Unknown target format {fmt!r}.")
    if not data:
        raise ConvertError("The file is empty.")
    if len(data) > MAX_FILE_BYTES:
        raise ConvertError(f"The file is larger than the {MAX_FILE_BYTES // (1024 * 1024)} MB "
                           "per-file limit.")
    if not audio_tools_available():
        raise ConvertError("Audio conversion is unavailable on this server (ffmpeg is missing).")

    out_name = safe_output_name(filename, fmt)
    in_path = out_path = None
    try:
        # A temp file, not a pipe: mp4/mov need a seekable input to find their index, and the
        # NAME is ours — the user's filename never becomes a path, only a label.
        fd_in, in_path = tempfile.mkstemp(suffix="_in")
        with os.fdopen(fd_in, "wb") as f:
            f.write(data)
        fd_out, out_path = tempfile.mkstemp(suffix=spec["ext"])
        os.close(fd_out)

        async with _SLOTS:
            # Argument list, never a shell: `-i in_path` and the encoder flags are all ours,
            # and nothing derived from the upload is ever an ffmpeg argument.
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-nostdin", "-y", "-i", in_path, *_PRELUDE, *spec["args"], out_path,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
            try:
                _, stderr = await asyncio.wait_for(proc.communicate(), timeout=TIMEOUT_S)
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                raise ConvertError(
                    f"Conversion timed out after {TIMEOUT_S} seconds. Try a shorter file.")

        if proc.returncode != 0:
            raise ConvertError(_explain(stderr))
        out = Path(out_path).read_bytes()
        if not out:
            raise ConvertError("Conversion produced an empty file.")
        return out, out_name
    except ConvertError:
        raise
    except OSError as exc:
        log.exception("conversion failed for %r -> %s", filename, fmt)
        raise ConvertError(f"The server could not convert this file: {exc}") from exc
    finally:
        for p in (in_path, out_path):
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass


def audio_tools_available() -> bool:
    """ffmpeg on PATH. Named separately from `audio.ffmpeg_available` only so this module can
    be read on its own; it answers the same question."""
    return shutil.which("ffmpeg") is not None


# --------------------------------------------------------------------------- #
# Batches on disk
# --------------------------------------------------------------------------- #
def new_token() -> str:
    """32 URL-safe characters from `secrets`. This token is the only thing standing between
    one visitor's audio and another's, alongside the owner check, so it is not derived from
    anything — not the time, not the principal, not the filenames."""
    return secrets.token_urlsafe(24)


def owner_key(kind: str, client_id: str | None, anon_key: str | None,
              integration_id: str | None = None, user_id: str | None = None) -> str:
    """The identity a batch belongs to, as one comparable string.

    An anonymous batch is keyed to its anon key (the caller's IP, by `services.auth`'s rule),
    which is weak on its own — hence the unguessable token. The two together mean guessing a
    token is not enough, and having the same IP as someone is not enough either.

    A registered user is keyed on `user_id` and NOT on the anon fallback: a user's token carries
    no client_id and no anon_key, so without its own branch every registered user would share
    the single `anon:unknown` owner — and each other's downloads.
    """
    if kind == "superadmin":
        return "superadmin"
    if kind == "tenant" and client_id:
        return f"tenant:{client_id}"
    if kind == "user" and user_id:
        return f"user:{user_id}"
    if kind == "integration" and integration_id:
        return f"integration:{integration_id}"
    return f"anon:{anon_key or 'unknown'}"


class Batch:
    """One conversion batch: a ZIP being appended to, plus the manifest that describes it.

    Entries are appended as each file finishes rather than collected and zipped at the end,
    so peak memory is one converted file, not the whole batch.
    """

    def __init__(self, token: str, fmt: str, owner: str):
        self.token = token
        self.fmt = fmt
        self.owner = owner
        self.dir = batch_dir(token)
        self.created = dt.datetime.now(dt.timezone.utc)
        self._zip: zipfile.ZipFile | None = None

    def open(self) -> None:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            # compresslevel=1: the ZIP is here to carry exact bytes and per-file names, and
            # compression is a bonus. Level 6 over a 40-file batch of raw PCM would be a CPU
            # event of its own on a box that is also serving retrieval.
            self._zip = zipfile.ZipFile(self.dir / _ZIP_NAME, "w",
                                        zipfile.ZIP_DEFLATED, compresslevel=1)
        except OSError as exc:
            log.exception("could not open a conversion batch at %s", self.dir)
            raise ConvertError("Conversion storage is unavailable on this server.") from exc

    async def add(self, name: str, payload: bytes) -> None:
        """Append one converted file. `writestr` deflates, which is CPU work, so it goes to a
        thread — this runs inside a request that is streaming progress."""
        if self._zip is None:
            raise ConvertError("Conversion storage is unavailable on this server.")
        await asyncio.to_thread(self._zip.writestr, name, payload)

    def close(self, entries: list[dict], ttl_seconds: int = TTL_SECONDS) -> dict:
        """Finish the ZIP and write the manifest. Returns the manifest.

        `ttl_seconds` is how long the batch stays downloadable, from when it was OPENED (not
        closed — a thirty-file batch can take minutes, and the caller's clock started at the
        upload). The default is the anonymous TTL; the router passes a longer one for a
        signed-in caller. The deadline is written into the manifest so that `locate()` and the
        sweep read the same number and neither has to know who the owner was.
        """
        if self._zip is not None:
            self._zip.close()
            self._zip = None
        expires = self.created + dt.timedelta(seconds=max(int(ttl_seconds), 1))
        manifest = {
            "token": self.token,
            "owner": self.owner,
            "format": self.fmt,
            "created_at": self.created.isoformat(),
            "expires_at": expires.isoformat(),
            "files": entries,
        }
        # Written LAST, and it is what `locate()` requires: a batch whose process died
        # mid-conversion leaves a directory with no manifest, which is undownloadable and is
        # swept on mtime like any other. There is no half-valid state to reason about.
        (self.dir / _MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
        return manifest

    def discard(self) -> None:
        """Throw the whole batch away — nothing converted, so there is nothing to download."""
        if self._zip is not None:
            try:
                self._zip.close()
            except Exception:  # noqa: BLE001
                pass
            self._zip = None
        shutil.rmtree(self.dir, ignore_errors=True)


def batch_dir(token: str) -> Path:
    if not TOKEN_RE.match(token or ""):
        raise ConvertError("Invalid download token.")
    return CONVERT_ROOT / token


def _expired(manifest: dict, fallback: Path) -> bool:
    raw = manifest.get("expires_at")
    try:
        return dt.datetime.fromisoformat(str(raw)) <= dt.datetime.now(dt.timezone.utc)
    except (TypeError, ValueError):
        # A manifest we cannot read the deadline out of still has one: the sweep's.
        try:
            return time.time() - fallback.stat().st_mtime > TTL_SECONDS
        except OSError:
            return True


def locate(token: str, owner: str) -> dict | None:
    """The ZIP for a batch this owner may download, or None.

    One return value for "no such batch", "not yours" and "expired", on purpose: the caller
    turns all three into a 404, so a token that belongs to someone else is indistinguishable
    from one that never existed.
    """
    try:
        d = batch_dir(token)
    except ConvertError:
        return None
    try:
        manifest = json.loads((d / _MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(manifest, dict) or manifest.get("owner") != owner:
        return None
    if _expired(manifest, d):
        return None
    zip_path = d / _ZIP_NAME
    if not zip_path.is_file():
        return None
    return {"manifest": manifest, "path": zip_path}


def _past_deadline(d: Path) -> bool:
    """Whether one batch directory is due for collection.

    The manifest's own `expires_at` decides when there is one to read: since a signed-in
    caller's batch can live for days, the sweep must apply the SAME deadline `locate()` does,
    or a week-long download would be collected at the two-hour mark and History would list a
    batch whose bytes are gone. A directory with no readable manifest — a batch whose process
    was killed mid-conversion (this stack redeploys on every push to main) — has no deadline of
    its own and is judged on its mtime against the default TTL; that is the case the sweep
    most exists for, and the one no manifest could describe.
    """
    try:
        manifest = json.loads((d / _MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        manifest = None
    if isinstance(manifest, dict):
        return _expired(manifest, d)
    try:
        return time.time() - d.stat().st_mtime > TTL_SECONDS
    except OSError:
        return False


def _sweep() -> int:
    """Delete every batch directory past its deadline. Filesystem-only and self-healing."""
    removed = 0
    try:
        entries = list(CONVERT_ROOT.iterdir())
    except FileNotFoundError:
        return 0
    except OSError:
        log.exception("convert sweep could not read %s", CONVERT_ROOT)
        return 0
    for d in entries:
        # Only ever the directories we made: a sweep that deletes what it does not recognise
        # is one shared-volume mistake away from deleting somebody's recordings.
        if not TOKEN_RE.match(d.name) or not d.is_dir() or not _past_deadline(d):
            continue
        shutil.rmtree(d, ignore_errors=True)
        removed += 1
    return removed


async def sweep_expired() -> int:
    """`_sweep()` off the event loop — it unlinks files, which blocks."""
    removed = await asyncio.to_thread(_sweep)
    if removed:
        log.info("convert sweep: removed %s expired batch(es)", removed)
    return removed
