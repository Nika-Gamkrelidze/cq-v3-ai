"""The AI provider registry: the four-layer resolution chain, its API, and its two invariants.

Driven through the real ASGI app with conftest's seed tenants (A and B, each with an API key,
which resolves to role "apikey" — a workspace-configuring principal). The resolver itself is
called on the app's own event loop through the TestClient portal, because it reads the pool.

No provider is ever reached: `llm.probe` and `voice.probe` are stubbed (they are built beside
this file), and every stub records the `Resolved` it was handed so the tests can assert that
what reaches an adapter is the DECRYPTED key — while every HTTP body is grepped to prove the
same key never leaves the server.

The registry is shared state in a developer's database. The `registry` fixture therefore
snapshots the defaults it finds, clears them for the duration of a test (so the chain starts
from the legacy layer), and restores them — and hard-deletes only the rows it created.
"""
import functools
import sys
import types
import uuid

import pytest

from app.config import settings
from app.services import ai_registry, ai_resolve, auth, llm
from app.services.providers import catalog
from conftest import sql

CAPS = ("llm", "stt", "tts")


def _admin() -> dict:
    return {"X-Admin-Token": settings.admin_token}


def _key(seed, label: str) -> dict:
    return {"X-API-Key": seed[label]["api_key"]}


def resolve(api, client_id, capability, **kw) -> ai_resolve.Resolved:
    """`ai_resolve.resolve` on the app's loop, cache dropped first so the test sees the row
    it just wrote rather than the 30 s cache."""
    ai_resolve.forget()
    return api.portal.call(functools.partial(ai_resolve.resolve, client_id, capability, **kw))


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
class Registry:
    """Creates connections through the API and remembers what to delete."""

    def __init__(self, api, seed):
        self.api, self.seed, self.created = api, seed, []

    def create(self, **body) -> dict:
        body.setdefault("name", f"test-{body.get('capability', 'x')}-{uuid.uuid4().hex[:6]}")
        r = self.api.post("/admin/ai/connections", headers=_admin(), json=body)
        assert r.status_code == 200, r.text
        row = r.json()
        self.created.append(row["id"])
        return row

    def make_default(self, conn_id: str) -> dict:
        r = self.api.post(f"/admin/ai/connections/{conn_id}/default", headers=_admin())
        assert r.status_code == 200, r.text
        return r.json()

    def assign(self, label: str, **patch) -> dict:
        r = self.api.put(f"/admin/ai/assignments/{self.seed[label]['client_id']}",
                         headers=_admin(), json=patch)
        assert r.status_code == 200, r.text
        return r.json()


@pytest.fixture
def registry(api, seed):
    a, b = uuid.UUID(seed["a"]["client_id"]), uuid.UUID(seed["b"]["client_id"])

    async def _setup(conn):
        prev = await conn.fetch("SELECT id FROM ai_connections WHERE is_default")
        await conn.execute("UPDATE ai_connections SET is_default = false WHERE is_default")
        await conn.execute(
            "DELETE FROM tenant_ai_assignments WHERE client_id = ANY($1::uuid[])", [a, b])
        await conn.execute(
            "DELETE FROM tenant_ai_overrides WHERE client_id = ANY($1::uuid[])", [a, b])
        return [r["id"] for r in prev]

    previous_defaults = sql(_setup)
    ai_resolve.forget()
    reg = Registry(api, seed)
    try:
        yield reg
    finally:
        async def _teardown(conn):
            await conn.execute(
                "DELETE FROM tenant_ai_assignments WHERE client_id = ANY($1::uuid[])", [a, b])
            await conn.execute(
                "DELETE FROM tenant_ai_overrides WHERE client_id = ANY($1::uuid[])", [a, b])
            if reg.created:
                await conn.execute("DELETE FROM ai_connections WHERE id = ANY($1::uuid[])",
                                   [uuid.UUID(i) for i in reg.created])
            await conn.execute("UPDATE ai_connections SET is_default = false WHERE is_default")
            for cid in previous_defaults:
                await conn.execute(
                    "UPDATE ai_connections SET is_default = true WHERE id = $1 AND is_active",
                    cid)
        sql(_teardown)
        ai_resolve.forget()


@pytest.fixture
def probes(monkeypatch):
    """Stub `llm.probe` and `voice.probe`; record every Resolved they receive."""
    seen: dict[str, list] = {"llm": [], "voice": [], "llm_client": []}

    # Mirrors the real signature: llm.probe(res, *, client_id=None). `client_id` is who the
    # probe is metered against, and it is recorded so a test can pin WHOSE spend a Test was.
    async def llm_probe(res, *, client_id=None):
        seen["llm"].append(res)
        seen["llm_client"].append(client_id)
        return {"ok": True, "detail": f"stub llm ok ({res.provider}/{res.model})"}

    async def voice_probe(res):
        seen["voice"].append(res)
        return {"ok": bool(res.api_key), "detail": "stub voice ok" if res.api_key else "no key"}

    monkeypatch.setattr(llm, "probe", llm_probe, raising=False)
    try:
        import app.services.voice as voice_mod
    except ImportError:
        voice_mod = types.ModuleType("app.services.voice")
        monkeypatch.setitem(sys.modules, "app.services.voice", voice_mod)
        import app.services as services_pkg
        monkeypatch.setattr(services_pkg, "voice", voice_mod, raising=False)
    monkeypatch.setattr(voice_mod, "probe", voice_probe, raising=False)
    return seen


