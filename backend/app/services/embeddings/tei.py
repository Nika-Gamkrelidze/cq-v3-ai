"""Self-hosted embeddings via Hugging Face Text-Embeddings-Inference (TEI).

Serves BAAI/bge-m3 (strong multilingual retrieval incl. Georgian) with no external
API key. TEI exposes POST /embed {inputs: [...]} -> [[...]].

Clients are module-level and never closed. The previous code built a fresh
`httpx.AsyncClient` per call, so every embed paid a TCP connect — invisible on a
30-second audio job, but it is a per-turn tax on the interactive chat path. Two
clients rather than one because the two callers want opposite failure modes:
`purpose="query"` is on a user-visible turn and must give up fast, while
`purpose="ingest"` batches a whole document and must not.
"""
import httpx

from ...config import settings
from .base import EmbeddingError

_clients: dict[str, httpx.AsyncClient] = {}


def _client(purpose: str) -> httpx.AsyncClient:
    """Lazily built so construction happens on the running loop, not at import."""
    client = _clients.get(purpose)
    if client is None:
        if purpose == "query":
            client = httpx.AsyncClient(
                timeout=httpx.Timeout(settings.embed_query_timeout_s, connect=1.0),
                limits=httpx.Limits(max_keepalive_connections=20),
            )
        else:
            client = httpx.AsyncClient(
                timeout=httpx.Timeout(120.0),
                limits=httpx.Limits(max_keepalive_connections=20),
            )
        _clients[purpose] = client
    return client


class TEIEmbeddings:
    def __init__(self, base_url: str, model: str = "BAAI/bge-m3", dim: int = 1024):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.dim = dim

    async def embed(self, texts: list[str], *, purpose: str = "ingest") -> list[list[float]]:
        if not texts:
            return []
        try:
            resp = await _client(purpose).post(
                f"{self.base_url}/embed",
                json={"inputs": texts, "normalize": True, "truncate": True},
            )
        except httpx.HTTPError as exc:
            raise EmbeddingError(f"Embeddings service unreachable at {self.base_url}: {exc}") from exc
        if resp.status_code >= 400:
            raise EmbeddingError(f"Embeddings failed ({resp.status_code}): {resp.text[:300]}")
        return resp.json()

    async def health(self) -> dict:
        vecs = await self.embed(["health check"])
        return {"ok": True, "dim": len(vecs[0]) if vecs else 0, "model": self.model}
