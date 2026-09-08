"""How a recording is sent to Scribe: four settings, one inheritance chain.

    code defaults  <-  superadmin default  <-  tenant override  <-  per-file override

Each layer sets only what it wants to change; anything unset is inherited. That is the same
resolution `chat_store` does for the bot (CHAT_CONFIG_DEFAULTS <- app_settings blob <- the
tenant's row), including the `is_default` flag that tells a UI which layer it is looking at,
and it is deliberately the same shape so the console can drive both from one mental model.

WHY THIS EXISTS. ElevenLabs Scribe mis-transcribed a Georgian insurance call: "36 თვემდე"
(36 MONTHS) came back "36 წლამდე" (36 YEARS). That transcript then feeds fact-check and rubric
scoring, so one misheard word becomes a compliance verdict about an agent. On ElevenLabs' own
site the SAME audio transcribed correctly with language=ka and diarize=true — but that test
also used the ORIGINAL file, while the product re-encodes to lossy MP3 first, so it moved two
variables at once. Hence four knobs rather than one, and hence `audio_format`.

THE FOUR
  language_code   ISO-639-1/3, or None = let Scribe detect. ElevenLabs documents it as a hint,
                  not enforcement; the owner has direct evidence it helps for Georgian.
  diarize         bool. Was hardcoded "true". Turning it off loses per-speaker analysis, so
                  every UI that offers the switch says so.
  keyterms        biases recognition toward given words. <=1000 terms, <50 chars each, <=5
                  words each, and `< > { } [ ] \\` are rejected by the API. +20% cost, so it
                  is opt-in. NOTE: ElevenLabs documents keyterms as a **Scribe v2** feature —
                  sending it with a v1 model may be refused upstream.
  audio_format    one of services/audio.STT_FORMATS.

Storage. The superadmin layer is an `app_settings` blob ('transcription_defaults'), read on
every transcription and therefore cached for 5 s exactly like the autopilot kill switch —
short enough that "I just saved it" is true within seconds, long enough that a busy tenant is
not hammering the row. The tenant layer is `clients.settings->'transcription'`, where the
tenant's other tunables (curation, per-day caps) already live; it needs no new table.
"""
import json
import logging
import re
import time
from datetime import datetime, timezone

from ..db import pool
from . import settings_store
from .audio import DEFAULT_STT_FORMAT, STT_FORMATS

log = logging.getLogger("cq")

DEFAULTS_KEY = "transcription_defaults"
DEFAULTS_TTL_S = 5.0
TENANT_SETTINGS_KEY = "transcription"

FIELDS = ("language_code", "diarize", "keyterms", "audio_format")

# The floor under every layer. `audio_format` is the lossy MP3 the product has always sent:
# switching the deployment default to lossless is the right change ONLY once a FLAC upload has
# been verified against the live API, because a default that ElevenLabs refuses would break
# every transcription in the product at once. Flip this one constant (and audio.py's
# DEFAULT_STT_FORMAT, which it mirrors) when that verification is done.
CODE_DEFAULTS: dict = {
    "language_code": None,          # None = detect
    "diarize": True,
    "keyterms": [],
    "audio_format": DEFAULT_STT_FORMAT,
}

MAX_KEYTERMS = 1000
MAX_KEYTERM_CHARS = 50
MAX_KEYTERM_WORDS = 5
# Exactly the characters ElevenLabs rejects. Kept as a set so the error can name the offender.
KEYTERM_FORBIDDEN = frozenset("<>{}[]\\")

# ISO-639-1 (2 letters) or ISO-639-3 (3 letters), optionally with a script/region suffix the
# API tolerates (e.g. "zh-Hans"). Deliberately permissive about WHICH code: ElevenLabs' list
# is theirs to change, and refusing a valid language here would be our bug, not theirs.
_LANG_RE = re.compile(r"^[A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{2,8})*$")

# (fetched_at, value). Per-process, correct for the single uvicorn worker (see settings_store).
_default_cache: tuple[float, dict] | None = None


# ---------------------------------------------------------------------------
# Validation — ONE place, so the admin route, the tenant route and the per-file
# override cannot disagree about what a legal setting is.
# ---------------------------------------------------------------------------
class TranscriptionSettingsError(ValueError):
    """Invalid settings. `field` names the offending key so the caller can point at it."""

    def __init__(self, message: str, field: str = ""):
        super().__init__(message)
        self.field = field