@pytest.fixture
def member_login(api, seed):
    """A member-role login for tenant A — reads allowed, writes refused."""
    suffix = uuid.uuid4().hex[:8]
    username, password = f"aicfg-member-{suffix}", f"pw-{suffix}"
    uid = sql(lambda c: c.fetchval(
        "INSERT INTO tenant_users (client_id, username, password_hash, role) "
        "VALUES ($1, $2, $3, 'member') RETURNING id",
        uuid.UUID(seed["a"]["client_id"]), username, auth.hash_password(password)))
    try:
        r = api.post("/auth/login", json={"username": username, "password": password})
        assert r.status_code == 200 and r.json()["scope"] == "tenant", r.text
        yield {"Authorization": f"Bearer {r.json()['token']}"}
    finally:
        sql(lambda c: c.execute("DELETE FROM tenant_users WHERE id = $1", uid))


# --------------------------------------------------------------------------- #
# The catalog (pure)
# --------------------------------------------------------------------------- #
def test_catalog_shape():
    assert set(catalog.CATALOG) == set(CAPS)
    for cap, providers in catalog.CATALOG.items():
        assert providers, cap
        for pid, entry in providers.items():
            assert set(entry) == {"label", "known_models", "allows_base_url", "fields"}, (cap, pid)
            assert entry["known_models"] and all(isinstance(m, str) for m in entry["known_models"])
            assert catalog.is_known(cap, pid)
    # The decisions taken with the owner.
    assert catalog.CATALOG["llm"]["anthropic"]["known_models"] == \
        ["claude-opus-4-8", "claude-sonnet-5", "claude-haiku-4-5"]
    assert set(catalog.CATALOG["llm"]) == {"anthropic", "openai", "gemini"}
    assert catalog.allows_base_url("llm", "anthropic") and catalog.allows_base_url("llm", "openai")
    assert not catalog.allows_base_url("llm", "gemini")
    assert not catalog.allows_base_url("tts", "elevenlabs")
    for pid, entry in catalog.CATALOG["tts"].items():
        assert "voice_id" in entry["fields"], pid
    assert not catalog.is_known("llm", "elevenlabs") and not catalog.is_known("stt", "gemini")
    assert catalog.providers_for("nope") == {}


def test_providers_route_is_the_catalog_and_admin_only(api, seed):
    assert api.get("/admin/ai/providers").status_code == 401
    assert api.get("/admin/ai/providers", headers=_key(seed, "a")).status_code == 401
    r = api.get("/admin/ai/providers", headers=_admin())
    assert r.status_code == 200 and r.json() == catalog.CATALOG


