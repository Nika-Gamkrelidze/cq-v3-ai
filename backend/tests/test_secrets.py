"""Encryption at rest for provider keys (services/secrets.py + its settings_store wiring).

What is pinned: the stored form is `enc:v1:…` and round-trips; a legacy plaintext value read
through `open()` passes through (the same column holds both during the transition); the
no-key state is a warned, working degraded mode — never an exception; a WRONG key fails
loudly with a message naming SECRETS_KEY rather than returning garbage; and the one-time
migration seals what it finds and is a no-op the second time.

The key is monkeypatched on `settings`, which is how `secrets` reads it. The settings_store
tests stub `_load_key` / `_save_key` so they need no database. The migration test does need
one — and runs inside a transaction it ALWAYS rolls back, because the developer's local DB may
hold real keys, and re-sealing those under a throwaway test key would make them unreadable.
"""
import json
import logging
import uuid

import pytest
from cryptography.fernet import Fernet

from app.config import settings
from app.services import secrets, settings_store
from conftest import sql  # loop-independent SQL; see its module docstring

KEY = Fernet.generate_key().decode()
OTHER_KEY = Fernet.generate_key().decode()


@pytest.fixture
def encrypted(monkeypatch):
    monkeypatch.setattr(settings, "secrets_key", KEY)


@pytest.fixture
def plaintext(monkeypatch):
    monkeypatch.setattr(settings, "secrets_key", "")
    monkeypatch.setattr(secrets, "_warned_plaintext", False)


# ---------------------------------------------------------------------------
# The contract
# ---------------------------------------------------------------------------
def test_roundtrip(encrypted):
    sealed = secrets.seal("sk-ant-api03-verysecret")
    assert sealed.startswith("enc:v1:")
    assert "verysecret" not in sealed
    assert secrets.is_sealed(sealed)
    assert secrets.open(sealed) == "sk-ant-api03-verysecret"


def test_roundtrip_survives_unicode(encrypted):
    assert secrets.open(secrets.seal("გასაღები-ключ")) == "გასაღები-ключ"


def test_legacy_plaintext_passes_through_open(encrypted):
    assert secrets.open("sk-legacy-plaintext") == "sk-legacy-plaintext"
    assert secrets.open(None) == ""
    assert secrets.open("") == ""


def test_is_sealed():
    assert secrets.is_sealed("enc:v1:abc")
    assert secrets.is_sealed("enc:v2:abc")          # any version counts as "not plaintext"
    assert not secrets.is_sealed("sk-ant-api03-x")
    assert not secrets.is_sealed("enc:")
    assert not secrets.is_sealed("")
    assert not secrets.is_sealed(None)
    assert not secrets.is_sealed(123)


def test_status_in_both_modes(monkeypatch):
    monkeypatch.setattr(settings, "secrets_key", KEY)
    assert secrets.status() == {"mode": "encrypted"}
    monkeypatch.setattr(settings, "secrets_key", "")
    assert secrets.status() == {"mode": "plaintext"}


def test_no_key_is_a_working_degraded_mode(plaintext):
    assert secrets.seal("sk-plain") == "sk-plain"
    assert not secrets.is_sealed(secrets.seal("sk-plain"))
    assert secrets.open("sk-plain") == "sk-plain"


def test_no_key_warns_exactly_once(plaintext, caplog):
    with caplog.at_level(logging.WARNING, logger="cq"):
        secrets.seal("one")
        secrets.seal("two")
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "SECRETS_KEY" in r.getMessage()]
    assert len(warnings) == 1


def test_empty_and_already_sealed_values_are_left_alone(encrypted):
    assert secrets.seal("") == ""
    sealed = secrets.seal("sk-once")
    assert secrets.seal(sealed) == sealed          # never a double seal


def test_wrong_key_fails_loudly(monkeypatch):
    monkeypatch.setattr(settings, "secrets_key", KEY)
    sealed = secrets.seal("sk-under-key-one")
    monkeypatch.setattr(settings, "secrets_key", OTHER_KEY)
    with pytest.raises(secrets.SecretsError, match="SECRETS_KEY"):
        secrets.open(sealed)


