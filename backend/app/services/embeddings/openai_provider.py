"""Managed embeddings via the OpenAI-compatible /embeddings API.

Alternative to the self-hosted TEI provider. Easy but costs money and is weaker on
lower-resource languages (e.g. Georgian). Works with OpenAI or any OpenAI-compatible
endpoint (base_url overridable).
"""
import httpx

from .base import EmbeddingError


class OpenAIEmbeddings:
    def __init__(self, model: str, api_key: str, base_url: str = "https://api.openai.com/v1",
                 dim: int = 3072):
        self.model = model
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.dim = dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if not self.api_key:
            raise EmbeddingError("OpenAI embeddings selected but no API key is configured.")
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(
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