# --------------------------------------------------------------------------- #
# The chain
# --------------------------------------------------------------------------- #
def test_four_layer_chain_each_layer_overrides_only_what_it_sets(api, seed, registry):
    A, B = seed["a"]["client_id"], seed["b"]["client_id"]
    k1, k2, k4 = (f"sk-default-{uuid.uuid4().hex}", f"sk-openai-{uuid.uuid4().hex}",
                  f"sk-tenant-{uuid.uuid4().hex}")

    # 0. Empty registry (defaults cleared by the fixture): the legacy layer answers.
    base = resolve(api, A, "llm")
    assert base.source == "legacy" and base.provider == "anthropic"
    assert base.connection_id is None and base.byo is False
    assert resolve(api, A, "llm", api_key="legacy-KEY", model="legacy-model").api_key == "legacy-KEY"

    # 1. A default connection: provider + model + key from the connection.
    d1 = registry.create(capability="llm", provider="anthropic", model="claude-sonnet-5",
                         api_key=k1)
    assert d1["has_key"] and d1["key_hint"] == "…" + k1[-4:] and d1["is_default"] is False
    registry.make_default(d1["id"])
    r = resolve(api, A, "llm")
    assert (r.source, r.provider, r.model, r.api_key, r.connection_id, r.byo) == \
        ("default", "anthropic", "claude-sonnet-5", k1, d1["id"], False)
    # A caller's legacy key/model are the BOTTOM of the chain: the default still wins.
    r = resolve(api, A, "llm", api_key="legacy-KEY", model="legacy-model")
    assert r.api_key == k1 and r.model == "claude-sonnet-5"

    # 2. An assigned connection on a DIFFERENT provider: the inherited model and key are
    #    dropped with the provider, and nothing legacy fills an OpenAI hole.
    d2 = registry.create(capability="llm", provider="openai", api_key=k2,
                         base_url="https://gateway.example.test/v1")
    out = registry.assign("a", llm=d2["id"])
    assert out["llm"]["connection_id"] == d2["id"]
    assert out["llm"]["effective"]["source"] == "assigned"
    assert out["llm"]["effective"]["connection"] == {"id": d2["id"], "name": d2["name"]}
    r = resolve(api, A, "llm")
    assert (r.source, r.provider, r.model, r.api_key, r.base_url, r.connection_id) == \
        ("assigned", "openai", None, k2, "https://gateway.example.test/v1", d2["id"])
    # The assignment is per tenant: B still runs on the default.
    rb = resolve(api, B, "llm")
    assert rb.source == "default" and rb.api_key == k1

    # 2b. An assigned connection on the SAME provider with nothing set inherits everything.
    d3 = registry.create(capability="llm", provider="anthropic")
    assert d3["has_key"] is False and d3["key_hint"] == ""
    registry.assign("a", llm=d3["id"])
    r = resolve(api, A, "llm")
    assert (r.source, r.provider, r.model, r.api_key, r.connection_id) == \
        ("assigned", "anthropic", "claude-sonnet-5", k1, d3["id"])

    # 3. The tenant's own row with a model only: source is honest (byo), spend is ours.
    r = api.put("/ai/config/llm", headers=_key(seed, "a"),
                json={"provider": "anthropic", "model": "claude-haiku-4-5"})
    assert r.status_code == 200, r.text
    view = r.json()
    assert view["override"]["has_key"] is False and view["override"]["enabled"] is True
    assert view["effective"]["source"] == "byo" and view["effective"]["byo"] is False
    assert set(view["providers"]) == {"anthropic", "openai", "gemini"}
    r = resolve(api, A, "llm")
    assert (r.source, r.byo, r.model, r.api_key, r.connection_id) == \
        ("byo", False, "claude-haiku-4-5", k1, d3["id"])

    # 4. Their own key: byo, no connection paid, the model they chose is kept (absent = keep).
    r = api.put("/ai/config/llm", headers=_key(seed, "a"),
                json={"provider": "anthropic", "api_key": k4})
    assert r.status_code == 200, r.text
    assert r.json()["override"]["has_key"] and r.json()["override"]["model"] == "claude-haiku-4-5"
    r = resolve(api, A, "llm")
    assert (r.source, r.byo, r.model, r.api_key, r.connection_id) == \
        ("byo", True, "claude-haiku-4-5", k4, None)
    # B is untouched by A's row.
    assert resolve(api, B, "llm").api_key == k1

    # 5. Switched off: the row stays (key on file) but the chain stops at the assignment.
    r = api.put("/ai/config/llm", headers=_key(seed, "a"),
                json={"provider": "anthropic", "enabled": False})
    assert r.status_code == 200 and r.json()["override"]["has_key"] is True
    r = resolve(api, A, "llm")
    assert r.source == "assigned" and r.api_key == k1 and r.byo is False

    # 6. Removed: same answer, and the row is gone.
    r = api.delete("/ai/config/llm", headers=_key(seed, "a"))
    assert r.status_code == 200 and r.json()["deleted"] is True
    assert r.json()["override"] is None
    assert resolve(api, A, "llm").source == "assigned"

    # 7. The assigned connection is deactivated: falls back to the default.
    r = api.delete(f"/admin/ai/connections/{d3['id']}", headers=_admin())
    assert r.status_code == 200 and r.json()["is_active"] is False
    r = resolve(api, A, "llm")
    assert r.source == "default" and r.connection_id == d1["id"] and r.api_key == k1
    got = api.get(f"/admin/ai/assignments/{A}", headers=_admin()).json()
    assert got["llm"]["effective"]["source"] == "default"

    # 8. The default itself deactivated: back to legacy for everyone.
    api.delete(f"/admin/ai/connections/{d1['id']}", headers=_admin())
    assert resolve(api, A, "llm").source == "legacy"
    assert resolve(api, B, "llm").source == "legacy"


def test_tts_settings_layer_and_provider_switch_drop_voice(api, seed, registry):
    A = seed["a"]["client_id"]
    d = registry.create(capability="tts", provider="elevenlabs", model="eleven_v3",
                        api_key="el-" + uuid.uuid4().hex, settings={"voice_id": "voice-D"})
    registry.make_default(d["id"])
    r = resolve(api, A, "tts")
    assert r.settings.get("voice_id") == "voice-D" and r.model == "eleven_v3"
    # A same-provider assignment without a voice inherits the default's voice.
    a = registry.create(capability="tts", provider="elevenlabs")
    registry.assign("a", tts=a["id"])
    r = resolve(api, A, "tts")
    assert r.settings.get("voice_id") == "voice-D" and r.source == "assigned"
    # A different provider drops it: an ElevenLabs voice id means nothing to OpenAI.
    o = registry.create(capability="tts", provider="openai", api_key="oa-" + uuid.uuid4().hex)
    registry.assign("a", tts=o["id"])
    r = resolve(api, A, "tts")
    assert r.provider == "openai" and "voice_id" not in r.settings and r.model is None
    # Capabilities are independent: llm and stt did not move.
    assert resolve(api, A, "llm").source == "legacy"
    assert resolve(api, A, "stt").source == "legacy"


def test_resolver_never_raises_on_a_registry_failure(api, seed, monkeypatch):
    async def boom(client_id, capability):
        raise RuntimeError("registry unavailable")

    monkeypatch.setattr(ai_resolve, "load_layers", boom)
    r = resolve(api, seed["a"]["client_id"], "llm", api_key="legacy-KEY", model="m")
    assert r.source == "legacy" and r.api_key == "legacy-KEY" and r.model == "m"
    with pytest.raises(ValueError):
        resolve(api, None, "vision")