def test_sealed_value_with_no_key_fails_loudly(monkeypatch):
    monkeypatch.setattr(settings, "secrets_key", KEY)
    sealed = secrets.seal("sk-sealed-then-key-lost")
    monkeypatch.setattr(settings, "secrets_key", "")
    with pytest.raises(secrets.SecretsError, match="SECRETS_KEY is not set"):
        secrets.open(sealed)


def test_malformed_key_refuses_to_store_plaintext(monkeypatch):
    monkeypatch.setattr(settings, "secrets_key", "definitely-not-a-fernet-key")
    with pytest.raises(secrets.SecretsError, match="SECRETS_KEY"):
        secrets.seal("sk-x")
    st = secrets.status()
    assert st["mode"] == "plaintext" and "SECRETS_KEY" in st["error"]
    # A legacy plaintext value is still readable — nothing needs the cipher for that.
    assert secrets.open("sk-legacy") == "sk-legacy"


def test_unknown_format_version_is_an_error_not_garbage(encrypted):
    with pytest.raises(secrets.SecretsError, match="v9"):
        secrets.open("enc:v9:whatever")


def test_a_rotated_key_is_picked_up_without_a_restart(monkeypatch):
    """The cipher is cached per key TEXT, so changing the setting changes the cipher."""
    monkeypatch.setattr(settings, "secrets_key", KEY)
    a = secrets.seal("sk-a")
    monkeypatch.setattr(settings, "secrets_key", OTHER_KEY)
    b = secrets.seal("sk-b")
    assert secrets.open(b) == "sk-b"
    with pytest.raises(secrets.SecretsError):
        secrets.open(a)


# ---------------------------------------------------------------------------
# settings_store: seal on write, open on read, decrypted key to callers
# ---------------------------------------------------------------------------
@pytest.fixture
def store(monkeypatch):
    """An in-memory app_settings: `_load_key` / `_save_key` swapped for a dict."""
    blobs: dict[str, dict] = {}

    async def _load(key):
        return dict(blobs.get(key) or {})

    async def _save(key, value):
        blobs[key] = dict(value)

    monkeypatch.setattr(settings_store, "_load_key", _load)
    monkeypatch.setattr(settings_store, "_save_key", _save)
    return blobs


async def test_update_seals_the_key_before_it_is_stored(encrypted, store):
    await settings_store.update({"anthropic_api_key": "sk-ant-new", "llm_model": "claude-x",
                                 "elevenlabs_api_key": ""})
    stored = store[settings_store.SETTINGS_KEY]
    assert secrets.is_sealed(stored["anthropic_api_key"])
    assert "sk-ant-new" not in json.dumps(stored)
    assert stored["llm_model"] == "claude-x"                 # non-secrets untouched
    assert "elevenlabs_api_key" not in stored                # '' = not sent, not wiped


async def test_get_effective_returns_the_decrypted_key(encrypted, store):
    store[settings_store.SETTINGS_KEY] = {"anthropic_api_key": secrets.seal("sk-ant-real"),
                                          "elevenlabs_api_key": secrets.seal("el-real")}
    cfg = await settings_store.get_effective()
    assert cfg["anthropic_api_key"] == "sk-ant-real"
    assert cfg["elevenlabs_api_key"] == "el-real"


async def test_get_public_masks_the_decrypted_key_and_never_leaks_either_form(encrypted, store):
    sealed = secrets.seal("sk-ant-real")
    store[settings_store.SETTINGS_KEY] = {"anthropic_api_key": sealed}
    pub = await settings_store.get_public()
    assert pub["anthropic_api_key_set"] is True
    assert pub["anthropic_api_key_hint"] == "…real"          # a hint of the REAL key
    body = json.dumps(pub)
    assert "sk-ant-real" not in body and sealed not in body


async def test_a_legacy_plaintext_blob_still_reads_before_migration(encrypted, store):
    store[settings_store.SETTINGS_KEY] = {"anthropic_api_key": "sk-ant-old"}
    assert (await settings_store.get_effective())["anthropic_api_key"] == "sk-ant-old"


async def test_clearing_a_secret_still_works(encrypted, store):
    store[settings_store.SETTINGS_KEY] = {"anthropic_api_key": secrets.seal("sk-gone")}
    await settings_store.update({"anthropic_api_key": "__clear__"})
    assert "anthropic_api_key" not in store[settings_store.SETTINGS_KEY]


