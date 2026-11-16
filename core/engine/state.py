"""Estado del sujeto reconstruido desde los eventos de control.

No hay una tabla de estado que pueda desincronizarse: pausa, reanudacion,
salida y reapertura son eventos append-only, y el estado vigente es el
pliegue de esa secuencia. Reiniciar el backend no pierde nada porque no
habia nada que perder.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

from core.events import kinds
from core.events.store import Event


@dataclass(frozen=True)
class SubjectState:
    paused: bool = False
    paused_until: datetime | None = None
    terminated: bool = False
    last_control_event_id: str | None = None

    @property
    def can_contact(self) -> bool:
        return not self.paused and not self.terminated


def fold_state(control_events: Sequence[Event], now: datetime) -> SubjectState:
    paused = False
    paused_until: datetime | None = None
    terminated = False
    last_id: str | None = None
    for ev in control_events:
        last_id = ev.id
        if ev.kind == kinds.CONTROL_PAUSE:
            paused = True
            until = ev.payload.get("until")
            paused_until = datetime.fromisoformat(until) if isinstance(until, str) else None
        elif ev.kind == kinds.CONTROL_RESUME:
            paused = False
            paused_until = None
        elif ev.kind == kinds.CONTROL_EXIT:
            terminated = True
        elif ev.kind == kinds.CONTROL_REOPEN:
            # Reapertura por el operador (nunca por el canal): borra la salida y cualquier pausa.
            terminated = False
            paused = False
            paused_until = None
    if paused and paused_until is not None and now >= paused_until:
        paused = False
        paused_until = None
    return SubjectState(paused=paused, paused_until=paused_until, terminated=terminated, last_control_event_id=last_id)