# --------------------------------------------------------------------------- #
# Invariants: one default, keys never leave
# --------------------------------------------------------------------------- #
def test_one_default_per_capability(api, seed, registry):
    c1 = registry.create(capability="stt", provider="elevenlabs")
    c2 = registry.create(capability="stt", provider="openai")
    registry.make_default(c1["id"])
    registry.make_default(c2["id"])
    rows = api.get("/admin/ai/connections?capability=stt", headers=_admin()).json()
    defaults = [r["id"] for r in rows if r["is_default"]]
    assert defaults == [c2["id"]]
    assert rows[0]["id"] == c2["id"], "the default lists first"
    # The database enforces it too, not only the transaction.
    import asyncpg

    with pytest.raises(asyncpg.UniqueViolationError):
        sql(lambda c: c.execute(
            "UPDATE ai_connections SET is_default = true WHERE id = $1", uuid.UUID(c1["id"])))
    # An inactive connection cannot become the default; a deactivated default stops being one.
    api.delete(f"/admin/ai/connections/{c1['id']}", headers=_admin())
    r = api.post(f"/admin/ai/connections/{c1['id']}/default", headers=_admin())
    assert r.status_code == 400 and r.json()["code"] == "connection_inactive"
    api.delete(f"/admin/ai/connections/{c2['id']}", headers=_admin())
    health = api.get("/health").json()
    assert health["ai"]["defaults"]["stt"] is None


def test_a_key_never_appears_in_any_response(api, seed, registry, probes):
    A = seed["a"]["client_id"]
    k_conn, k_byo = f"sk-conn-{uuid.uuid4().hex}", f"sk-byo-{uuid.uuid4().hex}"
    d = registry.create(capability="llm", provider="anthropic", api_key=k_conn)
    registry.make_default(d["id"])
    bodies = [api.post(f"/admin/ai/connections/{d['id']}/test", headers=_admin()).text]
    r = api.put("/ai/config/llm", headers=_key(seed, "a"),
                json={"provider": "anthropic", "api_key": k_byo})
    bodies.append(r.text)
    bodies.append(api.post("/ai/config/llm/test", headers=_key(seed, "a")).text)
    bodies += [
        api.get("/admin/ai/connections", headers=_admin()).text,
        api.put(f"/admin/ai/connections/{d['id']}", headers=_admin(), json={"name": "renamed"}).text,
        api.get(f"/admin/ai/assignments/{A}", headers=_admin()).text,
        api.get("/ai/config", headers=_key(seed, "a")).text,
        api.get(f"/admin/ai-config/{A}", headers=_admin()).text,
        api.get("/health").text,
    ]
    for body in bodies:
        assert k_conn not in body and k_byo not in body, body[:300]
    # ...while the adapters received the real, opened keys.
    assert probes["llm"][0].api_key == k_conn
    assert probes["llm"][1].api_key == k_byo and probes["llm"][1].byo is True
    # And what the database holds is the sealed form whenever a SECRETS_KEY is configured.
    stored = sql(lambda c: c.fetchval(
        "SELECT api_key_enc FROM ai_connections WHERE id = $1", uuid.UUID(d["id"])))
    if ai_registry.secrets_status()["mode"] == "encrypted":
        assert stored != k_conn and stored.startswith("enc:")
    assert ai_resolve.open_secret(stored) == k_conn


# --------------------------------------------------------------------------- #
# Routes: validation, authority, the Test button
# --------------------------------------------------------------------------- #
def test_tenant_cannot_set_a_base_url(api, seed, registry):
    r = api.put("/ai/config/llm", headers=_key(seed, "a"),
                json={"provider": "anthropic", "base_url": "https://evil.example/v1"})
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "base_url_not_allowed" and r.json()["field"] == "base_url"
    r = api.put("/ai/config/llm", headers=_key(seed, "a"),
                json={"provider": "anthropic", "settings": {"base_url": "https://evil.example"}})
    assert r.status_code == 400 and r.json()["code"] == "base_url_not_allowed"
    # Nothing was written.
    assert api.get("/ai/config", headers=_key(seed, "a")).json()["llm"]["override"] is None
    # An operator's base_url on the row survives a tenant save that (correctly) omits it.
    A = seed["a"]["client_id"]
    r = api.put(f"/admin/ai-config/{A}", headers=_admin(),
                json={"enabled": True, "provider": "anthropic",
                      "base_url": "https://gateway.example.test", "model": "claude-sonnet-5"})
    assert r.status_code == 200 and r.json()["base_url"] == "https://gateway.example.test"
    r = api.put("/ai/config/llm", headers=_key(seed, "a"),
                json={"provider": "anthropic", "model": "claude-haiku-4-5"})
    assert r.status_code == 200 and "base_url" not in r.json()["override"]
    assert api.get(f"/admin/ai-config/{A}", headers=_admin()).json()["base_url"] == \
        "https://gateway.example.test"
    assert resolve(api, A, "llm").base_url == "https://gateway.example.test"


