"""Alertas al operador.

Una alerta es corta, sin contenido del sujeto (dice que fallo, nunca que
decia el mensaje) y sale por un sink que no es el canal del sujeto. El
`Alerter` hace tres cosas:

1. **Dedupe.** La misma `dedupe_key` no vuelve a salir dentro de la ventana
   (default una hora). La memoria del proceso basta mientras corre; al
   reiniciar se consulta la episodica, asi que un reinicio tampoco repite.
2. **Emite primero, registra despues.** Si la base esta caida, la alerta
   igual sale: el registro en `events` es lo mejor que se pueda.
3. **Nunca levanta.** Un sink roto se loguea; el turno del sujeto no depende
   de que la alerta llegue.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Literal, Protocol

from core.events import kinds
from core.events.store import EventStore, NewEvent, utcnow

log = logging.getLogger(__name__)

Severity = Literal["info", "warning", "critical"]
SEVERITY_ORDER: dict[str, int] = {"info": 0, "warning": 1, "critical": 2}


@dataclass(frozen=True)
class Alert:
    code: str  # p. ej. "breaker.tripped", "model.failures", "job.failed"
    severity: Severity
    title: str
    detail: str = ""
    dedupe_key: str | None = None  # None = nunca se dedupea (p. ej. el OK diario)

    def render(self, instance_id: str) -> str:
        head = f"[{self.severity}] {instance_id}: {self.title}"
        return f"{head}\n{self.detail}" if self.detail else head


class AlertSink(Protocol):
    @property
    def name(self) -> str: ...

    async def emit(self, text: str) -> None: ...


class LogAlertSink:
    """Sink por defecto: solo loguea. Sirve cuando la instancia no configuro un canal de alertas."""

    @property
    def name(self) -> str:
        return "log"

    async def emit(self, text: str) -> None:
        log.warning("ALERTA (sin sink configurado): %s", text.replace("\n", " | "))


class Alerter:
    def __init__(
        self,
        *,
        instance_id: str,
        sink: AlertSink | None = None,
        events: EventStore | None = None,
        dedupe_window: timedelta = timedelta(hours=1),
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.instance_id = instance_id
        self._sink = sink or LogAlertSink()
        self._events = events
        self._window = dedupe_window
        self._clock = clock
        self._last_emitted: dict[str, datetime] = {}
        self.emitted: list[Alert] = []  # las que salieron en este proceso (pruebas y status)

    @property
    def sink_name(self) -> str:
        return self._sink.name

    async def emit(self, alert: Alert) -> bool:
        """Devuelve True si la alerta salio por el sink; False si se dedupeo o el sink fallo."""
        now = self._clock()
        key = alert.dedupe_key
        if key is not None and await self._recently_emitted(key, now):
            log.info("alerta %s deduplicada (ventana %s)", alert.code, self._window)
            return False

        delivered = True
        try:
            await self._sink.emit(alert.render(self.instance_id))
        except Exception as exc:  # noqa: BLE001 - la alerta no puede tumbar el turno
            delivered = False
            log.error("el sink de alertas (%s) fallo: %r | alerta: %s", self._sink.name, exc, alert.title)

        if key is not None:
            self._last_emitted[key] = now
        self.emitted.append(alert)
        await self._record(alert, now, delivered)
        return delivered

    async def _recently_emitted(self, key: str, now: datetime) -> bool:
        last = self._last_emitted.get(key)
        if last is not None and now - last < self._window:
            return True
        if self._events is None:
            return False
        try:
            recent = await self._events.list(self.instance_id, kinds=[kinds.HEALTH_ALERT], since=now - self._window)
        except Exception as exc:  # noqa: BLE001 - base caida: no hay con que dedupear, mejor avisar de mas
            log.warning("no se pudo consultar alertas previas (%r); se emite igual", exc)
            return False
        for ev in recent:
            if ev.payload.get("dedupe_key") == key:
                self._last_emitted[key] = ev.occurred_at
                return True
        return False

    async def _record(self, alert: Alert, now: datetime, delivered: bool) -> None:
        if self._events is None:
            return
        try:
            await self._events.append(
                NewEvent(
                    instance_id=self.instance_id,
                    kind=kinds.HEALTH_ALERT,
                    idempotency_key=kinds.key_alert(alert.dedupe_key or alert.code, now.isoformat()),
                    occurred_at=now,
                    payload={
                        "code": alert.code,
                        "severity": alert.severity,
                        "title": alert.title,
                        "detail": alert.detail,
                        "dedupe_key": alert.dedupe_key,
                        "delivered": delivered,
                        "sink": self._sink.name,
                    },
                )
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("alerta emitida pero no registrada en la episodica: %r", exc)


def worst(severities: list[str]) -> Severity:
    if not severities:
        return "info"
    return max(severities, key=lambda s: SEVERITY_ORDER.get(s, 0))  # type: ignore[return-value]
