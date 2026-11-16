"""Costura 3 — Sujeto como clave.

Todo lo que el motor guarda va con la clave (instancia, sujeto): cada persona
tiene su propia memoria y nada se comparte entre instancias.

El canal entrega un `subject_ref` (p. ej. el id de un chat); el motor lo
resuelve a un `SubjectKey` estable a traves de `SubjectResolver`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SubjectKey:
    instance_id: str
    subject_id: str


class SubjectResolver(Protocol):
    async def resolve(self, instance_id: str, subject_ref: str) -> SubjectKey:
        """Devuelve la clave estable del sujeto; la crea si no existe."""
        ...

    async def ref_for(self, subject: SubjectKey) -> str:
        """Referencia externa (del canal) para poder escribirle."""
        ...
