"""Catalogo de tipos de evento que el motor escribe y las claves de idempotencia que usa.

Las claves se derivan siempre de un identificador que ya existia antes del
efecto (id externo del canal, id del evento disparador, clave del job). Asi
un reinicio a mitad de turno encuentra la clave y no duplica.
"""

from __future__ import annotations

MESSAGE_RECEIVED = "message.received"
CHANNEL_EVENT = "channel.event"  # algo del canal que no es texto del sujeto (p. ej. abrir el chat)
TURN_INTENT = "turn.intent"  # registro de intencion: se escribe ANTES de enviar
MESSAGE_SENT = "message.sent"
PULSE_SCHEDULED = "pulse.scheduled"
PULSE_FIRED = "pulse.fired"
PULSE_SKIPPED = "pulse.skipped"
CONTROL_PAUSE = "control.pause"
CONTROL_RESUME = "control.resume"
CONTROL_EXIT = "control.exit"
CONTROL_REOPEN = "control.reopen"  # solo el operador, desde fuera del canal: vuelve a abrir tras una salida

CONTROL_KINDS = (CONTROL_PAUSE, CONTROL_RESUME, CONTROL_EXIT, CONTROL_REOPEN)

# Salud tecnica (F0.2b). Son eventos de la instancia, sin sujeto: el estado del
# cortacircuito es el pliegue de tripped/released, igual que pausa y salida.
HEALTH_ALERT = "health.alert"  # una alerta que salio (o intento salir) al operador
BREAKER_TRIPPED = "breaker.tripped"  # un tope se supero: el motor deja de llamar al modelo
BREAKER_RELEASED = "breaker.released"  # el operador libero el cortacircuito
MODEL_SPEND = "model.spend"  # cada llamada al modelo con su costo (rol y pieza, sin contenido): lo suma el tope de costo diario

BREAKER_KINDS = (BREAKER_TRIPPED, BREAKER_RELEASED)

# Memoria (F0.3). El cierre de sesion es historia de la persona: cuando se cerro y que salio.
MEMORY_SESSION_CLOSED = "memory.session_closed"

# Quien escribio un `message.sent`: la politica de turno o el backend (confirmaciones).
AUTHOR_POLICY = "policy"
AUTHOR_CONTROL = "control"


def key_received(external_id: str) -> str:
    return f"in:{external_id}"


def key_intent(trigger_event_id: str) -> str:
    return f"intent:{trigger_event_id}"


def key_sent(intent_event_id: str) -> str:
    return f"out:{intent_event_id}"


def key_control(external_id: str) -> str:
    return f"ctl:{external_id}"


def key_ack(control_event_id: str) -> str:
    return f"ack:{control_event_id}"


def key_pulse_job(subject_id: str, at_iso: str) -> str:
    return f"pulse:{subject_id}:{at_iso}"


def key_pulse_scheduled(job_key: str) -> str:
    return f"sched:{job_key}"


def key_pulse_fired(job_key: str) -> str:
    return f"fired:{job_key}"


def key_resume_job(subject_id: str, at_iso: str) -> str:
    return f"resume:{subject_id}:{at_iso}"


def key_auto_resume(job_key: str) -> str:
    return f"auto:{job_key}"


def key_reopen(subject_id: str, token: str) -> str:
    return f"reopen:{subject_id}:{token}"


def key_alert(dedupe_key: str, at_iso: str) -> str:
    return f"alert:{dedupe_key}:{at_iso}"


def key_breaker_trip(code: str, at_iso: str) -> str:
    return f"trip:{code}:{at_iso}"


def key_breaker_release(at_iso: str) -> str:
    return f"release:{at_iso}"


def key_turn_job(subject_id: str, message_event_id: str) -> str:
    return f"turn:{subject_id}:{message_event_id}"


def key_session_close_job(subject_id: str, intent_event_id: str) -> str:
    return f"session:{subject_id}:{intent_event_id}"


def key_session_closed(job_key: str) -> str:
    return f"closed:{job_key}"


OPERATOR_CHANGE = "operator.change"  # bitacora del operador: un ajuste a prompts, config, modelos o umbrales


def key_operator_change(token: str) -> str:
    return f"opchg:{token}"
