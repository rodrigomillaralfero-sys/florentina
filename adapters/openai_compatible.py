"""Adaptador para cualquier API con formato OpenAI (OpenRouter es el primero).

Configuracion por rol: un modelo (y opcionalmente una base_url) para
conversacion, clasificacion y embeddings. Cada respuesta registra
proveedor, modelo, version (la que reporta la API, si la reporta) y modo.

No se prueba en vivo en F0.1: el criterio de aceptacion usa dos adaptadores
simulados. Esta clase cumple el mismo contrato y se valida en el bake-off
de la Semana 0 con credenciales reales.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

import httpx

from core.interfaces.model_adapter import (
    EmbeddingResponse,
    ModelBadOutput,
    ModelIdentity,
    ModelQuotaExceeded,
    ModelRateLimited,
    ModelRequest,
    ModelResponse,
    ModelRole,
    ModelUnavailable,
)


@dataclass(frozen=True)
class RoleConfig:
    model: str
    mode: str  # "fast" | "reasoning" | "embedding"
    base_url: str | None = None
    api_key: str | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)  # p. ej. {"reasoning": {"effort": "high"}}


@dataclass(frozen=True)
class OpenAICompatibleConfig:
    provider: str
    base_url: str
    api_key: str
    roles: Mapping[ModelRole, RoleConfig]
    timeout_seconds: float = 60.0
    app_title: str | None = None
    app_referer: str | None = None


class OpenAICompatibleModelAdapter:
    def __init__(self, config: OpenAICompatibleConfig, *, client: httpx.AsyncClient | None = None) -> None:
        self._cfg = config
        self._client = client or httpx.AsyncClient(timeout=config.timeout_seconds)
        self._versions: dict[ModelRole, str] = {}

    def identity(self, role: ModelRole) -> ModelIdentity:
        rc = self._cfg.roles[role]
        return ModelIdentity(
            provider=self._cfg.provider,
            model=rc.model,
            version=self._versions.get(role, "api"),
            mode=rc.mode,
        )

    # ------------------------------------------------------------------ http

    def _headers(self, rc: RoleConfig) -> dict[str, str]:
        h = {"Authorization": f"Bearer {rc.api_key or self._cfg.api_key}", "Content-Type": "application/json"}
        if self._cfg.app_title:
            h["X-Title"] = self._cfg.app_title
        if self._cfg.app_referer:
            h["HTTP-Referer"] = self._cfg.app_referer
        return h

    async def _post(self, rc: RoleConfig, path: str, body: Mapping[str, Any]) -> Mapping[str, Any]:
        url = (rc.base_url or self._cfg.base_url).rstrip("/") + path
        try:
            resp = await self._client.post(url, headers=self._headers(rc), json=body)
        except httpx.HTTPError as exc:
            raise ModelUnavailable(str(exc)) from exc
        if resp.status_code == 429:
            raise ModelRateLimited(resp.text[:500])
        if resp.status_code in (401, 402, 403):
            # Sin saldo, key invalida o rechazada: repetir la llamada no arregla nada.
            raise ModelQuotaExceeded(f"{resp.status_code}: {resp.text[:300]}")
        if resp.status_code >= 500:
            raise ModelUnavailable(f"{resp.status_code}: {resp.text[:500]}")
        if resp.status_code >= 400:
            raise ModelBadOutput(f"{resp.status_code}: {resp.text[:500]}")
        try:
            return resp.json()
        except ValueError as exc:
            raise ModelBadOutput("respuesta no es JSON") from exc

    # ------------------------------------------------------------------ chat

    async def complete(self, request: ModelRequest) -> ModelResponse:
        rc = self._cfg.roles[request.role]
        body: dict[str, Any] = {
            "model": rc.model,
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
            **dict(rc.extra),
            **dict(request.extra),
        }
        if request.temperature is not None:
            body["temperature"] = request.temperature
        if request.max_tokens is not None:
            body["max_tokens"] = request.max_tokens
        data = await self._post(rc, "/chat/completions", body)
        return self._parse_chat(request.role, data)

    async def complete_structured(self, request: ModelRequest) -> ModelResponse:
        if not request.schema:
            raise ModelBadOutput("complete_structured requiere schema")
        rc = self._cfg.roles[request.role]
        instruction = (
            "Responde unicamente con un objeto JSON valido que cumpla este JSON Schema, sin texto adicional:\n"
            + json.dumps(request.schema, ensure_ascii=False)
        )
        messages = [{"role": m.role, "content": m.content} for m in request.messages]
        if messages and messages[0]["role"] == "system":
            messages[0] = {"role": "system", "content": messages[0]["content"] + "\n\n" + instruction}
        else:
            messages.insert(0, {"role": "system", "content": instruction})
        body: dict[str, Any] = {
            "model": rc.model,
            "messages": messages,
            "response_format": {"type": "json_object"},
            **dict(rc.extra),
            **dict(request.extra),
        }
        if request.temperature is not None:
            body["temperature"] = request.temperature
        if request.max_tokens is not None:
            body["max_tokens"] = request.max_tokens
        data = await self._post(rc, "/chat/completions", body)
        res = self._parse_chat(request.role, data)
        try:
            structured = _extract_json_object(res.text)
            if looks_like_schema(structured):
                # F0.12 (31-10): Grok 4.7 a veces devuelve el esquema en vez de llenarlo; se trata como salida mala para
                # que quien llama repregunte, en vez de reportar "faltan claves" o una ficha vacia.
                raise ModelBadOutput("devolvio el esquema JSON en vez de llenarlo con valores")
            missing = [k for k in request.schema.get("required", []) if k not in structured]
            if missing:
                raise ModelBadOutput(f"faltan claves requeridas: {missing}")
        except ModelBadOutput as exc:
            exc.usage = dict(res.usage)  # 11-11: la llamada ya se cobro; el medidor de gasto la cuenta igual
            raise
        return ModelResponse(text=res.text, identity=res.identity, structured=structured, usage=res.usage, raw_id=res.raw_id)

    def _parse_chat(self, role: ModelRole, data: Mapping[str, Any]) -> ModelResponse:
        try:
            choice = data["choices"][0]
            text = choice["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            err = ModelBadOutput(f"respuesta sin choices: {str(data)[:300]}")
            raw_cost = (data.get("usage") or {}).get("cost") if isinstance(data, Mapping) else None
            err.usage = {"cost_microusd": int(round(float(raw_cost) * 1_000_000))} if isinstance(raw_cost, (int, float)) else {}
            raise err from exc
        if isinstance(data.get("model"), str):
            self._versions[role] = data["model"]
        usage_raw = data.get("usage") or {}
        usage = {k: int(v) for k, v in usage_raw.items() if isinstance(v, (int, float)) and k != "cost"}
        if isinstance(usage_raw.get("cost"), (int, float)):
            usage["cost_microusd"] = int(round(float(usage_raw["cost"]) * 1_000_000))  # el costo real queda registrado
        return ModelResponse(text=text, identity=self.identity(role), usage=usage, raw_id=data.get("id"),
                             citations=_citations(choice.get("message") or {}))

    # ------------------------------------------------------------------ embeddings

    async def embed(self, texts: Sequence[str]) -> EmbeddingResponse:
        rc = self._cfg.roles[ModelRole.EMBEDDINGS]
        if not texts:
            return EmbeddingResponse(vectors=[], identity=self.identity(ModelRole.EMBEDDINGS), dim=0)
        data = await self._post(rc, "/embeddings", {"model": rc.model, "input": list(texts)})
        try:
            items = sorted(data["data"], key=lambda d: d.get("index", 0))
            vectors = [[float(x) for x in it["embedding"]] for it in items]
        except (KeyError, TypeError) as exc:
            raise ModelBadOutput("respuesta de embeddings malformada") from exc
        if isinstance(data.get("model"), str):
            self._versions[ModelRole.EMBEDDINGS] = data["model"]
        dim = len(vectors[0]) if vectors else 0
        if any(len(v) != dim for v in vectors):
            raise ModelBadOutput("vectores con dimension inconsistente")
        usage_raw = data.get("usage") or {}
        usage = {k: int(v) for k, v in usage_raw.items() if isinstance(v, (int, float)) and k != "cost"}
        if isinstance(usage_raw.get("cost"), (int, float)):
            usage["cost_microusd"] = int(round(float(usage_raw["cost"]) * 1_000_000))
        return EmbeddingResponse(vectors=vectors, identity=self.identity(ModelRole.EMBEDDINGS), dim=dim, usage=usage)

    async def aclose(self) -> None:
        await self._client.aclose()


_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


def _citations(message: Mapping[str, Any]) -> tuple[dict[str, str], ...]:
    """Las fuentes de una busqueda web (anotaciones `url_citation`), sin repetir y sin el contenido."""
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for a in message.get("annotations") or ():
        if not isinstance(a, Mapping) or a.get("type") != "url_citation":
            continue
        c = a.get("url_citation") if isinstance(a.get("url_citation"), Mapping) else a
        url = str(c.get("url") or "").strip()
        if url and url not in seen:
            seen.add(url)
            out.append({"url": url, "title": str(c.get("title") or "").strip()})
    return tuple(out)


def looks_like_schema(obj: Any) -> bool:
    """Un objeto que es un JSON Schema (type object + properties con definiciones) y no una respuesta."""
    return (isinstance(obj, dict) and obj.get("type") == "object" and isinstance(obj.get("properties"), dict)
            and any(isinstance(v, dict) and ("type" in v or "description" in v) for v in obj["properties"].values()))


def _extract_json_object(text: str) -> dict[str, Any]:
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = candidate.strip("`")
        candidate = candidate[candidate.find("{") :] if "{" in candidate else candidate
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        m = _JSON_BLOCK.search(text)
        if not m:
            # Sin citar la salida: el mensaje viaja a `problems`, al error del job y a las alertas (F0.8 r2).
            raise ModelBadOutput(f"sin JSON en la salida ({len(text)} caracteres)")
        try:
            parsed = json.loads(m.group(0))
        except json.JSONDecodeError as exc:
            raise ModelBadOutput(f"JSON invalido: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ModelBadOutput("la salida estructurada debe ser un objeto")
    return parsed
