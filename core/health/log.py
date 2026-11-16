"""Registro de salud: latidos, corridas del doctor y OK diario.

Va en una tabla aparte de `events` porque un latido cada cinco minutos son
~300 filas al dia y no son historia del sujeto. Igual que todo lo demas,
es append-only; en Postgres lo prohibe un trigger.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Protocol, Sequence

HEARTBEAT = "heartbeat"
DOCTOR = "doctor"
DAILY = "daily"


@dataclass(frozen=True)
class HealthEntry:
    instance_id: str
    kind: str  # heartbeat | doctor | daily
    ok: bool
    at: datetime
    detail: Mapping[str, Any] = field(default_factory=dict)


class HealthLog(Protocol):
    async def record(self, entry: HealthEntry) -> None: ...

    async def last(self, instance_id: str, kind: str) -> HealthEntry | None: ...

    async def list(
        self, instance_id: str, kind: str, *, since: datetime | None = None, limit: int | None = None
    ) -> Sequence[HealthEntry]:
        """Orden cronologico ascendente."""
        ...


class InMemoryHealthLog:
    def __init__(self) -> None:
        self._entries: list[HealthEntry] = []
        self._lock = asyncio.Lock()

    async def record(self, entry: HealthEntry) -> None:
        async with self._lock:
            self._entries.append(entry)

    async def last(self, instance_id: str, kind: str) -> HealthEntry | None:
        for e in reversed(self._entries):
            if e.instance_id == instance_id and e.kind == kind:
                return e
        return None

    async def list(
        self, instance_id: str, kind: str, *, since: datetime | None = None, limit: int | None = None
    ) -> Sequence[HealthEntry]:
        out = [
            e
            for e in self._entries
            if e.instance_id == instance_id and e.kind == kind and (since is None or e.at >= since)
        ]
        out.sort(key=lambda e: e.at)
        return out[-limit:] if limit is not None else out
