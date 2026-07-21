"""Provider-swappable embeddings.

Config comes from the admin panel (app_settings 'embeddings') merged over env defaults.
Providers implement `embed(texts, *, purpose=...) -> list[list[float]]` and expose `.dim`.

The provider is cached for `_TTL` seconds. Without the cache every single embed did a
pool acquire plus a SELECT on app_settings before it could send a byte — a rounding
error on a 30-second audio job, but real latency on an interactive chat turn, and it
borrows a connection from the same small pool the rest of the request needs. The TTL
(rather than a permanent cache) is what keeps an admin's provider/model change from
requiring a restart; `invalidate_provider_cache()` closes even that window on the one
path that knows a change happened.

`purpose` selects the timeout/keepalive profile inside the provider, not a different
provider: "query" is a user-visible turn that must fail fast, "ingest" is a document
batch that must not. Callers that pass nothing keep the old ingest behaviour.
"""
import asyncio
import json
import time

from ..settings_store import get_embedding_config
from .openai_provider import OpenAIEmbeddings
from .tei import TEIEmbeddings

_TTL = 60.0

# (config-fingerprint, provider, expires_at monotonic). Guarded by _lock so an
# expiry under concurrent chat turns costs one SELECT, not one per waiter.
_cached: tuple[str, object, float] | None = None
_lock = asyncio.Lock()


def _build(cfg: dict):
    provider = (cfg.get("provider") or "tei").lower()
    if provider == "openai":
        return OpenAIEmbeddings(
            model=cfg.get("model") or "text-embedding-3-large",
            api_key=cfg.get("api_key") or "",
            base_url=cfg.get("base_url") or "https://api.openai.com/v1",
            dim=int(cfg.get("dim") or 3072),
        )
    # default: self-hosted TEI (BGE-M3)
    return TEIEmbeddings(
        base_url=cfg.get("base_url") or "http://embeddings:80",
        model=cfg.get("model") or "BAAI/bge-m3",
        dim=int(cfg.get("dim") or 1024),
    )


def _fingerprint(cfg: dict) -> str:
    return json.dumps(cfg, sort_keys=True, default=str)


def invalidate_provider_cache() -> None:
    """Drop the cached provider so the next embed re-reads settings immediately."""
    global _cached
    _cached = None


async def get_provider(purpose: str = "ingest"):
    """The configured embeddings provider, rebuilt at most once per `_TTL`.

    `purpose` is accepted for symmetry with `embed_texts` and is deliberately NOT
    part of the cache key — one provider object serves both profiles so its
    keep-alive connections are shared.
    """
    global _cached
    now = time.monotonic()
    cached = _cached
    if cached is not None and now < cached[2]:
        return cached[1]

    async with _lock:
        # Re-check: another waiter may have refreshed while we queued.
        cached = _cached
        now = time.monotonic()
        if cached is not None and now < cached[2]:
            return cached[1]
        cfg = await get_embedding_config()
        key = _fingerprint(cfg)
        # Unchanged config keeps the SAME provider object (and its warm sockets);
        # only a real settings change builds a new one.
        provider = cached[1] if cached is not None and cached[0] == key else _build(cfg)
        _cached = (key, provider, now + _TTL)
        return provider


async def embed_texts(texts: list[str], *, purpose: str = "ingest") -> list[list[float]]:
    provider = await get_provider(purpose)
    return await provider.embed(texts, purpose=purpose)
