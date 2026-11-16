"""Sesiones: secuencia de turnos sin una pausa mayor a `gap`. Al cerrar corre el consolidador, si la instancia tiene uno.

El cierre es un job: cada turno lo fija a `gap` minutos de su intencion y cancela el
anterior (un solo cierre pendiente por sujeto, como el pulso). Si el job se dispara
y hubo un turno despues del que lo fijo, la sesion siguio y no hace nada. Si no, toma
los episodios desde el cierre anterior y llama al consolidador (p. ej. el acta de la sesion); el resultado queda
como evento `memory.session_closed` (su clave deriva del job: reintentar no repite).

Sobrevive reinicios y nunca llama al modelo dos veces por la misma sesion.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Mapping, Protocol, Sequence

from core.events import kinds
from core.events.store import EventStore, NewEvent, utcnow
from core.memory.episodes import Episode, EpisodeStore, TurnRecord
from core.scheduler.jobs import Job, JobStore, NewJob, Scheduler

log = logging.getLogger(__name__)

SESSION_CLOSE_JOB = "memory.session_close"


class ConsolidationResult(Protocol):
    @property
    def failed(self) -> str | None: ...

    def as_payload(self) -> Mapping[str, Any]: ...


class Consolidator(Protocol):
    """Lo que corre al cerrar una sesion con sus episodios. Lo pone la instancia; el motor no sabe que escribe."""

    async def run(self, subject_id: str, episodes: Sequence[Episode], *, now: datetime) -> ConsolidationResult: ...


class SessionTracker:
    def __init__(
        self,
        *,
        instance_id: str,
        events: EventStore,
        jobs: JobStore,
        episodes: EpisodeStore,
        consolidator: Consolidator | None,
        gap: timedelta = timedelta(minutes=45),
        clock: Callable[[], datetime] = utcnow,
        on_closed: Sequence[Callable[[Any], Awaitable[Any]]] = (),
    ) -> None:
        self.instance_id = instance_id
        self._on_closed = list(on_closed)  # quien quiere enterarse de un cierre de sesion
        self._events = events
        self._jobs = jobs
        self._episodes = episodes
        self._consolidator = consolidator
        self.gap = gap
        self._clock = clock

    def attach(self, scheduler: Scheduler) -> None:
        scheduler.register(SESSION_CLOSE_JOB, self.on_session_close)

    # ------------------------------------------------------------------ gancho del motor

    async def after_turn(self, record: TurnRecord) -> None:
        """Cada turno mueve el cierre de sesion a `gap` desde el ULTIMO turno. Idempotente por intencion.

        Solo lo mueve hacia adelante: si ya hay un cierre pendiente mas tarde que este, es que hubo un
        turno mas nuevo y este es una reanudacion o una reentrega vieja (F0.8 r2: antes cancelaba el
        cierre nuevo y, como la clave vieja ya estaba cancelada, la sesion quedaba sin cierre)."""
        at = record.intent.occurred_at + self.gap
        key = kinds.key_session_close_job(record.subject_id, record.intent.id)
        pending = await self._jobs.pending(self.instance_id, subject_id=record.subject_id, kinds=[SESSION_CLOSE_JOB])
        if any(j.run_at >= at and j.idempotency_key != key for j in pending):
            return
        await self._jobs.cancel(self.instance_id, subject_id=record.subject_id, kinds=[SESSION_CLOSE_JOB], exclude_keys=[key])
        await self._jobs.enqueue(
            NewJob(
                instance_id=self.instance_id,
                subject_id=record.subject_id,
                kind=SESSION_CLOSE_JOB,
                run_at=at,
                idempotency_key=key,
                payload={"intent_event_id": record.intent.id},
            )
        )

    # ------------------------------------------------------------------ job

    async def on_session_close(self, job: Job) -> Mapping[str, Any]:
        assert job.subject_id is not None
        anchor_id = str(job.payload.get("intent_event_id") or "")
        key = kinds.key_session_closed(job.idempotency_key)
        existing = await self._events.by_key(self.instance_id, key)
        if existing is not None:
            # Reintento tras un corte entre cerrar y lo que deriva del cierre: los ganchos son idempotentes por
            # evento, asi que se vuelven a llamar con el cierre ya escrito .
            await self._run_closed_hooks(existing)
            return {"skipped": "already_closed"}
        anchor = await self._events.get(anchor_id) if anchor_id else None
        if anchor is not None and await self._turn_after(job.subject_id, anchor.recorded_at):
            return {"skipped": "superseded"}  # la sesion siguio; otro job la cerrara
        return await self.close(job.subject_id, key=key, now=self._clock(), reason="gap")

    async def close(self, subject_id: str, *, key: str, now: datetime, reason: str) -> Mapping[str, Any]:
        """Cierra la sesion vigente de la persona: consolida lo que hay desde el cierre anterior y lo registra."""
        last = await self._last_close(subject_id)
        since = _until(last) if last is not None else None
        episodes = await self._episodes.list(self.instance_id, subject_id, since=since)
        if not episodes:
            return {"skipped": "empty"}
        until = max(e.occurred_at for e in episodes)
        payload: dict[str, Any] = {
            "reason": reason,
            "episodes": len(episodes),
            "since": since.isoformat() if since else None,
            "until": until.isoformat(),  # el proximo cierre parte estrictamente despues del ultimo episodio consolidado
        }
        if self._consolidator is not None:
            result = await self._consolidator.run(subject_id, episodes, now=now)
            payload["consolidation"] = result.as_payload()
            if result.failed:
                log.warning("consolidacion fallo (%s); la sesion se cierra igual y la proxima la cubre", result.failed)
        else:
            payload["consolidation"] = None
        res = await self._events.append(
            NewEvent(
                instance_id=self.instance_id,
                subject_id=subject_id,
                kind=kinds.MEMORY_SESSION_CLOSED,
                idempotency_key=key,
                occurred_at=now,
                payload=payload,
            )
        )
        await self._run_closed_hooks(res.event)
        return {"closed": res.created, **{k: v for k, v in payload.items() if k != "consolidation"}}

    async def _run_closed_hooks(self, closed) -> None:
        """Los ganchos corren despues de que el cierre quedo escrito. Si uno falla, se levanta al final: el job
        falla y el scheduler lo reintenta, encuentra el cierre ya escrito y vuelve a llamar a los ganchos."""
        failed: BaseException | None = None
        for hook in self._on_closed:
            try:
                await hook(closed)
            except Exception as exc:  # noqa: BLE001
                log.exception("un gancho on_closed fallo; la sesion quedo cerrada y el job se reintenta")
                failed = failed or exc
        if failed is not None:
            raise failed

    def add_on_closed(self, hook: Callable[[Any], Awaitable[Any]]) -> None:
        self._on_closed.append(hook)

    # ------------------------------------------------------------------ consultas

    async def _turn_after(self, subject_id: str, recorded_at: datetime) -> bool:
        intents = await self._events.list(
            self.instance_id, subject_id=subject_id, kinds=[kinds.TURN_INTENT], since=recorded_at - timedelta(minutes=10)
        )
        return any(i.recorded_at > recorded_at for i in intents)

    async def _last_close(self, subject_id: str):
        closes = await self._events.list(self.instance_id, subject_id=subject_id, kinds=[kinds.MEMORY_SESSION_CLOSED])
        return closes[-1] if closes else None

    async def pending_close_at(self, subject_id: str) -> datetime | None:
        pending = await self._jobs.pending(self.instance_id, subject_id=subject_id, kinds=[SESSION_CLOSE_JOB])
        return min((j.run_at for j in pending), default=None)


def _until(close_event) -> datetime:
    raw = close_event.payload.get("until")
    return datetime.fromisoformat(raw) if isinstance(raw, str) else close_event.occurred_at
