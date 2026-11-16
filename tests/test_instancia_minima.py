"""FL0 — prueba de transferencia: una instancia creada solo llenando el contrato, sin tocar `core/`.

Lo que se verifica de punta a punta, en memoria:
* se arma con `core.assembly.assemble` y nada mas;
* conversa con el modelo y cita lo que trae el conocimiento base;
* recuerda por persona: lo de una persona nunca aparece en otra;
* los comandos mecanicos corren antes del modelo;
* el tope de costo corta el turno y avisa;
* si el modelo falla, responde el respaldo y la intencion queda marcada.
"""

from __future__ import annotations

import ast
import sys
from datetime import datetime, timezone
from pathlib import Path

from adapters.fake_model import ScriptedModelAdapter
from core.assembly import assemble, memory_stores
from core.events import kinds
from core.interfaces.channel import InboundEvent
from core.interfaces.model_adapter import ModelRequest, ModelResponse, ModelUnavailable
from instances import minima
from tests.conftest import FakeChannel

ROOT = Path(__file__).resolve().parents[1]
T0 = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)
CONFIG = {"engine": {"recent_window": 3}, "knowledge": {"min_similarity": 0.1}, "memory": {"recall": {"episodes": 4}}}


class _Sink:
    name = "prueba"

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def emit(self, text: str) -> None:
        self.sent.append(text)


def _msg(who: str, text: str, n: int) -> InboundEvent:
    return InboundEvent(instance_id=minima.INSTANCE_ID, subject_ref=who, external_id=f"{who}-{n}", text=text, received_at=T0)


def _build(adapter=None, config=CONFIG, sink=None):
    channel = FakeChannel()
    adapter = adapter or ScriptedModelAdapter(lambda r: "ok", dim=64, semantic=True)
    c = minima.contract(channel=channel, adapter=adapter, config=config)
    c.alert_sink = sink
    return assemble(c, memory_stores()), channel, adapter


def _system(req: ModelRequest) -> str:
    return req.messages[0].content


def test_la_instancia_minima_solo_importa_el_nucleo():
    src = (ROOT / "instances" / "minima" / "__init__.py").read_text(encoding="utf-8")
    mods = {n.module for n in ast.walk(ast.parse(src)) if isinstance(n, ast.ImportFrom) and n.module}
    assert mods and all(m == "__future__" or m == "typing" or m.startswith("core.") for m in mods), mods


async def test_se_arma_y_conversa_sin_piezas_ajenas():
    app, channel, adapter = _build()
    await app.engine.receive(_msg("ana", "hola", 1))
    assert [m.text for m in channel.sent] == ["ok"]
    intent = (await app.stores.events.list(minima.INSTANCE_ID, kinds=[kinds.TURN_INTENT]))[-1]
    assert intent.payload["policy"] == "model" and intent.payload["model"]["provider"] == "fake"
    forbidden = ("instances.vera", "core.goals", "core.portfolio", "core.strategist", "core.overseer", "core.impulse",
                 "core.requests", "core.attempts", "core.critic", "core.life", "core.experiment", "core.hypotheses")
    assert not [m for m in sys.modules if m.startswith(forbidden)]


async def test_recuerda_por_persona_y_no_mezcla():
    app, channel, adapter = _build()
    await app.engine.receive(_msg("ana", "el caso de Kaufmann quedo con la prenda pendiente de firma", 1))
    await app.engine.receive(_msg("bea", "hola, tengo una duda de tarifas", 1))
    await app.engine.receive(_msg("ana", "gracias, sigo con otra cosa", 2))
    await app.engine.receive(_msg("ana", "que paso con Kaufmann y la prenda", 3))
    assert "Kaufmann" in _system(adapter.calls[-1]), "la memoria de Ana trae su caso aunque ya salio del hilo"
    await app.engine.receive(_msg("bea", "que paso con Kaufmann y la prenda", 2))
    assert "Kaufmann" not in _system(adapter.calls[-1]), "lo de Ana nunca aparece en la conversacion de Bea"
    ana = await app.stores.subjects.resolve(minima.INSTANCE_ID, "ana")
    bea = await app.stores.subjects.resolve(minima.INSTANCE_ID, "bea")
    assert len(await app.stores.episodes.list(minima.INSTANCE_ID, ana.subject_id)) == 3
    assert len(await app.stores.episodes.list(minima.INSTANCE_ID, bea.subject_id)) == 2


