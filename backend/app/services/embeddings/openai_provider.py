"""Managed embeddings via the OpenAI-compatible /embeddings API.

Alternative to the self-hosted TEI provider. Easy but costs money and is weaker on
lower-resource languages (e.g. Georgian). Works with OpenAI or any OpenAI-compatible
endpoint (base_url overridable).

Same module-level, never-closed client treatment as the TEI provider — here the
payoff is larger, since a per-call client also re-does the TLS handshake to a
remote host. The query client gets a longer connect budget than TEI's (1s is a
LAN number; this one crosses the internet).
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
                timeout=httpx.Timeout(settings.embed_query_timeout_s, connect=2.0),
                limits=httpx.Limits(max_keepalive_connections=20),
            )
        else:
            client = httpx.AsyncClient(
                timeout=httpx.Timeout(120.0),
                limits=httpx.Limits(max_keepalive_connections=20),
            )
        _clients[purpose] = client
    return client


class OpenAIEmbeddings:
    def __init__(self, model: str, api_key: str, base_url: str = "https://api.openai.com/v1",
                 dim: int = 3072):
        self.model = model
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.dim = dim

    async def embed(self, texts: list[str], *, purpose: str = "ingest") -> list[list[float]]:
        if not texts:
            return []
        if not self.api_key:
            raise EmbeddingError("OpenAI embeddings selected but no API key is configured.")
        try:
            resp = await _client(purpose).post(
                f"{self.base_url}/embeddings",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"model": self.model, "input": texts},
            )
        except httpx.HTTPError as exc:
            raise EmbeddingError(f"OpenAI embeddings unreachable: {exc}") from exc
        if resp.status_code >= 400:
            raise EmbeddingError(f"OpenAI embeddings failed ({resp.status_code}): {resp.text[:300]}")
        data = resp.json().get("data", [])
        return [row["embedding"] for row in data]

    async def health(self) -> dict:
        vecs = await self.embed(["health check"])
        return {"ok": True, "dim": len(vecs[0]) if vecs else 0, "model": self.model}
