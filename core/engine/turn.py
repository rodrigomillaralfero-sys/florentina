"""Politica de turno: lo que el motor llama para responder o para abrir contacto.

Dos implementaciones: `EchoTurnPolicy` (pruebas de canal, sin modelo) y
`core.engine.answer_turn.AnswerTurnPolicy` (contra el adaptador de modelo, con
lo recuperado de la memoria y del conocimiento). El motor no cambia: cambia la
politica que se le inyecta.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Mapping, Protocol, Sequence

from core.events.store import Event
from core.interfaces.channel import InboundEvent
from core.interfaces.subject_key import SubjectKey


@dataclass(frozen=True)
class TurnContext:
    subject: SubjectKey
    trigger: Literal["message", "pulse"]
    now: datetime
    inbound: InboundEvent | None
    recent: Sequence[Event]
    paused: bool = False
    pending_pulse_at: datetime | None = None  # el proximo contacto ya programado, si hay
    turn_messages: Sequence[Event] = ()  # los `message.received` que este turno responde (F0.3: agrupados)
    trigger_event_id: str | None = None  # el evento que disparo el turno (empareja la traza con su registro)

    @property
    def subject_text(self) -> str:
        """Lo que la persona escribio en este turno (varios mensajes seguidos, uno por linea)."""
        texts = [(e.payload.get("text") or "").strip() for e in self.turn_messages]
        texts = [t for t in texts if t]
        if texts:
            return "\n".join(texts)
        return self.inbound.text if self.inbound is not None else ""


@dataclass(frozen=True)
class TurnOutput:
    """Salida estructurada del turno. Es el registro de intencion y se escribe antes de enviar."""

    text: str | None
    next_pulse_at: datetime | None = None
    intent: Mapping[str, Any] = field(default_factory=dict)


class TurnPolicy(Protocol):
    async def respond(self, ctx: TurnContext) -> TurnOutput: ...

    async def initiate(self, ctx: TurnContext) -> TurnOutput: ...


class EchoTurnPolicy:
    """Devuelve lo que recibe. Sirve para probar el ciclo de punta a punta sin modelo."""

    def __init__(self, *, prefix: str = "", pulse_text: str = "(pulso)") -> None:
        self._prefix = prefix
        self._pulse_text = pulse_text

    async def respond(self, ctx: TurnContext) -> TurnOutput:
        text = ctx.subject_text.replace("\n", " | ")
        return TurnOutput(text=f"{self._prefix}{text}", intent={"policy": "echo", "trigger": ctx.trigger})

    async def initiate(self, ctx: TurnContext) -> TurnOutput:
        return TurnOutput(text=self._pulse_text, intent={"policy": "echo", "trigger": ctx.trigger})
