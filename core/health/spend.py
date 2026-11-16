"""Medidor de gasto (cortacircuito de costo con alcance "all").

Envuelve al adaptador de modelos: cada llamada, de cualquier pieza, deja un registro `model.spend` con su rol, la pieza
que la pidio (el `job` de la metadata) y su costo; despues mira el tope de costo diario. Con el cortacircuito activo,
ninguna llamada sale: levanta `ModelUnavailable` y cada pieza usa el respaldo que ya tiene ante una falla del modelo.
Sin contenido: ni mensajes ni salida.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Callable, Mapping, Sequence

from core.events import kinds
from core.events.store import EventStore, NewEvent, utcnow
from core.interfaces.model_adapter import EmbeddingResponse, ModelAdapter, ModelError, ModelIdentity, ModelRequest, ModelResponse, ModelRole, ModelUnavailable

log = logging.getLogger(__name__)


class MeteredAdapter:
    def __init__(self, inner: ModelAdapter, *, instance_id: str, events: EventStore, breakers: Any,
                 clock: Callable[[], datetime] = utcnow) -> None:
        self._inner = inner
        self.instance_id = instance_id
        self._events = events
        self._breakers = breakers
        self._clock = clock

    def __getattr__(self, name: str) -> Any:  # lo demas del adaptador (configuracion, saldo) pasa tal cual
        return getattr(self._inner, name)

    def identity(self, role: ModelRole) -> ModelIdentity:
        return self._inner.identity(role)

    async def _gate(self, what: str) -> None:
        code = await self._breakers.blocked()
        if code:
            raise ModelUnavailable(f"cortacircuito activo ({code}): no se llama al modelo ({what})")

    async def _record(self, role: str, job: str, usage: Mapping[str, Any]) -> None:
        cost = int((usage or {}).get("cost_microusd") or 0)
        now = self._clock()
        try:
            await self._events.append(NewEvent(instance_id=self.instance_id, kind=kinds.MODEL_SPEND, idempotency_key=f"spend:{uuid.uuid4().hex}",
                                               occurred_at=now, payload={"role": role, "job": job, "cost_microusd": cost}))
            await self._breakers.check_cost(now)
        except Exception:  # noqa: BLE001 - el medidor no tumba la llamada que ya se hizo
            log.exception("registro de gasto no escrito")

    async def _call(self, role: str, job: str, fn, *args):
        await self._gate(role)
        try:
            res = await fn(*args)
        except ModelError as exc:
            usage = getattr(exc, "usage", None)
            if usage:  # 11-11 (revision en frio): una salida invalida igual se cobro; cuenta para el tope
                await self._record(role, job, usage)
            raise
        await self._record(role, job, res.usage)
        return res

    async def complete(self, request: ModelRequest) -> ModelResponse:
        role = str(getattr(request.role, "value", request.role))
        return await self._call(role, str((request.metadata or {}).get("job") or ""), self._inner.complete, request)

    async def complete_structured(self, request: ModelRequest) -> ModelResponse:
        role = str(getattr(request.role, "value", request.role))
        return await self._call(role, str((request.metadata or {}).get("job") or ""), self._inner.complete_structured, request)

    async def embed(self, texts: Sequence[str]) -> EmbeddingResponse:
        return await self._call("embeddings", "embeddings", self._inner.embed, texts)
