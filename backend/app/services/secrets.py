"""Encryption at rest for provider keys.

Every provider credential the product stores — the deployment keys in the admin Integrations
blob, the embeddings key, a tenant's bring-your-own key, a registry connection's key — goes
through `seal()` on the way into the database and `open()` on the way out. Nothing else in the
codebase touches the cipher, so "are our keys encrypted" is answered by ONE module and one
setting: `SECRETS_KEY` in the server's `.env` (a Fernet key, urlsafe-base64 of 32 bytes).

Stored form: ``enc:v1:<fernet token>``. The prefix is what lets `open()` tell a sealed value
from a legacy plaintext one (the same column holds both during the transition), and the
version is what a later key rotation reaches for — `v2` can be introduced without touching a
single existing row.

Two deliberate behaviours that look like gaps and are not:

* **No key = plaintext mode, not a crash.** A server that has not been given `SECRETS_KEY`
  yet must still boot and keep working, or the very deploy that adds encryption would take
  the product down. `seal()` returns the plaintext unchanged, `status()` reports
  ``plaintext`` (which `/health` exposes), and the degraded state is logged as a WARNING once
  at startup.
* **A wrong key fails loudly.** `open()` on a sealed value the configured key cannot decrypt
  raises `SecretsError` naming `SECRETS_KEY` — never garbage, never a silent empty string that
  would read as "no key set" and send an unauthenticated request to a provider.

The key is read from `settings` on every call (cheap) so a test can monkeypatch it; the
built cipher is cached per key text.
"""
from __future__ import annotations

import json
import logging
import re

from cryptography.fernet import Fernet, InvalidToken

from ..config import settings

log = logging.getLogger("cq")

ENV_NAME = "SECRETS_KEY"
PREFIX = "enc:"
VERSION = "v1"
_SEALED_RE = re.compile(r"^enc:v\d+:")

GENERATE_HINT = ('python3 -c "from cryptography.fernet import Fernet; '
                 'print(Fernet.generate_key().decode())"')


class SecretsError(RuntimeError):
    """A stored secret cannot be read or written with the configured SECRETS_KEY."""


# (key text, cipher) — rebuilt whenever the configured key changes, so a monkeypatched
# setting is honoured and a stale cipher can never outlive a rotation.
_cache: tuple[str, Fernet] | None = None
_warned_plaintext = False


def _key_text() -> str:
    return (getattr(settings, "secrets_key", "") or "").strip()


def _fernet() -> Fernet | None:
    """The cipher for the configured key, or None when no key is set.

    A MALFORMED key raises: an operator who set one meant to encrypt, and quietly storing
    plaintext instead would be the one outcome worse than refusing.
    """
    global _cache
    key = _key_text()
    if not key:
        return None
    if _cache and _cache[0] == key:
        return _cache[1]
    try:
        cipher = Fernet(key.encode("ascii"))
    except (ValueError, TypeError, UnicodeEncodeError) as exc:
        raise SecretsError(
            f"{ENV_NAME} is not a valid key ({exc}). It must be a urlsafe-base64 32-byte "
            f"Fernet key — generate one with: {GENERATE_HINT}"
        ) from None
    _cache = (key, cipher)
    return cipher


def _warn_plaintext_once() -> None:
    global _warned_plaintext
    if _warned_plaintext:
        return
    _warned_plaintext = True
    log.warning(
        "%s is not set: provider keys are being stored in PLAINTEXT. Generate a key with %s, "
        "put it in the server's .env, and back it up — losing it makes every stored key "
        "unreadable.", ENV_NAME, GENERATE_HINT)


# ---------------------------------------------------------------------------
# The contract
# ---------------------------------------------------------------------------
def is_sealed(value) -> bool:
    """True for a value in the stored ``enc:v<n>:…`` form (any version)."""
    return isinstance(value, str) and bool(_SEALED_RE.match(value))


def seal(plaintext: str) -> str:
    """The stored form of a secret: ``enc:v1:<token>`` when SECRETS_KEY is set, else the
    plaintext unchanged (degraded mode, warned once). Empty stays empty, and a value that is
    already sealed is returned as is — which is what makes `migrate_plaintext` idempotent and
    protects against a double seal ever being possible."""
    if not plaintext:
        return plaintext if isinstance(plaintext, str) else ""
    if is_sealed(plaintext):
        return plaintext
    cipher = _fernet()
    if cipher is None:
        _warn_plaintext_once()
        return plaintext
    return f"{PREFIX}{VERSION}:" + cipher.encrypt(plaintext.encode("utf-8")).decode("ascii")