async def test_el_conocimiento_base_entra_al_prompt(tmp_path: Path):
    (tmp_path / "alzamiento.md").write_text("# Alzamiento\n\nEl alzamiento de prenda requiere la carta de la financiera.", encoding="utf-8")
    app, channel, adapter = _build()
    report = await app.memory.loader.load_dir(tmp_path)
    assert report is not None
    await app.engine.receive(_msg("ana", "que requiere el alzamiento de prenda", 1))
    system = _system(adapter.calls[-1])
    assert "[alzamiento.md]" in system and "carta de la financiera" in system


async def test_los_comandos_corren_antes_del_modelo():
    app, channel, adapter = _build()
    await app.engine.receive(_msg("ana", "/pausa 2h", 1))
    assert channel.sent[-1].text == "Listo, quedo en pausa por 2 horas." and adapter.calls == []
    await app.engine.receive(_msg("ana", "/baja", 2))
    assert channel.sent[-1].text == "Baja registrada. Ya no te escribo."
    await app.engine.receive(_msg("ana", "sigues ahi?", 3))
    assert adapter.calls == [] and len(channel.sent) == 2, "tras la baja no se responde"


class _Caro(ScriptedModelAdapter):
    async def complete(self, request: ModelRequest) -> ModelResponse:
        res = await super().complete(request)
        return ModelResponse(text=res.text, identity=res.identity, usage={"output_tokens": 1, "cost_microusd": 600_000})


async def test_el_tope_de_costo_corta_y_avisa():
    sink = _Sink()
    config = {**CONFIG, "health": {"limits": {"cost_usd_per_day": 1.0}}}
    app, channel, adapter = _build(adapter=_Caro(lambda r: "ok", dim=64, semantic=True), config=config, sink=sink)
    await app.engine.receive(_msg("ana", "uno", 1))
    await app.engine.receive(_msg("ana", "dos", 2))  # 1,20 > 1,00: salta despues de esta llamada
    assert await app.health.breakers.blocked() == "cost_usd_per_day"
    calls = len(adapter.calls)
    await app.engine.receive(_msg("ana", "tres", 3))
    assert len(adapter.calls) == calls, "con el tope activo no sale ninguna llamada"
    last = (await app.stores.events.list(minima.INSTANCE_ID, kinds=[kinds.TURN_INTENT]))[-1]
    assert last.payload["policy"] == "guard" and not last.payload.get("text")
    assert any("cost_usd_per_day" in s for s in sink.sent), "el operador recibe la alerta"


class _Caido(ScriptedModelAdapter):
    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls.append(request)
        raise ModelUnavailable("timeout")


async def test_si_el_modelo_falla_responde_el_respaldo():
    app, channel, adapter = _build(adapter=_Caido(lambda r: "", dim=64, semantic=True))
    await app.engine.receive(_msg("ana", "hola", 1))
    assert channel.sent[-1].text == "No pude responder ahora. Intenta de nuevo en un rato."
    intent = (await app.stores.events.list(minima.INSTANCE_ID, kinds=[kinds.TURN_INTENT]))[-1]
    assert intent.payload["failed"] is True and intent.payload["error"] == "ModelUnavailable"


class _Acta:
    """Consolidador de prueba: lo que la instancia pondria al cerrar una sesion (p. ej. el acta)."""

    def __init__(self) -> None:
        self.runs: list[tuple[str, int]] = []

    async def run(self, subject_id, episodes, *, now):
        self.runs.append((subject_id, len(episodes)))

        class _R:
            failed = None

            def as_payload(self):
                return {"episodios": len(episodes)}

        return _R()


async def test_la_sesion_se_cierra_por_pausa_y_corre_el_consolidador_de_la_instancia():
    from datetime import timedelta

    acta = _Acta()
    channel = FakeChannel()
    c = minima.contract(channel=channel, adapter=ScriptedModelAdapter(lambda r: "ok", dim=64, semantic=True), config=CONFIG)
    c.consolidator = acta
    app = assemble(c, memory_stores())
    await app.engine.receive(_msg("ana", "uno", 1))
    await app.engine.receive(_msg("ana", "dos", 2))
    assert await app.scheduler.tick(T0 + timedelta(minutes=10)) == 0, "antes de la pausa no se cierra"
    await app.scheduler.tick(datetime.now(timezone.utc) + timedelta(hours=1))
    ana = await app.stores.subjects.resolve(minima.INSTANCE_ID, "ana")
    assert acta.runs == [(ana.subject_id, 2)], "una sola acta por sesion, con sus dos turnos"
    closed = await app.stores.events.list(minima.INSTANCE_ID, kinds=[kinds.MEMORY_SESSION_CLOSED])
    assert len(closed) == 1 and closed[0].payload["consolidation"] == {"episodios": 2}
