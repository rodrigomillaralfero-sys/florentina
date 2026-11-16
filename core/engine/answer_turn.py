"""Politica de turno que responde: recupera, arma el prompt, llama al modelo y deja la intencion.

    recuperar (episodios de la persona + conocimiento) -> prompt de la instancia -> modelo -> intencion

El motor no decide que se dice ni como: eso lo pone la instancia en su `AnswerPromptBuilder`.
Aqui solo hay mecanica:

* La recuperacion corre antes del modelo. Si el proveedor de embeddings falla, el turno sigue con
  el hilo y la falta queda en la intencion.
* Si el modelo falla, la intencion queda como fallida (`failed`) con el error normalizado, sin
  texto o con el texto de respaldo que la instancia defina. Un turno nunca se cae por el modelo.
* La intencion lleva lo que el cortacircuito necesita contar (`policy: model`, `attempts`,
  `usage`) y la identidad del modelo que respondio. Sin contenido de la recuperacion: solo conteos.
* Sin iniciativa propia por defecto: un pulso no escribe salvo que la instancia lo pida
  (`initiate` del builder devuelve None = callar).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol, Sequence

from core.engine.turn import TurnContext, TurnOutput
from core.interfaces.model_adapter import ModelAdapter, ModelError, ModelMessage, ModelRequest, ModelRole
from core.memory.recall import Recall, RecallBundle

log = logging.getLogger(__name__)


class AnswerPromptBuilder(Protocol):
    def respond(self, ctx: TurnContext, recall: RecallBundle) -> Sequence[ModelMessage]:
        """Mensajes para responder a lo que la persona escribio."""
        ...

    def initiate(self, ctx: TurnContext, recall: RecallBundle) -> Sequence[ModelMessage] | None:
        """Mensajes para abrir contacto en un pulso. None = no escribir."""
        ...

    def fallback(self, ctx: TurnContext, error: ModelError) -> str | None:
        """Texto si el modelo falla. None = no escribir nada."""
        ...


@dataclass(frozen=True)
class AnswerRules:
    role: ModelRole = ModelRole.CONVERSATION
    temperature: float | None = None
    max_tokens: int | None = None


class AnswerTurnPolicy:
    def __init__(self, *, adapter: ModelAdapter, prompts: AnswerPromptBuilder, recall: Recall | None = None,
                 rules: AnswerRules | None = None) -> None:
        self._adapter = adapter
        self._prompts = prompts
        self._recall = recall
        self.rules = rules or AnswerRules()

    async def respond(self, ctx: TurnContext) -> TurnOutput:
        bundle = await self._bundle(ctx)
        return await self._answer(ctx, bundle, self._prompts.respond(ctx, bundle))

    async def initiate(self, ctx: TurnContext) -> TurnOutput:
        bundle = await self._bundle(ctx)
        messages = self._prompts.initiate(ctx, bundle)
        if not messages:
            return TurnOutput(text=None, intent={"policy": "answer", "trigger": ctx.trigger, "reason": "sin iniciativa"})
        return await self._answer(ctx, bundle, messages)

    async def _bundle(self, ctx: TurnContext) -> RecallBundle:
        if self._recall is None:
            return RecallBundle()
        try:
            return await self._recall.for_turn(ctx)
        except Exception:  # noqa: BLE001 - la memoria nunca tumba un turno
            log.exception("recuperacion fallo; el turno sigue con el hilo")
            return RecallBundle(enabled=True, embedding_failed=True)

    async def _answer(self, ctx: TurnContext, bundle: RecallBundle, messages: Sequence[ModelMessage]) -> TurnOutput:
        base: dict[str, Any] = {"policy": "model", "trigger": ctx.trigger, "attempts": 1, "recall": bundle.summary()}
        req = ModelRequest(role=self.rules.role, messages=list(messages), temperature=self.rules.temperature,
                           max_tokens=self.rules.max_tokens, metadata={"job": "turn"})
        try:
            res = await self._adapter.complete(req)
        except ModelError as exc:
            log.warning("modelo no disponible para el turno: %s", type(exc).__name__)
            usage = dict(getattr(exc, "usage", None) or {})
            return TurnOutput(text=self._prompts.fallback(ctx, exc),
                              intent={**base, "failed": True, "error": type(exc).__name__, "usage": usage})
        text = (res.text or "").strip() or None
        ident = res.identity
        return TurnOutput(text=text, intent={**base, "usage": dict(res.usage),
                                             "model": {"provider": ident.provider, "model": ident.model, "version": ident.version}})