def open(stored: str | None) -> str:  # noqa: A001 — the name is the contract
    """The plaintext of a stored secret. A legacy (unsealed) value passes straight through;
    None and '' become ''. Raises `SecretsError` — naming SECRETS_KEY — when a sealed value
    cannot be decrypted with the configured key, or there is no key to decrypt it with."""
    if not stored:
        return ""
    if not is_sealed(stored):
        return stored
    version, _, token = stored[len(PREFIX):].partition(":")
    if version != VERSION:
        raise SecretsError(
            f"a stored secret is in format {version!r}, which this build does not know how to "
            f"read (it knows {VERSION!r}); was it written by a newer version with a rotated "
            f"{ENV_NAME}?")
    cipher = _fernet()
    if cipher is None:
        raise SecretsError(
            f"a stored secret is encrypted but {ENV_NAME} is not set — restore the key this "
            f"deployment's secrets were sealed with to the server's .env")
    try:
        return cipher.decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeError):
        raise SecretsError(
            f"a stored secret cannot be decrypted with the configured {ENV_NAME}: the key does "
            f"not match the one it was sealed with. Restore the original key; a new one cannot "
            f"read existing secrets") from None


def status() -> dict:
    """``{"mode": "encrypted" | "plaintext"}`` for /health. A malformed key reports
    ``plaintext`` (nothing IS being encrypted) plus an ``error`` saying why."""
    try:
        cipher = _fernet()
    except SecretsError as exc:
        return {"mode": "plaintext", "error": str(exc)}
    return {"mode": "encrypted" if cipher else "plaintext"}


# ---------------------------------------------------------------------------
# One-time migration of secrets stored before encryption existed. Called from the lifespan
# on every boot; idempotent, and never the reason a boot fails.
# ---------------------------------------------------------------------------
# Columns that hold a provider key and may still hold one in plaintext. The registry's tables
# are listed too: whichever order the lifespan runs this and the registry's own
# tenant_ai_configs -> tenant_ai_overrides copy, the outcome is a sealed column. Each entry is
# skipped when the table or column does not exist yet.
_KEY_COLUMNS = (
    ("tenant_ai_configs", "api_key"),
    ("tenant_ai_overrides", "api_key_enc"),
    ("ai_connections", "api_key_enc"),
)


async def migrate_plaintext(*, conn=None) -> int:
    """Re-seal every legacy plaintext secret this module knows about; returns how many.

    Targets: the admin Integrations blob's SECRET_FIELDS, the embeddings blob's api_key, and
    the per-tenant / registry key columns above. A value already sealed is skipped, so a
    second run is a no-op. In plaintext mode (no key) it does nothing but log the WARNING —
    this is the once-at-startup warning the degraded mode promises. `conn` lets a test run it
    inside a transaction it then rolls back; production callers pass nothing.
    """
    try:
        cipher = _fernet()
    except SecretsError as exc:
        log.error("secrets: not migrating stored keys — %s", exc)
        return 0
    if cipher is None:
        _warn_plaintext_once()
        return 0
    if conn is not None:
        return await _migrate(conn)
    from ..db import pool  # lazy: keep this module importable without a pool

    async with pool().acquire() as c:
        return await _migrate(c)


async def _migrate(conn) -> int:
    from . import settings_store  # lazy: settings_store imports this module

    total = 0
    blobs = ((settings_store.SETTINGS_KEY, settings_store.SECRET_FIELDS),
             (settings_store.EMBEDDINGS_KEY, settings_store.EMBEDDING_SECRETS))
    for key, fields in blobs:
        try:
            total += await _reseal_blob(conn, key, fields)
        except Exception:  # noqa: BLE001 — a failed reseal leaves a working plaintext value
            log.exception("secrets: could not reseal app_settings[%s]", key)
    for table, column in _KEY_COLUMNS:
        try:
            total += await _reseal_column(conn, table, column)
        except Exception:  # noqa: BLE001 — same
            log.exception("secrets: could not reseal %s.%s", table, column)
    if total:
        log.info("secrets: sealed %d plaintext secret(s) at rest", total)
    return total


async def _reseal_blob(conn, key: str, fields) -> int:
    raw = await conn.fetchval("SELECT value FROM app_settings WHERE key = $1", key)
    if not raw:
        return 0
    blob = json.loads(raw) if isinstance(raw, str) else dict(raw)
    n = 0
    for field in fields:
        value = blob.get(field)
        if not isinstance(value, str) or not value or is_sealed(value):
            continue
        # One field at a time, so a concurrent admin save of an unrelated field is not
        # clobbered by a whole-blob rewrite.
        await conn.execute(
            "UPDATE app_settings SET value = jsonb_set(value, ARRAY[$2::text], to_jsonb($3::text)) "
            "WHERE key = $1",
            key, field, seal(value))
        n += 1
    return n


async def _reseal_column(conn, table: str, column: str) -> int:
    # `table`/`column` come from the constant tuple above, never from input.
    present = await conn.fetchval(
        "SELECT 1 FROM information_schema.columns WHERE table_name = $1 AND column_name = $2",
        table, column)
    if not present:
        return 0
    rows = await conn.fetch(
        f"SELECT DISTINCT {column} AS v FROM {table} "
        f"WHERE {column} IS NOT NULL AND {column} <> '' AND {column} NOT LIKE 'enc:v%'")
    n = 0
    for row in rows:
        # Matching on the value itself needs no knowledge of each table's primary key; two
        # rows sharing one plaintext get one token, which is still a sealed value.
        tag = await conn.execute(
            f"UPDATE {table} SET {column} = $1 WHERE {column} = $2", seal(row["v"]), row["v"])
        n += int(tag.rsplit(" ", 1)[-1] or 0)
    return n
