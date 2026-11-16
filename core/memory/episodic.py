"""Escritura de la episodica recuperable: un episodio con embedding al cerrar cada turno.

`EpisodicMemory.after_turn` es un gancho del motor. Escribe el episodio (idempotente
por evento de intencion) y luego pide el embedding. Si el proveedor de embeddings
falla, el episodio queda escrito sin vector y `embed_missing` lo completa despues:
el turno nunca se cae por la memoria, y nada se pierde.

Cada vector se guarda con el modelo que lo produjo y con su `usage` (tokens, costo),
para que `ops budget` lo sume aunque no pase por el cortacircuito de llamadas.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Callable, Mapping, Sequence

from core.events.store import utcnow
from core.interfaces.model_adapter import ModelAdapter, ModelError, ModelRole
from core.memory.episodes import Episode, EpisodeStore, TurnRecord, episode_from_turn

log = logging.getLogger(__name__)


class EpisodicMemory:
    def __init__(
        self,
        *,
        instance_id: str,
        episodes: EpisodeStore,
        adapter: ModelAdapter | None,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.instance_id = instance_id
        self._episodes = episodes
        self._adapter = adapter
        self._clock = clock

    @property
    def embedding_model(self) -> str | None:
        return self._adapter.identity(ModelRole.EMBEDDINGS).model if self._adapter is not None else None

    # ------------------------------------------------------------------ gancho del motor

    async def after_turn(self, record: TurnRecord) -> None:
        """Escribe el episodio del turno y lo embebe. No levanta por el proveedor: la memoria no rompe el turno."""
        if record.intent.payload.get("policy") == "guard":
            return  # el motor estaba cortado: no hubo turno que recordar
        episode, created = await self._episodes.write(episode_from_turn(record))
        if created:
            log.info("episodio %s escrito (%s)", episode.id, episode.kind)
        try:
            await self.embed([episode])
        except ModelError as exc:
            log.warning("embedding del episodio %s pospuesto: %s", episode.id, exc)

    # ------------------------------------------------------------------ embeddings

    async def embed(self, episodes: Sequence[Episode]) -> int:
        """Embebe los que aun no tienen vector del modelo vigente. Devuelve cuantos vectores escribio."""
        model = self.embedding_model
        if model is None or not episodes:
            return 0
        done = await self._episodes.embedded_ids([e.id for e in episodes], model=model)
        pending = [e for e in episodes if e.id not in done]
        if not pending:
            return 0
        res = await self._adapter.embed([e.text for e in pending])  # type: ignore[union-attr]
        per_item = split_usage(res.usage, len(pending))
        written = 0
        for ep, vec in zip(pending, res.vectors):
            if await self._episodes.set_embedding(ep.id, model=res.identity.model, dim=res.dim, vector=vec, usage=per_item):
                written += 1
        return written

    async def embed_missing(self, *, limit: int = 100) -> int:
        """Backfill: episodios sin vector del modelo vigente (tras un fallo del proveedor o un cambio de modelo)."""
        model = self.embedding_model
        if model is None:
            return 0
        missing = await self._episodes.missing_embeddings(self.instance_id, model=model, limit=limit)
        return await self.embed(missing)


def split_usage(usage: Mapping[str, int] | None, n: int) -> dict[str, int]:
    """Reparte el uso de una llamada por lotes entre sus items (aproximado; sirve para sumar costos)."""
    if not usage or n <= 0:
        return {}
    return {k: int(round(int(v) / n)) for k, v in usage.items() if isinstance(v, (int, float))}
