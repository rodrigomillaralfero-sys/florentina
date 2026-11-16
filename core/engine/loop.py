"""El motor: un solo ciclo que se repite en cada turno.

    recibir -> control mecanico -> registrar entrada -> turno -> registrar intencion -> enviar -> registrar salida

Lo que es definitivo aqui y no cambia en sprints posteriores:

* El control mecanico (pausa, reanudacion, salida) se evalua y ejecuta antes
  de que nada toque la politica de turno. No depende del modelo.
* Cada efecto se escribe con una clave derivada de algo previo. Reprocesar
  un mensaje reentregado, o reiniciar a mitad de turno, no duplica eventos,
  mensajes ni pulsos.
* El registro de intencion se escribe ANTES de enviar. Si el proceso muere
  entre la intencion y el envio, el reinicio encuentra la intencion y envia
  exactamente lo que se habia decidido.

F0.2 agrega:

* **Un solo pulso pendiente por sujeto.** Cada turno fija el proximo contacto
  (fecha-hora o ninguno): el anterior se cancela. Reproducir una intencion
  tras un reinicio no duplica el pulso ni lo pierde.
* **La baja corta.** Tras la palabra de baja, se cancelan los pulsos y la persona
  queda sin contacto hasta que el operador la reabra.
* **Reanudar deja un pulso de reconexion** para que la politica decida que
  hacer con la pausa, en vez de esperar mudo a que el sujeto escriba.
* **Eventos de canal** (p. ej. abrir el chat) se registran y no pasan por el modelo.
* **Reapertura por el operador**, fuera del canal, para volver a probar tras una salida.

F0.2b agrega:

* **Guardia del turno** (`TurnGuard`, p. ej. los cortacircuitos): antes de consultar a la
  politica, el motor pregunta si puede. Si no, registra una intencion sin texto y sin pulso
  (`policy: guard`), no envia nada, y el sujeto queda registrado igual. Los controles mecanicos
  no pasan por la guardia: pausa, reanudacion y salida siguen funcionando con el motor quieto.
* **Gancho tras la intencion** (`after_intent`): quien lo inyecta mira lo que paso (fallos
  seguidos, proveedor sin saldo) y avisa. Nunca puede romper el turno.

F0.3 agrega:

* **Mensajes muy seguidos se agrupan en un turno** (`group_window`). Cada mensaje se registra
  al llegar; la respuesta se difiere con un job a `group_window` del mensaje. Un mensaje nuevo
  reemplaza el job pendiente: un solo turno responde a todos. La intencion registra que
  mensajes cubrio; la reentrega de uno ya cubierto no genera otro turno. Con `group_window`
  en None el motor responde de inmediato, como en F0.2.
* **Ganchos tras el turno** (`after_turn`): reciben un `TurnRecord` (disparador, intencion,
  mensajes del sujeto que el turno respondio) una vez entregada la intencion. La memoria
  episodica y el cierre de sesion viven ahi. Un gancho que falla se loguea y el turno sigue.
* **Recuperacion de mensajes varados** al arrancar: un mensaje registrado sin turno (el proceso
  murio entre registrar y decidir, y el canal no reentrega) se responde ahora.

"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Mapping, Protocol, Sequence

from core.engine.state import SubjectState, fold_state
from core.engine.turn import TurnContext, TurnOutput, TurnPolicy
from core.events import kinds
from core.events.store import Event, EventStore, NewEvent, utcnow
from core.interfaces.channel import Channel, InboundEvent, OutboundMessage
from core.interfaces.interaction_policy import ControlKind, InteractionPolicy, MechanicalCommand
from core.interfaces.subject_key import SubjectKey, SubjectResolver
from core.memory.episodes import TurnRecord, subject_messages_for
from core.scheduler.jobs import Job, JobStore, NewJob, Scheduler

log = logging.getLogger(__name__)

PULSE_JOB = "pulse"
RESUME_JOB = "control.resume"
TURN_JOB = "turn.grouped"  # F0.3: el turno diferido que agrupa mensajes seguidos
CHANNEL_EVENT_PREFIX = "channel."  # `InboundEvent.kind` que empieza asi no es texto del sujeto


class TurnGuard(Protocol):
    async def before_turn(self, *, trigger: str, now: datetime) -> str | None:
        """None = la politica puede correr. Un texto = razon para no correrla en este turno."""
        ...


class AfterTurnIncomplete(RuntimeError):
    """Algun gancho posterior al turno fallo (el mensaje ya salio). El job se reintenta y los repite."""


IntentHook = Callable[[Event], Awaitable[None]]
TurnHook = Callable[[TurnRecord], Awaitable[None]]
ReceivedHook = Callable[[str, Event], Awaitable[Any]]  # (subject_id, message.received) apenas llega, antes del turno


class Engine:
    def __init__(
        self,
        *,
        instance_id: str,
        events: EventStore,
        subjects: SubjectResolver,
        channel: Channel,
        jobs: JobStore,
        turn_policy: TurnPolicy,
        interaction: InteractionPolicy,
        clock: Callable[[], datetime] = utcnow,
        recent_window: int = 50,
        recent_messages: int | None = None,
        resume_pulse: timedelta | None = timedelta(minutes=60),
        guard: TurnGuard | None = None,
        after_intent: IntentHook | None = None,
        group_window: timedelta | None = None,
        after_turn: Sequence[TurnHook] = (),
        typing_indicator: bool = False,
        typing_every: timedelta = timedelta(seconds=4),
        on_received: Sequence[ReceivedHook] = (),
    ) -> None:
        self.instance_id = instance_id
        self._on_received: list[ReceivedHook] = list(on_received)
        self._events = events
        self._subjects = subjects
        self._channel = channel
        self._jobs = jobs
        self._turn = turn_policy
        self._interaction = interaction
        self._clock = clock
        self._recent_window = recent_window
        self._recent_messages = recent_messages  # la ventana del turno se cuenta en mensajes (None = los ultimos `recent_window` eventos)
        self._resume_pulse = resume_pulse
        self._guard = guard
        self._after_intent = after_intent
        self._group_window = group_window if group_window and group_window.total_seconds() > 0 else None
        self._after_turn: list[TurnHook] = list(after_turn)
        self._typing = typing_indicator
        self._typing_every = typing_every

    # ------------------------------------------------------------------ cableado

    def attach(self, scheduler: Scheduler) -> None:
        """Registra los handlers de jobs que el motor sabe correr."""
        scheduler.register(PULSE_JOB, self.on_pulse)
        scheduler.register(RESUME_JOB, self.on_auto_resume)
        scheduler.register(TURN_JOB, self.on_turn_job)

    def add_after_turn(self, hook: TurnHook) -> None:
        self._after_turn.append(hook)

    @property
    def group_window(self) -> timedelta | None:
        return self._group_window

    # ------------------------------------------------------------------ entrada

    async def receive(self, inbound: InboundEvent) -> None:
        if inbound.instance_id != self.instance_id:
            log.warning("evento de otra instancia ignorado: %s", inbound.instance_id)
            return
        subject = await self._subjects.resolve(inbound.instance_id, inbound.subject_ref)

        command = self._interaction.mechanical_command(inbound)
        if command is not None:
            await self._apply_control(subject, inbound, command)
            return

        if inbound.kind.startswith(CHANNEL_EVENT_PREFIX):
            await self._events.append(
                NewEvent(
                    instance_id=self.instance_id,
                    subject_id=subject.subject_id,
                    kind=kinds.CHANNEL_EVENT,
                    idempotency_key=kinds.key_received(inbound.external_id),
                    payload={"kind": inbound.kind, "text": inbound.text, "subject_ref": inbound.subject_ref, **_refs(inbound)},
                    occurred_at=inbound.received_at,
                )
            )
            return

        received = await self._events.append(
            NewEvent(
                instance_id=self.instance_id,
                subject_id=subject.subject_id,
                kind=kinds.MESSAGE_RECEIVED,
                idempotency_key=kinds.key_received(inbound.external_id),
                payload={"text": inbound.text, "kind": inbound.kind, "subject_ref": inbound.subject_ref, **_refs(inbound)},
                occurred_at=inbound.received_at,
            )
        )
        if not received.created:
            # Reentrega del canal o reinicio: el turno ya empezo. Si quedo a medias, lo terminamos.
            await self._resume_turn_if_incomplete(subject, received.event, inbound)
            return

        for hook in self._on_received:  # lecturas al instante; nunca frenan el turno
            try:
                await hook(subject.subject_id, received.event)
            except Exception:  # noqa: BLE001
                log.exception("un gancho on_received fallo; el turno sigue")

        state = await self.state_of(subject)
        if state.terminated:
            log.info("sujeto terminado; mensaje registrado, sin respuesta")
            return

        if self._group_window is not None:
            await self._schedule_turn(subject, received.event)
            return
        await self._run_turn(subject, trigger_event=received.event, inbound=inbound, state=state)

    async def _run_turn(self, subject: SubjectKey, *, trigger_event: Event, inbound: InboundEvent | None, state: SubjectState) -> None:
        trigger = _trigger_of(trigger_event)
        ctx = await self._context(subject, trigger=trigger, inbound=inbound, paused=state.paused, trigger_event=trigger_event)
        output = await self._decide(ctx)
        await self._act(subject, output, ctx=ctx, trigger_event=trigger_event, reply_to=inbound.external_id if inbound else None, state=state)

    async def _decide(self, ctx: TurnContext) -> TurnOutput:
        """Guardia y luego politica. Con la guardia cerrada no se consulta al modelo ni se envia."""
        if self._guard is not None:
            reason = await self._guard.before_turn(trigger=ctx.trigger, now=ctx.now)
            if reason:
                log.warning("turno suprimido por la guardia: %s", reason)
                return TurnOutput(
                    text=None,
                    next_pulse_at=None,
                    intent={"policy": "guard", "suppressed": True, "trigger": ctx.trigger, "reason": reason},
                )
        if ctx.trigger != "message":
            return await self._turn.initiate(ctx)
        typing = await self._start_typing(ctx.subject) if self._typing else None
        try:
            return await self._turn.respond(ctx)
        finally:
            if typing is not None:
                typing.cancel()

    async def _start_typing(self, subject: SubjectKey) -> "asyncio.Task[None] | None":
        """Indicador "escribiendo..." mientras la politica decide, solo en turnos que el sujeto abrio.
        En un pulso no: mostrarlo y luego callar delataria que el agente estuvo por escribir.
        Es mecanico, no lo decide el modelo, y un fallo del canal no toca el turno. El primero sale
        antes de consultar a la politica; luego se repite (en canales tipicos dura ~5 s) hasta que decide."""
        typing = getattr(self._channel, "typing", None)
        if typing is None:
            return None
        ref = await self._subjects.ref_for(subject)
        try:
            await asyncio.wait_for(typing(ref), timeout=3)
        except Exception:  # noqa: BLE001 - es cosmetico
            log.debug("indicador de escritura fallo", exc_info=True)
            return None

        async def repeat() -> None:
            while True:
                await asyncio.sleep(self._typing_every.total_seconds())
                try:
                    await typing(ref)
                except Exception:  # noqa: BLE001
                    return

        return asyncio.create_task(repeat())

    # ------------------------------------------------------------------ agrupacion de mensajes (F0.3)

    async def _schedule_turn(self, subject: SubjectKey, message: Event) -> None:
        """Difiere el turno a `group_window` del mensaje; un mensaje nuevo reemplaza al job pendiente."""
        assert self._group_window is not None
        key = kinds.key_turn_job(subject.subject_id, message.id)
        await self._jobs.cancel(self.instance_id, subject_id=subject.subject_id, kinds=[TURN_JOB], exclude_keys=[key])
        await self._jobs.enqueue(
            NewJob(
                instance_id=self.instance_id,
                subject_id=subject.subject_id,
                kind=TURN_JOB,
                run_at=message.occurred_at + self._group_window,
                idempotency_key=key,
                payload={"message_event_id": message.id},
            )
        )

    async def on_turn_job(self, job: Job) -> Mapping[str, Any]:
        assert job.subject_id is not None
        subject = SubjectKey(job.instance_id, job.subject_id)
        message = await self._events.get(str(job.payload.get("message_event_id") or ""))
        if message is None:
            return {"skipped": "no_message"}
        if await self._events.by_key(self.instance_id, kinds.key_intent(message.id)) is not None:
            # F0.8: reintento de este mismo turno (el proceso murio o la base fallo despues de decidir):
            # se termina lo que falte (envio y ganchos, ambos idempotentes) sin decidir de nuevo.
            await self._resume_turn_if_incomplete(subject, message, _inbound_from(message))
            return {"resumed": True}
        if await self._covered(subject, message):
            return {"skipped": "covered"}
        state = await self.state_of(subject)
        if state.terminated:
            return {"skipped": "terminated"}
        await self._run_turn(subject, trigger_event=message, inbound=_inbound_from(message), state=state)
        return {"answered": True}

    async def _covered(self, subject: SubjectKey, message: Event) -> bool:
        """Un mensaje esta cubierto si una intencion lo tiene por disparador o lo lista entre los suyos."""
        if await self._events.by_key(self.instance_id, kinds.key_intent(message.id)) is not None:
            return True
        intents = await self._events.list(
            self.instance_id, subject_id=subject.subject_id, kinds=[kinds.TURN_INTENT], since=message.occurred_at - timedelta(minutes=10)
        )
        return any(message.id in (i.payload.get("messages") or []) for i in intents)

    async def _pending_turn_job(self, subject: SubjectKey) -> bool:
        return bool(await self._jobs.pending(self.instance_id, subject_id=subject.subject_id, kinds=[TURN_JOB]))

    # ------------------------------------------------------------------ pulsos

    async def schedule_pulse(self, subject: SubjectKey, at: datetime) -> bool:
        key = kinds.key_pulse_job(subject.subject_id, at.isoformat())
        job, created = await self._jobs.enqueue(
            NewJob(
                instance_id=self.instance_id,
                subject_id=subject.subject_id,
                kind=PULSE_JOB,
                run_at=at,
                idempotency_key=key,
            )
        )
        # F0.8: el evento se asegura aunque el job ya existiera (idempotente por clave): una caida entre
        # encolar y registrar ya no deja un pulso agendado sin su `pulse.scheduled`.
        await self._events.append(
            NewEvent(
                instance_id=self.instance_id,
                occurred_at=self._clock(),
                subject_id=subject.subject_id,
                kind=kinds.PULSE_SCHEDULED,
                idempotency_key=kinds.key_pulse_scheduled(key),
                payload={"at": at.isoformat(), "job_id": job.id},
            )
        )
        return created

    async def set_next_pulse(self, subject: SubjectKey, at: datetime | None) -> Mapping[str, Any]:
        """Fija el unico proximo contacto del sujeto: cancela los demas pendientes y programa `at` si hay.

        Idempotente: si `at` ya estaba programado, se conserva (no se cancela y recrea).
        """
        keep = [kinds.key_pulse_job(subject.subject_id, at.isoformat())] if at else []
        cancelled = await self._jobs.cancel(
            self.instance_id, subject_id=subject.subject_id, kinds=[PULSE_JOB], exclude_keys=keep
        )
        created = await self.schedule_pulse(subject, at) if at else False
        return {"cancelled": cancelled, "created": created}

    async def pending_pulse_at(self, subject: SubjectKey) -> datetime | None:
        pending = await self._jobs.pending(self.instance_id, subject_id=subject.subject_id, kinds=[PULSE_JOB])
        return min((j.run_at for j in pending), default=None)

    async def on_pulse(self, job: Job) -> Mapping[str, Any]:
        assert job.subject_id is not None
        subject = SubjectKey(job.instance_id, job.subject_id)
        state = await self.state_of(subject)
        if not state.can_contact:
            await self._events.append(
                NewEvent(
                    instance_id=self.instance_id,
                    occurred_at=self._clock(),
                    subject_id=subject.subject_id,
                    kind=kinds.PULSE_SKIPPED,
                    idempotency_key=kinds.key_pulse_fired(job.idempotency_key),
                    payload={"reason": "paused" if state.paused else "terminated"},
                )
            )
            return {"skipped": True}

        fired = await self._events.append(
            NewEvent(
                instance_id=self.instance_id,
                occurred_at=self._clock(),
                subject_id=subject.subject_id,
                kind=kinds.PULSE_FIRED,
                idempotency_key=kinds.key_pulse_fired(job.idempotency_key),
                payload={"job_id": job.id, "scheduled_for": job.run_at.isoformat()},
            )
        )
        if not fired.created:
            # Reintento de un job que ya disparo: terminamos lo que falte sin generar un turno nuevo.
            await self._resume_turn_if_incomplete(subject, fired.event, inbound=None)
            return {"resumed": True}

        await self._run_turn(subject, trigger_event=fired.event, inbound=None, state=state)
        return {"fired": True}


    # ------------------------------------------------------------------ control mecanico

    async def _apply_control(self, subject: SubjectKey, inbound: InboundEvent, command: MechanicalCommand) -> None:
        key = kinds.key_control(inbound.external_id)
        existing = await self._events.by_key(self.instance_id, key)
        if existing is not None:
            return  # ya ejecutado: reentrega del canal

        now = self._clock()
        payload: dict[str, Any] = {"raw": command.raw, "kind": command.kind.value}
        if command.kind in (ControlKind.PAUSE, ControlKind.EXIT):
            payload["cancelled_pulses"] = await self._jobs.cancel(
                self.instance_id, subject_id=subject.subject_id, kinds=[PULSE_JOB]
            )
        # Una reanudacion manual, una pausa nueva o la salida dejan sin efecto
        # cualquier reanudacion automatica pendiente: no debe quedar basura en la cola.
        payload["cancelled_resumes"] = await self._jobs.cancel(
            self.instance_id, subject_id=subject.subject_id, kinds=[RESUME_JOB]
        )
        if command.kind == ControlKind.PAUSE:
            payload["since"] = now.isoformat()
            if command.duration is not None:
                until = now + command.duration
                payload["until"] = until.isoformat()
                payload["duration_seconds"] = int(command.duration.total_seconds())
        kind_map = {
            ControlKind.PAUSE: kinds.CONTROL_PAUSE,
            ControlKind.RESUME: kinds.CONTROL_RESUME,
            ControlKind.EXIT: kinds.CONTROL_EXIT,
        }
        control = await self._events.append(
            NewEvent(
                instance_id=self.instance_id,
                subject_id=subject.subject_id,
                kind=kind_map[command.kind],
                idempotency_key=key,
                payload=payload,
                occurred_at=inbound.received_at,
            )
        )
        if command.kind == ControlKind.PAUSE and command.duration is not None:
            until_iso = payload["until"]
            await self._jobs.enqueue(
                NewJob(
                    instance_id=self.instance_id,
                    subject_id=subject.subject_id,
                    kind=RESUME_JOB,
                    run_at=now + command.duration,
                    idempotency_key=kinds.key_resume_job(subject.subject_id, until_iso),
                )
            )
        ack = self._interaction.acknowledgement(command)
        if ack:
            await self._send(
                subject, ack, idempotency_key=kinds.key_ack(control.event.id), reply_to=inbound.external_id, author=kinds.AUTHOR_CONTROL
            )
        if command.kind == ControlKind.RESUME:
            await self._after_resume(subject, control.event)

    async def on_auto_resume(self, job: Job) -> Mapping[str, Any]:
        assert job.subject_id is not None
        subject = SubjectKey(job.instance_id, job.subject_id)
        res = await self._events.append(
            NewEvent(
                instance_id=self.instance_id,
                occurred_at=self._clock(),
                subject_id=subject.subject_id,
                kind=kinds.CONTROL_RESUME,
                idempotency_key=kinds.key_auto_resume(job.idempotency_key),
                payload={"auto": True, "job_id": job.id},
            )
        )
        await self._after_resume(subject, res.event)
        return {"created": res.created}

    async def _after_resume(self, subject: SubjectKey, resume_event: Event) -> None:
        """La pausa cancelo los pulsos. Al reanudar queda un pulso de reconexion: la politica decide
        en ese momento si escribe, calla o fija otro. Clave derivada del evento: reintentar no duplica."""
        if self._resume_pulse is None:
            return
        state = await self.state_of(subject)
        if not state.can_contact:
            return
        at = resume_event.occurred_at + self._resume_pulse
        await self.set_next_pulse(subject, at)

    async def reopen(self, subject_ref: str, *, token: str, reason: str = "") -> bool:
        """Reapertura por el operador tras una salida. No existe como comando del canal."""
        subject = await self._subjects.resolve(self.instance_id, subject_ref)
        res = await self._events.append(
            NewEvent(
                instance_id=self.instance_id,
                occurred_at=self._clock(),
                subject_id=subject.subject_id,
                kind=kinds.CONTROL_REOPEN,
                idempotency_key=kinds.key_reopen(subject.subject_id, token),
                payload={"reason": reason, "actor": "operator"},
            )
        )
        return res.created

    # ------------------------------------------------------------------ recuperacion al arrancar

    async def recover(self, *, since: datetime | None = None) -> int:
        """Entrega intenciones que quedaron sin envio y responde mensajes que quedaron sin turno
        (el proceso murio a mitad de camino y el canal no reentrega). Idempotente."""
        since = since or (self._clock() - timedelta(days=1))
        recovered = 0
        intents = await self._events.list(self.instance_id, kinds=[kinds.TURN_INTENT], since=since)
        for intent in intents:
            if not intent.payload.get("text") or intent.subject_id is None:
                continue
            if await self._events.by_key(self.instance_id, kinds.key_sent(intent.id)) is not None:
                continue
            subject = SubjectKey(self.instance_id, intent.subject_id)
            state = await self.state_of(subject)
            if state.terminated:
                continue
            log.warning("intencion %s sin envio: se entrega ahora", intent.id)
            await self._deliver_intent(subject, intent, state)
            recovered += 1

        # Mensajes registrados sin turno. Con agrupacion, el job pendiente los cubre solo; sin ella,
        # el ultimo mensaje sin cubrir de cada sujeto se responde ahora (un turno cubre los anteriores).
        messages = await self._events.list(self.instance_id, kinds=[kinds.MESSAGE_RECEIVED], since=since)
        latest_by_subject: dict[str, Event] = {}
        for m in messages:
            if m.subject_id is not None:
                latest_by_subject[m.subject_id] = m
        for subject_id, message in latest_by_subject.items():
            subject = SubjectKey(self.instance_id, subject_id)
            if await self._covered(subject, message):
                continue
            if self._group_window is not None:
                if not await self._pending_turn_job(subject):
                    await self._schedule_turn(subject, message)
                    recovered += 1
                continue
            state = await self.state_of(subject)
            if state.terminated:
                continue
            log.warning("mensaje %s sin turno: se responde ahora", message.id)
            await self._run_turn(subject, trigger_event=message, inbound=_inbound_from(message), state=state)
            recovered += 1
        return recovered

    # ------------------------------------------------------------------ estado y contexto

    async def state_of(self, subject: SubjectKey, now: datetime | None = None) -> SubjectState:
        controls = await self._events.list(self.instance_id, subject_id=subject.subject_id, kinds=kinds.CONTROL_KINDS)
        return fold_state(controls, now or self._clock())

    RECENT_CAP = 600  # tope de eventos que se leen para armar la ventana (protege contra semanas de eventos sin mensajes)

    async def _recent_for_turn(self, subject: SubjectKey) -> Sequence[Event]:
        """El contexto del turno contiene los ultimos `recent_messages` mensajes (de la persona y del agente) con todo lo que
        paso entre ellos, para que los eventos tecnicos no desplacen la conversacion. Nunca menos que los ultimos
        `recent_window` eventos."""
        sid = subject.subject_id
        base = await self._events.list(self.instance_id, subject_id=sid, limit=self._recent_window)
        if not self._recent_messages:
            return base
        msgs = await self._events.list(self.instance_id, subject_id=sid, kinds=[kinds.MESSAGE_RECEIVED, kinds.MESSAGE_SENT], limit=self._recent_messages)
        if not msgs or (base and msgs[0].occurred_at >= base[0].occurred_at):
            return base
        return await self._events.list(self.instance_id, subject_id=sid, since=msgs[0].occurred_at, limit=self.RECENT_CAP)

    async def _context(
        self,
        subject: SubjectKey,
        *,
        trigger: str,
        inbound: InboundEvent | None,
        paused: bool,
        trigger_event: Event | None = None,
    ) -> TurnContext:
        recent = await self._recent_for_turn(subject)
        turn_messages = subject_messages_for(recent, trigger_event) if trigger_event is not None else ()
        return TurnContext(
            subject=subject,
            trigger=trigger,  # type: ignore[arg-type]
            now=self._clock(),
            inbound=inbound,
            recent=recent,
            paused=paused,
            pending_pulse_at=await self.pending_pulse_at(subject),
            turn_messages=turn_messages,
            trigger_event_id=trigger_event.id if trigger_event is not None else None,
        )

    # ------------------------------------------------------------------ actuar

    async def _act(
        self,
        subject: SubjectKey,
        output: TurnOutput,
        *,
        ctx: TurnContext,
        trigger_event: Event,
        reply_to: str | None,
        state: SubjectState,
    ) -> None:
        intent_payload: dict[str, Any] = {
            "text": output.text,
            "next_pulse_at": output.next_pulse_at.isoformat() if output.next_pulse_at else None,
            "reply_to": reply_to,
            "messages": [e.id for e in ctx.turn_messages],
            **dict(output.intent),
        }
        intent = await self._events.append(
            NewEvent(
                instance_id=self.instance_id,
                occurred_at=self._clock(),
                subject_id=subject.subject_id,
                kind=kinds.TURN_INTENT,
                idempotency_key=kinds.key_intent(trigger_event.id),
                payload=intent_payload,
            )
        )
        if self._after_intent is not None and intent.created:
            try:
                await self._after_intent(intent.event)
            except Exception:  # noqa: BLE001 - el gancho avisa; no decide ni corta el turno
                log.exception("el gancho after_intent fallo; el turno sigue")
        await self._deliver_intent(subject, intent.event, state)
        await self._run_after_turn(subject, ctx.trigger, trigger_event, intent.event, ctx.turn_messages)

    async def _run_after_turn(
        self, subject: SubjectKey, trigger: str, trigger_event: Event, intent: Event, turn_messages: Sequence[Event]
    ) -> None:
        if not self._after_turn:
            return
        record = TurnRecord(
            subject_id=subject.subject_id,
            instance_id=self.instance_id,
            trigger=trigger,
            trigger_event=trigger_event,
            intent=intent,
            subject_messages=list(turn_messages),
            now=self._clock(),
        )
        failed: list[str] = []
        for hook in self._after_turn:
            try:
                await hook(record)
            except Exception as exc:  # noqa: BLE001 - un gancho no frena a los demas; el turno ya se entrego
                log.exception("un gancho after_turn fallo; el turno ya se entrego")
                failed.append(f"{getattr(hook, '__qualname__', hook)}: {type(exc).__name__}")
        if failed:
            # F0.8: antes un gancho fallido (episodio, cierre de sesion, deteccion) se perdia para siempre.
            # Ahora el job del turno falla DESPUES de entregar y se reintenta: el reintento encuentra la
            # intencion entregada y vuelve a correr los ganchos, que son idempotentes por la intencion.
            raise AfterTurnIncomplete("; ".join(failed))

    async def _deliver_intent(self, subject: SubjectKey, intent: Event, state: SubjectState) -> None:
        """Ejecuta una intencion ya registrada. Es idempotente: se puede llamar tras un reinicio."""
        text = intent.payload.get("text")
        if text:
            await self._send(subject, text, idempotency_key=kinds.key_sent(intent.id), reply_to=intent.payload.get("reply_to"))
        if state.paused or state.terminated:
            return  # la pausa y la salida ya cancelaron los pulsos; no se programa nada
        next_pulse = intent.payload.get("next_pulse_at")
        await self.set_next_pulse(subject, datetime.fromisoformat(next_pulse) if next_pulse else None)

    async def _resume_turn_if_incomplete(self, subject: SubjectKey, trigger_event: Event, inbound: InboundEvent | None) -> None:
        intent = await self._events.by_key(self.instance_id, kinds.key_intent(trigger_event.id))
        if intent is None:
            if trigger_event.kind == kinds.MESSAGE_RECEIVED:
                # Agrupado: otra intencion ya lo cubrio, o un job pendiente lo cubrira.
                if await self._covered(subject, trigger_event) or (self._group_window is not None and await self._pending_turn_job(subject)):
                    return
            # Se registro la entrada pero no se alcanzo a decidir: no hubo intencion, no hubo envio.
            # El turno se rehace desde cero con la misma clave de intencion, asi que sigue siendo unico.
            state = await self.state_of(subject)
            if state.terminated:
                return
            await self._run_turn(subject, trigger_event=trigger_event, inbound=inbound, state=state)
            return
        if not intent.payload.get("text") or await self._events.by_key(self.instance_id, kinds.key_sent(intent.id)) is not None:
            # Entregado (o sin texto que enviar). Lo que pudo quedar pendiente son los ganchos: se repiten,
            # son idempotentes por la intencion (F0.8: antes una caida entre el envio y los ganchos perdia el
            # episodio, el cierre de sesion y la deteccion). El pulso siguiente tambien es idempotente.
            state = await self.state_of(subject)
            recent = await self._events.list(self.instance_id, subject_id=subject.subject_id, limit=self._recent_window)
            latest = [e for e in recent if e.kind == kinds.TURN_INTENT]
            next_pulse = intent.payload.get("next_pulse_at")
            # El pulso se reaplica solo si esta es la ultima intencion del sujeto y el pulso sigue en el
            # futuro: una reentrega vieja nunca pisa el pulso que fijo un turno posterior.
            if (not (state.paused or state.terminated) and latest and latest[-1].id == intent.id
                    and (next_pulse is None or datetime.fromisoformat(next_pulse) > self._clock())):
                await self.set_next_pulse(subject, datetime.fromisoformat(next_pulse) if next_pulse else None)
            trigger = _trigger_of(trigger_event)
            await self._run_after_turn(subject, trigger, trigger_event, intent, subject_messages_for(recent, trigger_event))
            return
        state = await self.state_of(subject)
        await self._deliver_intent(subject, intent, state)
        recent = await self._events.list(self.instance_id, subject_id=subject.subject_id, limit=self._recent_window)
        trigger = _trigger_of(trigger_event)
        await self._run_after_turn(subject, trigger, trigger_event, intent, subject_messages_for(recent, trigger_event))

    async def _send(
        self,
        subject: SubjectKey,
        text: str,
        *,
        idempotency_key: str,
        reply_to: str | None,
        author: str = kinds.AUTHOR_POLICY,
    ) -> bool:
        """Envia y registra. False si ya se habia enviado (misma clave): nada sale dos veces."""
        if await self._events.by_key(self.instance_id, idempotency_key) is not None:
            return False
        ref = await self._subjects.ref_for(subject)
        channel_id = await self._channel.send(
            OutboundMessage(
                instance_id=self.instance_id,
                subject_ref=ref,
                text=text,
                reply_to_external_id=reply_to,
                idempotency_key=idempotency_key,
            )
        )
        await self._events.append(
            NewEvent(
                instance_id=self.instance_id,
                occurred_at=self._clock(),
                subject_id=subject.subject_id,
                kind=kinds.MESSAGE_SENT,
                idempotency_key=idempotency_key,
                payload={"text": text, "channel_message_id": channel_id, "channel": self._channel.name, "author": author},
            )
        )
        return True


def _inbound_from(message: Event) -> InboundEvent:
    """Reconstruye el evento de canal desde el `message.received` registrado (para turnos diferidos)."""
    p = message.payload
    return InboundEvent(
        instance_id=message.instance_id,
        subject_ref=str(p.get("subject_ref") or ""),
        external_id=message.idempotency_key[len("in:") :] if message.idempotency_key.startswith("in:") else message.idempotency_key,
        text=str(p.get("text") or ""),
        received_at=message.occurred_at,
        kind=str(p.get("kind") or "message"),
        sender_ref=p.get("sender_ref"),
        conversation_ref=p.get("conversation_ref"),
    )


def _refs(inbound: InboundEvent) -> dict[str, str]:
    """Quien hablo y donde, si el canal lo sabe (se omite lo que no trae)."""
    out: dict[str, str] = {}
    if inbound.sender_ref:
        out["sender_ref"] = inbound.sender_ref
    if inbound.conversation_ref:
        out["conversation_ref"] = inbound.conversation_ref
    return out


def _trigger_of(event: Event) -> str:
    if event.kind == kinds.MESSAGE_RECEIVED:
        return "message"
    return "pulse"


def pulse_in(minutes: float, *, now: datetime | None = None) -> datetime:
    """Ayuda para politicas de turno: un pulso a N minutos de ahora."""
    return (now or utcnow()) + timedelta(minutes=minutes)


