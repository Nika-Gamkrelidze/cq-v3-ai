"""Provider-swappable embeddings.

Config comes from the admin panel (app_settings 'embeddings') merged over env defaults.
Providers implement `embed(texts) -> list[list[float]]` and expose `.dim`.
"""
from ..settings_store import get_embedding_config
from .openai_provider import OpenAIEmbeddings
from .tei import TEIEmbeddings


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


async def get_provider():
    """Build the configured embeddings provider from current settings."""
    return _build(await get_embedding_config())


async def embed_texts(texts: list[str]) -> list[list[float]]:
    provider = await get_provider()
    return await provider.embed(texts)
