"""Doctor: autochequeo del motor, y el OK diario.

Corre al arrancar, a diario como job y cuando el operador lo pide (`ops
doctor`). Cada chequeo es una funcion asincrona que devuelve un `Check`; el
doctor los corre todos (un chequeo que levanta es un chequeo en rojo, no
un doctor caido), registra la corrida y, si hay rojo, alerta una sola vez
por combinacion de chequeos caidos (dedupe).

Los chequeos genericos viven en `core.health.checks`; los de proveedores
(canal, modelo) los inyecta la instancia.

El OK diario es la alarma por silencio: si a la hora fijada no llega el
"OK", algo esta mal aunque ninguna alerta haya salido.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, tzinfo
from typing import Any, Awaitable, Callable, Mapping, Sequence
from zoneinfo import ZoneInfo

from core.events.store import utcnow
from core.health.alerts import Alert, Alerter, Severity, worst
from core.health.breakers import Breakers
from core.health.log import DAILY, DOCTOR, HealthEntry, HealthLog
from core.scheduler.jobs import Job, JobStore, NewJob, Scheduler

log = logging.getLogger(__name__)

DAILY_JOB = "health.daily"


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str = ""
    severity: Severity = "critical"  # cuanto pesa si esta en rojo

    def render(self) -> str:
        mark = "OK " if self.ok else ("!! " if self.severity == "critical" else "?  ")
        return f"{mark}{self.name}: {self.detail}" if self.detail else f"{mark}{self.name}"


CheckFn = Callable[[], Awaitable[Check]]


@dataclass
class DoctorReport:
    at: datetime
    checks: list[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)

    @property
    def failed(self) -> list[Check]:
        return [c for c in self.checks if not c.ok]

    @property
    def severity(self) -> Severity:
        return worst([c.severity for c in self.failed])

    def render(self) -> str:
        head = "doctor: verde" if self.ok else f"doctor: ROJO ({len(self.failed)} de {len(self.checks)})"
        return "\n".join([head, *(c.render() for c in self.checks)])

    def as_payload(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail, "severity": c.severity} for c in self.checks],
        }


class Doctor:
    def __init__(
        self,
        *,
        instance_id: str,
        checks: Sequence[tuple[str, CheckFn]],
        health_log: HealthLog | None = None,
        alerter: Alerter | None = None,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.instance_id = instance_id
        self._checks = list(checks)
        self._log = health_log
        self._alerter = alerter
        self._clock = clock

    async def run(self, *, alert: bool = True) -> DoctorReport:
        report = DoctorReport(at=self._clock())
        for name, fn in self._checks:
            try:
                check = await fn()
            except Exception as exc:  # noqa: BLE001 - un chequeo roto es un chequeo en rojo
                check = Check(name=name, ok=False, detail=f"{type(exc).__name__}: {str(exc)[:160]}")
            report.checks.append(check)
        if self._log is not None:
            try:
                await self._log.record(HealthEntry(instance_id=self.instance_id, kind=DOCTOR, ok=report.ok, at=report.at, detail=report.as_payload()))
            except Exception as exc:  # noqa: BLE001
                log.warning("corrida del doctor no registrada: %r", exc)
        if alert and not report.ok and self._alerter is not None:
            failed = report.failed
            await self._alerter.emit(
                Alert(
                    code="doctor",
                    severity=report.severity,
                    title=f"doctor en rojo: {', '.join(c.name for c in failed)}",
                    detail="\n".join(c.render() for c in failed),
                    dedupe_key="doctor:" + ",".join(sorted(c.name for c in failed)),
                )
            )
        return report


def next_local_time(now: datetime, at: time, tz: tzinfo) -> datetime:
    """El proximo instante (UTC) en que son las `at` en la zona `tz`, estrictamente despues de `now`."""
    local_now = now.astimezone(tz)
    candidate = local_now.replace(hour=at.hour, minute=at.minute, second=0, microsecond=0)
    if candidate <= local_now:
        candidate = candidate + timedelta(days=1)
    return candidate.astimezone(ZoneInfo("UTC"))


class DailyReport:
    """Job diario a hora fija: corre el doctor, resume contadores y manda un OK (o un rojo)."""

    def __init__(
        self,
        *,
        instance_id: str,
        jobs: JobStore,
        doctor: Doctor,
        breakers: Breakers | None,
        alerter: Alerter,
        health_log: HealthLog | None,
        at: time,
        timezone: str,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.instance_id = instance_id
        self._jobs = jobs
        self._doctor = doctor
        self._breakers = breakers
        self._alerter = alerter
        self._log = health_log
        self.at = at
        self._tz = ZoneInfo(timezone)
        self._clock = clock

    def attach(self, scheduler: Scheduler) -> None:
        scheduler.register(DAILY_JOB, self.on_job)

    async def start(self) -> bool:
        return await self._enqueue(next_local_time(self._clock(), self.at, self._tz))

    async def on_job(self, job: Job) -> Mapping[str, Any]:
        report = await self._doctor.run(alert=False)
        lines = [report.render().splitlines()[0]]
        if self._breakers is not None:
            c = await self._breakers.counters()
            st = await self._breakers.state()
            lines.append(c.render())
            lines.append("cortacircuito: " + (f"ACTIVO ({st.code})" if st.tripped else "no"))
        lines.extend(c.render() for c in report.failed)
        now = self._clock()
        if self._log is not None:
            await self._log.record(HealthEntry(instance_id=self.instance_id, kind=DAILY, ok=report.ok, at=now, detail={"lines": lines}))
        await self._alerter.emit(
            Alert(
                code="daily",
                severity="info" if report.ok else report.severity,
                title="OK diario" if report.ok else "diario en rojo",
                detail="\n".join(lines),
                dedupe_key=None,
            )
        )
        await self._enqueue(next_local_time(now, self.at, self._tz))
        return {"ok": report.ok}

    async def _enqueue(self, run_at: datetime) -> bool:
        day = run_at.astimezone(self._tz).strftime("%Y-%m-%d")
        _, created = await self._jobs.enqueue(
            NewJob(instance_id=self.instance_id, kind=DAILY_JOB, run_at=run_at, idempotency_key=f"daily:{self.instance_id}:{day}")
        )
        return created