async def test_embeddings_key_is_sealed_and_decrypted(encrypted, store, monkeypatch):
    from app.services import embeddings
    monkeypatch.setattr(embeddings, "invalidate_provider_cache", lambda: None)
    await settings_store.set_embedding_config({"provider": "openai", "api_key": "sk-emb"})
    stored = store[settings_store.EMBEDDINGS_KEY]
    assert secrets.is_sealed(stored["api_key"]) and stored["provider"] == "openai"
    cfg = await settings_store.get_embedding_config()
    assert cfg["api_key"] == "sk-emb"
    pub = await settings_store.get_embedding_public()
    assert pub["api_key_set"] is True and "sk-emb" not in json.dumps(pub)


# ---------------------------------------------------------------------------
# migrate_plaintext
# ---------------------------------------------------------------------------
async def test_migrate_without_a_key_is_a_warning_not_an_error(plaintext, caplog):
    with caplog.at_level(logging.WARNING, logger="cq"):
        assert await secrets.migrate_plaintext() == 0
    assert any("SECRETS_KEY" in r.getMessage() for r in caplog.records)


async def test_migrate_with_a_malformed_key_logs_and_does_nothing(monkeypatch, caplog):
    monkeypatch.setattr(settings, "secrets_key", "nope")
    with caplog.at_level(logging.ERROR, logger="cq"):
        assert await secrets.migrate_plaintext() == 0
    assert any(r.levelno == logging.ERROR and "SECRETS_KEY" in r.getMessage()
               for r in caplog.records)


def _blob(raw) -> dict:
    return json.loads(raw) if isinstance(raw, str) else dict(raw or {})


def test_migrate_seals_planted_plaintext_once(db_available, encrypted):
    mark = uuid.uuid4().hex[:8]
    plants = {"anthropic_api_key": f"plant-anth-{mark}", "elevenlabs_api_key": f"plant-el-{mark}"}
    emb_plant, tenant_plant = f"plant-emb-{mark}", f"plant-tenant-{mark}"

    async def _run(conn):
        tr = conn.transaction()
        await tr.start()
        try:
            upsert = ("INSERT INTO app_settings (key, value) VALUES ($1, $2::jsonb) "
                      "ON CONFLICT (key) DO UPDATE SET value = app_settings.value || EXCLUDED.value")
            await conn.execute(upsert, settings_store.SETTINGS_KEY, json.dumps(plants))
            await conn.execute(upsert, settings_store.EMBEDDINGS_KEY,
                               json.dumps({"api_key": emb_plant}))
            cid = await conn.fetchval(
                "INSERT INTO clients (slug, name, api_key) VALUES ($1, $2, $3) RETURNING id",
                f"secrets-{mark}", "secrets-test", f"tk-{mark}")
            await conn.execute(
                "INSERT INTO tenant_ai_configs (client_id, api_key, enabled) VALUES ($1, $2, true)",
                cid, tenant_plant)

            first = await secrets.migrate_plaintext(conn=conn)
            integ = _blob(await conn.fetchval(
                "SELECT value FROM app_settings WHERE key = $1", settings_store.SETTINGS_KEY))
            emb = _blob(await conn.fetchval(
                "SELECT value FROM app_settings WHERE key = $1", settings_store.EMBEDDINGS_KEY))
            row = await conn.fetchval(
                "SELECT api_key FROM tenant_ai_configs WHERE client_id = $1", cid)
            second = await secrets.migrate_plaintext(conn=conn)
            return first, second, integ, emb, row
        finally:
            await tr.rollback()          # ALWAYS — the dev DB keeps whatever it had

    first, second, integ, emb, row = sql(_run)
    # 2 integrations fields + the embeddings key + the tenant row; more only if the developer's
    # DB already held other plaintext rows (they were sealed inside the rolled-back transaction).
    assert first >= 4
    assert second == 0
    for field, want in plants.items():
        assert secrets.is_sealed(integ[field]) and secrets.open(integ[field]) == want
    assert secrets.is_sealed(emb["api_key"]) and secrets.open(emb["api_key"]) == emb_plant
    assert secrets.is_sealed(row) and secrets.open(row) == tenant_plant
