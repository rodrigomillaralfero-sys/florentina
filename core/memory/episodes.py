"""Episodios: la unidad recuperable de la episodica.

Un episodio es el resumen textual de un turno (lo que escribio la persona, lo que
respondio el agente, si fijo pulso) anclado al evento de
intencion de ese turno. Esa ancla lo hace idempotente: reiniciar y volver a
procesar el mismo turno encuentra el episodio y no lo duplica.

El texto lo compone el motor con etiquetas neutras; la instancia decide como
mostrarlo en el prompt. El embedding se guarda aparte, por modelo: cambiar el
modelo de embeddings es agregar vectores, no reescribir episodios.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Protocol, Sequence

from core.events import kinds
from core.events.store import Event

# Tipos de episodio. Solo `EXCHANGE` contiene palabras de la persona; los demas son
# contactos abiertos por el agente o silencios suyos. La distincion importa para las
# guardias de la consolidacion: un silencio no es una declaracion de la persona.
EXCHANGE = "exchange"  # la persona escribio en este turno
PULSE = "pulse"  # el agente abrio contacto; la persona no habia escrito
AGENT_SILENT = "agent_silent"  # el agente decidio no escribir (en un pulso)


@dataclass(frozen=True)
class NewEpisode:
    instance_id: str
    subject_id: str
    event_id: str  # evento de intencion del turno: la clave de idempotencia
    kind: str
    text: str
    occurred_at: datetime
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Episode:
    id: str
    instance_id: str
    subject_id: str
    event_id: str
    kind: str
    text: str
    occurred_at: datetime
    created_at: datetime
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def is_silence(self) -> bool:
        return self.kind != EXCHANGE


@dataclass(frozen=True)
class EpisodeHit:
    episode: Episode
    similarity: float  # coseno en [-1, 1]; 1 = identico


class EpisodeStore(Protocol):
    async def write(self, new: NewEpisode) -> tuple[Episode, bool]:
        """Devuelve (episodio, created). created=False si ya habia uno para ese event_id."""
        ...

    async def get_many(self, ids: Sequence[str]) -> Sequence[Episode]: ...

    async def list(
        self, instance_id: str, subject_id: str, *, since: datetime | None = None, limit: int | None = None
    ) -> Sequence[Episode]:
        """Orden cronologico ascendente por `occurred_at`. `since` es estricto (>)."""
        ...

    async def set_embedding(
        self, episode_id: str, *, model: str, dim: int, vector: Sequence[float], usage: Mapping[str, int] | None = None
    ) -> bool:
        """False si ya habia embedding de ese modelo para el episodio."""
        ...

    async def missing_embeddings(self, instance_id: str, *, model: str, limit: int = 50) -> Sequence[Episode]: ...

    async def embedded_ids(self, ids: Sequence[str], *, model: str) -> set[str]:
        """De `ids`, cuales ya tienen vector de `model`."""
        ...

    async def search(
        self,
        instance_id: str,
        subject_id: str,
        *,
        model: str,
        vector: Sequence[float],
        limit: int,
        exclude_event_ids: Sequence[str] = (),
    ) -> Sequence[EpisodeHit]:
        """Los `limit` episodios mas parecidos (coseno) con embedding de `model`, de mayor a menor similitud."""
        ...

    async def count(self, instance_id: str, subject_id: str | None = None) -> int: ...

    async def embedding_usage(self, instance_id: str, *, since: datetime | None = None) -> Mapping[str, int]:
        """Suma de `usage` de los embeddings (tokens, cost_microusd) desde `since`."""
        ...


# ---------------------------------------------------------------------- composicion


@dataclass(frozen=True)
class TurnRecord:
    """Lo que el motor sabe de un turno recien cerrado. Es lo que reciben los ganchos `after_turn`."""

    subject_id: str
    instance_id: str
    trigger: str  # "message" | "pulse"
    trigger_event: Event
    intent: Event
    subject_messages: Sequence[Event]  # los `message.received` que este turno respondio (agrupados)
    now: datetime


def episode_kind(record: TurnRecord) -> str:
    if record.trigger == "message" or record.subject_messages:
        return EXCHANGE
    return PULSE if record.intent.payload.get("text") else AGENT_SILENT


def compose_episode_text(record: TurnRecord) -> str:
    """Texto neutro y compacto del turno. Es lo que se embebe y lo que se recupera."""
    p = record.intent.payload
    lines: list[str] = []
    for ev in record.subject_messages:
        text = (ev.payload.get("text") or "").strip()
        if text:
            lines.append(f"persona: {text}")
    if not record.subject_messages and record.trigger == "pulse":
        lines.append("(el agente abrio el contacto; la persona no habia escrito)")
    agent_text = (p.get("text") or "").strip()
    if agent_text:
        lines.append(f"agente: {agent_text}")
    elif p.get("suppressed"):
        lines.append("(turno suprimido por la guardia tecnica; el agente no respondio)")
    elif p.get("failed"):
        lines.append("(fallo tecnico: el agente no pudo responder)")
    else:
        lines.append("(el agente decidio no escribir)")
    if p.get("next_pulse_at"):
        lines.append(f"proximo contacto fijado: {p.get('next_pulse_at')}")
    return "\n".join(lines)


def episode_from_turn(record: TurnRecord) -> NewEpisode:
    p = record.intent.payload
    return NewEpisode(
        instance_id=record.instance_id,
        subject_id=record.subject_id,
        event_id=record.intent.id,
        kind=episode_kind(record),
        text=compose_episode_text(record),
        occurred_at=record.intent.occurred_at,
        metadata={
            "trigger": record.trigger,
            "message_event_ids": [ev.id for ev in record.subject_messages],
            "next_pulse_at": p.get("next_pulse_at"),
            "failed": bool(p.get("failed")),
            "suppressed": bool(p.get("suppressed")),
            "policy": p.get("policy"),
        },
    )


def subject_messages_for(recent: Sequence[Event], trigger_event: Event) -> list[Event]:
    """Los mensajes de la persona que este turno responde: los `message.received` hasta el disparador
    inclusive que ninguna intencion cubrio todavia. Cubierto = disparador de una intencion (su clave
    lo nombra) o listado en `messages` de una intencion. Se mira por cobertura y no por orden porque
    un mensaje puede llegar mientras el turno anterior consulta al modelo: queda antes de esa
    intencion en el tiempo, pero no fue respondido por ella."""
    covered: set[str] = set()
    for ev in recent:
        if ev.kind == kinds.TURN_INTENT:
            if ev.idempotency_key.startswith("intent:"):
                covered.add(ev.idempotency_key[len("intent:") :])
            covered.update(str(m) for m in (ev.payload.get("messages") or []))
    group: list[Event] = []
    for ev in recent:
        if ev.kind == kinds.MESSAGE_RECEIVED and ev.id not in covered:
            group.append(ev)
        if ev.id == trigger_event.id:
            break
    if trigger_event.kind == kinds.MESSAGE_RECEIVED and not any(e.id == trigger_event.id for e in group):
        group.append(trigger_event)
    return group
