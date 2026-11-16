"""Eventos append-only.

La episodica y las trazas no se reescriben. Una etiqueta erronea se corrige
con un evento nuevo vinculado al anterior (`corrects_event_id`). La interfaz
no ofrece update ni delete; en Postgres ademas lo prohibe un trigger.

Idempotencia: `append` recibe una `idempotency_key` unica por instancia.
Si la clave ya existe devuelve el evento existente con `created=False`.
Reiniciar el backend y reprocesar lo mismo no produce filas nuevas.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol, Sequence


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class NewEvent:
    instance_id: str
    kind: str
    idempotency_key: str
    subject_id: str | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)
    occurred_at: datetime | None = None
    corrects_event_id: str | None = None


@dataclass(frozen=True)
class Event:
    id: str
    instance_id: str
    subject_id: str | None
    kind: str
    payload: Mapping[str, Any]
    idempotency_key: str
    occurred_at: datetime
    recorded_at: datetime
    corrects_event_id: str | None = None


@dataclass(frozen=True)
class AppendResult:
    event: Event
    created: bool


class EventStore(Protocol):
    async def append(self, new: NewEvent) -> AppendResult: ...

    async def get(self, event_id: str) -> Event | None: ...

    async def by_key(self, instance_id: str, idempotency_key: str) -> Event | None: ...

    async def list(
        self,
        instance_id: str,
        *,
        subject_id: str | None = None,
        kinds: Sequence[str] | None = None,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> Sequence[Event]:
        """Orden cronologico ascendente por `occurred_at`."""
        ...