def test_validation_names_the_field(api, seed, registry):
    r = api.post("/admin/ai/connections", headers=_admin(),
                 json={"name": "x", "capability": "vision", "provider": "anthropic"})
    assert r.status_code == 400 and r.json()["code"] == "invalid_capability"
    r = api.post("/admin/ai/connections", headers=_admin(),
                 json={"name": "x", "capability": "llm", "provider": "elevenlabs"})
    assert r.status_code == 400 and r.json()["code"] == "unknown_provider"
    assert r.json()["field"] == "provider"
    r = api.post("/admin/ai/connections", headers=_admin(),
                 json={"name": "x", "capability": "llm", "provider": "gemini",
                       "base_url": "https://x.example"})
    assert r.status_code == 400 and r.json()["code"] == "base_url_not_allowed"
    r = api.post("/admin/ai/connections", headers=_admin(),
                 json={"name": "  ", "capability": "llm", "provider": "gemini"})
    assert r.status_code == 400 and r.json()["field"] == "name"
    r = api.post("/admin/ai/connections", headers=_admin(),
                 json={"name": "x", "capability": "llm", "provider": "openai",
                       "settings": {"api_key": "smuggled"}})
    assert r.status_code == 400 and r.json()["code"] == "invalid_settings"
    assert api.get(f"/admin/ai/connections/{uuid.uuid4()}/test", headers=_admin()).status_code \
        in (404, 405)
    r = api.post(f"/admin/ai/connections/{uuid.uuid4()}/test", headers=_admin())
    assert r.status_code == 404 and r.json()["code"] == "not_found"
    r = api.get("/admin/ai/connections?capability=vision", headers=_admin())
    assert r.status_code == 400
    r = api.put("/ai/config/vision", headers=_key(seed, "a"), json={"provider": "anthropic"})
    assert r.status_code == 400 and r.json()["code"] == "invalid_capability"
    r = api.put("/ai/config/llm", headers=_key(seed, "a"), json={"provider": "nope"})
    assert r.status_code == 400 and r.json()["code"] == "unknown_provider"


def test_assignment_validation(api, seed, registry):
    A = seed["a"]["client_id"]
    stt = registry.create(capability="stt", provider="elevenlabs")
    r = api.put(f"/admin/ai/assignments/{A}", headers=_admin(), json={"llm": stt["id"]})
    assert r.status_code == 400 and r.json()["code"] == "connection_mismatch"
    assert r.json()["field"] == "llm"
    r = api.put(f"/admin/ai/assignments/{A}", headers=_admin(), json={"llm": str(uuid.uuid4())})
    assert r.status_code == 404 and r.json()["code"] == "not_found"
    api.delete(f"/admin/ai/connections/{stt['id']}", headers=_admin())
    r = api.put(f"/admin/ai/assignments/{A}", headers=_admin(), json={"stt": stt["id"]})
    assert r.status_code == 400 and r.json()["code"] == "connection_inactive"
    assert api.get(f"/admin/ai/assignments/{uuid.uuid4()}", headers=_admin()).status_code == 404
    # Absent keys are untouched; null clears.
    llm_c = registry.create(capability="llm", provider="anthropic")
    out = registry.assign("a", llm=llm_c["id"])
    assert out["llm"]["connection_id"] == llm_c["id"] and out["stt"]["connection_id"] is None
    out = registry.assign("a", tts=None)
    assert out["llm"]["connection_id"] == llm_c["id"]
    out = registry.assign("a", llm=None)
    assert out["llm"]["connection_id"] is None


def test_who_may_read_and_write_the_tenant_config(api, seed, member_login, registry):
    assert api.get("/ai/config").status_code == 401
    assert api.put("/ai/config/llm", json={"provider": "anthropic"}).status_code == 401
    user = {"Authorization": f"Bearer {auth.make_user_token(uuid.uuid4())}"}
    assert api.get("/ai/config", headers=user).status_code == 403
    assert api.put("/ai/config/llm", headers=user, json={"provider": "anthropic"}).status_code == 403
    r = api.get("/ai/config", headers=member_login)
    assert r.status_code == 200 and set(r.json()) == set(CAPS)
    assert api.put("/ai/config/llm", headers=member_login,
                   json={"provider": "anthropic"}).status_code == 403
    assert api.delete("/ai/config/llm", headers=member_login).status_code == 403
    assert api.post("/ai/config/llm/test", headers=member_login).status_code == 403
    # An operator acting as the workspace is an owner-shaped principal here, and is named.
    op = {**_admin(), "X-Act-As-Tenant": seed["a"]["client_id"]}
    r = api.put("/ai/config/llm", headers=op, json={"provider": "anthropic", "model": "claude-sonnet-5"})
    assert r.status_code == 200 and r.json()["override"]["updated_by"] == "tenant:superadmin"
    # The integration credential is not a tenant here.
    integ = {"X-CQ-Key": seed["integration"]["api_key"], "X-CQ-Tenant": seed["a"]["client_id"]}
    assert api.get("/ai/config", headers=integ).status_code == 401
    # /admin/ai is the superadmin's alone — a scoped operator is refused like on every /admin.
    assert api.get("/admin/ai/connections", headers=_key(seed, "a")).status_code == 401


