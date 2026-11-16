"""Costura — Politica de interaccion.

La instancia define los **controles mecanicos**: ordenes que el backend ejecuta
antes de consultar al modelo (pausa, reanudacion, baja). El motor las reconoce
por esta interfaz y las ejecuta sin preguntarle a nadie. El texto con que se
confirman lo pone la instancia, no el modelo.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from typing import Protocol

from core.interfaces.channel import InboundEvent


class ControlKind(StrEnum):
    PAUSE = "pause"
    RESUME = "resume"
    EXIT = "exit"


@dataclass(frozen=True)
class MechanicalCommand:
    kind: ControlKind
    duration: timedelta | None = None
    raw: str = ""


class InteractionPolicy(Protocol):
    def mechanical_command(self, event: InboundEvent) -> MechanicalCommand | None:
        """Si el evento es una orden mecanica, la devuelve. Se evalua ANTES del modelo."""
        ...

    def acknowledgement(self, command: MechanicalCommand) -> str:
        """Texto de confirmacion que el backend envia (no el modelo). Vacio = no se confirma."""
        ...
