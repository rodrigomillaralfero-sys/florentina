"""Controles mecanicos por comando: pausa, reanudacion y baja, en texto exacto.

Se evaluan ANTES del modelo. La pausa se pide con un comando, no en lenguaje natural: lo
mecanico tiene que ser inequivoco. Un "dejame tranquila un rato" en medio de un mensaje lo
lee el modelo como lo que es, no el backend.

* `<pausa>`               -> pausa hasta que llegue `<reanudar>`
* `<pausa> 2d|12h|90m`    -> pausa con duracion; el backend reanuda solo
* `<reanudar>`            -> fin de la pausa
* `<baja>`                -> la persona deja de recibir contacto hasta que el operador la reabra

Los comandos y los textos de confirmacion los pone la instancia.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Mapping

from core.interfaces.channel import InboundEvent
from core.interfaces.interaction_policy import ControlKind, MechanicalCommand

_DURATION = re.compile(r"^(\d+)\s*([dhm])$", re.IGNORECASE)
_UNITS = {"d": "days", "h": "hours", "m": "minutes"}


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(text.casefold().split())


def parse_duration(token: str) -> timedelta | None:
    m = _DURATION.match(token.strip())
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2).lower()
    if n <= 0:
        return None
    return timedelta(**{_UNITS[unit]: n})


def human_duration(d: timedelta) -> str:
    secs = int(d.total_seconds())
    if secs % 86400 == 0:
        n = secs // 86400
        return f"{n} día" if n == 1 else f"{n} días"
    if secs % 3600 == 0:
        n = secs // 3600
        return f"{n} hora" if n == 1 else f"{n} horas"
    n = secs // 60
    return f"{n} minuto" if n == 1 else f"{n} minutos"


@dataclass(frozen=True)
class CommandPolicy:
    """`InteractionPolicy` por comandos. `acks` lleva los textos de confirmacion por tipo; `{duracion}` se reemplaza."""

    pause: str = "/pausa"
    resume: str = "/reanudar"
    exit: str | None = None  # None = sin baja por el canal
    acks: Mapping[str, str] = field(default_factory=dict)

    def mechanical_command(self, event: InboundEvent) -> MechanicalCommand | None:
        raw = event.text.strip()
        if not raw:
            return None
        parts = raw.split(maxsplit=1)
        head = parts[0].casefold().split("@", 1)[0]  # algunos canales agregan @<bot> a los comandos en grupos
        if self.exit is not None and (normalize(raw) == normalize(self.exit) or head == self.exit.casefold()):
            return MechanicalCommand(kind=ControlKind.EXIT, raw=raw)
        if head == self.resume.casefold():
            return MechanicalCommand(kind=ControlKind.RESUME, raw=raw)
        if head == self.pause.casefold():
            duration = parse_duration(parts[1]) if len(parts) > 1 else None
            if len(parts) > 1 and duration is None:
                return None  # "/pausa manana" no es un comando valido: lo lee el modelo como texto
            return MechanicalCommand(kind=ControlKind.PAUSE, duration=duration, raw=raw)
        return None

    def acknowledgement(self, command: MechanicalCommand) -> str:
        if command.kind == ControlKind.PAUSE and command.duration is not None:
            text = self.acks.get("pause_for") or self.acks.get("pause", "")
            return text.replace("{duracion}", human_duration(command.duration))
        return self.acks.get(command.kind.value, "")
