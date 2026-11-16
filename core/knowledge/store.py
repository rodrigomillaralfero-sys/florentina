"""Contratos de almacenamiento del conocimiento base."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Protocol, Sequence


@dataclass(frozen=True)
class KnowledgeSource:
    instance_id: str
    source: str  # nombre relativo del archivo dentro de knowledge/
    content_hash: str | None  # None = el archivo ya no esta: sus trozos dejan de recuperarse
    chunks: int
    loaded_at: datetime


@dataclass(frozen=True)
class KnowledgeChunk:
    id: str
    instance_id: str
    source: str
    source_hash: str
    index: int
    text: str
    created_at: datetime


@dataclass(frozen=True)
class KnowledgeHit:
    chunk: KnowledgeChunk
    similarity: float


@dataclass(frozen=True)
class KnowledgeStats:
    sources: int
    chunks: int
    embedded: int  # trozos vigentes con vector del modelo consultado


class KnowledgeStore(Protocol):
    async def sources(self, instance_id: str) -> Sequence[KnowledgeSource]:
        """Todas las fuentes conocidas, vigentes o retiradas (content_hash None)."""
        ...

    async def put_source(
        self, instance_id: str, source: str, *, content_hash: str, chunks: Sequence[str], now: datetime
    ) -> tuple[KnowledgeSource, bool]:
        """Declara `content_hash` como version vigente de `source` y escribe sus trozos si esa version
        no existia. Devuelve (fuente, created) con created=True solo si escribio trozos nuevos."""
        ...

    async def retire_source(self, instance_id: str, source: str, *, now: datetime) -> bool:
        """El archivo desaparecio de la carpeta: la fuente deja de tener version vigente. Los trozos quedan."""
        ...

    async def missing_embeddings(self, instance_id: str, *, model: str, limit: int = 100) -> Sequence[KnowledgeChunk]:
        """Trozos de versiones vigentes sin vector de `model`."""
        ...

    async def set_embedding(
        self, chunk_id: str, *, model: str, dim: int, vector: Sequence[float], usage: Mapping[str, int] | None = None
    ) -> bool: ...

    async def search(
        self, instance_id: str, *, model: str, vector: Sequence[float], limit: int
    ) -> Sequence[KnowledgeHit]:
        """Trozos vigentes mas parecidos, de mayor a menor similitud. Sin umbral: lo aplica quien llama."""
        ...

    async def stats(self, instance_id: str, *, model: str) -> KnowledgeStats: ...
