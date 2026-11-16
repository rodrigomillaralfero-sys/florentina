"""Recuperacion por turno: lo que el motor pone delante del modelo ademas del hilo.

Dos corpus separados, una sola consulta:

* **Episodios** (lo conversado con esta persona): busqueda vectorial reordenada por recencia,
  excluyendo lo que ya esta en el hilo reciente. Puntaje = w·similitud + (1-w)·recencia,
  con recencia = 0.5^(edad/vida_media). Solo los de la persona: la memoria de una no aparece en otra.
* **Conocimiento base** pertinente, solo si supera el umbral de la instancia.

Que algo sea parecido no lo hace cierto: aqui se recupera, no se afirma. Si el proveedor de
embeddings falla, el turno sigue con el hilo; la falta queda registrada en el `RecallBundle`.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Sequence

from core.engine.turn import TurnContext
from core.events import kinds
from core.interfaces.model_adapter import ModelAdapter, ModelError, ModelRole
from core.knowledge.base import KnowledgeBase
from core.knowledge.store import KnowledgeHit
from core.memory.episodes import Episode, EpisodeStore

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RecalledEpisode:
    episode: Episode
    similarity: float
    recency: float
    score: float


@dataclass(frozen=True)
class RecallBundle:
    enabled: bool = False
    query: str | None = None
    episodes: Sequence[RecalledEpisode] = ()
    knowledge: Sequence[KnowledgeHit] = ()
    knowledge_enabled: bool = False
    embedding_failed: bool = False

    def summary(self) -> dict:
        return {"episodes": len(self.episodes), "knowledge": len(self.knowledge), "embedding_failed": self.embedding_failed}


@dataclass(frozen=True)
class RecallConfig:
    episodes: int = 6
    candidates: int = 24  # cuantos trae la busqueda vectorial antes de reordenar por recencia
    similarity_weight: float = 0.7
    recency_half_life: timedelta = timedelta(days=7)
    query_subject_messages: int = 3  # en un pulso, cuantos mensajes recientes de la persona forman la consulta


def recency_score(age: timedelta, half_life: timedelta) -> float:
    days = max(0.0, age.total_seconds()) / 86400.0
    hl = max(1e-6, half_life.total_seconds() / 86400.0)
    return math.pow(0.5, days / hl)


def build_query(ctx: TurnContext, *, last_subject_messages: int = 3) -> str | None:
    """Que se busca: lo que la persona acaba de escribir; en un pulso, lo ultimo que escribio."""
    if ctx.turn_messages:
        texts = [(e.payload.get("text") or "").strip() for e in ctx.turn_messages]
        q = "\n".join(t for t in texts if t)
        if q:
            return q
    if ctx.inbound is not None and ctx.inbound.text.strip():
        return ctx.inbound.text.strip()
    recent_subject = [(e.payload.get("text") or "").strip() for e in ctx.recent if e.kind == kinds.MESSAGE_RECEIVED]
    recent_subject = [t for t in recent_subject if t][-last_subject_messages:]
    return "\n".join(recent_subject) or None


class Recall:
    def __init__(
        self,
        *,
        instance_id: str,
        episodes: EpisodeStore,
        adapter: ModelAdapter | None,
        knowledge: KnowledgeBase | None = None,
        config: RecallConfig | None = None,
    ) -> None:
        self.instance_id = instance_id
        self._episodes = episodes
        self._adapter = adapter
        self._knowledge = knowledge
        self.config = config or RecallConfig()

    @property
    def embedding_model(self) -> str | None:
        return self._adapter.identity(ModelRole.EMBEDDINGS).model if self._adapter is not None else None

    async def for_turn(self, ctx: TurnContext) -> RecallBundle:
        subject_id = ctx.subject.subject_id
        query = build_query(ctx, last_subject_messages=self.config.query_subject_messages)
        in_thread = {e.id for e in ctx.recent if e.kind == kinds.TURN_INTENT}
        vector: Sequence[float] | None = None
        failed = False
        model = self.embedding_model
        if query and model:
            try:
                res = await self._adapter.embed([query])  # type: ignore[union-attr]
                vector = res.vectors[0] if res.vectors else None
                model = res.identity.model
            except ModelError as exc:
                failed = True
                log.warning("recuperacion sin embedding (proveedor): %s", exc)

        episodes = await self._recall_episodes(subject_id, model=model, vector=vector, exclude=in_thread, now=ctx.now)
        knowledge: Sequence[KnowledgeHit] = ()
        if self._knowledge is not None and vector is not None and model:
            knowledge = await self._knowledge.search(model=model, vector=vector)
        return RecallBundle(
            enabled=True,
            query=query,
            episodes=episodes,
            knowledge=knowledge,
            knowledge_enabled=self._knowledge is not None,
            embedding_failed=failed,
        )

    async def recall_text(self, subject_id: str, query: str, *, now: datetime, exclude: set[str] | None = None) -> Sequence[RecalledEpisode]:
        """Para `ops memory recall`: que traeria esta consulta."""
        model = self.embedding_model
        vector = None
        if model:
            res = await self._adapter.embed([query])  # type: ignore[union-attr]
            vector = res.vectors[0] if res.vectors else None
            model = res.identity.model
        return await self._recall_episodes(subject_id, model=model, vector=vector, exclude=exclude or set(), now=now)

    async def _recall_episodes(
        self, subject_id: str, *, model: str | None, vector: Sequence[float] | None, exclude: set[str], now: datetime
    ) -> Sequence[RecalledEpisode]:
        cfg = self.config
        if cfg.episodes <= 0:
            return ()
        if vector is None or not model:
            # Sin consulta (o sin proveedor): lo mas reciente que no esta en el hilo.
            recent = await self._episodes.list(self.instance_id, subject_id, limit=cfg.candidates)
            out = [
                RecalledEpisode(e, similarity=0.0, recency=recency_score(now - e.occurred_at, cfg.recency_half_life), score=0.0)
                for e in recent
                if e.event_id not in exclude
            ]
            out.sort(key=lambda r: r.episode.occurred_at, reverse=True)
            return out[: cfg.episodes]
        hits = await self._episodes.search(
            self.instance_id, subject_id, model=model, vector=vector, limit=cfg.candidates, exclude_event_ids=sorted(exclude)
        )
        scored = []
        for h in hits:
            rec = recency_score(now - h.episode.occurred_at, cfg.recency_half_life)
            score = cfg.similarity_weight * h.similarity + (1.0 - cfg.similarity_weight) * rec
            scored.append(RecalledEpisode(h.episode, similarity=h.similarity, recency=rec, score=score))
        scored.sort(key=lambda r: r.score, reverse=True)
        chosen = scored[: cfg.episodes]
        chosen.sort(key=lambda r: r.episode.occurred_at)  # al modelo se le muestran en orden cronologico
        return chosen