def test_test_button_stores_last_test_and_probes_the_right_layer(api, seed, registry, probes):
    A = seed["a"]["client_id"]
    llm_c = registry.create(capability="llm", provider="gemini", model="gemini-2.5-flash",
                            api_key="gk-" + uuid.uuid4().hex)
    r = api.post(f"/admin/ai/connections/{llm_c['id']}/test", headers=_admin())
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True and "gemini/gemini-2.5-flash" in r.json()["detail"]
    assert r.json()["at"]
    listed = {c["id"]: c for c in api.get("/admin/ai/connections", headers=_admin()).json()}
    assert listed[llm_c["id"]]["last_test"]["ok"] is True
    assert listed[llm_c["id"]]["last_test"]["at"] == r.json()["at"]
    assert probes["llm"][-1].connection_id == llm_c["id"]
    assert probes["llm_client"][-1] is None, "a deployment connection's Test is nobody's spend"

    tts_c = registry.create(capability="tts", provider="elevenlabs", settings={"voice_id": "v1"})
    r = api.post(f"/admin/ai/connections/{tts_c['id']}/test", headers=_admin())
    assert r.status_code == 200 and probes["voice"][-1].capability == "tts"
    assert probes["voice"][-1].settings.get("voice_id") == "v1"

    # A material edit discards the tick; a rename keeps it.
    r = api.put(f"/admin/ai/connections/{llm_c['id']}", headers=_admin(), json={"name": "Gemini"})
    assert r.json()["last_test"] and r.json()["name"] == "Gemini"
    r = api.put(f"/admin/ai/connections/{llm_c['id']}", headers=_admin(),
                json={"model": "gemini-2.5-pro"})
    assert r.json()["last_test"] is None and r.json()["model"] == "gemini-2.5-pro"
    # A provider change without a new key drops the old one; clear_key drops it explicitly.
    r = api.put(f"/admin/ai/connections/{llm_c['id']}", headers=_admin(), json={"provider": "openai"})
    assert r.json()["provider"] == "openai" and r.json()["has_key"] is False
    r = api.put(f"/admin/ai/connections/{llm_c['id']}", headers=_admin(), json={"api_key": "new-key-1234"})
    assert r.json()["has_key"] is True and r.json()["key_hint"] == "…1234"
    r = api.put(f"/admin/ai/connections/{llm_c['id']}", headers=_admin(), json={"clear_key": True})
    assert r.json()["has_key"] is False
    r = api.put(f"/admin/ai/connections/{llm_c['id']}", headers=_admin(), json={"capability": "stt"})
    assert r.status_code == 400 and r.json()["code"] == "capability_immutable"

    # The tenant's own Test: 404 without a row, then probes their row even while disabled.
    r = api.post("/ai/config/llm/test", headers=_key(seed, "a"))
    assert r.status_code == 404 and r.json()["code"] == "no_override"
    own = "sk-own-" + uuid.uuid4().hex
    api.put("/ai/config/llm", headers=_key(seed, "a"),
            json={"provider": "anthropic", "api_key": own, "enabled": False})
    r = api.post("/ai/config/llm/test", headers=_key(seed, "a"))
    assert r.status_code == 200 and r.json()["ok"] is True
    assert probes["llm"][-1].api_key == own and probes["llm"][-1].byo is True
    assert probes["llm_client"][-1] == A, "an owner's own Test is metered as that workspace"
    assert resolve(api, A, "llm").api_key != own, "disabled: not applied to real calls"


def test_probe_failure_is_a_result_not_a_500(api, seed, registry, monkeypatch):
    async def bad(res, *, client_id=None):
        raise RuntimeError("401 invalid x-api-key")

    monkeypatch.setattr(llm, "probe", bad, raising=False)
    c = registry.create(capability="llm", provider="anthropic")
    r = api.post(f"/admin/ai/connections/{c['id']}/test", headers=_admin())
    assert r.status_code == 200 and r.json()["ok"] is False
    assert "invalid x-api-key" in r.json()["detail"]


