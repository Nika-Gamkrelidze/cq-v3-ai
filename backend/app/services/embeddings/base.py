"""Shared embeddings helpers."""


class EmbeddingError(RuntimeError):
    pass


def to_pgvector(vec: list[float]) -> str:
    """Serialize a float list to the pgvector text literal, e.g. '[0.1,0.2,...]'."""
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"
