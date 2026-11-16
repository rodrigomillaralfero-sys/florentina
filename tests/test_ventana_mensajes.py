"""La ventana del turno se cuenta en mensajes, no en eventos.

Antes el turno leia los ultimos 50 eventos de cualquier tipo; impulsos, etiquetas, alertas y corridas dejaban
adentro solo 9-13 mensajes. Ahora ve los ultimos `recent_messages` mensajes (de la persona y del agente) con todo lo que paso
entre medio, nunca menos que los 50 eventos de antes y con un tope de lectura.

(Reconstruida el 05-11 en el F0.14: la imagen `s5-1104a` no lleva `tests/`.)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from core.engine.loop import Engine
from core.events import kinds
from core.events.store import NewEvent
from core.storage.memory import InMemoryEventStore

INSTANCE, SID = "t", "s1"
T0 = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)


async def _fill(store, spec):
    """spec: lista de kinds en orden; cada uno un minuto despues del anterior."""
    for i, kind in enumerate(spec):
        await store.append(NewEvent(instance_id=INSTANCE, kind=kind, idempotency_key=f"e{i}", subject_id=SID,
                                    payload={"text": f"m{i}"}, occurred_at=T0 + timedelta(minutes=i)))


def _engine(store, *, window=50, messages=24):
    return SimpleNamespace(_events=store, instance_id=INSTANCE, _recent_window=window, _recent_messages=messages,
                           RECENT_CAP=Engine.RECENT_CAP)


@pytest.mark.asyncio
async def test_window_holds_the_last_n_messages_with_the_noise_between():
    store = InMemoryEventStore()
    noise = ["impulse.skipped", "strategist.ran", "label.applied", "watch.alert"]
    spec = []
    for i in range(30):
        spec.append(kinds.MESSAGE_RECEIVED if i % 2 == 0 else kinds.MESSAGE_SENT)
        spec += noise  # 4 eventos de ruido por mensaje: 50 eventos dejaban solo ~10 mensajes
    await _fill(store, spec)
    recent = await Engine._recent_for_turn(_engine(store), SimpleNamespace(subject_id=SID))
    msgs = [e for e in recent if e.kind in (kinds.MESSAGE_RECEIVED, kinds.MESSAGE_SENT)]
    assert len(msgs) == 24 and len(recent) > 50
    assert any(e.kind == "strategist.ran" for e in recent), "lo que paso entre medio sigue en la ventana"


@pytest.mark.asyncio
async def test_never_less_than_the_old_event_window_and_off_means_as_before():
    store = InMemoryEventStore()
    await _fill(store, [kinds.MESSAGE_RECEIVED] * 70)
    recent = await Engine._recent_for_turn(_engine(store, messages=24), SimpleNamespace(subject_id=SID))
    assert len(recent) == 50, "con mensajes densos manda la ventana de eventos de antes"
    off = await Engine._recent_for_turn(_engine(store, messages=None), SimpleNamespace(subject_id=SID))
    assert len(off) == 50
