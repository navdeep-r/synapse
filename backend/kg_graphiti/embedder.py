"""Deterministic embedder built on the alias-matching core (R1).

This governs *recall* — which existing nodes Graphiti even considers as merge
candidates. A plain char-n-gram hash would place "Tech Corp Inc." far from
"TechCorp" and the merge decision would never get the chance to fire, so the
feature set is built from canonical tokens rather than raw characters.

No network, no model download, fully reproducible: the same string always
produces the same vector.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Iterable

from graphiti_core.embedder.client import EmbedderClient, EmbedderConfig

from kg_graphiti.resolution import canonical_keys, canonical_tokens, normalize

DEFAULT_DIM = 384


def _bucket(feature: str, dim: int) -> int:
    digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") % dim


def _sign(feature: str) -> float:
    digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=1).digest()
    return 1.0 if digest[0] & 1 else -1.0


def _char_ngrams(text: str, size: int = 3) -> list[str]:
    padded = f"^{text}$"
    if len(padded) <= size:
        return [padded]
    return [padded[i : i + size] for i in range(len(padded) - size + 1)]


def features(text: str) -> list[tuple[str, float]]:
    """Weighted features for a string.

    Canonical forms dominate so that alias variants land close together, with
    character n-grams providing a softer similarity floor for everything else.
    """
    weighted: list[tuple[str, float]] = []

    for key in canonical_keys(text):
        weighted.append((f"key::{key}", 3.0))
    for token in canonical_tokens(text):
        weighted.append((f"tok::{token}", 2.0))

    collapsed = normalize(text).replace(" ", "")
    for gram in _char_ngrams(collapsed):
        weighted.append((f"ng::{gram}", 0.5))

    for word in normalize(text).split():
        weighted.append((f"raw::{word}", 0.75))

    return weighted


def embed(text: str, dim: int = DEFAULT_DIM) -> list[float]:
    """Hash features into a unit-length vector."""
    vector = [0.0] * dim
    for feature, weight in features(text or ""):
        vector[_bucket(feature, dim)] += weight * _sign(feature)

    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0.0:
        # Keep a stable non-zero vector so cosine similarity stays defined.
        vector[0] = 1.0
        return vector
    return [value / norm for value in vector]


def cosine(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))


class DeterministicEmbedder(EmbedderClient):
    """`EmbedderClient` backed by hashed canonical features."""

    def __init__(self, dim: int = DEFAULT_DIM) -> None:
        self.config = EmbedderConfig(embedding_dim=dim)
        self.dim = dim

    async def create(
        self, input_data: str | list[str] | Iterable[int] | Iterable[Iterable[int]]
    ) -> list[float]:
        return embed(_coerce(input_data), self.dim)

    async def create_batch(self, input_data_list: list[str]) -> list[list[float]]:
        return [embed(_coerce(item), self.dim) for item in input_data_list]


def _coerce(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return " ".join(_coerce(v) for v in value)
    return str(value)
