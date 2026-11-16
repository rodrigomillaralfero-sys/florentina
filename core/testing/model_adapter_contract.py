"""Contrato que todo `ModelAdapter` debe cumplir.

Se ejecuta contra cualquier adaptador (simulado o real). Si dos adaptadores
distintos pasan este contrato, la prueba de sustitucion de modelo tiene
sentido: el motor no ve diferencia entre ellos.

Uso:

    checks = ModelAdapterContract(adapter)
    await checks.run_all()
"""

from __future__ import annotations

from typing import Callable, Sequence

from core.interfaces.model_adapter import (
    ModelAdapter,
    ModelBadOutput,
    ModelError,
    ModelIdentity,
    ModelMessage,
    ModelRequest,
    ModelRole,
)


class ContractViolation(AssertionError):
    pass


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise ContractViolation(msg)


class ModelAdapterContract:
    def __init__(self, adapter: ModelAdapter) -> None:
        self.adapter = adapter

    # -- identidad -------------------------------------------------------

    async def identity_is_complete_for_every_role(self) -> None:
        for role in ModelRole:
            ident = self.adapter.identity(role)
            _require(isinstance(ident, ModelIdentity), f"identity({role}) no es ModelIdentity")
            for f in ("provider", "model", "version", "mode"):
                _require(bool(getattr(ident, f)), f"identity({role}).{f} vacio")

    # -- completar -------------------------------------------------------

    async def complete_returns_text_with_identity(self) -> None:
        req = ModelRequest(role=ModelRole.CONVERSATION, messages=[ModelMessage("user", "hola")])
        res = await self.adapter.complete(req)
        _require(isinstance(res.text, str), "complete().text no es str")
        _require(res.identity == self.adapter.identity(ModelRole.CONVERSATION), "identity de la respuesta no coincide con identity(role)")
        _require(all(isinstance(v, int) for v in res.usage.values()), "usage debe ser int por clave")

    async def complete_with_other_role_reports_its_own_identity(self) -> None:
        req = ModelRequest(role=ModelRole.CLASSIFIER, messages=[ModelMessage("user", "resume")])
        res = await self.adapter.complete(req)
        _require(res.identity == self.adapter.identity(ModelRole.CLASSIFIER), "identity del clasificador no coincide")

    # -- estructurado ----------------------------------------------------

    async def structured_respects_schema_keys(self) -> None:
        schema = {
            "type": "object",
            "properties": {"text": {"type": "string"}, "ask": {"type": "boolean"}, "score": {"type": ["integer", "null"]}},
            "required": ["text", "ask"],
        }
        req = ModelRequest(role=ModelRole.CLASSIFIER, messages=[ModelMessage("user", "clasifica esto")], schema=schema)
        res = await self.adapter.complete_structured(req)
        _require(isinstance(res.structured, dict), "structured no es dict")
        for k in schema["required"]:
            _require(k in res.structured, f"structured sin clave requerida '{k}'")
        _require(isinstance(res.structured["text"], str), "structured.text no es str")
        _require(isinstance(res.structured["ask"], bool), "structured.ask no es bool")

    async def structured_without_schema_is_an_error(self) -> None:
        req = ModelRequest(role=ModelRole.CLASSIFIER, messages=[ModelMessage("user", "x")], schema=None)
        try:
            await self.adapter.complete_structured(req)
        except ModelBadOutput:
            return
        except ModelError as exc:
            raise ContractViolation(f"sin schema debe ser ModelBadOutput, fue {type(exc).__name__}")
        raise ContractViolation("complete_structured sin schema debe fallar con ModelBadOutput")

    # -- embeddings ------------------------------------------------------

    async def embed_returns_one_vector_per_text_with_stable_dim(self) -> None:
        texts = ["uno", "dos", "tres"]
        res = await self.adapter.embed(texts)
        _require(len(res.vectors) == len(texts), "embed devolvio un numero distinto de vectores")
        _require(res.dim > 0, "dim debe ser > 0")
        _require(all(len(v) == res.dim for v in res.vectors), "vectores con dim inconsistente")
        _require(all(isinstance(x, float) for v in res.vectors for x in v), "componentes deben ser float")
        again = await self.adapter.embed(["cuatro"])
        _require(again.dim == res.dim, "dim cambio entre llamadas")
        _require(res.identity == self.adapter.identity(ModelRole.EMBEDDINGS), "identity de embeddings no coincide")

    async def embed_is_stable(self) -> None:
        """El mismo texto da (casi) el mismo vector. Los proveedores reales tienen ruido de punto
        flotante entre llamadas (visto con text-embedding-3-small en F0.2), asi que se exige
        similitud coseno > 0.999, no igualdad exacta."""
        a = await self.adapter.embed(["mismo texto"])
        b = await self.adapter.embed(["mismo texto"])
        _require(_cosine(a.vectors[0], b.vectors[0]) > 0.999, "el mismo texto debe dar casi el mismo vector")

    async def embed_empty_is_empty(self) -> None:
        res = await self.adapter.embed([])
        _require(len(res.vectors) == 0, "embed([]) debe devolver cero vectores")

    # -- runner ----------------------------------------------------------

    def checks(self) -> Sequence[tuple[str, Callable[[], object]]]:
        return [
            ("identity_is_complete_for_every_role", self.identity_is_complete_for_every_role),
            ("complete_returns_text_with_identity", self.complete_returns_text_with_identity),
            ("complete_with_other_role_reports_its_own_identity", self.complete_with_other_role_reports_its_own_identity),
            ("structured_respects_schema_keys", self.structured_respects_schema_keys),
            ("structured_without_schema_is_an_error", self.structured_without_schema_is_an_error),
            ("embed_returns_one_vector_per_text_with_stable_dim", self.embed_returns_one_vector_per_text_with_stable_dim),
            ("embed_is_stable", self.embed_is_stable),
            ("embed_empty_is_empty", self.embed_empty_is_empty),
        ]

    async def run_all(self) -> list[str]:
        passed: list[str] = []
        for name, check in self.checks():
            await check()  # type: ignore[misc]
            passed.append(name)
        return passed
