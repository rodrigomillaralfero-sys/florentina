"""Registro de accesos al contenido de las personas.

Toda lectura de contenido cifrado que SALE del motor queda registrada: cualquier lectura del operador
(que exige decir por que) y cualquier reporte que la instancia arme con contenido. El motor leyendo lo
suyo para operar (armar un prompt) no es un acceso: es el sistema trabajando.

Lo que la app no ve: lecturas directas desde el panel de la base. Esas se declaran aparte.
La tabla `access_log` es append-only desde F0.1 (trigger); el registro no lleva contenido, solo que
se leyo, por que y quien.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol, Sequence

ACTOR_APP = "app"
ACTOR_OPERATOR = "operador"


@dataclass(frozen=True)
class AccessEntry:
    instance_id: str
    what: str  # que se leyo: "episodes:<persona>", ...
    why: str  # lo que el operador o el reporte declare
    actor: str  # "app" | "operador"
    created_at: datetime | None = None


class AccessLog(Protocol):
    async def record(self, entry: AccessEntry) -> None: ...

    async def list(self, instance_id: str, *, limit: int = 100) -> Sequence[AccessEntry]: ...


class InMemoryAccessLog:
    def __init__(self) -> None:
        self.entries: list[AccessEntry] = []

    async def record(self, entry: AccessEntry) -> None:
        self.entries.append(
            AccessEntry(entry.instance_id, entry.what, entry.why, entry.actor, entry.created_at or datetime.now(timezone.utc))
        )

    async def list(self, instance_id: str, *, limit: int = 100) -> Sequence[AccessEntry]:
        return [e for e in self.entries if e.instance_id == instance_id][-limit:]
