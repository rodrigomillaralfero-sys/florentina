"""Latido a un servicio externo.

Cada N minutos el motor hace un GET a una URL (healthchecks.io o
equivalente). Si el latido se corta, es el servicio externo el que avisa:
cuando el proceso murio, nada de lo que corre dentro del proceso puede
avisar. Por eso esta alerta vive afuera.

Es un job del scheduler que se reencola a si mismo. La clave de idempotencia
es el bucket de tiempo (inicio del intervalo), asi que reiniciar el proceso
no duplica latidos ni los pierde. Cada latido queda en el registro de salud
para que el doctor sepa cuando fue el ultimo.
"""

from __future__ import annotations

import asyncio
import logging
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Protocol

from core.events.store import utcnow
from core.health.log import HEARTBEAT, HealthEntry, HealthLog
from core.scheduler.jobs import Job, JobStore, NewJob, Scheduler

log = logging.getLogger(__name__)

HEARTBEAT_JOB = "health.heartbeat"


class Pinger(Protocol):
    async def ping(self, url: str) -> tuple[bool, str]:
        """(ok, detalle). No levanta: un latido fallido es un dato, no un error."""
        ...


class UrllibPinger:
    """GET con la libreria estandar, en un hilo para no bloquear el loop."""

    def __init__(self, *, timeout_seconds: float = 10.0) -> None:
        self._timeout = timeout_seconds

    async def ping(self, url: str) -> tuple[bool, str]:
        def _get() -> tuple[bool, str]:
            req = urllib.request.Request(url, headers={"User-Agent": "motor-heartbeat/1"})
            try:
                with urllib.request.urlopen(req, timeout=self._timeout) as resp:  # noqa: S310 - URL del operador
                    return 200 <= resp.status < 300, f"http {resp.status}"
            except urllib.error.HTTPError as exc:
                return False, f"http {exc.code}"
            except Exception as exc:  # noqa: BLE001
                return False, f"{type(exc).__name__}: {exc}"

        return await asyncio.to_thread(_get)


def bucket_start(at: datetime, every: timedelta) -> datetime:
    seconds = int(every.total_seconds()) or 60
    epoch = int(at.astimezone(timezone.utc).timestamp())
    return datetime.fromtimestamp(epoch - (epoch % seconds), tz=timezone.utc)


class Heartbeat:
    def __init__(
        self,
        *,
        instance_id: str,
        jobs: JobStore,
        health_log: HealthLog,
        pinger: Pinger,
        url: str | None,
        every: timedelta = timedelta(minutes=5),
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.instance_id = instance_id
        self._jobs = jobs
        self._log = health_log
        self._pinger = pinger
        self.url = url
        self.every = every
        self._clock = clock

    @property
    def enabled(self) -> bool:
        return bool(self.url)

    def attach(self, scheduler: Scheduler) -> None:
        scheduler.register(HEARTBEAT_JOB, self.on_job)

    async def start(self) -> bool:
        """Encola el primer latido (ahora). Idempotente por bucket."""
        if not self.enabled:
            log.warning("latido desactivado: sin URL configurada")
            return False
        return await self._enqueue(self._clock())

    async def on_job(self, job: Job) -> Mapping[str, Any]:
        assert self.url
        ok, detail = await self._pinger.ping(self.url)
        now = self._clock()
        await self._log.record(HealthEntry(instance_id=self.instance_id, kind=HEARTBEAT, ok=ok, at=now, detail={"detail": detail}))
        if not ok:
            log.warning("latido fallido: %s", detail)
        # El siguiente bucket, contado desde el que acaba de correr: asi un job atrasado no corre
        # el calendario. Tras una caida larga no se recuperan los buckets perdidos (seria una
        # rafaga de GETs sin valor): se salta al bucket actual.
        following = bucket_start(job.run_at, self.every) + self.every
        await self._enqueue(max(following, bucket_start(now, self.every)))
        return {"ok": ok, "detail": detail}

    async def _enqueue(self, at: datetime) -> bool:
        start = bucket_start(at, self.every)
        _, created = await self._jobs.enqueue(
            NewJob(
                instance_id=self.instance_id,
                kind=HEARTBEAT_JOB,
                run_at=start,
                idempotency_key=f"hb:{self.instance_id}:{start.isoformat()}",
            )
        )
        return created

    async def last(self) -> HealthEntry | None:
        return await self._log.last(self.instance_id, HEARTBEAT)
