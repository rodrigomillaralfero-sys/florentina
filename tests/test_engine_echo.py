"""Criterio F0.1: eco end-to-end, control mecanico antes del modelo, reinicio sin duplicados."""

from __future__ import annotations

from datetime import timedelta

from core.engine.turn import EchoTurnPolicy, TurnContext, TurnOutput
from core.events import kinds
from core.storage.memory import InMemoryEventStore, InMemoryJobStore, InMemorySubjectResolver
from tests.conftest import EXIT_WORD, INSTANCE, FakeChannel, inbound, make_engine


async def test_echo_end_to_end_records_intent_before_sending():
    engine, _, channel, _ = make_engine()
    await engine.receive(inbound("hola", "m1"))

    assert [m.text for m in channel.sent] == ["eco: hola"]
    events = await engine._events.list(INSTANCE)
    assert [e.kind for e in events] == [kinds.MESSAGE_RECEIVED, kinds.TURN_INTENT, kinds.MESSAGE_SENT]
    intent, sent = events[1], events[2]
    assert intent.payload["text"] == "eco: hola"
    assert sent.payload["channel_message_id"] == "fake-1"
    assert sent.recorded_at >= intent.recorded_at


async def test_redelivered_message_is_processed_once():
    engine, _, channel, _ = make_engine()
    await engine.receive(inbound("hola", "m1"))
    await engine.receive(inbound("hola", "m1"))  # Telegram reentrega el mismo update
    assert len(channel.sent) == 1
    assert engine._events.count() == 3


async def test_restart_mid_turn_finishes_the_turn_without_duplicating():
    """El proceso muere despues de registrar la intencion y antes de enviar."""
    events, jobs, subjects = InMemoryEventStore(), InMemoryJobStore(), InMemorySubjectResolver()

    class DiesBeforeSend(FakeChannel):
        async def send(self, message):
            raise ConnectionError("se cayo justo aqui")

    engine1, _, _, clock = make_engine(events=events, jobs=jobs, subjects=subjects, channel=DiesBeforeSend())
    try:
        await engine1.receive(inbound("hola", "m1"))
    except ConnectionError:
        pass
    assert [e.kind for e in await events.list(INSTANCE)] == [kinds.MESSAGE_RECEIVED, kinds.TURN_INTENT]

    # Reinicio: mismo almacen, canal sano, el canal reentrega el mensaje.
    channel2 = FakeChannel()
    engine2, _, _, _ = make_engine(events=events, jobs=jobs, subjects=subjects, channel=channel2, clock=clock)
    await engine2.receive(inbound("hola", "m1"))
    assert [m.text for m in channel2.sent] == ["eco: hola"]
    assert [e.kind for e in await events.list(INSTANCE)] == [kinds.MESSAGE_RECEIVED, kinds.TURN_INTENT, kinds.MESSAGE_SENT]

    # Y una tercera reentrega ya no hace nada.
    await engine2.receive(inbound("hola", "m1"))
    assert len(channel2.sent) == 1


async def test_pause_is_executed_by_the_backend_and_cancels_pulses():
    class WantsPulse(EchoTurnPolicy):
        async def respond(self, ctx: TurnContext) -> TurnOutput:
            return TurnOutput(text="ok", next_pulse_at=ctx.now + timedelta(minutes=30), intent={"policy": "test"})

    engine, scheduler, channel, clock = make_engine(turn_policy=WantsPulse())
    await engine.receive(inbound("hola", "m1"))
    assert len(await engine._jobs.pending(INSTANCE, kinds=["pulse"])) == 1

    await engine.receive(inbound("/pausa 2d", "m2"))
    assert len(await engine._jobs.pending(INSTANCE, kinds=["pulse"])) == 0
    pause = [e for e in await engine._events.list(INSTANCE) if e.kind == kinds.CONTROL_PAUSE]
    assert len(pause) == 1 and pause[0].payload["cancelled_pulses"] == 1 and pause[0].payload["duration_seconds"] == 2 * 86400
    assert channel.sent[-1].text.startswith("Pausa activada por 2 días")
    assert channel.sent[-1].idempotency_key == kinds.key_ack(pause[0].id)

    subject = await engine._subjects.resolve(INSTANCE, "chat-1")
    state = await engine.state_of(subject)
    assert state.paused and not state.terminated

    # Mientras esta en pausa, un pulso que llegara a dispararse se salta.
    await engine.schedule_pulse(subject, clock.now + timedelta(minutes=1))
    assert len(await engine._jobs.pending(INSTANCE, kinds=["pulse"])) == 1
    clock.advance(minutes=2)
    sent_before = len(channel.sent)
    await scheduler.tick()
    assert len(channel.sent) == sent_before
    assert any(e.kind == kinds.PULSE_SKIPPED for e in await engine._events.list(INSTANCE))

    # La pausa con duracion se reanuda sola.
    clock.advance(days=2, minutes=1)
    await scheduler.tick()
    state = await engine.state_of(subject)
    assert not state.paused


