"""Costura 1 — Canal.

La spec la define como `recibir(evento)` / `enviar(mensaje)`:

* `enviar` es `Channel.send(message)`.
* `recibir` vive en el motor (`Engine.receive(event)`); el canal lo invoca a
  traves del handler que recibe en `Channel.run(handler)`.

El canal no sabe que hay un modelo detras. Solo mueve
texto y entrega identificadores estables para que el motor sea idempotente.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable, Mapping, Protocol


@dataclass(frozen=True)
class InboundEvent:
    """Algo que entro por el canal.

    `external_id` debe ser unico por (instancia, canal): es la clave de
    idempotencia del evento `message.received`. Si el canal reentrega el
    mismo mensaje, el motor lo reconoce y no lo procesa dos veces.
    """

    instance_id: str
    subject_ref: str
    external_id: str
    text: str
    received_at: datetime
    kind: str = "message"
    metadata: Mapping[str, Any] = field(default_factory=dict)
    # Escala (anexo de escala, 16-10): quien hablo y donde. En un chat privado los tres coinciden; en
    # un grupo, `conversation_ref` es el grupo y `sender_ref` la persona. Hoy el sujeto es la
    # conversacion (`subject_ref`); se guardan los dos para poder separar memoria individual y de
    # conversacion sin migrar datos cuando una instancia lo necesite.
    sender_ref: str | None = None
    conversation_ref: str | None = None


@dataclass(frozen=True)
class OutboundMessage:
    """Algo que el motor quiere sacar por el canal."""

    instance_id: str
    subject_ref: str
    text: str
    reply_to_external_id: str | None = None
    idempotency_key: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


InboundHandler = Callable[[InboundEvent], Awaitable[None]]


class Channel(Protocol):
    """Contrato minimo de un canal."""

    @property
    def name(self) -> str: ...

    async def send(self, message: OutboundMessage) -> str:
        """Envia y devuelve el identificador del mensaje en el canal."""
        ...

    async def run(self, handler: InboundHandler) -> None:
        """Bucle bloqueante: por cada evento entrante llama a `handler`."""
        ...

    async def stop(self) -> None: ...


class TypingChannel(Protocol):
    """Capacidad opcional: indicador de "escribiendo..." mientras el motor decide. Un canal que no
    la tiene simplemente no muestra nada; el motor la busca con `getattr`."""

    async def typing(self, subject_ref: str) -> None: ...
