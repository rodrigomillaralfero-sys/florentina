"""Instancia minima: la prueba de que el motor se arma solo llenando el contrato.

No toca `core/`. Entrega comandos, un armador de prompts de pocas lineas y su configuracion; el
resto (memoria por persona, conocimiento, salud, topes, motor) lo pone `core.assembly`.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from core.assembly import InstanceContract
from core.engine.commands import CommandPolicy
from core.engine.thread import thread_items
from core.engine.turn import TurnContext
from core.interfaces.channel import Channel
from core.interfaces.model_adapter import ModelAdapter, ModelError, ModelMessage
from core.memory.recall import RecallBundle

INSTANCE_ID = "minima"

SYSTEM = "Eres una asistente de prueba. Responde breve, usando solo lo que aparece en CONOCIMIENTO y en MEMORIA."

ACKS = {
    "pause": "Listo, quedo en pausa hasta que escribas /reanudar.",
    "pause_for": "Listo, quedo en pausa por {duracion}.",
    "resume": "De vuelta.",
    "exit": "Baja registrada. Ya no te escribo.",
}


class MinimaPrompts:
    def respond(self, ctx: TurnContext, recall: RecallBundle) -> Sequence[ModelMessage]:
        blocks = [SYSTEM]
        if recall.knowledge:
            blocks.append("CONOCIMIENTO:\n" + "\n".join(f"- [{h.chunk.source}] {h.chunk.text}" for h in recall.knowledge))
        if recall.episodes:
            blocks.append("MEMORIA:\n" + "\n".join(f"- {r.episode.text}" for r in recall.episodes))
        out = [ModelMessage("system", "\n\n".join(blocks))]
        for item in thread_items(ctx.recent):
            if item.kind == "subject" and item.text:
                out.append(ModelMessage("user", item.text))
            elif item.kind == "agent" and item.text:
                out.append(ModelMessage("assistant", item.text))
        if not out[-1].role == "user":
            out.append(ModelMessage("user", ctx.subject_text))
        return out

    def initiate(self, ctx: TurnContext, recall: RecallBundle) -> Sequence[ModelMessage] | None:
        return None  # sin iniciativa propia

    def fallback(self, ctx: TurnContext, error: ModelError) -> str | None:
        return "No pude responder ahora. Intenta de nuevo en un rato."


def contract(*, channel: Channel, adapter: ModelAdapter | None, config: Mapping[str, Any] | None = None) -> InstanceContract:
    return InstanceContract(instance_id=INSTANCE_ID, interaction=CommandPolicy(exit="/baja", acks=ACKS), prompts=MinimaPrompts(),
                            channel=channel, adapter=adapter, config=dict(config or {}))