def _err(field: str, message: str) -> TranscriptionSettingsError:
    return TranscriptionSettingsError(f"{field}: {message}", field)


def _language_code(value) -> str | None:
    """None/"" both mean "detect automatically" — an empty select is not an error."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise _err("language_code", "must be a language code such as 'ka', or null to detect")
    code = value.strip()
    if not code:
        return None
    if not _LANG_RE.match(code):
        raise _err("language_code",
                   f"{code!r} is not an ISO-639 language code (expected 2-3 letters, e.g. 'ka')")
    return code.lower()


def _diarize(value) -> bool:
    if not isinstance(value, bool):
        raise _err("diarize", "must be true or false")
    return value


def _keyterms(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raise _err("keyterms", "must be a list of terms, not a single string")
    if not isinstance(value, (list, tuple)):
        raise _err("keyterms", "must be a list of terms")
    out: list[str] = []
    for raw in value:
        if not isinstance(raw, str):
            raise _err("keyterms", "every term must be text")
        term = raw.strip()
        if not term:
            continue                      # a blank row in a UI list is not an error
        if len(term) >= MAX_KEYTERM_CHARS:
            raise _err("keyterms",
                       f"{term[:20]!r}… is {len(term)} characters; each term must be under "
                       f"{MAX_KEYTERM_CHARS}")
        if len(term.split()) > MAX_KEYTERM_WORDS:
            raise _err("keyterms",
                       f"{term!r} has {len(term.split())} words; each term may have at most "
                       f"{MAX_KEYTERM_WORDS}")
        bad = sorted(KEYTERM_FORBIDDEN.intersection(term))
        if bad:
            raise _err("keyterms",
                       f"{term!r} contains {' '.join(bad)} — the characters < > {{ }} [ ] \\ "
                       f"are rejected by the transcription API")
        if term not in out:
            out.append(term)
    if len(out) > MAX_KEYTERMS:
        raise _err("keyterms", f"{len(out)} terms; at most {MAX_KEYTERMS} are allowed")
    return out


def _audio_format(value) -> str:
    if not isinstance(value, str) or value.strip() not in STT_FORMATS:
        raise _err("audio_format",
                   f"must be one of {', '.join(sorted(STT_FORMATS))}")
    return value.strip()


_VALIDATORS = {
    "language_code": _language_code,
    "diarize": _diarize,
    "keyterms": _keyterms,
    "audio_format": _audio_format,
}


def validate(patch: dict | None, *, partial: bool = True) -> dict:
    """Clean one LAYER's worth of settings.

    Returns only the keys actually present, so "unset" survives as absence and the layer below
    keeps showing through — that is the whole inheritance contract. `language_code` is the one
    field whose explicit `None` is a VALUE ("detect"), not an absence, so a layer that wants to
    stop inheriting Georgian can say so.

    Unknown keys are refused rather than ignored: a UI that misspells a field would otherwise
    look like it saved and silently change nothing.
    """
    if patch is None:
        return {}
    if not isinstance(patch, dict):
        raise TranscriptionSettingsError("transcription settings must be an object")
    unknown = [k for k in patch if k not in _VALIDATORS]
    if unknown:
        raise _err(unknown[0], f"unknown setting (expected any of {', '.join(FIELDS)})")
    out: dict = {}
    for key, fn in _VALIDATORS.items():
        if key not in patch:
            continue
        if patch[key] is None and key != "language_code":
            continue                      # None = "not sent", house rule (settings_store)
        out[key] = fn(patch[key])
    if not partial:
        for key in FIELDS:
            out.setdefault(key, CODE_DEFAULTS[key])
    return out


def merge(base: dict, over: dict | None) -> dict:
    """One layer over another. A key the upper layer never set is inherited whole — including
    `keyterms`, which REPLACES rather than concatenating: a workspace that narrowed the
    operator's term list must not silently get the operator's terms back."""
    out = dict(base)
    for key in FIELDS:
        if over and key in over:
            out[key] = over[key]
    out["keyterms"] = list(out.get("keyterms") or [])
    return out


# ---------------------------------------------------------------------------
# Layer 2 — the superadmin default (app_settings blob, 5 s cache)
# ---------------------------------------------------------------------------
def _default_from(stored: dict) -> dict:
    """CODE_DEFAULTS with the stored blob laid over it. Never raises: a blob that somehow
    holds an illegal value is logged and that ONE field falls back, because a corrupt setting
    must not take transcription down for every tenant."""
    stored = stored or {}
    cfg = dict(CODE_DEFAULTS)
    clean: dict = {}
    for key in FIELDS:
        if key not in stored:
            continue
        try:
            clean[key] = _VALIDATORS[key](stored[key])
        except ValueError as exc:
            log.warning("stored transcription default is invalid (%s); using the code default",
                        exc)
    cfg = merge(cfg, clean)
    cfg["source"] = "stored" if clean else "builtin"
    cfg["updated_at"] = stored.get("updated_at")
    cfg["updated_by"] = stored.get("updated_by")
    return cfg


async def get_default(*, force: bool = False) -> dict:
    """The deployment default every workspace inherits, cached for 5 seconds.

    Never raises. On a DB failure it serves the last value it saw, or the code defaults — the
    alternative is refusing to transcribe because a *settings* read failed, which trades a
    working product for a strictly worse outcome.
    """
    global _default_cache
    now = time.monotonic()
    if not force and _default_cache and (now - _default_cache[0]) < DEFAULTS_TTL_S:
        return dict(_default_cache[1], keyterms=list(_default_cache[1]["keyterms"]))
    try:
        cfg = _default_from(await settings_store.get_blob(DEFAULTS_KEY))
    except Exception as exc:  # noqa: BLE001 — see docstring
        log.warning("transcription defaults read failed: %s", exc)
        if _default_cache:
            return dict(_default_cache[1], keyterms=list(_default_cache[1]["keyterms"]))
        return dict(CODE_DEFAULTS, keyterms=[], source="builtin",
                    updated_at=None, updated_by=None)
    _default_cache = (now, cfg)
    return dict(cfg, keyterms=list(cfg["keyterms"]))


def invalidate_default_cache() -> None:
    """Forget the cached default. `set_default` does this for its own process; this is for the
    cases that write the blob some other way — a test fixture, or a future admin path that
    edits `app_settings` directly."""
    global _default_cache
    _default_cache = None


async def set_default(patch: dict | None, *, updated_by: str = "superadmin") -> dict:
    """Replace the stored default and drop the cache, so the operator's own next read is the
    truth. Raises TranscriptionSettingsError (a ValueError) naming the offending field."""
    global _default_cache
    blob = validate(patch, partial=False)
    blob["updated_at"] = datetime.now(timezone.utc).isoformat()
    blob["updated_by"] = (updated_by or "superadmin").strip() or "superadmin"
    await settings_store.set_blob(DEFAULTS_KEY, blob)
    _default_cache = None
    log.info("transcription defaults saved by %s: language=%s diarize=%s keyterms=%d format=%s",
             blob["updated_by"], blob["language_code"], blob["diarize"],
             len(blob["keyterms"]), blob["audio_format"])
    return await get_default(force=True)


# ---------------------------------------------------------------------------
# Layer 3 — the tenant override (clients.settings->'transcription')
# ---------------------------------------------------------------------------
def _settings_blob(raw) -> dict:
    """asyncpg hands jsonb back as str unless a codec is registered (settings_store._load_key
    documents the house workaround)."""
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


async def get_tenant_override(client_id: str) -> dict:
    """The keys this workspace set for itself — {} when it is inheriting everything.

    Cleaned through the same validators as everything else, and a field that fails is dropped
    rather than raising: a workspace whose blob was hand-edited into an illegal state should
    fall back to what it inherits, not stop transcribing.
    """
    if not client_id:
        return {}
    async with pool().acquire() as conn:
        raw = await conn.fetchval(
            "SELECT settings FROM clients WHERE id = $1", client_id)
    stored = _settings_blob(raw).get(TENANT_SETTINGS_KEY)
    if not isinstance(stored, dict):
        return {}
    out: dict = {}
    for key in FIELDS:
        if key not in stored:
            continue
        try:
            out[key] = _VALIDATORS[key](stored[key])
        except ValueError as exc:
            log.warning("client %s has an invalid transcription.%s (%s); inheriting instead",
                        client_id, key, exc)
    return out


async def set_tenant_override(client_id: str, patch: dict | None) -> dict:
    """Replace this workspace's override with `patch` (validated). Returns the stored override.

    A REPLACE, not a merge: the form posts the whole panel, and "the field I cleared is now
    inherited again" has to be expressible. Written with jsonb `||` so the tenant's other
    settings (curation, per-day caps) are untouched.
    """
    if not client_id:
        raise TranscriptionSettingsError("client_id is required")
    clean = validate(patch, partial=True)
    async with pool().acquire() as conn:
        updated = await conn.fetchval(
            """
            UPDATE clients
               SET settings = COALESCE(settings, '{}'::jsonb) || jsonb_build_object($2::text, $3::jsonb)
             WHERE id = $1
            RETURNING id
            """,
            client_id, TENANT_SETTINGS_KEY, json.dumps(clean))
    if updated is None:
        raise TranscriptionSettingsError("workspace not found")
    return clean


async def clear_tenant_override(client_id: str) -> None:
    """Drop the override entirely — the workspace goes back to inheriting every field."""
    if not client_id:
        raise TranscriptionSettingsError("client_id is required")
    async with pool().acquire() as conn:
        await conn.execute(
            "UPDATE clients SET settings = COALESCE(settings, '{}'::jsonb) - $2::text "
            "WHERE id = $1",
            client_id, TENANT_SETTINGS_KEY)


# ---------------------------------------------------------------------------
# The chain
# ---------------------------------------------------------------------------
def _public(cfg: dict) -> dict:
    """Just the four settings — no bookkeeping keys — for embedding as an `inherited` layer."""
    return {key: (list(cfg[key]) if key == "keyterms" else cfg[key]) for key in FIELDS}


async def effective_for_tenant(client_id: str | None) -> dict:
    """The UI's view of one workspace: the resolved settings plus which layer they came from.

      is_default  true when the workspace has set nothing of its own (it is inheriting)
      inherited   the layer UNDERNEATH, so the UI can show what it would fall back to
      override    only the keys the workspace actually set, so a form can mark them

    A caller with no workspace (a registered user, the public page) gets the system layer with
    is_default=true — the same shape, so no UI needs a second code path.
    """
    base = await get_default()
    override = await get_tenant_override(client_id) if client_id else {}
    cfg = merge(base, override)
    return {
        **_public(cfg),
        "is_default": not override,
        "inherited": {**_public(base), "source": "system"},
        "override": override,
        "source": base.get("source"),
        "updated_at": base.get("updated_at"),
        "updated_by": base.get("updated_by"),
    }


async def resolve(client_id: str | None = None, per_file: dict | None = None) -> dict:
    """The four settings this ONE request will transcribe with.

    code defaults <- superadmin default <- tenant override <- `per_file`. `per_file` is already
    validated by the route (`parse_override`); it is re-validated here anyway, because this is
    the function every transcribing path calls and it is the last place a bad value can be
    stopped before it reaches the provider.
    """
    cfg = merge(await get_default(), await get_tenant_override(client_id) if client_id else {})
    cfg = merge(cfg, validate(per_file, partial=True))
    return _public(cfg)


def parse_override(raw: str | dict | None) -> dict:
    """A per-file override off the wire. Multipart carries it as a JSON string in a form field
    (the upload routes are multipart/form-data, so there is no JSON body to put it in); a JSON
    route may hand the object straight through.

    Raises TranscriptionSettingsError, which every route turns into a 400 naming the field.
    """
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return validate(raw, partial=True)
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return {}
        try:
            value = json.loads(text)
        except ValueError as exc:
            raise TranscriptionSettingsError(
                f"transcription: not valid JSON ({exc})", "transcription") from exc
        if not isinstance(value, dict):
            raise TranscriptionSettingsError(
                "transcription: must be a JSON object", "transcription")
        return validate(value, partial=True)
    raise TranscriptionSettingsError("transcription: must be a JSON object", "transcription")


def as_kwargs(cfg: dict | None) -> dict:
    """Resolved settings → `elevenlabs.transcribe()` keyword arguments.

    An empty/None cfg yields `{}` — literally the call the product made before these settings
    existed — so a path that has not been taught about them cannot accidentally change what
    leaves the process.
    """
    if not cfg:
        return {}
    return {
        "language_code": cfg.get("language_code"),
        "diarize": bool(cfg.get("diarize", True)),
        "keyterms": list(cfg.get("keyterms") or []),
        "audio_format": cfg.get("audio_format"),
    }
