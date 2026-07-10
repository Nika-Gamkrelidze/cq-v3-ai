"""Self-hosted embeddings via Hugging Face Text-Embeddings-Inference (TEI).

Serves BAAI/bge-m3 (strong multilingual retrieval incl. Georgian) with no external
API key. TEI exposes POST /embed {inputs: [...]} -> [[...]].
"""
import httpx

from .base import EmbeddingError


class TEIEmbeddings:
    def __init__(self, base_url: str, model: str = "BAAI/bge-m3", dim: int = 1024):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.dim = dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(
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