async def test_resume_command_ends_pause_early():
    engine, _, channel, _ = make_engine()
    await engine.receive(inbound("/pausa", "m1"))
    subject = await engine._subjects.resolve(INSTANCE, "chat-1")
    assert (await engine.state_of(subject)).paused
    await engine.receive(inbound("/reanudar", "m2"))
    assert not (await engine.state_of(subject)).paused
    assert channel.sent[-1].text == "Pausa terminada."


async def test_manual_resume_or_exit_cancels_the_pending_auto_resume():
    """Visto en la prueba real: /pausa 1h + /reanudar dejaba el job de reanudacion automatica vivo."""
    from core.engine.loop import RESUME_JOB

    engine, _, _, _ = make_engine()
    await engine.receive(inbound("/pausa 1h", "m1"))
    assert len(await engine._jobs.pending(INSTANCE, kinds=[RESUME_JOB])) == 1
    await engine.receive(inbound("/reanudar", "m2"))
    assert await engine._jobs.pending(INSTANCE, kinds=[RESUME_JOB]) == []

    await engine.receive(inbound("/pausa 2h", "m3"))
    assert len(await engine._jobs.pending(INSTANCE, kinds=[RESUME_JOB])) == 1
    await engine.receive(inbound(EXIT_WORD, "m4"))
    assert await engine._jobs.pending(INSTANCE, kinds=[RESUME_JOB]) == []
    events = await engine._events.list(INSTANCE)
    exit_event = [e for e in events if e.kind == kinds.CONTROL_EXIT][0]
    assert exit_event.payload["cancelled_resumes"] == 1


async def test_exit_word_terminates_before_any_policy_runs():
    calls = 0

    class Counting(EchoTurnPolicy):
        async def respond(self, ctx):
            nonlocal calls
            calls += 1
            return await super().respond(ctx)

    engine, _, channel, clock = make_engine(turn_policy=Counting())
    await engine.receive(inbound("hola", "m1"))
    assert calls == 1
    await engine.receive(inbound(f"  {EXIT_WORD.upper()} ", "m2"))
    assert calls == 1, "la baja nunca llega a la politica de turno"
    assert channel.sent[-1].text == "Baja registrada.", "la baja se confirma y no revela nada"

    subject = await engine._subjects.resolve(INSTANCE, "chat-1")
    assert (await engine.state_of(subject)).terminated
    # Despues de la salida, los mensajes se registran pero no se responden.
    await engine.receive(inbound("sigues ahi?", "m3"))
    assert calls == 1
    kinds_after = [e.kind for e in await engine._events.list(INSTANCE)][-1:]
    assert kinds_after == [kinds.MESSAGE_RECEIVED]


async def test_pulse_fires_once_even_if_the_job_is_retried():
    engine, scheduler, channel, clock = make_engine()
    subject = await engine._subjects.resolve(INSTANCE, "chat-1")
    at = clock.now + timedelta(minutes=5)
    assert await engine.schedule_pulse(subject, at)
    assert not await engine.schedule_pulse(subject, at), "reprogramar el mismo pulso no crea otro"
    clock.advance(minutes=6)
    assert await scheduler.tick() == 1
    assert [m.text for m in channel.sent] == ["(pulso)"]

    # Un segundo intento del mismo job (lease vencido, por ejemplo) no vuelve a escribir.
    job = (await engine._jobs.pending(INSTANCE)) or engine._jobs.all()
    await engine.on_pulse(job[0])
    assert [m.text for m in channel.sent] == ["(pulso)"]


async def test_events_from_another_instance_are_ignored():
    engine, _, channel, _ = make_engine()
    await engine.receive(inbound("hola", "m1", instance="otra"))
    assert channel.sent == []
    assert engine._events.count() == 0


async def test_message_keeps_who_spoke_and_where_for_future_multi_subject_memory():
    """Anexo de escala: el mensaje guarda remitente y conversacion (en un grupo difieren del sujeto)."""
    from dataclasses import replace

    from core.engine.loop import _inbound_from
    from core.events import kinds

    engine, scheduler, channel, clock = make_engine()
    ev = replace(inbound("hola", "s1"), sender_ref="user-7", conversation_ref="chat-1")
    await engine.receive(ev)
    received = [e for e in await engine._events.list(INSTANCE) if e.kind == kinds.MESSAGE_RECEIVED]  # noqa: SLF001
    assert received[0].payload["sender_ref"] == "user-7" and received[0].payload["conversation_ref"] == "chat-1"
    back = _inbound_from(received[0])
    assert (back.sender_ref, back.conversation_ref) == ("user-7", "chat-1")
    # un canal que no sabe quien hablo no deja claves vacias
    await engine.receive(inbound("otro", "s2"))
    plain = [e for e in await engine._events.list(INSTANCE) if e.kind == kinds.MESSAGE_RECEIVED][-1]  # noqa: SLF001
    assert "sender_ref" not in plain.payload
