"""Adaptador nativo de la API de Gemini (Google AI Studio) y enrutador por rol.

Por que nativo: llave y tope de gasto propios, sin intermediario. Los filtros de seguridad quedan en los valores
por defecto del proveedor (no se envian `safetySettings`).

- `GoogleNativeAdapter` atiende los roles que se le asignan (`complete` y `complete_structured`). No hace embeddings.
- `RoutedModelAdapter` manda cada rol a su adaptador: los de Google aqui, el resto (y los embeddings) a OpenRouter.
- El costo se calcula con el precio de lista configurado (US$ por millon) y viaja en `usage.cost_microusd`, para que
  el medidor de gasto y el cortacircuito lo cuenten igual que lo de OpenRouter. El pensamiento cuenta como salida.
- Mensajes: el primer `system` va como `systemInstruction`; los `system` del medio del hilo (notas del sistema) van
  como texto de usuario; mensajes seguidos del mismo rol se juntan (Gemini alterna usuario/modelo).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

import httpx

from adapters.openai_compatible import _extract_json_object, looks_like_schema
from core.interfaces.model_adapter import (
    EmbeddingResponse, ModelBadOutput, ModelIdentity, ModelQuotaExceeded, ModelRateLimited, ModelRequest, ModelResponse,
    ModelRole, ModelUnavailable,
)

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
PREFIX = "google:"


@dataclass(frozen=True)
class GoogleRoleConfig:
    model: str  # sin el prefijo `google:`
    mode: str = "fast"
    price_in: float = 0.0  # US$ por millon de tokens de entrada
    price_out: float = 0.0  # US$ por millon de tokens de salida (incluye pensamiento)
    generation: Mapping[str, Any] = field(default_factory=dict)  # se mezcla en generationConfig (p. ej. thinkingConfig)


class GoogleNativeAdapter:
    def __init__(self, *, api_key: str, roles: Mapping[ModelRole, GoogleRoleConfig], timeout_seconds: float = 120.0,
                 retries: int = 2, client: httpx.AsyncClient | None = None, base_url: str = BASE_URL) -> None:
        if not api_key:
            raise RuntimeError("falta GOOGLE_API_KEY para los roles de Google")
        self._key = api_key
        self._roles = dict(roles)
        self._retries = retries
        self._base = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)
        self._versions: dict[ModelRole, str] = {}

    def roles(self) -> set[ModelRole]:
        return set(self._roles)

    def identity(self, role: ModelRole) -> ModelIdentity:
        rc = self._roles[role]
        return ModelIdentity(provider="google", model=rc.model, version=self._versions.get(role, "api"), mode=rc.mode)

    # ------------------------------------------------------------------ cuerpo

    @staticmethod
    def contents(messages: Sequence[Any]) -> tuple[str, list[dict[str, Any]]]:
        system = ""
        out: list[dict[str, Any]] = []
        for i, m in enumerate(messages):
            role, text = m.role, m.content or ""
            if role == "system" and i == 0:
                system = text
                continue
            g_role = "model" if role == "assistant" else "user"
            if role == "system":
                text = text if text.lstrip().startswith("[") else f"[sistema] {text}"
            if out and out[-1]["role"] == g_role:
                out[-1]["parts"].append({"text": text})
            else:
                out.append({"role": g_role, "parts": [{"text": text}]})
        if not out:
            out = [{"role": "user", "parts": [{"text": "[sistema] Es tu turno."}]}]
        return system, out

    def _body(self, rc: GoogleRoleConfig, request: ModelRequest, *, system_extra: str = "", json_mode: bool = False) -> dict[str, Any]:
        system, contents = self.contents(request.messages)
        if system_extra:
            system = (system + "\n\n" + system_extra).strip()
        gen: dict[str, Any] = dict(rc.generation)
        if request.temperature is not None:
            gen["temperature"] = request.temperature
        if request.max_tokens is not None:
            gen["maxOutputTokens"] = request.max_tokens
        if json_mode:
            gen["responseMimeType"] = "application/json"
        body: dict[str, Any] = {"contents": contents, "generationConfig": gen}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        return body

    async def _post(self, rc: GoogleRoleConfig, body: Mapping[str, Any]) -> Mapping[str, Any]:
        url = f"{self._base}/models/{rc.model}:generateContent"
        last: Exception | None = None
        for attempt in range(self._retries + 1):
            try:
                resp = await self._client.post(url, headers={"x-goog-api-key": self._key, "Content-Type": "application/json"}, json=body)
            except httpx.HTTPError as exc:
                last = ModelUnavailable(str(exc))
            else:
                if resp.status_code == 200:
                    try:
                        return resp.json()
                    except ValueError as exc:
                        raise ModelBadOutput("respuesta no es JSON") from exc
                if resp.status_code == 429:
                    last = ModelRateLimited(resp.text[:300])
                elif resp.status_code in (401, 402, 403):
                    raise ModelQuotaExceeded(f"{resp.status_code}: {resp.text[:300]}")
                elif resp.status_code >= 500:
                    last = ModelUnavailable(f"{resp.status_code}: {resp.text[:300]}")
                else:
                    raise ModelBadOutput(f"{resp.status_code}: {resp.text[:300]}")
            if attempt < self._retries:
                await asyncio.sleep(3 * (attempt + 1))  # saturacion del servicio (503/429): espera corta y reintenta
        assert last is not None
        raise last

    def _parse(self, role: ModelRole, rc: GoogleRoleConfig, data: Mapping[str, Any]) -> ModelResponse:
        meta = data.get("usageMetadata") or {}
        p_in = int(meta.get("promptTokenCount") or 0)
        p_out = int(meta.get("candidatesTokenCount") or 0)
        p_think = int(meta.get("thoughtsTokenCount") or 0)
        usage = {"prompt_tokens": p_in, "completion_tokens": p_out + p_think, "reasoning_tokens": p_think,
                 "cost_microusd": int(round(p_in * rc.price_in + (p_out + p_think) * rc.price_out))}
        if isinstance(data.get("modelVersion"), str):
            self._versions[role] = data["modelVersion"]
        cand = (data.get("candidates") or [{}])[0]
        parts = (cand.get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if "text" in p and not p.get("thought"))
        reasoning = "\n".join(p.get("text", "").strip() for p in parts if p.get("thought") and p.get("text")).strip() or None
        if not text.strip():
            block = (data.get("promptFeedback") or {}).get("blockReason")
            err = ModelBadOutput(f"salida vacia (finish={cand.get('finishReason')}, bloqueo={block})")
            err.usage = usage  # ya se cobro: el medidor la cuenta igual
            raise err
        return ModelResponse(text=text, identity=self.identity(role), usage=usage, raw_id=data.get("responseId"), reasoning=reasoning)

    # ------------------------------------------------------------------ API

    async def complete(self, request: ModelRequest) -> ModelResponse:
        rc = self._roles[request.role]
        return self._parse(request.role, rc, await self._post(rc, self._body(rc, request)))

    async def complete_structured(self, request: ModelRequest) -> ModelResponse:
        if not request.schema:
            raise ModelBadOutput("complete_structured requiere schema")
        rc = self._roles[request.role]
        instruction = ("Responde unicamente con un objeto JSON valido que cumpla este JSON Schema, sin texto adicional:\n"
                       + json.dumps(request.schema, ensure_ascii=False))
        res = self._parse(request.role, rc, await self._post(rc, self._body(rc, request, system_extra=instruction, json_mode=True)))
        try:
            structured = _extract_json_object(res.text)
            if looks_like_schema(structured):
                raise ModelBadOutput("devolvio el esquema JSON en vez de llenarlo con valores")
            missing = [k for k in request.schema.get("required", []) if k not in structured]
            if missing:
                raise ModelBadOutput(f"faltan claves requeridas: {missing}")
        except ModelBadOutput as exc:
            exc.usage = dict(res.usage)
            raise
        return ModelResponse(text=res.text, identity=res.identity, structured=structured, usage=res.usage, raw_id=res.raw_id,
                             reasoning=res.reasoning)

    async def embed(self, texts: Sequence[str]) -> EmbeddingResponse:  # pragma: no cover - no se le asignan embeddings
        raise ModelBadOutput("el adaptador de Google no hace embeddings en este motor")

    async def aclose(self) -> None:
        await self._client.aclose()


class RoutedModelAdapter:
    """Cada rol a su adaptador: los de `google` al nativo, el resto y los embeddings al de base (OpenRouter)."""

    def __init__(self, base: Any, google: GoogleNativeAdapter) -> None:
        self._base = base
        self._google = google
        self._g_roles = google.roles()

    def _for(self, role: ModelRole) -> Any:
        return self._google if role in self._g_roles else self._base

    def identity(self, role: ModelRole) -> ModelIdentity:
        return self._for(role).identity(role)

    async def complete(self, request: ModelRequest) -> ModelResponse:
        return await self._for(request.role).complete(request)

    async def complete_structured(self, request: ModelRequest) -> ModelResponse:
        return await self._for(request.role).complete_structured(request)

    async def embed(self, texts: Sequence[str]) -> EmbeddingResponse:
        return await self._base.embed(texts)

    def __getattr__(self, name: str) -> Any:  # lo demas (p. ej. atributos propios del adaptador de base) va al de base
        return getattr(self._base, name)

    async def aclose(self) -> None:
        await self._google.aclose()
        if hasattr(self._base, "aclose"):
            await self._base.aclose()
