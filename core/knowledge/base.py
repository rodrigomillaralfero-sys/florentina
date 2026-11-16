"""Recuperacion del conocimiento base por turno, con umbral de pertinencia."""

from __future__ import annotations

from typing import Sequence

from core.knowledge.store import KnowledgeHit, KnowledgeStats, KnowledgeStore


class KnowledgeBase:
    def __init__(self, *, instance_id: str, store: KnowledgeStore, min_similarity: float = 0.35, limit: int = 3) -> None:
        self.instance_id = instance_id
        self._store = store
        self.min_similarity = min_similarity
        self.limit = limit

    async def search(self, *, model: str, vector: Sequence[float], limit: int | None = None) -> Sequence[KnowledgeHit]:
        """Trozos pertinentes: los mas parecidos que superan el umbral. Vacio = nada pertinente.
        `limit` permite a un proceso de fondo pedir mas que el turno."""
        hits = await self._store.search(self.instance_id, model=model, vector=vector, limit=limit or self.limit)
        return [h for h in hits if h.similarity >= self.min_similarity]

    async def stats(self, *, model: str) -> KnowledgeStats:
        return await self._store.stats(self.instance_id, model=model)
