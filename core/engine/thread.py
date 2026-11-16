"""Lectura estructurada de la episodica reciente: el hilo y las senales de control.

El motor sabe que significan sus propios eventos; la instancia decide con que
palabras se los muestra al modelo. Por eso aqui se producen items tipados y
no texto.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal, Mapping, Sequence

from core.events import kinds
from core.events.store import Event

ThreadKind = Literal["subject", "agent", "agent_silent", "control", "channel", "pulse"]


@dataclass(frozen=True)
class ThreadItem:
    at: datetime
    kind: ThreadKind
    text: str | None = None
    detail: Mapping[str, Any] = field(default_factory=dict)


def thread_items(events: Sequence[Event]) -> list[ThreadItem]:
    """Convierte eventos en orden cronologico en items del hilo. Omite lo que no aporta al modelo."""
    out: list[ThreadItem] = []
    for ev in events:
        p = ev.payload
        if ev.kind == kinds.MESSAGE_RECEIVED:
            out.append(ThreadItem(ev.occurred_at, "subject", p.get("text")))
        elif ev.kind == kinds.MESSAGE_SENT:
            author = p.get("author", kinds.AUTHOR_POLICY)
            out.append(ThreadItem(ev.occurred_at, "agent" if author == kinds.AUTHOR_POLICY else "control", p.get("text"), {"author": author}))
        elif ev.kind == kinds.TURN_INTENT and not p.get("text"):
            out.append(
                ThreadItem(
                    ev.occurred_at,
                    "agent_silent",
                    None,
                    {"reason": p.get("reason"), "failed": bool(p.get("failed")), "trigger": p.get("trigger")},
                )
            )
        elif ev.kind == kinds.PULSE_FIRED:
            out.append(ThreadItem(ev.occurred_at, "pulse"))
        elif ev.kind in (kinds.CONTROL_PAUSE, kinds.CONTROL_RESUME, kinds.CONTROL_EXIT, kinds.CONTROL_REOPEN):
            out.append(ThreadItem(ev.occurred_at, "control", None, {**dict(p), "kind": ev.kind}))
        elif ev.kind == kinds.CHANNEL_EVENT:
            out.append(ThreadItem(ev.occurred_at, "channel", None, dict(p)))
    return out


@dataclass(frozen=True)
class ControlSignals:
    """Lo que la instancia debe mostrarle al modelo ademas del hilo."""

    last_pause_at: datetime | None = None
    last_pause_duration: timedelta | None = None
    last_pause_until: datetime | None = None
    resumed_at: datetime | None = None
    last_subject_message_at: datetime | None = None
    last_agent_message_at: datetime | None = None
    last_pulse_at: datetime | None = None
    unanswered_pulse_streak: int = 0  # pulsos seguidos (hasta el ultimo) sin mensaje del sujeto entre medio
    pending_pulse_at: datetime | None = None
    reopened_at: datetime | None = None


def control_signals(events: Sequence[Event], *, pending_pulse_at: datetime | None = None) -> ControlSignals:
    last_pause_at = last_pause_until = resumed_at = reopened_at = None
    last_pause_duration: timedelta | None = None
    last_subject_at = last_agent_at = last_pulse_at = None
    streak = 0
    for ev in events:
        p = ev.payload
        if ev.kind == kinds.CONTROL_PAUSE:
            last_pause_at = ev.occurred_at
            secs = p.get("duration_seconds")
            last_pause_duration = timedelta(seconds=int(secs)) if secs is not None else None
            until = p.get("until")
            last_pause_until = datetime.fromisoformat(until) if isinstance(until, str) else None
            resumed_at = None
        elif ev.kind == kinds.CONTROL_RESUME:
            resumed_at = ev.occurred_at
        elif ev.kind == kinds.CONTROL_REOPEN:
            reopened_at = ev.occurred_at
        elif ev.kind == kinds.MESSAGE_RECEIVED:
            last_subject_at = ev.occurred_at
            streak = 0
        elif ev.kind == kinds.MESSAGE_SENT and p.get("author", kinds.AUTHOR_POLICY) == kinds.AUTHOR_POLICY:
            last_agent_at = ev.occurred_at
        elif ev.kind == kinds.PULSE_FIRED:
            last_pulse_at = ev.occurred_at
            streak += 1
    return ControlSignals(
        last_pause_at=last_pause_at,
        last_pause_duration=last_pause_duration,
        last_pause_until=last_pause_until,
        resumed_at=resumed_at,
        last_subject_message_at=last_subject_at,
        last_agent_message_at=last_agent_at,
        last_pulse_at=last_pulse_at,
        unanswered_pulse_streak=streak,
        pending_pulse_at=pending_pulse_at,
        reopened_at=reopened_at,
    )
