"""Scheduler idempotente.

Un job es una fila con clave de idempotencia. Encolar la misma clave dos
veces produce un solo job. Un proceso que muere a mitad de un job deja un
lease que vence; otro proceso (o el mismo tras reiniciar) lo reclama y lo
reintenta. La garantia del scheduler es *al menos una vez*; la de
*efectivamente una vez* la cierra el handler escribiendo sus efectos con
claves derivadas de `job.idempotency_key` (eventos, resultados, sellos).

Pulsos, vencimientos, trabajo nocturno y cuestionarios son jobs. Pausa y
salida cancelan los pendientes del sujeto.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any, Awaitable, Callable, Mapping, Protocol, Sequence

log = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class NewJob:
    instance_id: str
    kind: str
    run_at: datetime
    idempotency_key: str
    subject_id: str | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Job:
    id: str
    instance_id: str
    subject_id: str | None
    kind: str
    run_at: datetime
    idempotency_key: str
    payload: Mapping[str, Any]
    status: JobStatus
    attempts: int
    created_at: datetime
    lease_until: datetime | None = None
    finished_at: datetime | None = None
    result: Mapping[str, Any] | None = None
    error: str | None = None


class JobStore(Protocol):
    async def enqueue(self, new: NewJob) -> tuple[Job, bool]:
        """Devuelve (job, created). created=False si la clave ya existia."""
        ...

    async def claim_due(self, now: datetime, lease: timedelta, limit: int = 10, *, instance_id: str | None = None) -> Sequence[Job]:
        """Reclama atomicamente jobs pendientes vencidos o con lease expirado. Con `instance_id`, solo
        los de esa instancia: cada instancia es su propio proceso sobre la misma base (F0.4)."""
        ...

    async def complete(self, job_id: str, result: Mapping[str, Any] | None = None) -> None: ...

    async def fail(self, job_id: str, error: str, retry_at: datetime | None) -> None: ...

    async def cancel(
        self,
        instance_id: str,
        *,
        subject_id: str | None = None,
        kinds: Sequence[str] | None = None,
        exclude_keys: Sequence[str] = (),
    ) -> int:
        """Cancela pendientes (salvo los de `exclude_keys`). Devuelve cuantos cancelo."""
        ...

    async def get(self, job_id: str) -> Job | None: ...

    async def pending(
        self, instance_id: str, *, subject_id: str | None = None, kinds: Sequence[str] | None = None, include_running: bool = False
    ) -> Sequence[Job]:
        """Jobs pendientes; con `include_running` tambien los reclamados que estan corriendo."""
        ...


JobHandler = Callable[[Job], Awaitable[Mapping[str, Any] | None]]
FailureHook = Callable[[Job, str], Awaitable[None]]  # (job, error) cuando un job fallo definitivamente
ErrorHook = Callable[[BaseException], Awaitable[None]]  # cuando un tick completo fallo (p. ej. base caida)


class Scheduler:
    def __init__(
        self,
        store: JobStore,
        *,
        lease: timedelta = timedelta(minutes=5),
        max_attempts: int = 5,
        clock: Callable[[], datetime] = utcnow,
        on_failed: FailureHook | None = None,
        on_tick_error: ErrorHook | None = None,
        instance_id: str | None = None,
    ) -> None:
        self._store = store
        self._instance_id = instance_id  # None = reclama de todas (solo pruebas y herramientas)
        self._lease = lease
        self._max_attempts = max_attempts
        self._clock = clock
        self._handlers: dict[str, JobHandler] = {}
        self._stopping = asyncio.Event()
        self._on_failed = on_failed
        self._on_tick_error = on_tick_error

    def set_hooks(self, *, on_failed: FailureHook | None = None, on_tick_error: ErrorHook | None = None) -> None:
        """Para cablear la salud despues de construir el scheduler."""
        if on_failed is not None:
            self._on_failed = on_failed
        if on_tick_error is not None:
            self._on_tick_error = on_tick_error

    def register(self, kind: str, handler: JobHandler) -> None:
        self._handlers[kind] = handler

    async def tick(self, now: datetime | None = None, limit: int = 10) -> int:
        """Una pasada: reclama lo vencido y lo ejecuta. Devuelve cuantos jobs corrio."""
        now = now or self._clock()
        jobs = await self._store.claim_due(now, self._lease, limit=limit, instance_id=self._instance_id)
        ran = 0
        for job in jobs:
            handler = self._handlers.get(job.kind)
            if handler is None:
                error = f"sin handler para kind={job.kind}"
                await self._store.fail(job.id, error, retry_at=None)
                await self._notify_failed(job, error)
                continue
            try:
                result = await handler(job)
            except Exception as exc:  # noqa: BLE001 - el scheduler no decide que es recuperable
                log.exception("job %s (%s) fallo en intento %s", job.id, job.kind, job.attempts)
                if job.attempts >= self._max_attempts:
                    await self._store.fail(job.id, repr(exc), retry_at=None)
                    await self._notify_failed(job, repr(exc))
                else:
                    backoff = timedelta(seconds=min(300, 10 * (2 ** (job.attempts - 1))))
                    await self._store.fail(job.id, repr(exc), retry_at=now + backoff)
                continue
            await self._store.complete(job.id, result)
            ran += 1
        return ran

    async def run_forever(self, poll_seconds: float = 5.0) -> None:
        self._stopping.clear()
        while not self._stopping.is_set():
            try:
                await self.tick()
            except Exception as exc:  # noqa: BLE001
                log.exception("tick del scheduler fallo; sigo")
                if self._on_tick_error is not None:
                    try:
                        await self._on_tick_error(exc)
                    except Exception:  # noqa: BLE001
                        log.exception("el gancho on_tick_error tambien fallo")
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=poll_seconds)
            except asyncio.TimeoutError:
                pass

    def stop(self) -> None:
        self._stopping.set()

    async def _notify_failed(self, job: Job, error: str) -> None:
        if self._on_failed is None:
            return
        try:
            await self._on_failed(job, error)
        except Exception:  # noqa: BLE001
            log.exception("el gancho on_failed fallo")
