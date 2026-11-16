"""Fixtures comunes: canal falso, relojes controlables, motor en memoria."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core.engine.loop import Engine
from core.engine.turn import EchoTurnPolicy
from core.interfaces.channel import InboundEvent, InboundHandler, OutboundMessage
from core.scheduler.jobs import Scheduler
from core.storage.memory import InMemoryEventStore, InMemoryJobStore, InMemorySubjectResolver
from core.engine.commands import CommandPolicy

INSTANCE = "inst-test"
SUBJECT_REF = "chat-1"
EXIT_WORD = "/baja"
ACKS = {"pause": "Pausa activada.", "pause_for": "Pausa activada por {duracion}.", "resume": "Pausa terminada.", "exit": "Baja registrada."}


class FakeChannel:
    """Canal en memoria: guarda lo enviado y numera los mensajes."""

    def __init__(self) -> None:
        self.sent: list[OutboundMessage] = []
        self._n = 0
        self.handler: InboundHandler | None = None

    @property
    def name(self) -> str:
        return "fake"

    async def send(self, message: OutboundMessage) -> str:
        self._n += 1
        self.sent.append(message)
        return f"fake-{self._n}"

    async def run(self, handler: InboundHandler) -> None:
        self.handler = handler

    async def stop(self) -> None:
        pass


class Clock:
    def __init__(self, start: datetime | None = None) -> None:
        self.now = start or datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs) -> datetime:
        self.now = self.now + timedelta(**kwargs)
        return self.now


def inbound(text: str, external_id: str, *, at: datetime | None = None, instance: str = INSTANCE) -> InboundEvent:
    return InboundEvent(
        instance_id=instance,
        subject_ref=SUBJECT_REF,
        external_id=external_id,
        text=text,
        received_at=at or datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc),
    )


def make_engine(
    *,
    events: InMemoryEventStore | None = None,
    jobs: InMemoryJobStore | None = None,
    subjects: InMemorySubjectResolver | None = None,
    channel: FakeChannel | None = None,
    clock: Clock | None = None,
    turn_policy=None,
) -> tuple[Engine, Scheduler, FakeChannel, Clock]:
    """Construye un motor. Pasar los mismos almacenes simula un reinicio del backend."""
    events = events or InMemoryEventStore()
    jobs = jobs or InMemoryJobStore()
    subjects = subjects or InMemorySubjectResolver()
    channel = channel or FakeChannel()
    clock = clock or Clock()
    engine = Engine(
        instance_id=INSTANCE,
        events=events,
        subjects=subjects,
        channel=channel,
        jobs=jobs,
        turn_policy=turn_policy or EchoTurnPolicy(prefix="eco: ", pulse_text="(pulso)"),
        interaction=CommandPolicy(exit=EXIT_WORD, acks=ACKS),
        clock=clock,
    )
    scheduler = Scheduler(jobs, lease=timedelta(minutes=5), clock=clock)
    engine.attach(scheduler)
    return engine, scheduler, channel, clock
