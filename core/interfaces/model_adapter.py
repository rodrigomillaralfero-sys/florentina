"""Costura — Adaptador de modelo.

Interfaz independiente de proveedor para los roles de inferencia:
conversacion, clasificacion y embeddings. Normaliza mensajes, salidas
estructuradas, errores y versiones. No almacena memoria: eso vive en el
motor, no en el adaptador.

Cada respuesta lleva su `ModelIdentity` (proveedor, modelo, version, modo)
para que cada decision quede registrada con quien la produjo. Cambiar de
proveedor es cambiar de adaptador; nada mas se reinicia.

El contrato que todo adaptador debe cumplir esta en
`core.testing.model_adapter_contract` y lo exige `tests/test_model_adapter_contract.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping, Protocol, Sequence


class ModelRole(StrEnum):
    CONVERSATION = "conversation"  # responde a la persona
    CLASSIFIER = "classifier"  # tareas cortas y estructuradas (ruteo, etiquetas)
    EMBEDDINGS = "embeddings"


@dataclass(frozen=True)
class ModelIdentity:
    provider: str
    model: str
    version: str
    mode: str  # "fast" | "reasoning" | "embedding"


@dataclass(frozen=True)
class ModelMessage:
    role: str  # "system" | "user" | "assistant"
    content: str


@dataclass(frozen=True)
class ModelRequest:
    role: ModelRole
    messages: Sequence[ModelMessage]
    temperature: float | None = None
    max_tokens: int | None = None
    schema: Mapping[str, Any] | None = None  # JSON Schema (subconjunto: type/properties/required)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    extra: Mapping[str, Any] = field(default_factory=dict)  # mejora 2: campos del cuerpo de ESTA llamada (p. ej. busqueda web); pisan los del rol

    @property
    def last_user_text(self) -> str:
        for m in reversed(self.messages):
            if m.role == "user":
                return m.content
        return ""


@dataclass(frozen=True)
class ModelResponse:
    text: str
    identity: ModelIdentity
    structured: Mapping[str, Any] | None = None
    usage: Mapping[str, int] = field(default_factory=dict)
    raw_id: str | None = None
    citations: tuple[Mapping[str, str], ...] = ()  # mejora 2: fuentes que trajo una busqueda web ({url, title}), si hubo
    reasoning: str | None = None  # 14-11: resumen del razonamiento propio del modelo, si el proveedor lo entrega (Gemini)


@dataclass(frozen=True)
class EmbeddingResponse:
    vectors: Sequence[Sequence[float]]
    identity: ModelIdentity
    dim: int
    usage: Mapping[str, int] = field(default_factory=dict)  # tokens y, si el proveedor lo reporta, cost_microusd


class ModelError(Exception):
    """Base de todos los errores normalizados del adaptador."""


class ModelUnavailable(ModelError):
    """Proveedor caido, timeout, red."""


class ModelRateLimited(ModelError):
    """429 o equivalente. Reintentable con espera."""


class ModelQuotaExceeded(ModelError):
    """402/403: sin saldo o key rechazada. No se reintenta en el turno; la salud avisa al operador."""


class ModelBadOutput(ModelError):
    """El modelo respondio, pero no en la forma pedida (p. ej. JSON invalido)."""


class ModelAdapter(Protocol):
    def identity(self, role: ModelRole) -> ModelIdentity: ...

    async def complete(self, request: ModelRequest) -> ModelResponse: ...

    async def complete_structured(self, request: ModelRequest) -> ModelResponse:
        """Como `complete`, pero `structured` es un dict que respeta `request.schema`.

        Si el modelo no produce algo parseable, levanta `ModelBadOutput`.
        """
        ...

    async def embed(self, texts: Sequence[str]) -> EmbeddingResponse: ...
