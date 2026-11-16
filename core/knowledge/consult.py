"""Consulta del conocimiento base fuera del turno (procesos de fondo: revisiones, analisis).

El turno en vivo consulta el conocimiento con lo que la persona acaba de escribir (`core.memory.recall`).
Un proceso de fondo lo consulta con la situacion que tiene que resolver (p. ej. una pregunta pendiente
o un patron por revisar). Misma base, mismo umbral; otro limite de trozos (de fondo no hay apuro).

Reglas:
- Con el corpus vacio no se calcula el embedding de la consulta: costo cero.
- Si la busqueda falla, el proceso sigue sin conocimiento y queda la falta registrada (`failed`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Sequence

from core.interfaces.model_adapter import ModelAdapter, ModelError, ModelRole
from core.knowledge.base import KnowledgeBase
from core.knowledge.store import KnowledgeHit

log = logging.getLogger(__name__)

MAX_QUERY_CHARS = 4000  # la consulta es un resumen de la situacion, no el prompt entero


@dataclass(frozen=True)
class Consultation:
    hits: Sequence[KnowledgeHit] = ()
    enabled: bool = False  # hay conocimiento cableado (aunque este vacio)
    skipped: str | None = None  # "sin consulta" | "corpus vacio"
    failed: bool = False

    def summary(self) -> dict:
        return {"hits": len(self.hits), "skipped": self.skipped, "failed": self.failed}


class KnowledgeConsultant:
    def __init__(self, *, base: KnowledgeBase, adapter: ModelAdapter, limit: int = 6) -> None:
        self._base = base
        self._adapter = adapter
        self.limit = max(1, limit)

    async def consult(self, query: str | None) -> Consultation:
        query = (query or "").strip()[:MAX_QUERY_CHARS]
        if not query:
            return Consultation(enabled=True, skipped="sin consulta")
        try:
            model = self._adapter.identity(ModelRole.EMBEDDINGS).model
            stats = await self._base.stats(model=model)
            if stats.embedded <= 0:
                return Consultation(enabled=True, skipped="corpus vacio")
            res = await self._adapter.embed([query])
            vector = res.vectors[0] if res.vectors else None
            if vector is None:
                return Consultation(enabled=True, failed=True)
            hits = await self._base.search(model=res.identity.model, vector=vector, limit=self.limit)
            return Consultation(hits=tuple(hits), enabled=True)
        except ModelError as exc:
            log.warning("conocimiento sin consulta (proveedor): %s", exc)
            return Consultation(enabled=True, failed=True)
        except Exception as exc:  # noqa: BLE001 — el conocimiento nunca tumba un proceso
            log.warning("conocimiento sin consulta (%s)", type(exc).__name__)
            return Consultation(enabled=True, failed=True)
