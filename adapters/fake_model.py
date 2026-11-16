"""Adaptadores de modelo simulados.

Dos implementaciones distintas del mismo contrato, para demostrar que el
motor no distingue entre ellas:

* `EchoModelAdapter`: devuelve el ultimo mensaje del usuario. Estructurado:
  rellena el schema con valores neutros. Embeddings: hash determinista.
* `ScriptedModelAdapter`: devuelve respuestas de una lista (o de una
  funcion), util para simular conversaciones en pruebas.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any, Callable, Mapping, Sequence

from core.interfaces.model_adapter import (
    EmbeddingResponse,
    ModelBadOutput,
    ModelIdentity,
    ModelRequest,
    ModelResponse,
    ModelRole,
)


def hashed_vector(text: str, dim: int) -> list[float]:
    """Vector determinista a partir del texto. No es semantico; es estable."""
    out: list[float] = []
    counter = 0
    while len(out) < dim:
        digest = hashlib.sha256(f"{counter}:{text}".encode("utf-8")).digest()
        for i in range(0, len(digest) - 1, 2):
            if len(out) >= dim:
                break
            val = int.from_bytes(digest[i : i + 2], "big") / 65535.0
            out.append(val * 2.0 - 1.0)
        counter += 1
    norm = math.sqrt(sum(x * x for x in out)) or 1.0
    return [x / norm for x in out]


_TOKEN = re.compile(r"[\wáéíóúüñ]+", re.IGNORECASE)


def bag_of_words_vector(text: str, dim: int) -> list[float]:
    """Vector por 'hashing trick': cada palabra suma 1 en un bucket. Textos que comparten
    palabras se parecen; textos sin palabras en comun dan coseno ~0. Determinista y sin red:
    es lo que las pruebas de memoria y de conocimiento base usan en vez de un modelo real."""
    out = [0.0] * dim
    for tok in _TOKEN.findall(text.lower()):
        if len(tok) < 3:
            continue
        h = int.from_bytes(hashlib.md5(tok.encode("utf-8")).digest()[:4], "big")
        out[h % dim] += 1.0
    norm = math.sqrt(sum(x * x for x in out))
    if norm == 0.0:
        return hashed_vector(text, dim)
    return [x / norm for x in out]


def fill_from_schema(schema: Mapping[str, Any], *, text: str) -> dict[str, Any]:
    """Rellena un objeto que cumple el subconjunto de JSON Schema que usa el motor."""
    props: Mapping[str, Any] = schema.get("properties", {})
    out: dict[str, Any] = {}
    for key, spec in props.items():
        types = spec.get("type", "string")
        if isinstance(types, list):
            types = [t for t in types if t != "null"] or ["null"]
            t = types[0]
        else:
            t = types
        if key == "text" and t == "string":
            out[key] = text
        elif t == "string":
            out[key] = spec.get("default", "")
        elif t == "boolean":
            out[key] = bool(spec.get("default", False))
        elif t == "integer":
            out[key] = int(spec.get("default", 0))
        elif t == "number":
            out[key] = float(spec.get("default", 0.0))
        elif t == "array":
            out[key] = list(spec.get("default", []))
        elif t == "object":
            out[key] = dict(spec.get("default", {}))
        else:
            out[key] = None
    return out


class _BaseFake:
    def __init__(self, *, provider: str, model: str, version: str, dim: int, semantic: bool = False) -> None:
        self._provider = provider
        self._model = model
        self._version = version
        self._dim = dim
        self._semantic = semantic  # True: bolsa de palabras (parecido = palabras en comun); False: hash opaco
        self.calls: list[ModelRequest] = []
        self.embed_calls: list[Sequence[str]] = []
        self.fail_embeddings: Exception | None = None  # para simular un proveedor de embeddings caido

    def identity(self, role: ModelRole) -> ModelIdentity:
        mode = {
            ModelRole.CONVERSATION: "fast",
            ModelRole.CLASSIFIER: "fast",
            ModelRole.EMBEDDINGS: "embedding",
        }[role]
        return ModelIdentity(provider=self._provider, model=self._model, version=self._version, mode=mode)

    async def embed(self, texts: Sequence[str]) -> EmbeddingResponse:
        self.embed_calls.append(list(texts))
        if self.fail_embeddings is not None:
            raise self.fail_embeddings
        fn = bag_of_words_vector if self._semantic else hashed_vector
        return EmbeddingResponse(
            vectors=[fn(t, self._dim) for t in texts],
            identity=self.identity(ModelRole.EMBEDDINGS),
            dim=self._dim,
            usage={"prompt_tokens": sum(len(t.split()) for t in texts), "cost_microusd": len(texts)},
        )


class EchoModelAdapter(_BaseFake):
    def __init__(self, *, dim: int = 8, semantic: bool = False) -> None:
        super().__init__(provider="fake", model="echo", version="1", dim=dim, semantic=semantic)

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls.append(request)
        text = request.last_user_text
        return ModelResponse(
            text=text,
            identity=self.identity(request.role),
            usage={"input_tokens": len(text.split()), "output_tokens": len(text.split())},
        )

    async def complete_structured(self, request: ModelRequest) -> ModelResponse:
        self.calls.append(request)
        if not request.schema:
            raise ModelBadOutput("complete_structured requiere schema")
        text = request.last_user_text
        structured = fill_from_schema(request.schema, text=text)
        return ModelResponse(
            text=json.dumps(structured, ensure_ascii=False),
            identity=self.identity(request.role),
            structured=structured,
            usage={"input_tokens": len(text.split()), "output_tokens": 1},
        )


Script = Sequence[str] | Callable[[ModelRequest], str]


class ScriptedModelAdapter(_BaseFake):
    """Responde segun un guion. Para estructurado, el guion debe producir JSON valido."""

    def __init__(self, script: Script, *, model: str = "scripted", dim: int = 16, semantic: bool = False) -> None:
        super().__init__(provider="fake", model=model, version="2", dim=dim, semantic=semantic)
        self._script = script
        self._i = 0

    def _next(self, request: ModelRequest) -> str:
        if callable(self._script):
            return self._script(request)
        if not self._script:
            return ""
        text = self._script[min(self._i, len(self._script) - 1)]
        self._i += 1
        return text

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls.append(request)
        text = self._next(request)
        return ModelResponse(text=text, identity=self.identity(request.role), usage={"output_tokens": len(text.split())})

    async def complete_structured(self, request: ModelRequest) -> ModelResponse:
        self.calls.append(request)
        if not request.schema:
            raise ModelBadOutput("complete_structured requiere schema")
        raw = self._next(request)
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError as exc:
            raise ModelBadOutput(f"JSON invalido: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ModelBadOutput("la salida estructurada debe ser un objeto")
        # Un guion que no cubre el schema completo se completa con valores neutros,
        # igual que haria un adaptador real que valida y rellena ausentes.
        structured = {**fill_from_schema(request.schema, text=request.last_user_text), **parsed}
        return ModelResponse(text=raw, identity=self.identity(request.role), structured=structured, usage={"output_tokens": 1})