# --------------------------------------------------------------------------- #
# Boot: seeding, the legacy-table copy, /health
# --------------------------------------------------------------------------- #
def test_seed_from_legacy_is_idempotent(api, seed, registry, monkeypatch):
    from app.services import settings_store

    real = settings_store.get_effective

    async def fake():
        cfg = await real()
        cfg.update(anthropic_api_key="sk-seed-" + uuid.uuid4().hex,
                   elevenlabs_api_key="el-seed-" + uuid.uuid4().hex,
                   llm_model="claude-sonnet-5", stt_model="scribe_v1",
                   tts_model="eleven_v3", tts_voice_id="voice-seed")
        return cfg

    monkeypatch.setattr(settings_store, "get_effective", fake)

    def count_active():
        return sql(lambda c: c.fetchval("SELECT count(*) FROM ai_connections WHERE is_active"))

    # With active connections present the seed is a no-op, whatever the panel holds.
    if count_active():
        n = count_active()
        lines = api.portal.call(ai_registry.seed_from_legacy)
        assert count_active() == n and "skipped" in lines[0], lines

    # Park every active connection so the seed sees an empty registry — restored below —
    # then run it twice: the second run must find the first's rows and add nothing.
    parked = sql(lambda c: c.fetch("SELECT id FROM ai_connections WHERE is_active"))
    parked_ids = [r["id"] for r in parked]
    pre = {str(r["id"]) for r in sql(lambda c: c.fetch("SELECT id FROM ai_connections"))}
    sql(lambda c: c.execute(
        "UPDATE ai_connections SET is_active = false WHERE id = ANY($1::uuid[])", parked_ids))
    try:
        first = api.portal.call(ai_registry.seed_from_legacy)
        after_first = count_active()
        second = api.portal.call(ai_registry.seed_from_legacy)
        assert count_active() == after_first, second
        assert all("seed" in line for line in first + second), first + second
        assert "skipped" in second[0] or after_first == 0, second

        rows = sql(lambda c: c.fetch(
            "SELECT id, name, capability, provider, model, settings, is_default, updated_by "
            "FROM ai_connections WHERE updated_by = $1 AND is_active", ai_registry.SEED_ACTOR))
        new = [r for r in rows if str(r["id"]) not in pre]
        registry.created += [str(r["id"]) for r in new]
        by_cap = {r["capability"]: r for r in new}
        for cap in CAPS:
            line = next(l for l in first if ai_registry.SEED_NAMES[cap] in l)
            if "seeded default" in line:
                assert cap in by_cap, (cap, first)
            else:
                # A seed row from an earlier boot was parked: not re-created, by design.
                assert "not re-created" in line and cap not in by_cap
        if "llm" in by_cap:
            assert by_cap["llm"]["name"] == "Anthropic (deployment)" and by_cap["llm"]["is_default"]
            assert by_cap["llm"]["provider"] == "anthropic"
            assert by_cap["llm"]["model"] == "claude-sonnet-5"
            assert api.get("/health").json()["ai"]["defaults"]["llm"] == "Anthropic (deployment)"
        if "stt" in by_cap:
            assert by_cap["stt"]["provider"] == "elevenlabs" and by_cap["stt"]["is_default"]
        if "tts" in by_cap:
            assert ai_resolve._jsonb(by_cap["tts"]["settings"]) == {"voice_id": "voice-seed"}
            assert by_cap["tts"]["model"] == "eleven_v3"
        # The sealed key is the panel's key, opened — and it is not in the listing.
        listing = api.get("/admin/ai/connections", headers=_admin()).text
        assert "sk-seed-" not in listing and "el-seed-" not in listing
    finally:
        sql(lambda c: c.execute(
            "UPDATE ai_connections SET is_active = true WHERE id = ANY($1::uuid[])", parked_ids))
        ai_resolve.forget()


def test_legacy_tenant_ai_configs_are_copied_once(api, seed, registry):
    from app.db import pool
    from app.services.migrate import _migrate_tenant_ai_configs

    B = uuid.UUID(seed["b"]["client_id"])
    legacy_key = "sk-legacy-" + uuid.uuid4().hex
    sql(lambda c: c.execute(
        "INSERT INTO tenant_ai_configs (client_id, provider, model, api_key, enabled, notes) "
        "VALUES ($1, 'anthropic', 'claude-haiku-4-5', $2, true, 'from the old table') "
        "ON CONFLICT (client_id) DO UPDATE SET api_key = EXCLUDED.api_key, "
        "model = EXCLUDED.model, enabled = true", B, legacy_key))

    async def run():
        async with pool().acquire() as conn:
            return await _migrate_tenant_ai_configs(conn)

    try:
        line = api.portal.call(run)
        assert line.endswith("1 row(s) copied"), line
        row = sql(lambda c: c.fetchrow(
            "SELECT provider, model, api_key_enc, enabled, notes FROM tenant_ai_overrides "
            "WHERE client_id = $1 AND capability = 'llm'", B))
        assert row["model"] == "claude-haiku-4-5" and row["enabled"] is True
        assert ai_resolve.open_secret(row["api_key_enc"]) == legacy_key
        if ai_registry.secrets_status()["mode"] == "encrypted":
            assert row["api_key_enc"] != legacy_key
        # The owner edits the copied row; a second boot must not overwrite their edit.
        r = api.put("/ai/config/llm", headers=_key(seed, "b"),
                    json={"provider": "anthropic", "model": "claude-sonnet-5"})
        assert r.status_code == 200
        line = api.portal.call(run)
        assert line.endswith("0 row(s) copied"), line
        assert resolve(api, str(B), "llm").model == "claude-sonnet-5"
        assert resolve(api, str(B), "llm").api_key == legacy_key
        # The compat route reads the same row.
        r = api.get(f"/admin/ai-config/{B}", headers=_admin()).json()
        assert r["has_key"] and r["model"] == "claude-sonnet-5" and r["enabled"] is True
        assert legacy_key not in str(r)
    finally:
        sql(lambda c: c.execute("DELETE FROM tenant_ai_configs WHERE client_id = $1", B))


