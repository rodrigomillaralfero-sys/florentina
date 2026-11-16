"""Cortacircuito de costo con alcance "all".

Toda llamada al modelo, de cualquier pieza, deja su gasto en `model.spend`; el tope suma todo y salta tambien sin turnos
(una pieza que gasta de noche). Con el cortacircuito activo ninguna llamada sale, hasta `release`. La alarma sale como
alerta critica al operador.
"""

from __future__ import annotations

import json

import pytest

from adapters.fake_model import ScriptedModelAdapter
from core.events import kinds
from core.health.alerts import Alerter
from core.health.breakers import BreakerLimits, Breakers
from core.health.spend import MeteredAdapter
from core.interfaces.model_adapter import ModelMessage, ModelRequest, ModelResponse, ModelRole, ModelUnavailable
from core.storage.memory import InMemoryEventStore
from tests.conftest import Clock

INST = "inst-gasto"


class _Sink:
    name = "prueba"

    def __init__(self):
        self.sent = []

    async def emit(self, text: str) -> None:
        self.sent.append(text)


class _Costly(ScriptedModelAdapter):
    """Cada llamada cuesta `cost` dolares (como lo reporta OpenRouter, en microdolares)."""

    def __init__(self, cost: float):
        super().__init__(lambda r: json.dumps({"ok": True}) if r.schema else "texto")
        self._cost = int(cost * 1_000_000)

    async def complete(self, request):
        res = await super().complete(request)
        return ModelResponse(text=res.text, identity=res.identity, usage={"cost_microusd": self._cost})

    async def complete_structured(self, request):
        res = await super().complete_structured(request)
        return ModelResponse(text=res.text, identity=res.identity, structured=res.structured, usage={"cost_microusd": self._cost})


def _rig(cost=1.5, limit=4.0, scope="all"):
    clock = Clock()
    events = InMemoryEventStore()
    sink = _Sink()
    alerter = Alerter(instance_id=INST, sink=sink, events=events, clock=clock)
    br = Breakers(instance_id=INST, events=events, limits=BreakerLimits(cost_usd_per_day=limit, cost_scope=scope), alerter=alerter, clock=clock)
    ad = MeteredAdapter(_Costly(cost), instance_id=INST, events=events, breakers=br, clock=clock)
    return clock, events, sink, br, ad


def _req(role=ModelRole.CLASSIFIER, job="pendientes.revision"):
    return ModelRequest(role=role, messages=[ModelMessage("user", "x")], schema={"type": "object"}, metadata={"job": job})


async def test_el_tope_suma_toda_pieza_y_salta_sin_turnos_y_corta_todo():
    clock, events, sink, br, ad = _rig(cost=1.5)
    await ad.complete_structured(_req())  # proceso de fondo 1,50
    await ad.complete(_req(ModelRole.CONVERSATION, "turn"))  # turno 1,50 -> 3,00
    assert await br.blocked() is None
    await ad.complete_structured(_req(ModelRole.CLASSIFIER, "patrones"))  # otro proceso 1,50 -> 4,50: salta
    assert await br.blocked() == "cost_usd_per_day"
    spends = await events.list(INST, kinds=[kinds.MODEL_SPEND])
    assert [e.payload["role"] for e in spends] == ["classifier", "conversation", "classifier"] and all("content" not in e.payload for e in spends)
    assert sink.sent and "cortacircuito: cost_usd_per_day" in sink.sent[-1] and "ops release" in sink.sent[-1], "la alarma sale como alerta critica"
    for role in (ModelRole.CONVERSATION, ModelRole.CLASSIFIER):
        with pytest.raises(ModelUnavailable):
            await ad.complete(_req(role))  # ninguna pieza llama al modelo
    with pytest.raises(ModelUnavailable):
        await ad.embed(["hola"])
    assert len(await events.list(INST, kinds=[kinds.MODEL_SPEND])) == 3, "lo bloqueado no gasta ni se registra"
    # el turno tambien lo ve
    assert (await br.before_turn(trigger="message")).startswith("cortacircuito activo (cost_usd_per_day)")
    # liberado, los contadores parten de cero
    clock.advance(minutes=5)
    assert await br.release(note="revisado")
    assert await br.blocked() is None
    await ad.complete(_req())
    assert (await br.counters(floor=(await br.state()).released_at)).cost_usd_last_day == pytest.approx(1.5)


async def test_el_dia_es_movil_y_lo_viejo_no_cuenta():
    clock, events, sink, br, ad = _rig(cost=1.5)
    await ad.complete(_req())
    await ad.complete(_req())
    clock.advance(hours=25)
    await ad.complete(_req())
    await ad.complete(_req())
    assert await br.blocked() is None and (await br.counters()).cost_usd_last_day == pytest.approx(3.0)


async def test_con_alcance_turno_queda_como_antes():
    clock, events, sink, br, ad = _rig(cost=3.0, scope="turn")
    await ad.complete(_req())
    await ad.complete(_req())
    await br.check_cost()
    assert await br.blocked() is None, "con alcance turno, lo que llama fuera del turno no cuenta"
    assert (await br.counters()).cost_usd_last_day == 0