def test_compat_route_is_a_shim_over_the_llm_override(api, seed, registry):
    A = seed["a"]["client_id"]
    r = api.get(f"/admin/ai-config/{A}", headers=_admin())
    assert r.status_code == 200
    assert r.json() == {"enabled": False, "provider": "anthropic", "model": None,
                        "base_url": None, "has_key": False, "key_hint": "", "notes": None,
                        "updated_at": None, "updated_by": None}
    key = "sk-compat-" + uuid.uuid4().hex
    r = api.put(f"/admin/ai-config/{A}", headers=_admin(),
                json={"enabled": True, "provider": "anthropic", "model": "claude-sonnet-5",
                      "api_key": key, "notes": "billed to them"})
    assert r.status_code == 200, r.text
    assert r.json()["has_key"] and r.json()["updated_by"] == "superadmin"
    assert r.json()["key_hint"] == "…" + key[-4:] and key not in r.text
    view = api.get("/ai/config", headers=_key(seed, "a")).json()["llm"]
    assert view["override"]["has_key"] and view["override"]["notes"] == "billed to them"
    assert view["effective"]["source"] == "byo" and view["effective"]["byo"] is True
    res = resolve(api, A, "llm")
    assert res.api_key == key and res.byo is True and res.model == "claude-sonnet-5"
    # Editing the model without a key keeps the key (absent = keep); clear_key removes it.
    r = api.put(f"/admin/ai-config/{A}", headers=_admin(),
                json={"enabled": True, "provider": "anthropic", "model": "claude-haiku-4-5"})
    assert r.json()["has_key"] is True and r.json()["model"] == "claude-haiku-4-5"
    r = api.put(f"/admin/ai-config/{A}", headers=_admin(),
                json={"enabled": True, "provider": "anthropic", "clear_key": True})
    assert r.json()["has_key"] is False
    assert resolve(api, A, "llm").byo is False
    r = api.put(f"/admin/ai-config/{A}", headers=_admin(),
                json={"enabled": True, "provider": "martian"})
    assert r.status_code == 400 and r.json()["code"] == "unknown_provider"
    assert api.put(f"/admin/ai-config/{uuid.uuid4()}", headers=_admin(),
                   json={"enabled": True}).status_code == 404


def test_keys_are_sealed_at_rest_when_the_vault_encrypts(api, seed, registry, monkeypatch):
    """The registry seals on write and opens only in the resolver. A real SECRETS_KEY cannot
    be set for one test — boot's migrate_plaintext would re-seal the shared database's keys
    with it — so the vault is swapped for a reversible stand-in with the contract's shape."""
    class FakeVault:
        @staticmethod
        def seal(plaintext):
            return "enc:v1:" + plaintext[::-1]

        @staticmethod
        def open(stored):
            if not stored:
                return ""
            return stored[len("enc:v1:"):][::-1] if stored.startswith("enc:v1:") else stored

        @staticmethod
        def is_sealed(value):
            return bool(value) and value.startswith("enc:v1:")

        @staticmethod
        def status():
            return {"mode": "encrypted"}

    monkeypatch.setattr(ai_resolve, "vault", lambda: FakeVault)
    assert api.get("/health").json()["secrets"] == "encrypted"
    raw = "sk-sealed-" + uuid.uuid4().hex
    c = registry.create(capability="llm", provider="anthropic", api_key=raw)
    registry.make_default(c["id"])
    stored = sql(lambda x: x.fetchval(
        "SELECT api_key_enc FROM ai_connections WHERE id = $1", uuid.UUID(c["id"])))
    assert stored.startswith("enc:v1:") and raw not in stored
    assert c["has_key"] and c["key_hint"] == "…" + raw[-4:] and raw not in str(c)
    assert resolve(api, seed["a"]["client_id"], "llm").api_key == raw
    # The tenant's own key takes the same path.
    own = "sk-own-sealed-" + uuid.uuid4().hex
    r = api.put("/ai/config/llm", headers=_key(seed, "a"), json={"provider": "anthropic", "api_key": own})
    assert r.status_code == 200 and own not in r.text
    stored = sql(lambda x: x.fetchval(
        "SELECT api_key_enc FROM tenant_ai_overrides WHERE client_id = $1 AND capability = 'llm'",
        uuid.UUID(seed["a"]["client_id"])))
    assert stored.startswith("enc:v1:") and own not in stored
    assert resolve(api, seed["a"]["client_id"], "llm").api_key == own


def test_health_reports_secrets_and_ai(api):
    h = api.get("/health").json()
    assert h["secrets"] in ("encrypted", "plaintext")
    assert isinstance(h["ai"]["connections"], int)
    assert set(h["ai"]["defaults"]) == set(CAPS)
    for v in h["ai"]["defaults"].values():
        assert v is None or isinstance(v, str)



def test_resolve_survives_the_legacy_settings_read_failing(api, monkeypatch):
    """The resolver's contract is "never raises for a lookup failure", and voice.py calls it
    with no guard of its own — so a settings read that fails must degrade to an empty legacy
    layer, not take every transcription and every clip down with it."""
    import asyncio
    from app.services import ai_resolve, settings_store

    async def _boom():
        raise RuntimeError("settings unavailable")
    monkeypatch.setattr(settings_store, "get_effective", _boom)
    ai_resolve.forget()
    res = asyncio.run(ai_resolve.resolve(None, "stt"))
    assert res.capability == "stt" and res.provider == "elevenlabs"
    assert res.api_key == ""            # the hole is reported, not invented
